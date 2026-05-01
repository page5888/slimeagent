"""Tests for sentinel.cron — the shared per-tick periodic check.

Both daemon.monitor_loop and gui.py's observation thread call cron.tick()
to run emergent_self_mark + loneliness arc on a schedule. This test
module verifies tick() honors the contract: calls both checks on every
invocation, isolates failures so one broken check doesn't block the
other.
"""
from __future__ import annotations

import unittest
from unittest import mock

from sentinel import cron


class TestCronTick(unittest.TestCase):
    def test_calls_both_checks(self):
        with mock.patch("sentinel.emergent_self_mark.record_emergent_moment_if_due") as esm, \
             mock.patch("sentinel.identity.record_loneliness_arc_if_due") as lon:
            cron.tick()
        esm.assert_called_once_with()
        lon.assert_called_once_with()

    def test_emergent_failure_does_not_block_loneliness(self):
        # If emergent_self_mark blows up (LLM unreachable, etc.),
        # loneliness arc must still get its turn this tick.
        with mock.patch("sentinel.emergent_self_mark.record_emergent_moment_if_due",
                        side_effect=RuntimeError("LLM down")), \
             mock.patch("sentinel.identity.record_loneliness_arc_if_due") as lon:
            cron.tick()  # Should not raise.
        lon.assert_called_once_with()

    def test_loneliness_failure_does_not_propagate(self):
        # Same isolation in the other direction. Both fail → tick still
        # returns cleanly so the caller's observation loop survives.
        with mock.patch("sentinel.emergent_self_mark.record_emergent_moment_if_due") as esm, \
             mock.patch("sentinel.identity.record_loneliness_arc_if_due",
                        side_effect=RuntimeError("memory unwritable")):
            cron.tick()  # Should not raise.
        esm.assert_called_once_with()

    def test_both_failing_does_not_raise(self):
        with mock.patch("sentinel.emergent_self_mark.record_emergent_moment_if_due",
                        side_effect=RuntimeError("a")), \
             mock.patch("sentinel.identity.record_loneliness_arc_if_due",
                        side_effect=RuntimeError("b")):
            # Most important invariant: tick is robust enough that the
            # observation loop never crashes from a cron-side failure.
            try:
                cron.tick()
            except Exception as e:
                self.fail(f"cron.tick() raised {e}; must swallow check errors")


if __name__ == "__main__":
    unittest.main()
