"""Watch Claude Code activity by monitoring its conversation logs."""
import json
import time
from pathlib import Path
from sentinel.config import CLAUDE_CODE_LOG_DIR


def find_recent_claude_sessions(max_age_hours=2) -> list[Path]:
    """Find recently active Claude Code session files."""
    sessions = []
    if not CLAUDE_CODE_LOG_DIR.exists():
        return sessions

    cutoff = time.time() - (max_age_hours * 3600)
    for project_dir in CLAUDE_CODE_LOG_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.rglob("*.jsonl"):
            try:
                if f.stat().st_mtime > cutoff:
                    sessions.append(f)
            except OSError:
                continue
    return sessions


def read_recent_messages(session_file: Path, last_n=10) -> list[dict]:
    """Read the last N messages from a Claude Code JSONL session."""
    messages = []
    try:
        with open(session_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    messages.append(msg)
                except json.JSONDecodeError:
                    continue
    except (OSError, PermissionError):
        return []
    return messages[-last_n:]


def get_claude_activity_summary() -> str:
    """Get a summary of recent Claude Code activity."""
    sessions = find_recent_claude_sessions()
    if not sessions:
        return ""

    # Get the most recent session
    sessions.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    latest = sessions[0]
    messages = read_recent_messages(latest, last_n=15)

    if not messages:
        return ""

    lines = [f"Recent Claude Code session ({latest.parent.name}):"]
    for msg in messages:
        role = msg.get('role', msg.get('type', '?'))
        # Extract text content
        content = msg.get('content', '')
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get('type') == 'text':
                        texts.append(block.get('text', '')[:200])
                    elif block.get('type') == 'tool_use':
                        texts.append(f"[tool: {block.get('name', '?')}]")
                    elif block.get('type') == 'tool_result':
                        texts.append(f"[tool result]")
            content = ' | '.join(texts)
        elif isinstance(content, str):
            content = content[:200]
        else:
            content = str(content)[:200]

        if content:
            lines.append(f"  [{role}] {content}")

    return "\n".join(lines[-20:])  # Keep it manageable
