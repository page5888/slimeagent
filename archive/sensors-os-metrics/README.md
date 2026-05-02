# archive/sensors-os-metrics/

Archived 2026-05-02 as **Phase 1 of the v0.8 sensor refactor**. The principle that named this archive directory:

> 「主人不在意電腦怎麼了。主人在意你看到他在做什麼。」

## What's here

### `system_monitor.py`

The OS-metrics sensor: CPU% / RAM% / disk% / top-process list via `psutil`. `take_snapshot()` produced a `SystemSnapshot` that flowed into:

- `chat._build_system_prompt()` — fed `snapshot.summary()` into the chat system prompt's `<<SYSTEM_STATE>>` slot, which made slime talk about CPU/RAM/disk to the master
- `daemon.monitor_loop` and `gui.MainWindow._start_daemon._run` — main observation loop, `build_context(snapshot, ...)` published "system" entries to the context bus, fed to `analyze_events()` via the impulse engine
- `daemon.cmd_status` and `gui.cmd_status` — Telegram `/status` command rendered the snapshot as a status card
- `idle_report.compose_message` — gated on `snapshot.warnings` (CPU > 90%, RAM > 85%, etc.)

The whole thing observed **the laptop**, not **the master**. Manifesto and 7-ADR philosophy assume slime sees主人的生活, but the sensor saw 電腦的內臟. All philosophical claims sit on the wrong foundation until this is replaced.

## What's not here (yet)

This is **Phase 1**: archive the wrong-direction sensor + disable its callsites. Phase 2-6 of the施工指示 add the right-direction sensor:

- Phase 2: strengthen `activity_tracker` (active window title + duration; already exists, needs reinforcement)
- Phase 3: window-title semantic understanding (rule + LLM hybrid)
- Phase 4: master-activity track persisted to memory store
- Phase 5: impulse engine redesigned on master-activity signals
- Phase 6: integration test — 0xspeter feels「Slime 真的在看我」

When Phase 4 lands, the `None` placeholder in `build_context(snapshot=None, ...)` and `compose_message(warnings=[], snapshot_summary="", ...)` gets replaced with the new master-activity feed.

## Live callsites — disabled, not deleted

Per施工指示「不要刪除程式碼,加 TODO comment 標記等 v0.8 完成後重新接」:

| Site | What it was | Now |
|---|---|---|
| `sentinel/chat.py:_build_system_prompt` | `snapshot = take_snapshot()` → `<<SYSTEM_STATE>>` | `system_summary = ""` (slot empty) |
| `sentinel/daemon.py:monitor_loop` (×2) | `snapshot = take_snapshot()` | `snapshot = None` |
| `sentinel/gui.py:_start_daemon._run` | `snapshot = take_snapshot()` | `snapshot = None` |
| `sentinel/brain.py:build_context` | required `system_snapshot` arg | accepts `None`, skips publishing |
| `sentinel/daemon.py:cmd_status` | OS-metrics card | Slime-self card (form / days / box / profile) |
| `sentinel/gui.py:cmd_status` (Telegram) | OS-metrics card | Slime-self card (form / days / box / observations) |
| `idle_report.compose_message` callers | `warnings=snapshot.warnings, snapshot_summary=snapshot.summary()` | `warnings=[], snapshot_summary=""` (snapshot block never fires; only `llm_warning` can produce a message) |

Every site has a `TODO(v0.8 sensor cycle)` comment pointing back here.

## Why `/status` changed content instead of being deleted

The `/status` Telegram command lives in 調度面 (per `2026-04-30-co-sediment-architecture.md` — Slime's 兩個面). It's an admin/debug surface, not slime's voice. Keeping it is consistent with the two-face design; what's wrong is *what it shows*. New content focuses on slime-self state (name, form, days alive, memorable_moments count) — info aligned with sediment philosophy, not OS metrics.

## Untracked artifacts in `sentinel/skills/`

If your working tree has `bun_resource_monitor.py` and/or `resource_task_scheduler.py` in `sentinel/skills/`, those are **untracked** SKILL_GEN-era artifacts (predate PR #132). They were never committed — git ignores them. They're inert (no caller imports them post-PR #132). Safe to delete with `rm sentinel/skills/bun_resource_monitor.py sentinel/skills/resource_task_scheduler.py`. They were not moved into this archive because they were never in git history to begin with.

## Dead-but-kept config constants

`sentinel/config.py` still exports:

```python
CPU_WARN_PERCENT = 90
RAM_WARN_PERCENT = 85
DISK_WARN_PERCENT = 90
```

Left in place because (a) they're frozen historical thresholds, (b) deleting them might surprise some other archive (`core_backup`) that references them on a `rollback_to_core`. If a future sweep wants to delete them, it should also confirm `archive/sensors-os-metrics/system_monitor.py` is the only consumer.

`EMOTION_TRIGGERS["worried"]["conditions"]` in `sentinel/chat.py` still lists `"CPU 使用率超過 90"` etc. as keyword matches for the worried emotion. With `system_summary` now empty, those keywords can no longer fire — they're dead match terms. Left in place; Phase 5 will redesign the emotion-trigger keyword list around master-activity signals.

## Resurrection conditions

If a future cycle wants to bring OS metrics back into slime's perception, the bar is:

1. **Concrete user-facing scenario** that requires CPU/RAM/disk data and isn't covered by the master-activity sensor (Phases 2-6).
2. **The data goes to the調度面 (orchestrator face), not the陪伴面 (companion voice)**. Slime's chat output should never narrate electronics.
3. **Manifesto check**: does this serve "看到主人在做什麼" or does it slip back into "報告電腦狀態"? If the latter, archive it again.
