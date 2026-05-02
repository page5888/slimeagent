"""SKILL_GEN — archived 2026-05-02.

ARCHIVED. This file is not imported by anything. It is preserved as
historical reference for what the SKILL_GEN feature attempted, in
case a future cycle revisits "auto-generated skills with sandboxed
runtime" as a real feature.

# Why archived

The slime was supposed to:
  1. Watch user behavior patterns
  2. Ask the LLM to identify a "need"
  3. Have the LLM write a Python file with `execute()` entry point
  4. Submit it for user approval
  5. After approval, run that skill at relevant moments

Steps 1-4 worked. Step 5 was never wired:

  - `execute_skill()` — implemented at line 405 below — had **zero
    callers** in the entire codebase. grep confirmed.
  - Approved skills landed as `.py` files in `SKILLS_DIR/` and stayed
    inert. Nothing imported them, nothing invoked them.

The user (0xspeter) on 2026-05-02 named the principle this violated:
**「真實的累積」(real accumulation)**. UI showed an "approve" button
for skills the slime claimed to want; pressing it changed nothing
about the slime's behavior. That contradicts manifesto 守則 #2
(不欺騙) — the approval button was lying to the master.

# What value this would have had if completed

Estimated 5-15% incremental value over chat + ACTION:

  - "Persistence of analysis logic" — once generated, runnable
    repeatedly without LLM cost (chat is one-shot per call).
  - "Custom monitoring tailored to user patterns" — each user gets
    analyzers shaped by their behavior.

# Why we didn't fix instead of archive

Fixing required:

  1. An invocation decision system (when does slime decide to run
     skill X?). Autonomy concerns: slime self-deciding feels intrusive;
     master invoking turns this into a fancy macro library overlapping
     with chat.
  2. Output destination wiring (execute_skill returns a string —
     where? chat / notification / log?).
  3. Sandboxing. The runtime call (`importlib.util.spec_from_file_
     location → exec_module → module.execute()`) really executes
     LLM-written Python in-process. The existing `_is_code_safe()`
     string-matching guard was already known to be insufficient —
     `growth/safety.py` was supposed to replace it (per its docstring,
     "Replaces the string-matching check in self_evolution._is_code_
     safe()") but `growth/scan_code` is only used on submission, not
     at execution time.

The user opted to archive rather than invest in this stack because:

  - SKILL_GEN's intended value is small and overlaps with chat + ACTION
  - Sandboxed Python execution is a non-trivial design problem on its
    own; v0.8 cycle's focus is birth_signature + title system
  - "Honest" beats "performative" in this codebase

# Resurrection conditions

If a future cycle wants to bring this back, the bar is:

  - A concrete invocation policy that resolves the autonomy question
  - A real sandbox (subprocess + restricted environment, or restricted
    AST + capability tokens, or similar) — not just AST-level static
    scanning
  - At least one concrete user-facing scenario that chat + ACTION
    cannot already cover

Until then this stays archived.

# Original module docstring (paraphrased)

The file was Layer 2 of `sentinel/self_evolution.py`'s three-layer
evolution model: memory accumulation (learner.py) → skill generation
(this file) → self-modification (kept active in self_evolution.py).
"""

# ──────────────────────────────────────────────────────────────────────
# Below: original SKILL_GEN code, frozen as of 2026-05-02. Imports
# may not resolve in this archived form — that's fine, this file is
# documentation, not a runnable module.
# ──────────────────────────────────────────────────────────────────────

# from sentinel.self_evolution.py top:
#   SKILLS_DIR = SENTINEL_DIR / "skills"
#   import json, time, shutil, logging, importlib, traceback, etc.
#   log = logging.getLogger("sentinel.self_evolution")


# ─── Skill Generation (Layer 2) ─────────────────────────────────────────

