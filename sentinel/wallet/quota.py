"""Quota manager — bridges desktop app to relay server for paid users.

Desktop app never talks to 5888 directly. Flow:
  Desktop → Relay Server → 5888 Wallet (spend)
  Desktop → Relay Server → LLM API (using server's key)
  Desktop ← Relay Server ← LLM response

This module handles the desktop side of that flow.
"""
import json
import time
import logging
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.wallet.quota")

# Persisted auth state
AUTH_FILE = Path.home() / ".hermes" / "aislime_auth.json"

# ── Cost Table ───────────────────────────────────────────────────────
# Points per LLM call type. Server enforces actual costs; client shows estimates.
# These are fixed prices — no equipment discounts (honest pricing).
COST_TABLE = {
    "chat": 5,           # 聊天一次 5 點
    "analysis": 2,       # 背景分析 2 點
    "distill": 3,        # 蒸餾學習 3 點
    "vision": 8,         # 截圖分析 8 點（多模態較貴）
    "evolution": 10,     # 自我進化 10 點
}

# ── Quota Packs ──────────────────────────────────────────────────────
# Displayed in UI. Actual purchase goes through 5888 wallet.
QUOTA_PACKS = [
    {"id": "starter",  "name": "入門包",   "points": 500,    "price_twd": 30,  "desc": "約 100 次聊天"},
    {"id": "standard", "name": "標準包",   "points": 2000,   "price_twd": 99,  "desc": "約 400 次聊天"},
    {"id": "pro",      "name": "進階包",   "points": 5000,   "price_twd": 199, "desc": "約 1000 次聊天"},
    {"id": "unlimited","name": "月費無限", "points": 999999, "price_twd": 499, "desc": "30天無限使用"},
]


class QuotaManager:
    """Manages user authentication and quota for paid mode."""

    def __init__(self, relay_url: str = ""):
        self.relay_url = relay_url.rstrip("/") if relay_url else ""
        self._auth = self._load_auth()
        self._balance_cache: Optional[int] = None
        self._balance_ts: float = 0

    # ── Auth State ───────────────────────────────────────────────────

    @property
    def is_logged_in(self) -> bool:
        return bool(self._auth.get("uid"))

    @property
    def uid(self) -> str:
        return self._auth.get("uid", "")

    @property
    def display_name(self) -> str:
        return self._auth.get("display_name", "")

    @property
    def email(self) -> str:
        return self._auth.get("email", "")

    @property
    def referral_code(self) -> str:
        return self._auth.get("referral_code", "")

    @property
    def mode(self) -> str:
        """Current mode: 'byok' or 'quota'."""
        return self._auth.get("mode", "byok")

    @mode.setter
    def mode(self, value: str):
        self._auth["mode"] = value
        self._save_auth()

    def _load_auth(self) -> dict:
        if AUTH_FILE.exists():
            try:
                return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"mode": "byok"}

    def _save_auth(self):
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTH_FILE.write_text(
            json.dumps(self._auth, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── Login / Logout ───────────────────────────────────────────────

    def login(self, google_token: str, referral_code: str = "") -> dict:
        """Login via relay server (which calls s2sEnsureUser).

        Returns {uid, balance, referralCode, displayName}.
        """
        result = self._relay_post("auth/login", {
            "google_token": google_token,
            "referral_code": referral_code,
        })
        self._auth.update({
            "uid": result["uid"],
            "email": result.get("email", ""),
            "display_name": result.get("displayName", ""),
            "referral_code": result.get("referralCode", ""),
            "mode": "quota",
        })
        self._balance_cache = result.get("balance", 0)
        self._balance_ts = time.time()
        self._save_auth()
        return result

    def logout(self):
        """Clear local auth. No server call needed per 5888 spec."""
        self._auth = {"mode": "byok"}
        self._balance_cache = None
        self._save_auth()

    # ── Balance ──────────────────────────────────────────────────────

    def get_balance(self, force: bool = False) -> int:
        """Get current point balance. Cached for 30 seconds."""
        if not self.is_logged_in:
            return 0

        now = time.time()
        if not force and self._balance_cache is not None and now - self._balance_ts < 30:
            return self._balance_cache

        try:
            result = self._relay_post("wallet/balance", {"uid": self.uid})
            self._balance_cache = result.get("balance", 0)
            self._balance_ts = now
            return self._balance_cache
        except Exception as e:
            log.warning(f"Balance check failed: {e}")
            return self._balance_cache or 0

    def can_afford(self, task_type: str) -> bool:
        """Check if user has enough points for a task."""
        if self.mode == "byok":
            return True  # BYOK doesn't use points
        cost = COST_TABLE.get(task_type, 5)
        return self.get_balance() >= cost

    # ── LLM Proxy ────────────────────────────────────────────────────

    def relay_llm_call(self, prompt: str, task_type: str = "chat",
                       system: str = "", temperature: float = 0.5,
                       max_tokens: int = 800) -> Optional[str]:
        """Call LLM through relay server (auto-deducts points).

        The relay server:
        1. Checks balance via s2sGetBalance
        2. Calls LLM with server-side API key
        3. Deducts points via s2sSpend
        4. Returns LLM response
        """
        if not self.is_logged_in:
            return None

        try:
            result = self._relay_post("llm/call", {
                "uid": self.uid,
                "prompt": prompt,
                "system": system,
                "task_type": task_type,
                "temperature": temperature,
                "max_tokens": max_tokens,
            })
            # Update cached balance from response
            if "balance_after" in result:
                self._balance_cache = result["balance_after"]
                self._balance_ts = time.time()
            return result.get("response")
        except QuotaError:
            raise
        except Exception as e:
            log.error(f"Relay LLM call failed: {e}")
            return None

    # ── Purchase URL ─────────────────────────────────────────────────

    def get_topup_url(self, pack_id: str = "") -> str:
        """Get URL where user can buy points.

        Opens in browser → 5888 wallet → ECPay → points credited.
        """
        base = self._auth.get("wallet_url", "https://wallet-5888.web.app")
        if pack_id:
            return f"{base}/topup?pack={pack_id}&ref={self.referral_code}"
        return f"{base}/topup?ref={self.referral_code}"

    def get_wallet_url(self) -> str:
        """Get URL to user's wallet dashboard."""
        return self._auth.get("wallet_url", "https://wallet-5888.web.app")

    # ── Internal ─────────────────────────────────────────────────────

    def _relay_post(self, endpoint: str, body: dict) -> dict:
        """POST to relay server."""
        if not self.relay_url:
            raise QuotaError("RELAY_NOT_CONFIGURED",
                             "中繼伺服器未設定。請在設定中填寫 Relay URL 或切換為 BYOK 模式。")

        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.relay_url}/{endpoint}",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            try:
                err = json.loads(error_body)
            except json.JSONDecodeError:
                err = {"error": "UNKNOWN", "message": error_body[:200]}

            code = err.get("error", "UNKNOWN")
            msg = err.get("message", "")

            if code == "INSUFFICIENT_BALANCE":
                raise QuotaError(code, "點數不足，請先儲值。") from e
            raise QuotaError(code, msg) from e


class QuotaError(Exception):
    """Raised when quota/wallet operations fail."""

    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")

    @property
    def is_insufficient(self) -> bool:
        return self.error_code == "INSUFFICIENT_BALANCE"
