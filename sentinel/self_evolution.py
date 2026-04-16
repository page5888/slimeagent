"""Self-Evolution Engine - AI Slime's ability to modify and improve itself.

Three layers of evolution:
1. Memory accumulation (learner.py) - already exists
2. Skill generation - create new .py skill files based on observed needs
3. Self-modification - improve own prompts, thresholds, analysis logic

Safety principle:
- User data (memory, logs, activity) is NEVER touched by rollback
- Before any self-modification, a snapshot is taken
- If anything breaks, rollback to last known good state
- Core backup is created on first boot and never modified

Directory structure:
  sentinel/core_backup/   ← pristine copy of original files (created once, read-only)
  sentinel/skills/        ← generated skill files (safe to delete)
  ~/.hermes/evolution_snapshots/  ← pre-modification backups
  ~/.hermes/user_data/    ← user data that rollback NEVER touches:
                             sentinel_memory.json, sentinel_activity.jsonl,
                             sentinel_chats.jsonl, aislime_evolution.json, etc.
"""
import json
import time
import shutil
import logging
import importlib
import traceback
from pathlib import Path

log = logging.getLogger("sentinel.self_evolution")

SENTINEL_DIR = Path(__file__).parent
CORE_BACKUP_DIR = SENTINEL_DIR / "core_backup"
SKILLS_DIR = SENTINEL_DIR / "skills"
SNAPSHOTS_DIR = Path.home() / ".hermes" / "evolution_snapshots"
EVOLUTION_LOG_FILE = Path.home() / ".hermes" / "self_evolution_log.jsonl"

# Files that can be self-modified (white list)
MODIFIABLE_FILES = [
    "brain.py",       # Analysis prompts and logic
    "learner.py",     # Distillation prompts
    "chat.py",        # Chat persona and system prompt
    "config.py",      # Thresholds, intervals
]

# Files that are NEVER modified (safety)
PROTECTED_FILES = [
    "self_evolution.py",  # Can't modify its own safety system
    "gui.py",             # UI shouldn't break
    "llm.py",             # API connectivity
    "__main__.py",        # Entry point
]

# User data files that rollback NEVER touches
USER_DATA_PATTERNS = [
    "sentinel_memory.json",
    "sentinel_activity.jsonl",
    "sentinel_chats.jsonl",
    "sentinel_input.jsonl",
    "aislime_evolution.json",
    "aislime_learning_log.jsonl",
    "self_evolution_log.jsonl",
    "sentinel_settings.json",
]


# ─── Core Backup (first-boot safety net) ────────────────────────────────

def ensure_core_backup():
    """Create a pristine backup of all sentinel files on first boot.
    This is the ultimate safety net - if everything goes wrong,
    we can always restore to this state.
    """
    marker = CORE_BACKUP_DIR / ".backup_complete"
    if marker.exists():
        return  # Already backed up

    CORE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for py_file in SENTINEL_DIR.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        dst = CORE_BACKUP_DIR / py_file.name
        shutil.copy2(py_file, dst)
        count += 1

    marker.write_text(f"Backed up {count} files at {time.time()}")
    log.info(f"Core backup created: {count} files")
    _log_event("core_backup", f"初始備份完成，{count} 個核心檔案已保存")


# ─── Snapshot (pre-modification backup) ─────────────────────────────────

