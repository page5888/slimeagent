"""Generate slime app icon programmatically (no external file needed)."""
from PySide6.QtGui import (QPixmap, QPainter, QColor, QIcon,
                            QRadialGradient, QBrush, QPen)
from PySide6.QtCore import Qt, QPoint


def create_icon(size=64) -> QIcon:
    """Create a cute slime icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)

    cx = size // 2
    cy = int(size * 0.55)

    # Body - blue translucent slime with gradient
    body_w = int(size * 0.4)
    body_h = int(size * 0.32)
    gradient = QRadialGradient(cx - body_w * 0.2, cy - body_h * 0.3, body_w * 1.2)
    gradient.setColorAt(0, QColor(150, 230, 255, 220))
    gradient.setColorAt(0.5, QColor(0, 180, 255, 200))
    gradient.setColorAt(1, QColor(0, 100, 200, 180))
    p.setBrush(QBrush(gradient))
    p.setPen(QPen(QColor(0, 160, 220, 180), max(1, size // 32)))
    p.drawEllipse(QPoint(cx, cy), body_w, body_h)

    # Small top bump (antenna)
    bump_w = int(size * 0.08)
    bump_h = int(size * 0.12)
    bump_cy = cy - body_h - bump_h // 3
    bump_grad = QRadialGradient(cx, bump_cy - bump_h * 0.3, bump_w * 1.5)
    bump_grad.setColorAt(0, QColor(180, 240, 255, 220))
    bump_grad.setColorAt(1, QColor(0, 180, 255, 200))
    p.setBrush(QBrush(bump_grad))
    p.drawEllipse(QPoint(cx, bump_cy), bump_w, bump_h)

    # Eyes - white with black pupils
    eye_y = cy - int(body_h * 0.1)
    eye_spacing = int(body_w * 0.35)
    eye_r = max(2, size // 12)

    # Eye whites
    p.setBrush(QBrush(QColor(255, 255, 255, 230)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx - eye_spacing, eye_y), eye_r + 1, eye_r + 1)
    p.drawEllipse(QPoint(cx + eye_spacing, eye_y), eye_r + 1, eye_r + 1)

    # Pupils
    pupil_r = max(1, eye_r - 1)
    p.setBrush(QBrush(QColor(20, 20, 20)))
    p.drawEllipse(QPoint(cx - eye_spacing, eye_y), pupil_r, pupil_r)
    p.drawEllipse(QPoint(cx + eye_spacing, eye_y), pupil_r, pupil_r)

    # Eye shine
    shine_r = max(1, size // 24)
    p.setBrush(QBrush(QColor(255, 255, 255, 220)))
    p.drawEllipse(QPoint(cx - eye_spacing - 1, eye_y - 1), shine_r, shine_r)
    p.drawEllipse(QPoint(cx + eye_spacing - 1, eye_y - 1), shine_r, shine_r)

    # Smile
    mouth_y = cy + int(body_h * 0.2)
    smile_w = max(2, size // 10)
    p.setPen(QPen(QColor(30, 30, 80, 180), max(1, size // 32)))
    p.setBrush(Qt.NoBrush)
    from PySide6.QtCore import QRect
    p.drawArc(QRect(cx - smile_w, mouth_y - smile_w // 2, smile_w * 2, smile_w),
              0, -180 * 16)

    p.end()
    return QIcon(pixmap)


def create_tray_icon(size=32) -> QIcon:
    return create_icon(size)
