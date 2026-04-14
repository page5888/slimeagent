"""Telegram notification module."""
import asyncio
import time
import logging
from telegram import Bot
from sentinel.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, NOTIFICATION_COOLDOWN

log = logging.getLogger("sentinel.notifier")

# Track last notification time per category to avoid spam
_last_sent: dict[str, float] = {}


async def _send(text: str):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    # Telegram max message length is 4096
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
    )


def send_notification(text: str, category: str = "general"):
    """Send a Telegram message. Respects cooldown per category."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.debug("Telegram not configured, skipping notification")
        return False

    now = time.time()
    last = _last_sent.get(category, 0)
    if now - last < NOTIFICATION_COOLDOWN:
        log.debug(f"Cooldown active for '{category}', skipping notification")
        return False

    try:
        try:
            asyncio.get_event_loop().run_until_complete(_send(text))
        except RuntimeError:
            asyncio.run(_send(text))
        _last_sent[category] = now
        log.info(f"Sent notification [{category}]")
        return True
    except Exception as e:
        log.warning(f"Telegram notification failed: {e}")
        return False


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