def take_snapshot(reason: str = "") -> str:
    """Take a snapshot before self-modification.
    Returns the snapshot ID (timestamp-based).
    """
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snap_id = f"snap_{int(time.time())}"
    snap_dir = SNAPSHOTS_DIR / snap_id

    snap_dir.mkdir(parents=True)

    # Copy all modifiable files
    for fname in MODIFIABLE_FILES:
        src = SENTINEL_DIR / fname
        if src.exists():
            shutil.copy2(src, snap_dir / fname)

    # Also snapshot any existing skills
    skills_snap = snap_dir / "skills"
    if SKILLS_DIR.exists() and any(SKILLS_DIR.glob("*.py")):
        shutil.copytree(SKILLS_DIR, skills_snap)

    # Save metadata
    meta = {
        "id": snap_id,
        "time": time.time(),
        "reason": reason,
        "files": [f.name for f in snap_dir.glob("*.py")],
    }
    (snap_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(f"Snapshot taken: {snap_id} ({reason})")
    _log_event("snapshot", f"快照已建立：{snap_id}（{reason}）")
    return snap_id


def list_snapshots() -> list[dict]:
    """List all available snapshots."""
    if not SNAPSHOTS_DIR.exists():
        return []
    snaps = []
    for d in sorted(SNAPSHOTS_DIR.iterdir(), reverse=True):
        meta_file = d / "meta.json"
        if meta_file.exists():
            try:
                snaps.append(json.loads(meta_file.read_text(encoding="utf-8")))
            except Exception:
                pass
    return snaps


# ─── Rollback ───────────────────────────────────────────────────────────

def rollback_to_snapshot(snap_id: str) -> bool:
    """Rollback code to a specific snapshot.
    User data is NEVER affected.
    """
    snap_dir = SNAPSHOTS_DIR / snap_id
    if not snap_dir.exists():
        log.error(f"Snapshot not found: {snap_id}")
        return False

    try:
        # Restore modifiable files
        for py_file in snap_dir.glob("*.py"):
            dst = SENTINEL_DIR / py_file.name
            if py_file.name not in PROTECTED_FILES:
                shutil.copy2(py_file, dst)
                log.info(f"Restored: {py_file.name}")

        # Restore skills
        skills_snap = snap_dir / "skills"
        if skills_snap.exists():
            # Clear current skills and restore
            if SKILLS_DIR.exists():
                shutil.rmtree(SKILLS_DIR)
            shutil.copytree(skills_snap, SKILLS_DIR)

        _log_event("rollback", f"已回滾到快照 {snap_id}")
        return True
    except Exception as e:
        log.error(f"Rollback failed: {e}")
        return False


def rollback_to_core() -> bool:
    """Nuclear option: restore everything to original factory state.
    User data (memory, logs, etc.) is preserved.
    """
    if not CORE_BACKUP_DIR.exists():
        log.error("No core backup found!")
        return False

    try:
        for py_file in CORE_BACKUP_DIR.glob("*.py"):
            dst = SENTINEL_DIR / py_file.name
            shutil.copy2(py_file, dst)
            log.info(f"Factory restored: {py_file.name}")

        # Clear all generated skills
        if SKILLS_DIR.exists():
            shutil.rmtree(SKILLS_DIR)
            SKILLS_DIR.mkdir()

        _log_event("factory_reset", "已恢復出廠設定（使用者資料完整保留）")
        return True
    except Exception as e:
        log.error(f"Factory reset failed: {e}")
        return False


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
            "skill_name": filename.replace(".py", ""),
            "description": need_description,
            "status": "pending",
        }

    except Exception as e:
        log.error(f"Skill generation error: {e}")
        return None


def _is_code_safe(code: str) -> bool:
    """Check if generated code is safe to execute."""
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
            # Check if it's part of a safe override
            is_safe = False
            for s in safe_overrides:
                if s.lower() in code_lower and d.lower() in s.lower():
                    is_safe = True
                    break
            if not is_safe:
                log.warning(f"Dangerous code detected: {d}")
                return False
    return True


def _validate_skill(skill_path: Path) -> tuple[str, str]:
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
    """Execute a generated skill and return its output."""
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


# ─── Self-Modification (Layer 3) ────────────────────────────────────────

SELF_MODIFY_PROMPT = """你是 AI Slime，一個正在進化的 AI agent。
你覺得自己的某個能力可以改進。

要修改的檔案：<<FILENAME>>
當前內容：
```python
<<CURRENT_CODE>>
```

改進原因：
<<REASON>>

規則：
1. 只修改需要改的部分，保持其他程式碼不變
2. 不能破壞核心功能
3. 不能刪除安全檢查
4. 修改必須是向後相容的
5. 回覆完整的新檔案內容

回覆格式：
```python
完整的新檔案內容
```"""


