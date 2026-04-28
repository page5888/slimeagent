# Codespaces dev environment

Cloud dev env for AI Slime so the code path doesn't need a local
machine. Open via:

> https://github.com/page5888/slimeagent → Code → Codespaces → Create

First boot ~2 min. Subsequent boots ~10 s.

## What works in here

- Editing code, running tests, committing, opening PRs
- LLM API calls (Gemini, OpenAI, etc. — keys from your environment)
- `python -m py_compile` smoke checks
- Running `claude` (Claude Code CLI is pre-installed)

## What does NOT work in here

- Running the desktop GUI (`python -m sentinel`) — Codespaces is
  Linux + headless. PySide6 imports work; rendering doesn't.
- Anything Windows-specific (start.bat kill loop, autostart task,
  shortcut creation)

For UI smoke tests, switch back to your local Windows machine —
that's the only place to actually see the daily reflection card,
the chat tab, or the Slime avatar.

## Quick claude prompt for new sessions

```
讀 docs/manifesto.md。
我們在做 v0.7-alpha — Slime 自我表達 + 多 key fallback。
最近 PR：#42–#46（已 merged）。
下個任務：[describe what you want to do]
```

## Files in this folder

- `devcontainer.json` — VS Code Codespaces config (image, extensions, post-create hook)
- `post-create.sh` — installs Python deps + Claude Code + git defaults
- `README.md` — this file
