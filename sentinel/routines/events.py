"""EventBus — reactive trigger plumbing for routines.

Phase G: routines can fire on environmental events, not just clock
times. Most useful examples (idle reminder, error-on-screen detect,
"you opened VS Code, want me to focus the auth project") are
fundamentally event-driven; cron-style triggers can't express them.

How it fits with the rest of the system:

  Observation modules (file_watcher, activity_tracker, input_tracker,
  screen_watcher, claude_watcher) already publish their findings to
  the Context Bus for LLM consumption. We extend the same modules to
  also publish to this EventBus when an environmental change happens
  (file changed, active window switched, user idle threshold crossed).

  ReactiveDispatcher subscribes to all event types, on each event
  scans enabled routines whose trigger.kind matches, applies a
  per-routine cooldown to avoid spam, and fires through the same
  fire_routine() path used by the cron-style scheduler.

The ContextBus is "what should the LLM read"; the EventBus is "what
should react now". They're both pub-sub but for different consumers,
so keeping them as separate modules avoids accidental coupling.

Threading:
  Subscribers are called inline on the publishing thread. Callbacks
  must not block — they should do trivial bookkeeping or queue the
  real work. ReactiveDispatcher's callback uses a short critical
  section + invokes fire_routine which runs the workflow inline; if
  any routine step is slow, that blocks the publishing thread until
  the routine completes. For v1 this is acceptable — events are
  rare (file watcher debounces, window-switch is human-paced) and
  routines are quick (5-step cap, mostly local actions).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


log = logging.getLogger("sentinel.routines.events")


# ── Event type constants ──────────────────────────────────────────
# Stable strings so on-disk routine triggers (which reference these)
# don't break if we rename the python identifier later.

EVENT_FILE_CHANGE = "file_change"
EVENT_APP_OPEN = "app_open"
EVENT_APP_CLOSE = "app_close"
EVENT_IDLE_REACHED = "idle_reached"


@dataclass
class Event:
    """One environmental event. payload schema depends on event_type:

    file_change:    {"path": str, "type": "modified|created|deleted"}
    app_open:       {"title": str, "process_name": str}
    app_close:      {"title": str, "process_name": str}
    idle_reached:   {"duration_minutes": int}
    """
    event_type: str
    payload: dict


# ── EventBus ──────────────────────────────────────────────────────


_subscribers: dict[str, list[Callable[[Event], None]]] = {}
_subscribers_lock = threading.RLock()


def subscribe(event_type: str, callback: Callable[[Event], None]) -> None:
    """Register a callback for one event_type, or '*' for all events.

    Multiple callbacks per event type are allowed; they fire in
    registration order. A subscriber's exception is caught + logged
    so one buggy listener can't stop the rest from running.
    """
    with _subscribers_lock:
        _subscribers.setdefault(event_type, []).append(callback)


def publish(event_type: str, payload: dict) -> None:
    """Fire an event to all matching subscribers + the wildcard '*'.

    Inline dispatch (no thread/queue) — keeps ordering deterministic
    and adds zero infrastructure. Subscribers must be quick or
    delegate.
    """
    event = Event(event_type=event_type, payload=dict(payload or {}))
    with _subscribers_lock:
        targeted = list(_subscribers.get(event_type, []))
        wildcard = list(_subscribers.get("*", []))
    for cb in targeted + wildcard:
        try:
            cb(event)
        except Exception:
            log.exception(f"event subscriber raised on {event_type}")


def clear_subscribers(event_type: Optional[str] = None) -> None:
    """Remove subscribers (mainly for tests)."""
    with _subscribers_lock:
        if event_type is None:
            _subscribers.clear()
        else:
            _subscribers.pop(event_type, None)
