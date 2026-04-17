"""5888 Wallet S2S client — server-side only.

This module is used by the RELAY SERVER (not the desktop app).
The desktop app talks to the relay server; the relay server talks to 5888.

HMAC_SECRET never leaves the server.

5888 deploys each S2S function as its own Cloud Run service, so there is
no shared base URL — we take a per-endpoint mapping at construction.
"""
import hashlib
import hmac
import json
import time
import logging
import urllib.request
from typing import Any

log = logging.getLogger("sentinel.wallet.client")


# Canonical endpoint names we currently call. Kept here so typos in
# callers fail loudly at construction instead of silently at request time.
REQUIRED_ENDPOINTS = (
    "s2sEnsureUser",
    "s2sGetBalance",
    "s2sSpend",
    "s2sGrant",
    "s2sRefund",
)

# Optional endpoints: only present in certain environments. s2sResetTestBalance
# exists on staging/test (5888 enforces a siteId guard that prod would reject
# anyway), so we don't make it required.
OPTIONAL_ENDPOINTS = (
    "s2sResetTestBalance",
)


class WalletClient:
    """S2S client for 5888 wallet API."""

    def __init__(self, endpoints: dict[str, str], site_id: str,
                 api_key: str, hmac_secret: str):
        """
        Args:
            endpoints: map of 5888 function name (e.g. "s2sEnsureUser")
                to its full HTTPS URL. Each 5888 function lives on its
                own Cloud Run service, so there is no shared base URL.
        """
        missing = [e for e in REQUIRED_ENDPOINTS if e not in endpoints]
        if missing:
            raise ValueError(
                f"WalletClient missing endpoint URL(s): {missing}. "
                f"Got: {sorted(endpoints.keys())}"
            )
        self.endpoints = {k: v.rstrip("/") for k, v in endpoints.items()}
        self.site_id = site_id
        self.api_key = api_key
        self.hmac_secret = hmac_secret

    # ── HMAC Signing ─────────────────────────────────────────────────

    def _sign(self, body: dict) -> tuple[str, dict[str, str]]:
        """Sign a request body. Returns (raw_body, headers)."""
        raw_body = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
        signed_payload = f"{timestamp}.{body_hash}"
        signature = hmac.new(
            self.hmac_secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key,
            "X-Site-Id": self.site_id,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }
        return raw_body, headers

    def _post(self, endpoint: str, body: dict) -> dict:
        """POST to wallet API with HMAC signature."""
        url = self.endpoints.get(endpoint)
        if not url:
            raise WalletError(
                500, "ENDPOINT_NOT_CONFIGURED",
                f"No URL configured for endpoint {endpoint}",
            )

        raw_body, headers = self._sign(body)

        req = urllib.request.Request(
            url,
            data=raw_body.encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            try:
                err = json.loads(error_body)
            except json.JSONDecodeError:
                err = {"error": "UNKNOWN", "message": error_body[:200]}
            log.warning(f"Wallet API {endpoint} HTTP {e.code}: {err}")
            raise WalletError(e.code, err.get("error", "UNKNOWN"),
                              err.get("message", "")) from e

    # ── API Methods ──────────────────────────────────────────────────

    def ensure_user(self, google_sub: str, email: str,
                    display_name: str = "", photo_url: str = "",
                    referral_code: str = "") -> dict:
        """Register or get user. Returns {uid, balance, referralCode, ...}."""
        body: dict[str, Any] = {
            "googleSub": google_sub,
            "email": email,
        }
        if display_name:
            body["displayName"] = display_name
        if photo_url:
            body["photoURL"] = photo_url
        if referral_code:
            body["referralCode"] = referral_code
        return self._post("s2sEnsureUser", body)

    def get_balance(self, uid: str) -> dict:
        """Get user balance. Returns {balance, ...}."""
        return self._post("s2sGetBalance", {"uid": uid})

    def spend(self, uid: str, amount: int, reason: str,
              idempotency_key: str) -> dict:
        """Spend points. Triggers L1/L2 referral commissions.

        Returns {balanceAfter, commissions, tier, isFirstPurchase, ...}.
        """
        return self._post("s2sSpend", {
            "uid": uid,
            "amount": amount,
            "reason": reason,
            "idempotencyKey": idempotency_key,
        })

    def grant(self, uid: str, amount: int, reason: str,
              idempotency_key: str) -> dict:
        """Grant bonus points (no commissions triggered)."""
        return self._post("s2sGrant", {
            "uid": uid,
            "amount": amount,
            "reason": reason,
            "idempotencyKey": idempotency_key,
        })

    def refund(self, uid: str, original_key: str, refund_key: str,
               reason: str, clawback: bool = False) -> dict:
        """Refund a previous spend."""
        return self._post("s2sRefund", {
            "uid": uid,
            "originalIdempotencyKey": original_key,
            "refundIdempotencyKey": refund_key,
            "reason": reason,
            "clawbackCommissions": clawback,
        })

    def get_reconciliation_url(self, date: str = "") -> str:
        """Get download URL for daily reconciliation file."""
        body = {"date": date} if date else {}
        result = self._post("s2sGetReconciliationUrl", body)
        return result.get("url", "")

    def reset_test_balance(self, uid: str, balance: int,
                           reason: str = "smoke_test_fixture") -> dict:
        """Reset a test account's balance. Staging/test only.

        5888 enforces a runtime double guard (siteId must contain "test" or
        "staging"), so calling this in prod will 403 even if the endpoint
        were somehow configured. Safe to expose.
        """
        return self._post("s2sResetTestBalance", {
            "uid": uid,
            "balance": balance,
            "reason": reason,
        })


class WalletError(Exception):
    """Wallet API error with structured fields."""

    def __init__(self, http_code: int, error_code: str, message: str):
        self.http_code = http_code
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{http_code}] {error_code}: {message}")

    @property
    def is_insufficient_balance(self) -> bool:
        return self.error_code == "INSUFFICIENT_BALANCE"

    @property
    def is_retryable(self) -> bool:
        return self.http_code >= 500
