"""Generate .ico file and create a desktop shortcut with custom icon.

Run once: python create_shortcut.py
"""
import sys
import os

def generate_ico():
    """Generate slime.ico from the app's icon module."""
    # Need QApplication for QPixmap
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPixmap, QPainter, QColor, QRadialGradient, QBrush, QPen
    from PySide6.QtCore import Qt, QPoint, QRect

    app = QApplication.instance() or QApplication(sys.argv)

    sizes = [16, 32, 48, 64, 128, 256]
    pixmaps = []

    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)

        cx = size // 2
        cy = int(size * 0.55)

        # Body
        body_w = int(size * 0.4)
        body_h = int(size * 0.32)
        gradient = QRadialGradient(cx - body_w * 0.2, cy - body_h * 0.3, body_w * 1.2)
        gradient.setColorAt(0, QColor(150, 230, 255, 220))
        gradient.setColorAt(0.5, QColor(0, 180, 255, 200))
        gradient.setColorAt(1, QColor(0, 100, 200, 180))
        p.setBrush(QBrush(gradient))
        p.setPen(QPen(QColor(0, 160, 220, 180), max(1, size // 32)))
        p.drawEllipse(QPoint(cx, cy), body_w, body_h)

        # Antenna bump
        bump_w = int(size * 0.08)
        bump_h = int(size * 0.12)
        bump_cy = cy - body_h - bump_h // 3
        bump_grad = QRadialGradient(cx, bump_cy - bump_h * 0.3, bump_w * 1.5)
        bump_grad.setColorAt(0, QColor(180, 240, 255, 220))
        bump_grad.setColorAt(1, QColor(0, 180, 255, 200))
        p.setBrush(QBrush(bump_grad))
        p.drawEllipse(QPoint(cx, bump_cy), bump_w, bump_h)

        # Eyes
        eye_y = cy - int(body_h * 0.1)
        eye_spacing = int(body_w * 0.35)
        eye_r = max(2, size // 12)

        p.setBrush(QBrush(QColor(255, 255, 255, 230)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(cx - eye_spacing, eye_y), eye_r + 1, eye_r + 1)
        p.drawEllipse(QPoint(cx + eye_spacing, eye_y), eye_r + 1, eye_r + 1)

        pupil_r = max(1, eye_r - 1)
        p.setBrush(QBrush(QColor(20, 20, 20)))
        p.drawEllipse(QPoint(cx - eye_spacing, eye_y), pupil_r, pupil_r)
        p.drawEllipse(QPoint(cx + eye_spacing, eye_y), pupil_r, pupil_r)

        shine_r = max(1, size // 24)
        p.setBrush(QBrush(QColor(255, 255, 255, 220)))
        p.drawEllipse(QPoint(cx - eye_spacing - 1, eye_y - 1), shine_r, shine_r)
        p.drawEllipse(QPoint(cx + eye_spacing - 1, eye_y - 1), shine_r, shine_r)

        # Smile
        mouth_y = cy + int(body_h * 0.2)
        smile_w = max(2, size // 10)
        p.setPen(QPen(QColor(30, 30, 80, 180), max(1, size // 32)))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRect(cx - smile_w, mouth_y - smile_w // 2, smile_w * 2, smile_w),
                  0, -180 * 16)

        p.end()
        pixmaps.append(pixmap)

    # Save as .ico (Qt can write ICO directly)
    ico_path = os.path.join(os.path.dirname(__file__), "slime.ico")
    # Use the largest pixmap for the ico, Qt will embed it
    # For proper multi-size ICO, save via QImage
    from PySide6.QtGui import QImage
    import struct

    # Build ICO file manually for multi-size support
    images = []
    for px in pixmaps:
        img = px.toImage().convertToFormat(QImage.Format_ARGB32)
        images.append(img)

    with open(ico_path, "wb") as f:
        count = len(images)
        # ICO header: reserved(2) + type(2) + count(2)
        f.write(struct.pack("<HHH", 0, 1, count))

        # Calculate offsets
        header_size = 6 + count * 16
        offset = header_size
        png_data_list = []

        for img in images:
            from PySide6.QtCore import QBuffer, QIODevice
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "PNG")
            data = buf.data().data()
            png_data_list.append(data)

        # Write directory entries
        for i, img in enumerate(images):
            w = img.width() if img.width() < 256 else 0
            h = img.height() if img.height() < 256 else 0
            data_size = len(png_data_list[i])
            # width, height, colors, reserved, planes, bpp, size, offset
            f.write(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, data_size, offset))
            offset += data_size

        # Write image data
        for data in png_data_list:
            f.write(data)

    print(f"[OK] Icon saved: {ico_path}")
    return ico_path


def create_shortcut(ico_path: str):
    """Create a Windows shortcut (.lnk) on the desktop."""
    try:
        import winshell
    except ImportError:
        # Fallback: use PowerShell
        _create_shortcut_powershell(ico_path)
        return

    desktop = winshell.desktop()
    link_path = os.path.join(desktop, "AI Slime Agent.lnk")
    target = os.path.join(os.path.dirname(__file__), "start.bat")
    work_dir = os.path.dirname(__file__)

    with winshell.shortcut(link_path) as link:
        link.path = target
        link.working_directory = work_dir
        link.icon_location = (ico_path, 0)
        link.description = "AI Slime Agent — 轉生守護靈"

    print(f"[OK] Shortcut created: {link_path}")


def _create_shortcut_powershell(ico_path: str):
    """Create shortcut using PowerShell (no extra dependencies)."""
    import subprocess

    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    link_path = os.path.join(desktop, "AI Slime Agent.lnk")
    target = os.path.join(os.path.dirname(__file__), "start.bat")
    work_dir = os.path.dirname(__file__)

    ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("{link_path}")
$sc.TargetPath = "{target}"
$sc.WorkingDirectory = "{work_dir}"
$sc.IconLocation = "{ico_path}, 0"
$sc.Description = "AI Slime Agent"
$sc.Save()
'''
    subprocess.run(["powershell", "-Command", ps_script],
                   capture_output=True, text=True)
    print(f"[OK] Shortcut created: {link_path}")


if __name__ == "__main__":
    ico = generate_ico()
    create_shortcut(ico)
    print("\nDone! You can now launch AI Slime from the desktop shortcut.")
