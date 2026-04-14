"""Sentinel configuration."""
import os
from pathlib import Path

# Telegram
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = 0

# ─── LLM Providers ───────────────────────────────────────────────────────
# Each provider: {"name", "api_key", "base_url", "model", "enabled"}
# The system tries providers in order; if one fails, it falls back to next.

LLM_PROVIDERS = [
    {
        "name": "Gemini",
        "api_key": "",
        "base_url": "",  # Uses google-genai SDK directly
        "models": ["gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-3-flash-preview"],
        "type": "gemini",
        "enabled": True,
    },
    {
        "name": "OpenRouter",
        "api_key": "",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["google/gemini-2.5-flash-preview", "meta-llama/llama-4-maverick", "deepseek/deepseek-chat-v3"],
        "type": "openai_compat",
        "enabled": False,
    },
    {
        "name": "OpenAI",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1-mini", "gpt-4.1-nano"],
        "type": "openai_compat",
        "enabled": False,
    },
    {
        "name": "Anthropic",
        "api_key": "",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-5", "claude-haiku-4-5"],
        "type": "anthropic",
        "enabled": False,
    },
    {
        "name": "Groq",
        "api_key": "",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "type": "openai_compat",
        "enabled": False,
    },
    {
        "name": "DeepSeek",
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat"],
        "type": "openai_compat",
        "enabled": False,
    },
]

# Legacy single-provider config (used as default)
GOOGLE_API_KEY = LLM_PROVIDERS[0]["api_key"]
GEMINI_MODEL = LLM_PROVIDERS[0]["models"][0]

# Monitoring targets
WATCH_DIRS = [
    Path("D:/srbow_bots"),
]

# Claude Code conversation logs (if available)
CLAUDE_CODE_LOG_DIR = Path.home() / ".claude" / "projects"

# How often to check system health (seconds)
SYSTEM_CHECK_INTERVAL = 30

# How often to send a heartbeat summary if idle (seconds)
IDLE_REPORT_INTERVAL = 1800  # 30 min

# Event buffer - collect events before asking LLM to analyze
EVENT_BUFFER_SECONDS = 10

# Severity thresholds
CPU_WARN_PERCENT = 90
RAM_WARN_PERCENT = 85
DISK_WARN_PERCENT = 90

# Don't spam - minimum seconds between notifications for same issue
NOTIFICATION_COOLDOWN = 300  # 5 min

# ─── Model Preference ───────────────────────────────────────────────────
# "cloud_first" = 雲端優先（推薦聊天）
# "local_first" = 本地優先（省額度）
# "local_only"  = 僅本地（不用雲端）
CHAT_MODEL_PREF = "cloud_first"
ANALYSIS_MODEL_PREF = "local_first"

# ─── Commercial Layer (5888 Wallet) ─────────────────────────────────
# Relay server URL — handles LLM proxy + wallet billing for quota users.
# BYOK users don't need this; it's only for paid point-pack mode.
RELAY_SERVER_URL = ""

# User mode: "byok" (self-provided API keys) or "quota" (5888 wallet points)
# Saved per-user in ~/.hermes/rimuru_auth.json, this is just the default.
DEFAULT_USER_MODE = "byok"

# Marketplace transaction fee (percentage taken by system)
MARKETPLACE_FEE_PERCENT = 10  # 10% on P2P trades (base rate)
