"""Tests for sentinel/birth_signature_render.py + the render wiring
in slime_avatar.py / overlay.py.

Two layers:

  - Helper-level (apply_signature_to_colors / _to_dimensions /
    draw_marking) — pure Qt math, deterministic, easy to assert on.

  - Widget-level — instantiate SlimeWidget + SlimeOverlay under
    offscreen Qt, force a paint with a non-empty birth_signature
    plugged in, and confirm the paint loop completes without
    raising. Same role as test_gui_smoke for MemoryTab — protects
    against the next 'NameError inside a Qt slot' regression.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestApplySignatureToColors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _base_colors(self):
        from PySide6.QtGui import QColor
        return {
            "body": QColor(0, 180, 255, 200),
            "highlight": QColor(150, 230, 255, 180),
            "glow": QColor(0, 180, 255, 40),
            "eye": QColor(30, 30, 30),
            "mouth": QColor(30, 30, 30),
        }

    def test_empty_signature_returns_colors_unchanged(self):
        from sentinel.birth_signature_render import apply_signature_to_colors
        base = self._base_colors()
        out = apply_signature_to_colors(base, {})
        self.assertIs(out, base)

    def test_zero_offsets_short_circuit(self):
        from sentinel.birth_signature_render import apply_signature_to_colors
        base = self._base_colors()
        sig = {"body_hue_offset": 0.0, "body_saturation_factor": 1.0}
        out = apply_signature_to_colors(base, sig)
        # Same dict — short-circuit returns input
        self.assertIs(out, base)

    def test_hue_offset_changes_body_color(self):
        from sentinel.birth_signature_render import apply_signature_to_colors
        base = self._base_colors()
        before_hue = base["body"].getHsv()[0]
        sig = {"body_hue_offset": 30.0, "body_saturation_factor": 1.0}
        out = apply_signature_to_colors(base, sig)
        after_hue = out["body"].getHsv()[0]
        self.assertNotEqual(before_hue, after_hue)
        # And the original dict is untouched (caller may still need it)
        self.assertEqual(base["body"].getHsv()[0], before_hue)

    def test_eye_and_mouth_untouched(self):
        from sentinel.birth_signature_render import apply_signature_to_colors
        base = self._base_colors()
        sig = {"body_hue_offset": 30.0, "body_saturation_factor": 1.0}
        out = apply_signature_to_colors(base, sig)
        # Eyes and mouth should not be shifted — they're not "body".
        self.assertEqual(out["eye"].rgb(), base["eye"].rgb())
        self.assertEqual(out["mouth"].rgb(), base["mouth"].rgb())

    def test_alpha_preserved(self):
        from sentinel.birth_signature_render import apply_signature_to_colors
        base = self._base_colors()
        sig = {"body_hue_offset": 50.0, "body_saturation_factor": 0.9}
        out = apply_signature_to_colors(base, sig)
        # Alpha must survive the HSV round-trip — the slime is
        # translucent on purpose, losing the alpha would make it
        # opaque against the desktop.
        self.assertEqual(out["body"].alpha(), base["body"].alpha())
        self.assertEqual(out["glow"].alpha(), base["glow"].alpha())


class TestApplySignatureToDimensions(unittest.TestCase):
    def test_empty_signature_returns_unchanged(self):
        from sentinel.birth_signature_render import apply_signature_to_dimensions
        self.assertEqual(apply_signature_to_dimensions(50, 40, {}), (50, 40))

    def test_default_factors_are_unity(self):
        from sentinel.birth_signature_render import apply_signature_to_dimensions
        sig = {"body_width_factor": 1.0, "body_height_factor": 1.0}
        self.assertEqual(apply_signature_to_dimensions(50, 40, sig), (50, 40))

    def test_factors_scale_dimensions(self):
        from sentinel.birth_signature_render import apply_signature_to_dimensions
        sig = {"body_width_factor": 1.05, "body_height_factor": 0.95}
        w, h = apply_signature_to_dimensions(100, 100, sig)
        self.assertEqual(w, 105)
        self.assertEqual(h, 95)


class TestDrawMarking(unittest.TestCase):
    """draw_marking should never raise, even with edge-case signatures.
    The pixel content isn't asserted (would couple tests to QPainter
    implementation), only that the call completes."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _new_painter(self):
        """Build a QPainter on a small QImage so draw calls are valid."""
        from PySide6.QtGui import QImage, QPainter, QColor
        img = QImage(100, 100, QImage.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        return painter, img, QColor(0, 180, 255, 200)

    def test_no_marking_is_noop(self):
        from sentinel.birth_signature_render import draw_marking
        painter, _, body = self._new_painter()
        try:
            draw_marking(painter, {}, 50, 50, 30, 25, body)
            draw_marking(painter, {"marking": None}, 50, 50, 30, 25, body)
        finally:
            painter.end()

    def test_each_marking_type_renders(self):
        from sentinel.birth_signature_render import draw_marking
        for mtype in ("dot", "swirl", "line"):
            painter, _, body = self._new_painter()
            sig = {"marking": {
                "type": mtype,
                "position_x": 0.3,
                "position_y": -0.2,
                "hue_delta": 20.0,
                "lightness_delta": 0.1,
            }}
            try:
                draw_marking(painter, sig, 50, 50, 30, 25, body)
            finally:
                painter.end()

    def test_unknown_marking_type_is_noop(self):
        from sentinel.birth_signature_render import draw_marking
        painter, _, body = self._new_painter()
        sig = {"marking": {"type": "unknown_future_type",
                           "position_x": 0.0, "position_y": 0.0,
                           "hue_delta": 0.0, "lightness_delta": 0.0}}
        try:
            # Forward compatibility — a save written by a future schema
            # that adds new marking types should not crash older code.
            draw_marking(painter, sig, 50, 50, 30, 25, body)
        finally:
            painter.end()


class TestWidgetPaintSmoke(unittest.TestCase):
    """The integration check — both slime widgets must paint cleanly
    with a non-empty signature plugged in. PR #128's bug was a
    NameError inside a Qt slot; this is the same shape of regression
    we're protecting against here. paintEvent runs on every screen
    refresh, so any reference error is fatal."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _sample_signature(self) -> dict:
        # Includes a marking so we exercise the full draw path, not
        # just the no-op branch.
        return {
            "body_hue_offset": 15.0,
            "body_saturation_factor": 1.05,
            "body_height_factor": 1.02,
            "body_width_factor": 0.98,
            "marking": {
                "type": "swirl",
                "position_x": 0.2,
                "position_y": -0.1,
                "hue_delta": 25.0,
                "lightness_delta": 0.08,
            },
        }

    def _force_paint(self, widget):
        """Trigger a paint by drawing into a QPixmap. widget.update()
        on offscreen Qt doesn't reliably fire paintEvent; rendering
        into a pixmap does."""
        from PySide6.QtGui import QPixmap
        widget.resize(200, 200)
        pix = QPixmap(widget.size())
        pix.fill(0)
        widget.render(pix)

    def test_slime_widget_paints_with_signature(self):
        # Patch load_evolution to inject a signature without touching
        # the user's real evolution.json.
        with mock.patch("sentinel.evolution.load_evolution") as mock_load:
            mock_load.return_value = mock.Mock(
                birth_signature=self._sample_signature())
            from sentinel.slime_avatar import SlimeWidget
            w = SlimeWidget()
            self.assertEqual(w._birth_signature["body_hue_offset"], 15.0)
            self._force_paint(w)

    def test_slime_overlay_paints_with_signature(self):
        with mock.patch("sentinel.evolution.load_evolution") as mock_load:
            mock_load.return_value = mock.Mock(
                birth_signature=self._sample_signature())
            from sentinel.overlay import SlimeOverlay
            w = SlimeOverlay()
            self.assertEqual(w._birth_signature["body_hue_offset"], 15.0)
            self._force_paint(w)

    def test_slime_widget_paints_with_empty_signature(self):
        # The fallback path — load_evolution returns no signature.
        # Render must still succeed; that's the whole point of the
        # graceful degradation in _load_birth_signature.
        with mock.patch("sentinel.evolution.load_evolution") as mock_load:
            mock_load.return_value = mock.Mock(birth_signature={})
            from sentinel.slime_avatar import SlimeWidget
            w = SlimeWidget()
            self.assertEqual(w._birth_signature, {})
            self._force_paint(w)

    def test_slime_widget_paints_when_load_evolution_raises(self):
        # The 'evolution file is corrupt' / 'IO error' path.
        with mock.patch("sentinel.evolution.load_evolution",
                        side_effect=RuntimeError("simulated IO failure")):
            from sentinel.slime_avatar import SlimeWidget
            w = SlimeWidget()
            self.assertEqual(w._birth_signature, {})
            self._force_paint(w)


if __name__ == "__main__":
    unittest.main()
