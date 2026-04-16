"""Relay server client for desktop app.

Handles JWT auth and all API calls to the relay server.
"""
import json
import logging
import urllib.request
import urllib.error
from pathlib import Path
from sentinel import config

log = logging.getLogger("sentinel.relay")

AUTH_FILE = Path.home() / ".hermes" / "aislime_auth.json"


def _load_auth() -> dict:
    if AUTH_FILE.exists():
        try:
            return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _get_token() -> str:
    return _load_auth().get("token", "")


def _relay_url() -> str:
    return (config.RELAY_SERVER_URL or "").rstrip("/")


class RelayError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _request(method: str, endpoint: str, body: dict | None = None,
             auth: bool = True) -> dict:
    """Make HTTP request to relay server."""
    url = _relay_url()
    if not url:
        raise RelayError("NOT_CONFIGURED", "Relay server URL not set")

    headers = {"Content-Type": "application/json"}
    if auth:
        token = _get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{url}/{endpoint}", data=data, headers=headers, method=method,
    )

    # Render free-tier cold start can take 30–60s on the first call
    # after idle. Use a generous timeout so refresh/list don't time out.
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(error_body)
        except json.JSONDecodeError:
            err = {"detail": error_body[:200]}
        raise RelayError(
            str(e.code), err.get("detail", err.get("message", str(err)))
        ) from e
    except Exception as e:
        raise RelayError("NETWORK", str(e)) from e


# ── Equipment Submissions ────────────────────────────────────────────

def get_submissions(status: str = "pending", slot: str = "",
                    page: int = 1) -> dict:
    params = f"?status={status}&page={page}"
    if slot:
        params += f"&slot={slot}"
    return _request("GET", f"equipment/submissions{params}", auth=True)


def get_submission(submission_id: str) -> dict:
    return _request("GET", f"equipment/submissions/{submission_id}", auth=False)


def vote(submission_id: str) -> dict:
    return _request("POST", f"equipment/submissions/{submission_id}/vote")


def get_pool_version() -> dict:
    return _request("GET", "equipment/pool/version", auth=False)


def get_pool(since_version: int = 0) -> dict:
    return _request("GET", f"equipment/pool?since_version={since_version}",
                    auth=False)


# ── Marketplace ──────────────────────────────────────────────────────

def get_listings(slot: str = "", rarity: str = "", page: int = 1) -> dict:
    params = f"?page={page}"
    if slot:
        params += f"&slot={slot}"
    if rarity:
        params += f"&rarity={rarity}"
    return _request("GET", f"marketplace/listings{params}", auth=False)


def buy_listing(listing_id: str) -> dict:
    return _request("POST", "marketplace/buy", {"listing_id": listing_id})


def list_item(item_id: str, template_name: str, slot: str,
              rarity: str, price: int) -> dict:
    return _request("POST", "marketplace/list", {
        "item_id": item_id, "template_name": template_name,
        "slot": slot, "rarity": rarity, "price": price,
    })


def delist_item(listing_id: str) -> dict:
    return _request("POST", "marketplace/delist", {"listing_id": listing_id})


def get_trade_history(page: int = 1) -> dict:
    return _request("GET", f"marketplace/history?page={page}")


# ── Evolution ────────────────────────────────────────────────────────

def evolve(idempotency_key: str | None = None) -> dict:
    """Deduct 2 pts for a manual evolution trigger.

    On success returns {"ok": True, "cost": 2, "balance_after": int,
    "idempotency_key": str}. After this call returns success, the desktop
    should call sentinel.evolution.perform_evolution() locally.

    Raises RelayError with code "402" when balance is insufficient.
    """
    body: dict = {}
    if idempotency_key:
        body["idempotency_key"] = idempotency_key
    return _request("POST", "evolution/evolve", body)


# ── Federation (公頻) ────────────────────────────────────────────────

def list_patterns(limit: int = 20, category: str | None = None) -> dict:
    """Fetch recent community patterns for the 公頻 tab.

    Returns {"items": [...], "count": int}. Each item includes a
    `user_voted` field — null if the caller hasn't voted, else one of
    'confirm' / 'refute' / 'unclear'. The GUI uses this to disable vote
    buttons on patterns the user has already scored.
    """
    params = f"?limit={limit}"
    if category:
        params += f"&category={category}"
    return _request("GET", f"federation/patterns{params}", auth=True)


def vote_pattern(pattern_id: str, vote: str) -> dict:
    """Cast a vote on a pattern. `vote` is 'confirm', 'refute', or 'unclear'.

    Raises RelayError(400) if the user already voted or the pattern is
    no longer accepting votes, RelayError(404) if the pattern was
    deleted between list and vote.
    """
    return _request("POST", f"federation/patterns/{pattern_id}/vote",
                    {"vote": vote}, auth=True)
