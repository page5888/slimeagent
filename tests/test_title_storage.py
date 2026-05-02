"""Tests for sentinel/title_storage.py — the 箱子 metadata layer.

Covers schema invariants from ADR `2026-04-30-title-system.md`
紅線 1-3 / 8 / 10 (the ones storage can enforce or expose), plus
the persistence guarantees: atomic write, corrupt-file backup, and
the load → mutate → save round-trip used by every caller.

What's NOT tested here (because it's not in this module yet):
  - Title generation (LLM, morality vet) — future title_system.py
  - Context tag matching for chat invocation — future title_invoker.py
  - GUI rendering — future gui.py changes
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sentinel.title_storage import (
    Title, EventReference, InvocationRecord,
    Trigger, MasterResponse, InvocationResponse,
    new_title_id,
    load_titles, save_titles,
    add_title, find_title, update_title, accepted_titles,
)


def _make_title(**overrides) -> Title:
    """Build a Title with sensible defaults — accepted, well-formed,
    one event referenced. Tests override only what they care about."""
    base = dict(
        id=new_title_id(),
        title="陪過低潮的史萊姆",
        day_marker=198,
        created_at=time.time(),
        trigger=Trigger.EMERGENT,
        events_referenced=[EventReference(day=198, summary="我先不推你")],
        master_response=MasterResponse.ACCEPTED,
    )
    base.update(overrides)
    return Title(**base)


class TestDisplayText(unittest.TestCase):
    def test_includes_day_marker(self):
        t = _make_title(title="水底的史萊姆", day_marker=89)
        self.assertEqual(t.display_text(), "水底的史萊姆 (D89)")

    def test_renamed_uses_new_name(self):
        t = _make_title(
            title="拆過依附的史萊姆",
            master_response=MasterResponse.RENAMED,
            master_renamed_to="放手的史萊姆",
            day_marker=67,
        )
        # Master's chosen name wins; original is not surfaced.
        self.assertIn("放手的史萊姆", t.display_text())
        self.assertNotIn("拆過依附", t.display_text())


class TestStateMethods(unittest.TestCase):
    def test_is_in_box_accepted(self):
        self.assertTrue(_make_title(master_response=MasterResponse.ACCEPTED).is_in_box())

    def test_is_in_box_renamed(self):
        t = _make_title(master_response=MasterResponse.RENAMED,
                        master_renamed_to="新名字")
        self.assertTrue(t.is_in_box())

    def test_is_in_box_pending(self):
        self.assertFalse(_make_title(master_response=MasterResponse.PENDING).is_in_box())

    def test_is_in_box_rejected(self):
        self.assertFalse(_make_title(master_response=MasterResponse.REJECTED).is_in_box())

    def test_is_frozen_none_active(self):
        self.assertFalse(_make_title(frozen_until=None).is_frozen())

    def test_is_frozen_future_epoch(self):
        future = time.time() + 86400
        self.assertTrue(_make_title(frozen_until=future).is_frozen())

    def test_is_frozen_past_epoch(self):
        past = time.time() - 86400
        self.assertFalse(_make_title(frozen_until=past).is_frozen())


class TestWellFormedness(unittest.TestCase):
    """is_well_formed encodes ADR 紅線 1-3 / 10 — the invariants
    storage can sanity-check without depending on title_system."""

    def test_happy_path(self):
        self.assertTrue(_make_title().is_well_formed())

    def test_empty_title_is_not_well_formed(self):
        self.assertFalse(_make_title(title="").is_well_formed())
        self.assertFalse(_make_title(title="   ").is_well_formed())

    def test_negative_day_marker_is_not_well_formed(self):
        # ADR 紅線 #2 — 時間標籤永遠存在 (and meaningful)
        self.assertFalse(_make_title(day_marker=-1).is_well_formed())

    def test_unknown_trigger_is_not_well_formed(self):
        self.assertFalse(_make_title(trigger="some_future_value").is_well_formed())

    def test_unknown_master_response_is_not_well_formed(self):
        self.assertFalse(
            _make_title(master_response="some_future_value").is_well_formed()
        )

    def test_accepted_without_events_is_not_well_formed(self):
        # ADR 紅線 #1 — 稱號必須對應實際發生的事件
        self.assertFalse(
            _make_title(events_referenced=[]).is_well_formed()
        )

    def test_renamed_without_new_name_is_not_well_formed(self):
        # If the master renamed it, the new name must be there
        t = _make_title(master_response=MasterResponse.RENAMED,
                        master_renamed_to=None)
        self.assertFalse(t.is_well_formed())
        # Empty string also invalid
        t = _make_title(master_response=MasterResponse.RENAMED,
                        master_renamed_to="   ")
        self.assertFalse(t.is_well_formed())

    def test_pending_without_events_is_well_formed(self):
        # Pending titles are mid-flight; events constraint only applies
        # at accept time. A proposal might not yet have its event list.
        t = _make_title(master_response=MasterResponse.PENDING,
                        events_referenced=[])
        self.assertTrue(t.is_well_formed())


class TestPersistence(unittest.TestCase):
    """Round-trip + corrupt-file behaviour for load_titles / save_titles."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.titles_file = Path(self.tmp.name) / "aislime_titles.json"
        self.patcher = mock.patch(
            "sentinel.title_storage.TITLES_FILE", self.titles_file)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_load_when_no_file_returns_empty(self):
        self.assertFalse(self.titles_file.exists())
        self.assertEqual(load_titles(), [])

    def test_save_then_load_round_trip(self):
        t = _make_title()
        save_titles([t])
        loaded = load_titles()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0], t)

    def test_round_trip_preserves_nested_dataclasses(self):
        t = _make_title(
            events_referenced=[
                EventReference(day=178, summary="連續 30 天少於一分鐘"),
                EventReference(day=195, summary="主人說『我快撐不下去了』"),
            ],
            invocation_history=[
                InvocationRecord(date=1700000000.0,
                                 master_responded=InvocationResponse.POSITIVE),
                InvocationRecord(date=1700100000.0,
                                 master_responded=InvocationResponse.SILENT),
            ],
            context_tags=["主人提及低潮", "主人質疑撐不撐得下去"],
            do_not_invoke_when=["主人在開心分享"],
        )
        save_titles([t])
        loaded = load_titles()
        self.assertEqual(loaded[0], t)
        # Specifically check the nested dataclasses survived as objects
        # (a JSON round-trip without _title_from_dict would degrade them
        # to plain dicts; this confirms the reconstructor wired up).
        self.assertIsInstance(loaded[0].events_referenced[0], EventReference)
        self.assertIsInstance(loaded[0].invocation_history[0], InvocationRecord)

    def test_save_writes_atomically(self):
        # tmp file should not linger after a successful save.
        t = _make_title()
        save_titles([t])
        tmp_path = self.titles_file.with_suffix(".tmp")
        self.assertFalse(tmp_path.exists(),
                         f".tmp file lingered: {tmp_path}")
        self.assertTrue(self.titles_file.exists())

    def test_corrupt_file_is_backed_up_and_load_returns_empty(self):
        # Pre-v0.8 incident pattern: a corrupt save should never wipe
        # silently. Same justification as evolution.load_evolution.
        self.titles_file.write_text("this is not json {{{", encoding="utf-8")

        loaded = load_titles()
        self.assertEqual(loaded, [])

        # The original file got renamed to a .broken.<epoch>.json
        backups = list(self.titles_file.parent.glob("aislime_titles*.broken.*.json"))
        self.assertEqual(len(backups), 1)
        # And the live path is gone (so the next save starts clean,
        # not appending to a corrupt file)
        self.assertFalse(self.titles_file.exists())

    def test_non_list_payload_returns_empty(self):
        # Defensive — if the JSON parses but is e.g. an object, we
        # don't try to iterate keys as if they were rows.
        self.titles_file.write_text(
            json.dumps({"oops": "wrong shape"}), encoding="utf-8")
        self.assertEqual(load_titles(), [])

    def test_malformed_row_does_not_torpedo_other_rows(self):
        # One row with a bad shape; others valid. We expect the loader
        # to skip the broken one and keep the rest — losing 200 good
        # titles because of one rotten dict would be a massive misery.
        good = _make_title(title="第一個").id, _make_title(title="第三個").id
        good_titles = [
            _make_title(id=good[0], title="第一個"),
            _make_title(id=good[1], title="第三個"),
        ]
        payload = [
            {"id": good[0], **{k: v for k, v in
                               {"title": "第一個", "day_marker": 1,
                                "created_at": 0.0, "trigger": Trigger.EMERGENT,
                                "events_referenced": [{"day": 1, "summary": "x"}],
                                "master_response": MasterResponse.ACCEPTED,
                                }.items()}},
            {"id": "broken", "this is not": "a real title row"},
            {"id": good[1], **{
                "title": "第三個", "day_marker": 3,
                "created_at": 0.0, "trigger": Trigger.EMERGENT,
                "events_referenced": [{"day": 3, "summary": "x"}],
                "master_response": MasterResponse.ACCEPTED,
            }},
        ]
        self.titles_file.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        loaded = load_titles()
        loaded_ids = sorted(t.id for t in loaded)
        self.assertEqual(loaded_ids, sorted(good))


