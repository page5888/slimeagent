"""Smoke test — gui.py imports and main tab classes can be instantiated.

This exists because PR #128 silently broke MemoryTab.refresh() by
deleting `_render_box_html` (along with its helpers), and 119/119
unit tests were green at merge time. The unit suite never touches
the GUI render path, so a bulk refactor that nukes a helper called
only inside a Qt slot ships clean.

The minimum that would have caught it: instantiate MemoryTab under a
QApplication and call refresh(). If a name's missing, this fails
loudly. Run on a headless box via QT_QPA_PLATFORM=offscreen.

Scope: classes only. Heavy MainWindow init (timers, daemon threads,
file IO) is intentionally out of scope — the goal is catching dead
references after refactors, not full integration.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock


# Headless Qt — has to be set before any PySide6 import down the chain.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestGuiSmoke(unittest.TestCase):
    """Imports + Tab instantiation. No business assertions — just 'no crash'."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_gui_module_imports(self):
        import sentinel.gui  # noqa: F401

    def test_top_level_tab_classes_defined(self):
        from sentinel.gui import (
            ChatTab, HomeTab, MemoryTab, EvolutionTab,
            SettingsTab, ApprovalTab, RoutinesTab, MainWindow,
        )
        # Just touch them — if any name is missing, import would have
        # already raised. Keep them referenced so linters don't strip.
        for cls in (ChatTab, HomeTab, MemoryTab, EvolutionTab,
                    SettingsTab, ApprovalTab, RoutinesTab, MainWindow):
            self.assertTrue(callable(cls))

    def test_memory_tab_instantiates_and_refreshes(self):
        """The exact path PR #128 broke. MemoryTab.__init__ calls
        self.refresh() which calls _render_box_html(...). If any helper
        is missing this fails with NameError."""
        from sentinel.gui import MemoryTab

        # MemoryTab pulls memory state on init via learner.load_memory
        # and evolution.load_evolution. Mock both so we don't depend on
        # local state files; we only care that the render path runs.
        with mock.patch("sentinel.learner.load_memory",
                        return_value={"memorable_moments": [],
                                      "speech_style": {}}), \
             mock.patch("sentinel.evolution.load_evolution",
                        return_value=mock.Mock(birth_time=0)):
            tab = MemoryTab()
            # Re-call refresh explicitly with a non-empty box to hit
            # every branch of _render_box_html (day_n / phrase / letter).
            with mock.patch("sentinel.identity.list_box_entries",
                            return_value=[
                                {"day_n": 5, "category": "naming",
                                 "headline": "命名了", "detail": "",
                                 "letter_to_master": "嗨",
                                 "master_phrase": "像在水底",
                                 "has_letter": True, "has_phrase": True},
                            ]):
                tab.refresh()


if __name__ == "__main__":
    unittest.main()
