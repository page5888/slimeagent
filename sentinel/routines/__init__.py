"""Routines — recurring actions the slime runs on its own (with prior approval).

Phase F is the qualitative leap from "tool that responds when asked" to
"agent that anticipates". The mechanism reuses everything we've built:

  - Observation engine (B1 context bus + screen/file/window tracking)
    feeds the detector.
  - LLM distillation (B2) finds the recurring patterns from raw logs.
  - Approval queue (C1) gates every routine: user explicitly opts in
    once, then the routine fires autonomously until they disable it.
  - Workflow engine (C3) executes the multi-step routine with
    checkpointing + retry.
  - Action vocabulary (C2 surface + D1-D5) is what the routine does.

The slime never schedules its own behavior. It *proposes* a routine
("I noticed you do X every morning, want me to take this over?"), the
user approves, and only then does the scheduler start firing.

Storage: ~/.hermes/routines/<id>.json — one file per routine.
Audit:   ~/.hermes/routines/routines.jsonl — every fire/create/disable.

Public API (this package):
    create_routine(...)       create + enable on disk
    list_routines()           all routines, newest first
    get_routine(id)
    enable_routine(id)        toggle on
    disable_routine(id)       toggle off (kept on disk)
    delete_routine(id)        permanent removal
    start_scheduler()         spawn the daemon that fires due routines
    propose_via_detector()    one-shot LLM-driven pattern detection
"""
from sentinel.routines.storage import (
    Routine,
    create_routine,
    list_routines,
    get_routine,
    enable_routine,
    disable_routine,
    delete_routine,
)
from sentinel.routines.scheduler import (
    start_scheduler,
    stop_scheduler,
    fire_routine,
)
from sentinel.routines.detector import propose_via_detector
from sentinel.routines.reflection import (
    reflect,
    format_summary,
    ReflectionReport,
    RoutineStats,
)

__all__ = [
    "Routine",
    "create_routine", "list_routines", "get_routine",
    "enable_routine", "disable_routine", "delete_routine",
    "start_scheduler", "stop_scheduler", "fire_routine",
    "propose_via_detector",
    "reflect", "format_summary", "ReflectionReport", "RoutineStats",
]