def self_modify(filename: str, reason: str) -> dict | None:
    """Let AI Slime PROPOSE a modification to one of its own files.

    IMPORTANT CHANGE (growth PR 1):
    This used to write the modification directly and rollback if the
    import failed. It now routes through the approval queue — the
    modification is a proposal until a human approves it.

    Returns:
      {"approval_id": "...", "filename": "...", "status": "pending"}
    on successful proposal, or None on refusal.
    """
    from sentinel.growth import (
        can_perform, Capability, scan_code, submit_for_approval,
    )
    from sentinel.growth.approval import SELF_MOD
    from dataclasses import asdict

    # Capability gate — only True Demon Lord+ can even propose core mods
    decision = can_perform(Capability.PROPOSE_SELF_MOD)
    if not decision.allowed:
        log.info("self_modify refused: %s", decision.reason)
        _log_event("self_modify_refused",
                   f"拒絕自我改良：{decision.reason}")
        return None

    if filename in PROTECTED_FILES:
        log.warning(f"Cannot modify protected file: {filename}")
        return None

    if filename not in MODIFIABLE_FILES:
        log.warning(f"File not in modifiable list: {filename}")
        return None

    file_path = SENTINEL_DIR / filename
    if not file_path.exists():
        return None

    # Read current code
    current_code = file_path.read_text(encoding="utf-8")

    # Ask LLM for improvement
    from sentinel.llm import call_llm
    import re

    prompt = SELF_MODIFY_PROMPT.replace(
        "<<FILENAME>>", filename
    ).replace(
        "<<CURRENT_CODE>>", current_code
    ).replace(
        "<<REASON>>", reason
    )

    text = call_llm(prompt, temperature=0.3, max_tokens=3000)
    if not text:
        log.warning("Self-modification failed: no LLM response")
        return None

    # Extract new code
    code_match = re.search(r'```python\s*\n(.*?)```', text, re.DOTALL)
    if not code_match:
        return None
    new_code = code_match.group(1).strip()

    # Size / dangerous-addition heuristic (unchanged from original)
    if not _is_modification_safe(current_code, new_code):
        log.warning("Self-modification failed heuristic safety check")
        _log_event("self_modify_blocked",
                   f"自我改良被啟發式規則擋下：{filename}")
        return None

    # AST scan — catches reflection / alias bypasses that string
    # matching misses
    report = scan_code(new_code)
    if not report.safe:
        for f in report.blocking:
            log.warning("self_modify %s blocked by AST: [%s] %s",
                        filename, f.rule, f.message)
        _log_event("self_modify_blocked",
                   f"自我改良未通過 AST 掃描：{filename} — {report.summary()}")
        return None

    # Submit to approval queue. Human must approve before file is
    # actually written. Snapshot will happen at approval time via
    # the approve() caller.
    approval = submit_for_approval(
        kind=SELF_MOD,
        title=f"改良 {filename}",
        reason=reason,
        target_path=str(file_path),
        source=new_code,
        previous_source=current_code,
        safety_findings=[asdict(f) for f in report.findings],
        proposer_tier=decision.tier,
    )
    _log_event("self_modify_proposed",
               f"提議改良 {filename}（id={approval.id}），等待使用者核准")
    return {
        "approval_id": approval.id,
        "filename": filename,
        "status": "pending",
    }


def _is_modification_safe(old_code: str, new_code: str) -> bool:
    """Check if the modification is safe."""
    # New code shouldn't be dramatically different (>50% change = suspicious)
    old_lines = old_code.strip().split("\n")
    new_lines = new_code.strip().split("\n")

    if len(new_lines) < len(old_lines) * 0.3:
        log.warning("Modification removed too much code")
        return False

    if len(new_lines) > len(old_lines) * 3:
        log.warning("Modification added too much code")
        return False

    # Check for dangerous additions
    dangerous = ["os.remove", "shutil.rmtree", "subprocess", "os.system",
                 "eval(", "exec(", "__import__"]
    new_text = new_code.lower()
    for d in dangerous:
        if d.lower() in new_text and d.lower() not in old_code.lower():
            log.warning(f"Modification adds dangerous code: {d}")
            return False

    return True