SKILL_GEN_PROMPT = """你是 AI Slime，一個正在進化的 AI agent。
根據你對使用者的觀察，你決定創造一個新技能來更好地服務他。

使用者的 Profile：
<<PROFILE>>

使用者的行為模式：
<<PATTERNS>>

你觀察到的需求：
<<NEED>>

請產生一個 Python 技能檔案。規則：
1. 檔名用英文小寫加底線（例如 auto_backup.py）
2. 必須有一個 execute() 函數作為入口
3. 必須有 SKILL_NAME（中文技能名）和 SKILL_DESCRIPTION（描述）
4. 不能刪除或修改使用者的檔案
5. 不能發送網路請求（除了透過 sentinel.llm）
6. 只能讀取、分析、產生建議

回覆格式：
FILENAME: xxx.py
```python
程式碼
```"""


def generate_skill(need_description: str) -> dict | None:
    """Let AI Slime propose a new skill based on observed needs.

    IMPORTANT CHANGE (growth PR 1):
    This used to auto-deploy the generated skill into SKILLS_DIR.
    It now routes through the approval queue — the skill file is
    written to ~/.hermes/approvals/pending/ as a proposal. A human
    must call sentinel.growth.approval.approve() before the skill
    becomes runnable.

    Returns:
      {"approval_id": "...", "filename": "...", "skill_name": "...",
       "description": "...", "status": "pending"}
    on successful proposal, or None on refusal / generation failure.
    """
    from sentinel.llm import call_llm
    from sentinel.learner import load_memory
    from sentinel.growth import (
        can_perform, Capability, scan_code, submit_for_approval,
    )
    from sentinel.growth.approval import SKILL_GEN
    from dataclasses import asdict

    # Capability gate — refuse if this tier can't propose skills
    decision = can_perform(Capability.PROPOSE_SKILL)
    if not decision.allowed:
        log.info("generate_skill refused: %s", decision.reason)
        _log_event("skill_refused",
                   f"拒絕技能生成：{decision.reason}")
        return None

    memory = load_memory()
    profile = memory.get("profile", "(尚無)")
    patterns = json.dumps(memory.get("patterns", {}), ensure_ascii=False)

    prompt = SKILL_GEN_PROMPT.replace(
        "<<PROFILE>>", profile
    ).replace(
        "<<PATTERNS>>", patterns
    ).replace(
        "<<NEED>>", need_description
    )

    text = call_llm(prompt, temperature=0.4, max_tokens=1500)
    if not text:
        return None

    try:
        # Parse filename
        import re
        fname_match = re.search(r'FILENAME:\s*(\w+\.py)', text)
        if not fname_match:
            return None
        filename = fname_match.group(1)

        # Parse code
        code_match = re.search(r'```python\s*\n(.*?)```', text, re.DOTALL)
        if not code_match:
            return None
        code = code_match.group(1).strip()

        # AST safety scan — blocks obvious attacks regardless of
        # whitespace/alias/reflection tricks
        report = scan_code(code)
        if not report.safe:
            for f in report.blocking:
                log.warning("Skill %s blocked by safety: [%s] %s",
                            filename, f.rule, f.message)
            _log_event("skill_blocked",
                       f"技能「{filename}」未通過安全掃描：{report.summary()}")
            return None

        # Submit to approval queue — NOT deployed yet
        target = SKILLS_DIR / filename
        approval = submit_for_approval(
            kind=SKILL_GEN,
            title=need_description[:60],
            reason=need_description,
            target_path=str(target),
            source=code,
            safety_findings=[asdict(f) for f in report.findings],
            proposer_tier=decision.tier,
        )
        _log_event("skill_proposed",
                   f"提議新技能「{filename}」(id={approval.id})，等待使用者核准")
        return {
            "approval_id": approval.id,
            "filename": filename,
            "skill_name": filename.replace(".py", "").replace("_", " ").title(),
            "description": need_description,
            "status": "pending",
        }
    except Exception as e:
        log.error("generate_skill error: %s", e)
        return None


