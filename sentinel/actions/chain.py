"""chain.run — a single approval-gated action that runs N sub-actions.

Why this exists
---------------
Phase D3 gave the slime one-shot actions ("open this", "see the
screen"). Real requests often chain: "open VS Code, focus it, then
take a screenshot". Doing these as separate proposals means three
approval cards, three clicks, and the user has to remember what's
coming next.

This module ships a `chain.run` action type. The LLM proposes a
single `chain.run` whose payload is an ordered list of sub-actions.
The approval card displays all the steps together. One click of
同意 executes the whole chain via the Phase C3 workflow engine —
each step is a workflow Step, with retry and checkpointing free.
Per-step failure skips the rest of the chain rather than partially
running; the user sees the full result trail in the action_result
audit entry.

Scope for D4 (this phase)
-------------------------
- **Static chains only.** Every step's payload must be fully
  specified up-front by the LLM. Inter-step data flow (e.g. "use
  the path from step N-1 as input to step N") is NOT supported;
  that's Phase D5.
- **All sub-actions go through their normal policy checks.** A
  chain can't hide an unsafe step behind bulk approval — if any
  step's policy denies, the whole chain is denied at submit time
  with the per-step findings aggregated.
- **Max 5 steps per chain.** Above that the LLM is probably wrong
  about what the user wants; a real multi-step plan that big should
  be broken into confirmable stages.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("sentinel.actions.chain")

MAX_STEPS = 5


def _step_shape(step: Any) -> Optional[dict]:
    """Validate one step declaration. Returns the step dict if OK,
    None if it's malformed (to drop + flag at policy time)."""
    if not isinstance(step, dict):
        return None
    if not isinstance(step.get("action_type"), str) or not step["action_type"]:
        return None
    payload = step.get("payload", {})
    if not isinstance(payload, dict):
        return None
    return {
        "action_type": step["action_type"],
        "payload": payload,
        "title": str(step.get("title") or step["action_type"]),
    }


def policy_check(payload: dict) -> tuple[bool, list[dict]]:
    """Policy for chain.run.

    Validates the shape, per-step cap, and recursively runs every
    sub-action's own policy (if registered) so a chain can't sneak
    in a disallowed step.
    """
    findings: list[dict] = []
    steps_raw = (payload or {}).get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        return False, [{"level": "error", "msg": "steps must be a non-empty list"}]
    if len(steps_raw) > MAX_STEPS:
        return False, [{
            "level": "error",
            "msg": f"too many steps ({len(steps_raw)}); max {MAX_STEPS}",
        }]

    # Block recursive chains at the proposal layer — a chain containing
    # chain.run is a footgun: nested policy checks, nested workflow
    # engine runs, nested audit entries. If a real need emerges we can
    # lift this, but the first cut shouldn't allow it.
    for idx, raw in enumerate(steps_raw):
        normalized = _step_shape(raw)
        if normalized is None:
            return False, [{
                "level": "error",
                "msg": f"step {idx} malformed: need {{action_type, payload}}",
            }]
        if normalized["action_type"] == "chain.run":
            return False, [{
                "level": "error",
                "msg": f"step {idx}: nested chain.run not allowed",
            }]

    # Run each step's own policy through the approval registry. We
    # import lazily to avoid a circular import at module load.
    from sentinel.growth.approval import _action_handlers

    for idx, raw in enumerate(steps_raw):
        normalized = _step_shape(raw)
        assert normalized is not None  # validated above
        at = normalized["action_type"]
        entry = _action_handlers.get(at)
        if entry is None:
            return False, [{
                "level": "error",
                "msg": f"step {idx}: unknown action_type {at!r}",
            }]
        sub_policy = entry.get("policy")
        if sub_policy is None:
            continue  # no policy = no constraints (read-only actions)
        try:
            allowed, sub_findings = sub_policy(normalized["payload"])
        except Exception as e:
            log.warning(f"chain step {idx} policy check raised: {e}")
            return False, [{
                "level": "error",
                "msg": f"step {idx}: policy check failed: {e}",
            }]
        if not allowed:
            # Aggregate sub-findings with step index so the approval
            # card can point at the specific offending step.
            aggregated = [
                {**f, "msg": f"step {idx} ({at}): {f.get('msg', '')}"}
                for f in sub_findings
            ]
            return False, aggregated
        # Forward any warn/info findings so they surface on the card
        # even though the step is allowed. Lets the user see e.g.
        # "step 2 will launch an executable" before clicking approve.
        findings.extend(
            {**f, "msg": f"step {idx} ({at}): {f.get('msg', '')}"}
            for f in sub_findings
        )
    return True, findings


