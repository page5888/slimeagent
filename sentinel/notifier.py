"""Notification module — Telegram + desktop toast fallback."""
import asyncio
import time
import logging
import re
from sentinel.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, NOTIFICATION_COOLDOWN

log = logging.getLogger("sentinel.notifier")

# Track last notification time per category to avoid spam
_last_sent: dict[str, float] = {}

# GUI signal bridge reference — set by MainWindow on startup
_signal_bridge = None


def set_signal_bridge(bridge):
    """Called by GUI to register the signal bridge for desktop notifications."""
    global _signal_bridge
    _signal_bridge = bridge


def _strip_markdown(text: str) -> str:
    """Remove Telegram markdown for desktop toast display."""
    return re.sub(r'\*([^*]+)\*', r'\1', text)


async def _send_telegram(text: str):
    from telegram import Bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
    )


def _send_desktop(text: str, category: str):
    """Send desktop toast notification via system tray."""
    if _signal_bridge is None:
        log.debug("No signal bridge, cannot send desktop notification")
        return False
    clean = _strip_markdown(text)
    # Split first line as title, rest as body
    lines = clean.strip().split("\n", 1)
    title = lines[0] if lines else "AI Slime"
    body = lines[1].strip() if len(lines) > 1 else ""
    _signal_bridge.desktop_notify.emit(title, body)
    return True


def send_notification(text: str, category: str = "general"):
    """Send notification — tries Telegram first, falls back to desktop toast."""
    now = time.time()
    last = _last_sent.get(category, 0)
    if now - last < NOTIFICATION_COOLDOWN:
        log.debug(f"Cooldown active for '{category}', skipping notification")
        return False

    sent = False

    # Try Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            try:
                asyncio.get_event_loop().run_until_complete(_send_telegram(text))
            except RuntimeError:
                asyncio.run(_send_telegram(text))
            sent = True
            log.info(f"Sent Telegram notification [{category}]")
        except Exception as e:
            log.warning(f"Telegram notification failed: {e}")

    # Always also send desktop toast (non-intrusive)
    try:
        _send_desktop(text, category)
        sent = True
    except Exception as e:
        log.debug(f"Desktop notification failed: {e}")

    if sent:
        _last_sent[category] = now
    return sent


def send_startup_message():
    """Announce that the slime is awake."""
    send_notification(
        "🟢 *AI Slime 已甦醒*\n"
        "大賢者已啟動，正在觀察你的世界。\n"
        "有狀況會主動通知你。",
        category="startup",
    )


def send_shutdown_message():
    """Announce that the slime is sleeping."""
    _last_sent.pop("shutdown", None)
    send_notification(
        "🔵 *AI Slime 進入沉睡*\n下次見。",
        category="shutdown",
    )
