"""Sentinel daemon - the main loop that ties everything together.

Runs in background. Watches. Learns. Chats. Notifies only when it matters.
"""
import time
import signal
import logging
import asyncio
import threading
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters
from sentinel.config import (
    SYSTEM_CHECK_INTERVAL, WATCH_DIRS, IDLE_REPORT_INTERVAL,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, NOTIFICATION_COOLDOWN,
)
from sentinel.system_monitor import take_snapshot
from sentinel.file_watcher import FileWatcher
from sentinel.claude_watcher import get_claude_activity_summary
from sentinel.brain import analyze_events, build_context
from sentinel.learner import distill_from_activity, distill_speech_style, get_profile_summary, load_memory
from sentinel.chat import handle_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            str(__import__('pathlib').Path.home() / ".hermes" / "sentinel.log"),
            encoding='utf-8',
        ),
    ],
)
log = logging.getLogger("sentinel")

running = True


def handle_signal(sig, frame):
    global running
    log.info("Shutdown signal received")
    running = False


# ─── Telegram Bot Handlers ───────────────────────────────────────────────

async def on_message(update: Update, context):
    """Handle incoming Telegram messages from the user."""
    if update.message is None:
        return
    # Only respond to the owner
    if update.message.chat_id != TELEGRAM_CHAT_ID:
        return

    user_text = update.message.text
    if not user_text:
        return

    log.info(f"Received message from user: {user_text[:50]}...")
    reply = handle_message(user_text)

    await update.message.reply_text(reply)


async def cmd_status(update: Update, context):
    """Handle /status command - show current system state."""
    if update.message.chat_id != TELEGRAM_CHAT_ID:
        return

    snapshot = take_snapshot()
    profile = get_profile_summary()

    text = (
        f"📊 *系統狀態*\n"
        f"{snapshot.summary()}\n\n"
        f"🧠 *對你的理解*\n"
        f"{profile[:500]}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_memory(update: Update, context):
    """Handle /memory command - show what Sentinel has learned."""
    if update.message.chat_id != TELEGRAM_CHAT_ID:
        return

    memory = load_memory()
    obs = memory.get("observations", [])[-10:]
    patterns = memory.get("patterns", {})
    sessions = memory.get("session_count", 0)
    chats = memory.get("chat_count", 0)

    text = (
        f"🧠 *AI Slime 記憶*\n"
        f"學習次數: {sessions} | 對話次數: {chats}\n\n"
        f"*Profile:*\n{memory.get('profile', '(學習中)')}\n\n"
        f"*最近觀察:*\n"
    )
    for o in obs:
        text += f"• {o}\n"

    if len(text) > 4000:
        text = text[:4000] + "\n..."

    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Monitor Thread ──────────────────────────────────────────────────────

def monitor_loop(bot_send_fn):
    """System monitoring loop - runs in a separate thread."""
    global running

    watcher = FileWatcher(WATCH_DIRS)
    watcher.start()
    log.info(f"Watching directories: {[str(d) for d in WATCH_DIRS]}")

    last_check = 0
    last_distill = time.time()
    last_idle_report = time.time()
    last_notify_time: dict[str, float] = {}
    last_chat_count = load_memory().get("chat_count", 0)
    activity_buffer = []

    try:
        while running:
            now = time.time()

            if now - last_check >= SYSTEM_CHECK_INTERVAL:
                last_check = now

                snapshot = take_snapshot()
                file_events = watcher.get_events()
                claude_activity = get_claude_activity_summary()
                context = build_context(snapshot, file_events, claude_activity)

                if file_events or claude_activity:
                    activity_buffer.append(context)
                    last_idle_report = now

                # Analyze if something looks off
                has_warnings = bool(snapshot.warnings)
                has_burst = len(file_events) > 20
                if has_warnings or has_burst:
                    decision = analyze_events(context)
                    if decision and decision.get("should_notify"):
                        cat = decision.get("category", "general")
                        last_t = last_notify_time.get(cat, 0)
                        if now - last_t >= NOTIFICATION_COOLDOWN:
                            severity = decision.get("severity", "info")
                            emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")
                            msg = f"{emoji} *AI Slime*\n{decision['message']}"
                            bot_send_fn(msg)
                            last_notify_time[cat] = now

                if snapshot.warnings:
                    log.warning(f"Warnings: {snapshot.warnings}")

            # Learning cycle (every 10 min)
            if now - last_distill >= 600 and activity_buffer:
                last_distill = now
                combined = "\n---\n".join(activity_buffer[-10:])
                result = distill_from_activity(combined)
                if result is not None:
                    activity_buffer.clear()
                    log.info(f"Learning cycle done. Profile: {get_profile_summary()[:100]}...")
                else:
                    log.info("Distill failed (rate limit?), keeping buffer for next cycle")

                # Speech-style learning: only if chats happened since last run
                current_chat_count = load_memory().get("chat_count", 0)
                if current_chat_count > last_chat_count:
                    try:
                        distill_speech_style()
                    except Exception as e:
                        log.warning(f"speech-style distill error: {e}")
                    last_chat_count = current_chat_count

            # Idle report (every 30 min)
            if now - last_idle_report >= IDLE_REPORT_INTERVAL:
                last_idle_report = now
                snapshot = take_snapshot()
                bot_send_fn(
                    f"💤 *AI Slime 定期報告*\n系統正常。\n{snapshot.summary()}"
                )
                # I. Narrative arc: check for loneliness during idle reports
                # (rate-limited internally — at most one loneliness moment per
                # 30 days regardless of how often we call this).
                try:
                    from sentinel import identity
                    identity.record_loneliness_arc_if_due()
                except Exception as e:
                    log.warning(f"loneliness arc check error: {e}")

            time.sleep(2)

    except Exception as e:
        log.error(f"Monitor error: {e}", exc_info=True)
        bot_send_fn(f"🔴 *AI Slime 監控異常*\n{e}")
    finally:
        watcher.stop()


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    global running
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("=== AI Slime Agent starting ===")

    # Build Telegram bot application
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    # Helper to send messages from the monitor thread
    def send_from_thread(text: str):
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            import asyncio as _aio
            loop = _aio.new_event_loop()
            loop.run_until_complete(bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="Markdown"
            ))
            loop.close()
        except Exception as e:
            log.error(f"Send error: {e}")

    # Send startup message
    send_from_thread(
        "🟢 *AI Slime 已上線*\n"
        "正在監控你的電腦和開發活動。\n"
        "你可以隨時傳訊息給我聊天。\n\n"
        "指令：\n"
        "/status - 查看系統狀態\n"
        "/memory - 查看我學到了什麼"
    )

    # Start monitor in background thread
    monitor_thread = threading.Thread(
        target=monitor_loop, args=(send_from_thread,), daemon=True
    )
    monitor_thread.start()

    # Run Telegram bot (blocks main thread)
    log.info("Telegram bot listening...")
    app.run_polling(drop_pending_updates=True)

    running = False
    monitor_thread.join(timeout=5)
    send_from_thread("🔴 *AI Slime 已離線*")
    log.info("=== AI Slime Agent stopped ===")


if __name__ == "__main__":
    main()
