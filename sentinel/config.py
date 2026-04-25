"""AI Slime Agent configuration."""
import os
import json
import logging
from pathlib import Path

_log = logging.getLogger("sentinel.config")

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

# How often to run LLM distillation on accumulated observations (seconds)
DISTILL_INTERVAL = 300  # 5 min

# Screenshot capture: random interval between MIN and MAX (seconds)
SCREEN_CAPTURE_MIN = 120   # 2 min
SCREEN_CAPTURE_MAX = 600   # 10 min

# Voice features (Phase D5). Master toggle for voice.listen + voice.speak
# action types. When False:
#   - format_catalog_for_prompt strips voice.* from the LLM-visible
#     action menu (slime won't propose voice actions)
#   - the policy gates in surface/handlers.py refuse voice.* even if
#     somehow proposed (defense in depth — covers script callers, the
#     bare-JSON parser, etc.)
# Default True because the actions are already shipped enabled and the
# user opted into the dependency install. The setting tab exposes a
# checkbox so users who never want their mic touched can flip it off.
VOICE_ENABLED = True

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
RELAY_SERVER_URL = "https://slimeagent-relay.onrender.com"

# Google OAuth Client ID — needed for marketplace login.
# Create at https://console.cloud.google.com/apis/credentials
# Type: "Web application", add http://127.0.0.1:18510 to redirect URIs.
GOOGLE_CLIENT_ID = ""
# Desktop OAuth client secret — per Google docs, not confidential for installed apps.
# Loaded from ~/.hermes/google_oauth.json to avoid GitHub secret scanning.
GOOGLE_CLIENT_SECRET = ""


def _load_google_oauth():
    """Load Google OAuth credentials from ~/.hermes/google_oauth.json."""
    global GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    creds_file = Path.home() / ".hermes" / "google_oauth.json"
    if creds_file.exists():
        try:
            data = json.loads(creds_file.read_text(encoding="utf-8"))
            GOOGLE_CLIENT_ID = data.get("client_id", "")
            GOOGLE_CLIENT_SECRET = data.get("client_secret", "")
        except Exception as e:
            _log.warning("Failed to load google_oauth.json: %s", e)


_load_google_oauth()

# User mode: "byok" (self-provided API keys) or "quota" (5888 wallet points)
# Saved per-user in ~/.hermes/aislime_auth.json, this is just the default.
DEFAULT_USER_MODE = "byok"

# Marketplace transaction fee (percentage taken by system)
MARKETPLACE_FEE_PERCENT = 10  # 10% on P2P trades (base rate)


# ─── Auto-load persisted settings on import ────────────────────────
def _load_persisted_settings():
    """Load user settings from disk. Called once on module import."""
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LLM_PROVIDERS
    global CHAT_MODEL_PREF, ANALYSIS_MODEL_PREF
    global SYSTEM_CHECK_INTERVAL, IDLE_REPORT_INTERVAL, WATCH_DIRS
    global DISTILL_INTERVAL, SCREEN_CAPTURE_MIN, SCREEN_CAPTURE_MAX
    global VOICE_ENABLED

    settings_file = Path.home() / ".hermes" / "sentinel_settings.json"
    if not settings_file.exists():
        return
    try:
        s = json.loads(settings_file.read_text(encoding="utf-8"))
        TELEGRAM_BOT_TOKEN = s.get("telegram_bot_token", "") or ""
        try:
            TELEGRAM_CHAT_ID = int(s.get("telegram_chat_id", 0) or 0)
        except (ValueError, TypeError):
            TELEGRAM_CHAT_ID = 0
        if "llm_providers" in s:
            LLM_PROVIDERS = s["llm_providers"]
        CHAT_MODEL_PREF = s.get("chat_model_pref", CHAT_MODEL_PREF)
        ANALYSIS_MODEL_PREF = s.get("analysis_model_pref", ANALYSIS_MODEL_PREF)
        # RELAY_SERVER_URL and GOOGLE_CLIENT_ID are loaded separately
        SYSTEM_CHECK_INTERVAL = s.get("check_interval", SYSTEM_CHECK_INTERVAL)
        IDLE_REPORT_INTERVAL = s.get("idle_report_interval", IDLE_REPORT_INTERVAL)
        DISTILL_INTERVAL = max(60, int(s.get("distill_interval", DISTILL_INTERVAL)))
        SCREEN_CAPTURE_MIN = max(30, int(s.get("screen_capture_min", SCREEN_CAPTURE_MIN)))
        SCREEN_CAPTURE_MAX = max(SCREEN_CAPTURE_MIN + 30,
                                 int(s.get("screen_capture_max", SCREEN_CAPTURE_MAX)))
        if "watch_dirs" in s:
            WATCH_DIRS = [Path(d) for d in s["watch_dirs"]]
        # Phase D5 voice toggle. Stored as a plain bool; bool() coerces
        # legacy values (truthy strings, ints) sensibly.
        VOICE_ENABLED = bool(s.get("voice_enabled", VOICE_ENABLED))
        _log.debug("Settings loaded from %s", settings_file)
    except Exception as e:
        _log.warning("Failed to load settings: %s", e)


_load_persisted_settings()