def _is_code_safe(code: str) -> bool:
    """Check if generated code is safe to execute.

    NOTE per growth/safety.py docstring: this string-matching check
    is documented as 'replaced' by AST-based scan_code(). In practice
    BOTH ran on submission (scan_code() in generate_skill body, but
    _is_code_safe was never invoked at all by the time SKILL_GEN was
    archived — it was already orphan-by-orphan).
    """
    dangerous = [
        "os.remove", "os.unlink", "shutil.rmtree", "shutil.move",
        "subprocess", "os.system", "eval(", "exec(",
        "open(", "write(",  # No file writing
        "__import__",
        "requests.", "urllib.", "http.",  # No direct network
        "ctypes.",  # No system calls
        "import os", "from os",
    ]

    # Allow specific safe patterns
    safe_overrides = [
        "from sentinel.llm import",
        "from sentinel.learner import",
        "from sentinel.system_monitor import",
        "import json", "import re", "import time", "import datetime",
        "from pathlib import Path",
    ]

    code_lower = code.lower()
    for d in dangerous:
        if d.lower() in code_lower:
            is_safe = False
            for s in safe_overrides:
                if s.lower() in code_lower and d.lower() in s.lower():
                    is_safe = True
                    break
            if not is_safe:
                log.warning(f"Dangerous code detected: {d}")
                return False
    return True


def _validate_skill(skill_path):
    """Try to import a skill and check it has required attributes."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("skill_test", skill_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        name = getattr(module, "SKILL_NAME", None)
        desc = getattr(module, "SKILL_DESCRIPTION", "")
        has_execute = hasattr(module, "execute")

        if name and has_execute:
            return name, desc
        return None, None
    except Exception as e:
        log.error(f"Skill validation failed: {e}")
        return None, None


def list_skills() -> list[dict]:
    """List all generated skills."""
    if not SKILLS_DIR.exists():
        return []
    skills = []
    for f in SKILLS_DIR.glob("*.py"):
        if f.name.startswith("_"):
            continue
        name, desc = _validate_skill(f)
        if name:
            skills.append({
                "filename": f.name,
                "skill_name": name,
                "description": desc,
            })
    return skills


def execute_skill(filename: str) -> str:
    """Execute a generated skill and return its output.

    THE ORPHAN. This function was the entire promise of SKILL_GEN —
    "after the user approves, we run the skill." It was implemented
    correctly (importlib spec_from_file_location → exec_module →
    module.execute()) but nothing ever called it. That's why
    SKILL_GEN was archived: a working callee with no caller is the
    same as no feature at all.
    """
    skill_path = SKILLS_DIR / filename
    if not skill_path.exists():
        return "技能檔案不存在"

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("skill_exec", skill_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.execute()
        return str(result) if result else "執行完成"
    except Exception as e:
        log.error(f"Skill execution error: {e}")
        return f"執行失敗：{e}"


def _identify_skill_need(patterns: dict, profile: str, existing_skills: list):
    """Use LLM to identify what new skill would be useful."""
    from sentinel.llm import call_llm

    existing_names = [s["skill_name"] for s in existing_skills]

    prompt = f"""根據以下使用者 profile 和行為模式，判斷 AI Slime 需要什麼新技能。

Profile: {profile}
Patterns: {json.dumps(patterns, ensure_ascii=False)}
已有技能: {', '.join(existing_names) if existing_names else '(無)'}

用一句話描述需要的新技能（如果不需要新技能，回覆 NONE）："""

    result = call_llm(prompt, temperature=0.5, max_tokens=100)
    if result and "NONE" not in result.upper():
        return result.strip()
    return None


# Original maybe_evolve() SKILL_GEN branch (the proposer that called
# generate_skill above). Removed from sentinel/self_evolution.py
# alongside the archive of this file:
#
#     if learnings > 0 and learnings % 10 == 0:
#         existing_skills = list_skills()
#         if len(existing_skills) < 10:
#             need = _identify_skill_need(patterns, profile, existing_skills)
#             if need:
#                 result = generate_skill(need)
#                 if result and result.get("status") == "pending":
#                     events.append(
#                         f"提議了新技能「{result['skill_name']}」，"
#                         f"等你確認（id={result['approval_id']}）"
#                     )
#
# The SELF_MOD branch in maybe_evolve was kept — that path actually
# has effect (overwrites a MODIFIABLE_FILE on approve, picked up at
# next launch).