def execute(payload: dict) -> dict:
    """Handler: run each sub-action via the Phase C3 workflow engine.

    We translate the declarative step list into a Workflow + run it.
    Per-step results land in a WorkflowRun; we summarize that into a
    single dict so the audit log and chat formatter have something
    compact to show.

    Using the workflow engine (instead of just a for-loop) gives us:
      - Checkpoint on every step transition: if the process is killed
        mid-chain, resume_with() picks up where we left off.
      - Retry policy per step (max_attempts / backoff).
      - Uniform audit trail format shared with other workflow runs.
    """
    from sentinel.workflow import Workflow, Step, WorkflowEngine
    from sentinel.growth.approval import _action_handlers

    steps_raw = (payload or {}).get("steps") or []

    # Build a Workflow where each sub-action is a Step. Each Step's
    # fn dispatches to the registered action handler — we don't
    # re-run policy at execution (policy ran at submit time; the user
    # saw and accepted the findings).
    wf_steps: list[Step] = []
    step_ids: list[tuple[str, str]] = []  # (step_name, action_type) for summary
    for idx, raw in enumerate(steps_raw):
        normalized = _step_shape(raw) or {}
        at = normalized.get("action_type", "")
        title = normalized.get("title", at)
        step_name = f"s{idx}_{at.replace('.', '_')}"
        step_ids.append((step_name, at))

        entry = _action_handlers.get(at)
        handler = entry["handler"] if entry else None

        def _step_fn(ctx, handler=handler, at=at,
                     step_payload=normalized.get("payload", {})) -> dict:
            # Defensive: the policy check already covered unknown
            # types, but a race (handler unregistered after submit)
            # would land here — fail the step cleanly.
            if handler is None:
                raise ValueError(f"handler for {at} not registered")
            return handler(step_payload)

        # Retry policy: one attempt per step by default. Callers can
        # opt into retries later via a "max_attempts" field on the
        # step declaration; keeping it simple for v1.
        wf_step = Step(
            name=step_name,
            fn=_step_fn,
            depends_on=[step_ids[idx - 1][0]] if idx > 0 else [],
            max_attempts=int(normalized.get("max_attempts", 1) or 1),
        )
        wf_steps.append(wf_step)

    wf = Workflow(id="chain.run", steps=wf_steps,
                  description=f"{len(wf_steps)}-step chain")
    run = WorkflowEngine().run(wf)

    # Summarize: step-by-step status + truncated per-step result.
    step_summaries: list[dict] = []
    for (name, at), step in zip(step_ids, wf_steps):
        state = run.steps.get(name)
        if state is None:
            step_summaries.append({"action_type": at, "status": "missing"})
            continue
        # Don't dump full handler result dicts — trim to a compact
        # preview so chain result stays JSON-sane in the audit log.
        preview = state.result
        if isinstance(preview, dict):
            preview = {
                k: (v if len(repr(v)) < 200 else repr(v)[:200] + "…")
                for k, v in list(preview.items())[:6]
            }
        step_summaries.append({
            "action_type": at,
            "status": state.status,
            "attempts": state.attempts,
            "result_preview": preview,
            "error": state.error,
        })

    return {
        "ok": run.status == "success",
        "run_id": run.run_id,
        "status": run.status,
        "steps": step_summaries,
    }


def register() -> None:
    """Register chain.run with the approval queue. Called from the
    surface handlers bootstrap."""
    from sentinel.growth import register_action_handler
    register_action_handler(
        action_type="chain.run",
        handler=execute,
        policy=policy_check,
    )