def _validate_modified_file(filename: str) -> bool:
    """Try to import the modified file to check for syntax errors."""
    try:
        import py_compile
        py_compile.compile(str(SENTINEL_DIR / filename), doraise=True)
        return True
    except py_compile.PyCompileError as e:
        log.error(f"Modified file has syntax error: {e}")
        return False


# ─── Auto-Evolution (called periodically) ───────────────────────────────

def maybe_evolve(evolution_state, memory: dict) -> list[str]:
    """Check if AI Slime should evolve based on accumulated learnings.
    Called periodically from the daemon loop.
    Returns a list of evolution events (messages).
    """
    events = []
    learnings = evolution_state.total_learnings
    patterns = memory.get("patterns", {})
    profile = memory.get("profile", "")

    # Skill generation: every 10 learnings, consider PROPOSING a new skill.
    # Proposals land in the approval queue; the user decides whether to
    # actually deploy them. We don't announce them as "acquired" because
    # they aren't yet — they're pending human review.
    if learnings > 0 and learnings % 10 == 0:
        existing_skills = list_skills()
        if len(existing_skills) < 10:  # Cap at 10 approved skills
            need = _identify_skill_need(patterns, profile, existing_skills)
            if need:
                result = generate_skill(need)
                if result and result.get("status") == "pending":
                    events.append(
                        f"提議了新技能「{result['skill_name']}」，"
                        f"等你確認（id={result['approval_id']}）"
                    )

    # Self-modification: every 30 learnings, consider PROPOSING an
    # improvement. Same rule — proposal only, no auto-deploy.
    if learnings > 0 and learnings % 30 == 0 and learnings >= 30:
        improvement = _identify_improvement(patterns, profile)
        if improvement:
            filename, reason = improvement
            result = self_modify(filename, reason)
            if result and result.get("status") == "pending":
                events.append(
                    f"提議改良 {filename}（{reason}），"
                    f"等你確認（id={result['approval_id']}）"
                )

    return events


def _identify_skill_need(patterns: dict, profile: str, existing_skills: list) -> str | None:
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


def _identify_improvement(patterns: dict, profile: str) -> tuple[str, str] | None:
    """Identify which file could be improved and why."""
    from sentinel.llm import call_llm

    prompt = f"""你是 AI Slime，一個正在進化的 AI。根據你對使用者的了解，
你覺得自己的哪個能力需要改進？

Profile: {profile}
Patterns: {json.dumps(patterns, ensure_ascii=False)}

可以改進的檔案：
- brain.py（分析和通知邏輯）
- learner.py（學習和蒸餾邏輯）
- chat.py（聊天人格和回覆風格）
- config.py（監控參數和閾值）

用以下格式回覆（如果不需要改進，回覆 NONE）：
FILE: 檔案名
REASON: 改進原因"""

    result = call_llm(prompt, temperature=0.4, max_tokens=200)
    if not result or "NONE" in result.upper():
        return None

    import re
    file_match = re.search(r'FILE:\s*(\w+\.py)', result)
    reason_match = re.search(r'REASON:\s*(.+)', result)
    if file_match and reason_match:
        filename = file_match.group(1)
        reason = reason_match.group(1).strip()
        if filename in MODIFIABLE_FILES:
            return filename, reason
    return None


# ─── Logging ────────────────────────────────────────────────────────────

def _log_event(event_type: str, message: str):
    """Log an evolution event."""
    try:
        EVOLUTION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVOLUTION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": time.time(),
                "type": event_type,
                "message": message,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def get_evolution_log(last_n: int = 20) -> list[dict]:
    """Read recent self-evolution events."""
    if not EVOLUTION_LOG_FILE.exists():
        return []
    entries = []
    try:
        for line in EVOLUTION_LOG_FILE.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return entries[-last_n:]