class TestHelpers(unittest.TestCase):
    """add_title / find_title / update_title / accepted_titles."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.titles_file = Path(self.tmp.name) / "aislime_titles.json"
        self.patcher = mock.patch(
            "sentinel.title_storage.TITLES_FILE", self.titles_file)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_add_title_appends(self):
        t1 = _make_title(title="第一個")
        t2 = _make_title(title="第二個")
        add_title(t1)
        add_title(t2)
        self.assertEqual(len(load_titles()), 2)

    def test_add_title_rejects_id_collision(self):
        t1 = _make_title()
        add_title(t1)
        # Same id, different content — should refuse rather than
        # silently overwrite (could be a generator regression).
        t1_again = _make_title(id=t1.id, title="not the same")
        with self.assertRaises(ValueError):
            add_title(t1_again)

    def test_find_title_returns_match(self):
        t = _make_title()
        add_title(t)
        found = find_title(t.id)
        self.assertEqual(found, t)

    def test_find_title_returns_none_when_missing(self):
        self.assertIsNone(find_title("nonexistent_id"))

    def test_update_title_replaces_in_place(self):
        t = _make_title(master_response=MasterResponse.PENDING,
                        events_referenced=[])
        add_title(t)
        # Master accepts → swap in the accepted version
        accepted = _make_title(
            id=t.id,
            master_response=MasterResponse.ACCEPTED,
            events_referenced=[EventReference(day=t.day_marker,
                                              summary="x")],
        )
        ok = update_title(accepted)
        self.assertTrue(ok)
        loaded = find_title(t.id)
        self.assertEqual(loaded.master_response, MasterResponse.ACCEPTED)

    def test_update_title_returns_false_when_id_unknown(self):
        unknown = _make_title()
        ok = update_title(unknown)
        self.assertFalse(ok)
        # And no row sneaks in via update — caller's job to decide.
        self.assertEqual(load_titles(), [])

    def test_accepted_titles_filter(self):
        ones = [
            _make_title(title="A", master_response=MasterResponse.PENDING,
                        events_referenced=[]),
            _make_title(title="B", master_response=MasterResponse.ACCEPTED),
            _make_title(title="C", master_response=MasterResponse.REJECTED),
            _make_title(title="D", master_response=MasterResponse.RENAMED,
                        master_renamed_to="主人改的"),
        ]
        save_titles(ones)
        kept = {t.title for t in accepted_titles()}
        self.assertEqual(kept, {"B", "D"})

    def test_accepted_titles_includes_frozen(self):
        # Cold storage from invocation perspective only — frozen titles
        # still appear in the user-visible 箱子.
        future = time.time() + 86400
        save_titles([
            _make_title(title="frozen-but-accepted", frozen_until=future),
        ])
        kept = accepted_titles()
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].is_frozen())


if __name__ == "__main__":
    unittest.main()
