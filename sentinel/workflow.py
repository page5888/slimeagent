"""Workflow engine — multi-step flows with checkpoint, retry, audit.

Why this exists
---------------
The slime's async pipelines have the same shape everywhere but are
implemented ad-hoc with try/except ladders:

  distill:   collect → LLM distill → persist → update profile
  replay:    fetch pending ledger → for each: grant → mark settled
  federation: approve candidate → submit → reward drop → store memory
  Phase D:   any "do X, then Y, handle errors" computer-use chain

Each iteration over these patterns ended with failures that were
hard to debug (which step broke? can we retry just that one?) and
impossible to resume (a crash mid-distill loses the collected events
and we re-collect next time). A shared workflow primitive solves all
three at once.

Inspired by moeru-ai/airi's
`services/computer-use-mcp/src/workflows/engine.ts` + task-memory
scratch. We keep it narrow:

  - Topological (DAG) step execution
  - Per-step retry with max_attempts + optional backoff
  - Per-run persistence to ~/.hermes/workflows/<run_id>.json —
    survive process restart, resume from last completed step
  - Audit log of every step transition
  - Sync for now; async wrappers can come later since the persistence
    layer doesn't care what the step returns

Non-goals for C3
----------------
- Parallel step execution. A step only starts when all its deps are
  done; we run one at a time. Safe default; parallelism can be added
  via `concurrency_limit` once we have a workflow that benefits.
- Dynamic step graphs (generated mid-run). Steps are declared up-front.
- Distributed execution. Single desktop app, single process.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
import traceback
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


log = logging.getLogger("sentinel.workflow")

WORKFLOWS_DIR = Path.home() / ".hermes" / "workflows"
AUDIT_LOG = WORKFLOWS_DIR / "workflows.jsonl"

# ── Types ──────────────────────────────────────────────────────────


class StepStatus(str, Enum):
    PENDING = "pending"       # not yet started
    RUNNING = "running"        # currently executing (or mid-retry)
    SUCCESS = "success"
    FAILED = "failed"          # exhausted retries
    SKIPPED = "skipped"        # upstream dep failed


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"        # all steps succeeded or explicitly skipped
    FAILED = "failed"          # at least one step failed
    ABANDONED = "abandoned"    # process crashed / user gave up mid-run


@dataclass
class StepResult:
    """Per-step checkpoint. Written to disk so resume knows what to
    do. Plain dataclass so asdict() produces JSON-friendly output;
    `result` is whatever the step returned and MUST be JSON-serializable
    — callers that want rich state should return dicts, not custom
    objects.
    """
    status: str = StepStatus.PENDING.value
    attempts: int = 0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    # Full traceback for debugging — stored on disk so even after
    # process exit the user can reconstruct what broke.
    traceback: Optional[str] = None


# A step callable receives a Context and returns anything JSON-safe.
StepFn = Callable[["Context"], Any]


@dataclass
class Step:
    """A named node in the DAG.

    `depends_on` holds names of other steps that must reach SUCCESS
    before this one runs. A step whose deps fail gets marked SKIPPED
    and the downstream chain is skipped transitively — keeps the run
    honest: we never half-complete a pipeline that lost a prerequisite.
    """
    name: str
    fn: StepFn
    depends_on: list[str] = field(default_factory=list)
    max_attempts: int = 1
    backoff_seconds: float = 0.0
    # Exception types considered "retryable". Others bypass retry and
    # fail the step immediately. Default: all exceptions retryable.
    retry_on: tuple = (Exception,)


@dataclass
class Workflow:
    """A collection of steps + metadata. Static — the same Workflow
    instance can be run many times; each run produces its own
    WorkflowRun state."""
    id: str                                # stable identifier
    steps: list[Step]
    description: str = ""


# ── Run state ──────────────────────────────────────────────────────


@dataclass
class WorkflowRun:
    """One execution's state, persisted to disk for resumability.

    Persistence shape (JSON) mirrors this dataclass via asdict(). We
    serialize/deserialize ourselves rather than pickling because a
    pickle-based schema would break across Python versions and couldn't
    be inspected in a text editor.
    """
    run_id: str
    workflow_id: str
    status: str = RunStatus.RUNNING.value
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    steps: dict[str, StepResult] = field(default_factory=dict)
    # Caller-provided initial inputs available to every step via
    # ctx.inputs. Kept separate from step results so steps can't
    # accidentally overwrite them.
    inputs: dict = field(default_factory=dict)


# ── Execution context ──────────────────────────────────────────────


class Context:
    """What a step callable sees.

    Intentionally a thin class rather than a dict so steps that mis-
    type a field name get an AttributeError immediately rather than
    a silent None. The `previous` dict holds JSON-safe results of
    already-completed upstream steps.
    """

    def __init__(self, run: WorkflowRun, current_step: str):
        self.run_id = run.run_id
        self.workflow_id = run.workflow_id
        self.inputs = dict(run.inputs)   # defensive copy
        self.current_step = current_step
        # Build a name → result map from completed steps. Copy so a
        # step can mutate what it received without clobbering on-disk
        # state.
        self.previous: dict[str, Any] = {
            name: (res.result if res.status == StepStatus.SUCCESS.value else None)
            for name, res in run.steps.items()
            if res.status in (StepStatus.SUCCESS.value, StepStatus.FAILED.value)
        }


# ── Engine ─────────────────────────────────────────────────────────


def _new_run_id() -> str:
    return "run_" + secrets.token_hex(4)


def _ensure_dir() -> None:
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


def _run_path(run_id: str) -> Path:
    return WORKFLOWS_DIR / f"{run_id}.json"


def _persist(run: WorkflowRun) -> None:
    """Write the run state atomically. Tempfile + rename so a crash
    mid-write leaves the previous checkpoint intact rather than a
    half-written JSON the resume path can't parse."""
    _ensure_dir()
    target = _run_path(run.run_id)
    tmp = target.with_suffix(".json.tmp")
    data = {
        "run_id": run.run_id,
        "workflow_id": run.workflow_id,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "inputs": run.inputs,
        "steps": {name: asdict(res) for name, res in run.steps.items()},
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(target)


def _load_run(run_id: str) -> Optional[WorkflowRun]:
    path = _run_path(run_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"resume: couldn't read {path}: {e}")
        return None
    steps = {
        name: StepResult(**res) for name, res in data.get("steps", {}).items()
    }
    return WorkflowRun(
        run_id=data["run_id"],
        workflow_id=data["workflow_id"],
        status=data.get("status", RunStatus.RUNNING.value),
        started_at=float(data.get("started_at") or 0.0),
        completed_at=data.get("completed_at"),
        inputs=data.get("inputs", {}),
        steps=steps,
    )


def _audit(event: str, run_id: str, extra: dict) -> None:
    """Append an audit line. Never raises — logging must not break
    flow even if disk fills up mid-run."""
    try:
        _ensure_dir()
        entry = {"at": time.time(), "event": event, "run_id": run_id, **extra}
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _topo_order(steps: list[Step]) -> list[Step]:
    """Topological sort so we run steps only after their deps finish.

    Cycles raise ValueError — a cyclic DAG is a programming bug, not
    a runtime condition we should silently tolerate.
    """
    by_name = {s.name: s for s in steps}
    if len(by_name) != len(steps):
        raise ValueError("duplicate step names in workflow")
    visited: set[str] = set()
    ordered: list[Step] = []
    in_progress: set[str] = set()

    def visit(n: str, path: list[str]) -> None:
        if n in visited:
            return
        if n in in_progress:
            raise ValueError(f"cyclic dependency: {' -> '.join(path + [n])}")
        if n not in by_name:
            raise ValueError(f"step {n!r} depends on unknown step")
        in_progress.add(n)
        for d in by_name[n].depends_on:
            visit(d, path + [n])
        in_progress.discard(n)
        visited.add(n)
        ordered.append(by_name[n])

    for s in steps:
        visit(s.name, [])
    return ordered


class WorkflowEngine:
    """Synchronous DAG runner with checkpointing.

    Usage:
        engine = WorkflowEngine()
        run = engine.run(my_workflow, inputs={"key": "value"})

    Resume:
        run = engine.resume(run_id)  # picks up where it left off
    """

    # ── Public API ────────────────────────────────────────────────

    def run(self, workflow: Workflow, inputs: Optional[dict] = None,
            run_id: Optional[str] = None) -> WorkflowRun:
        """Execute a workflow end-to-end. Returns the final run state.

        If run_id is passed, that identifier is used (lets callers
        correlate a run with something external — e.g. a submission
        id). Otherwise a random one is generated.
        """
        run = WorkflowRun(
            run_id=run_id or _new_run_id(),
            workflow_id=workflow.id,
            inputs=dict(inputs or {}),
        )
        for step in workflow.steps:
            run.steps[step.name] = StepResult()
        _persist(run)
        _audit("start", run.run_id, {"workflow": workflow.id})
        return self._run_loop(workflow, run)

    def resume(self, run_id: str) -> Optional[WorkflowRun]:
        """Resume a partially-completed run.

        Needs a way to re-locate the Workflow definition — callers
        provide via `resume_with` (workflow instance). We don't keep a
        global workflow registry because workflows are module-level
        constants declared by features, not dynamically-built things
        we'd want to pickle.
        """
        raise NotImplementedError(
            "use resume_with(run_id, workflow) — workflow definitions "
            "aren't stored on disk, so the caller must re-provide them"
        )

    def resume_with(self, run_id: str, workflow: Workflow) -> Optional[WorkflowRun]:
        """Resume a run using the caller-supplied workflow definition."""
        run = _load_run(run_id)
        if run is None:
            return None
        if run.status != RunStatus.RUNNING.value:
            log.info(f"resume: run {run_id} already in terminal state "
                     f"{run.status}; returning as-is")
            return run
        # Align step dict to the current workflow definition (in case
        # the workflow grew a step between start and resume — unlikely
        # in practice, but defensive).
        for step in workflow.steps:
            run.steps.setdefault(step.name, StepResult())
        _audit("resume", run_id, {"workflow": workflow.id})
        return self._run_loop(workflow, run)

    # ── Inner loop ────────────────────────────────────────────────

    def _run_loop(self, workflow: Workflow, run: WorkflowRun) -> WorkflowRun:
        """Topologically iterate steps, executing or skipping each.

        Early-terminates on FAILED (no point running downstream of a
        confirmed failure). Persists after every state transition so
        a hard crash between steps still leaves a recoverable file.
        """
        ordered = _topo_order(workflow.steps)
        by_name = {s.name: s for s in workflow.steps}

        for step in ordered:
            state = run.steps[step.name]
            if state.status == StepStatus.SUCCESS.value:
                continue  # already done (resume path)
            if state.status in (StepStatus.FAILED.value, StepStatus.SKIPPED.value):
                continue  # already terminal (resume of a post-mortem)

            # Dependency check: if any upstream dep isn't SUCCESS,
            # skip this one.
            blocked = self._dep_blocker(step, run)
            if blocked is not None:
                state.status = StepStatus.SKIPPED.value
                state.error = f"skipped — dep {blocked!r} not successful"
                _persist(run)
                _audit("skip", run.run_id, {"step": step.name, "reason": blocked})
                continue

            # Execute with retry.
            ok = self._execute_step(step, state, run)
            _persist(run)
            _audit(
                "success" if ok else "fail",
                run.run_id,
                {"step": step.name, "attempts": state.attempts},
            )
            if not ok:
                # Downstream steps will be skipped by the dep check in
                # subsequent iterations.
                continue

        # Finalize run status.
        any_failed = any(
            s.status in (StepStatus.FAILED.value, StepStatus.SKIPPED.value)
            for s in run.steps.values()
        )
        run.status = RunStatus.FAILED.value if any_failed else RunStatus.SUCCESS.value
        run.completed_at = time.time()
        _persist(run)
        _audit("end", run.run_id, {"status": run.status})
        return run

    # ── Helpers ──────────────────────────────────────────────────

    def _dep_blocker(self, step: Step, run: WorkflowRun) -> Optional[str]:
        """Return the first dep whose status isn't SUCCESS, or None if
        all deps passed. A missing dep status (shouldn't happen post
        _topo_order) is treated as blocking."""
        for dep_name in step.depends_on:
            dep = run.steps.get(dep_name)
            if dep is None or dep.status != StepStatus.SUCCESS.value:
                return dep_name
        return None

    def _execute_step(self, step: Step, state: StepResult,
                      run: WorkflowRun) -> bool:
        """Invoke step.fn with retry. Mutates `state` in place.

        Returns True on SUCCESS, False on FAILED. The step's final
        state is fully captured in `state` (status, attempts, error,
        traceback, result) — no info hides in this method's locals.
        """
        ctx = Context(run, step.name)
        state.status = StepStatus.RUNNING.value
        state.started_at = state.started_at or time.time()
        for attempt in range(1, step.max_attempts + 1):
            state.attempts = attempt
            try:
                result = step.fn(ctx)
                # Result must be JSON-serializable. If caller returned
                # something weird (e.g. a dataclass), log and store
                # best-effort repr so we don't corrupt the checkpoint
                # file on next _persist.
                try:
                    json.dumps(result, ensure_ascii=False, default=str)
                except (TypeError, ValueError) as e:
                    log.warning(
                        f"step {step.name}: result not JSON-serializable "
                        f"({e}); coercing via repr"
                    )
                    result = repr(result)
                state.result = result
                state.status = StepStatus.SUCCESS.value
                state.completed_at = time.time()
                state.error = None
                state.traceback = None
                return True
            except step.retry_on as e:
                state.error = f"{type(e).__name__}: {e}"
                state.traceback = traceback.format_exc()
                log.warning(
                    f"step {step.name} attempt {attempt}/{step.max_attempts} "
                    f"failed: {state.error}"
                )
                if attempt < step.max_attempts:
                    if step.backoff_seconds > 0:
                        time.sleep(step.backoff_seconds)
                    continue
                # Retries exhausted.
                state.status = StepStatus.FAILED.value
                state.completed_at = time.time()
                return False
            except Exception as e:
                # Non-retryable: fail immediately (no further attempts).
                state.error = f"{type(e).__name__}: {e}"
                state.traceback = traceback.format_exc()
                state.status = StepStatus.FAILED.value
                state.completed_at = time.time()
                log.warning(
                    f"step {step.name} failed with non-retryable {e!r}"
                )
                return False
        # Shouldn't reach here (loop always returns), but defensive:
        state.status = StepStatus.FAILED.value
        return False


# ── Introspection helpers ─────────────────────────────────────────


def list_runs(limit: int = 20) -> list[dict]:
    """Recent run summaries, newest first. Used by debugging UI /
    admin scripts to see what ran and how it went."""
    if not WORKFLOWS_DIR.exists():
        return []
    runs: list[dict] = []
    for p in sorted(WORKFLOWS_DIR.glob("run_*.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            runs.append({
                "run_id": data["run_id"],
                "workflow_id": data["workflow_id"],
                "status": data.get("status"),
                "started_at": data.get("started_at"),
                "completed_at": data.get("completed_at"),
                "step_count": len(data.get("steps", {})),
                "failed_steps": [
                    n for n, s in data.get("steps", {}).items()
                    if s.get("status") in (StepStatus.FAILED.value,
                                           StepStatus.SKIPPED.value)
                ],
            })
        except Exception:
            continue
    return runs
