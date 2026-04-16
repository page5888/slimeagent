"""Google OAuth for desktop app — loopback redirect flow.

Opens system browser → Google login → redirects to localhost → captures auth code
→ exchanges for id_token → sends to relay server → saves JWT locally.

No client_secret needed: uses PKCE (Proof Key for Code Exchange).
"""
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
from pathlib import Path

log = logging.getLogger("sentinel.google_auth")

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Loopback redirect
_REDIRECT_HOST = "127.0.0.1"
_REDIRECT_PORT_RANGE = range(18510, 18520)  # try a few ports

# Auth file
AUTH_FILE = Path.home() / ".hermes" / "aislime_auth.json"


class OAuthResult:
    """Holds the result of the OAuth flow."""
    def __init__(self):
        self.auth_code: str | None = None
        self.error: str | None = None
        self.state: str | None = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handles the OAuth redirect callback on localhost."""

    result: OAuthResult  # set by caller
    expected_state: str  # set by caller

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            self.result.error = params["error"][0]
        elif "code" in params:
            # Verify state parameter
            returned_state = params.get("state", [""])[0]
            if returned_state != self.expected_state:
                self.result.error = "state_mismatch"
            else:
                self.result.auth_code = params["code"][0]
                self.result.state = returned_state
        else:
            self.result.error = "no_code_in_response"

        # Show success/failure page
        if self.result.error:
            body = "<h2>登入失敗</h2><p>請關閉此頁面回到 App。</p>"
        else:
            body = (
                "<h2>登入成功！</h2>"
                "<p>已取得授權，可以關閉此頁面回到 AI Slime Agent。</p>"
                "<script>setTimeout(()=>window.close(),2000)</script>"
            )

        html = (
            f"<html><head><meta charset='utf-8'>"
            f"<style>body{{font-family:sans-serif;text-align:center;padding:60px;}}</style>"
            f"</head><body>{body}</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # suppress HTTP server logs


def _find_free_port() -> int:
    """Find a free port in our range."""
    import socket
    for port in _REDIRECT_PORT_RANGE:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((_REDIRECT_HOST, port))
                return port
        except OSError:
            continue
    raise RuntimeError("No free port available for OAuth callback")


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode("ascii")).digest()
    # base64url encode without padding
    import base64
    challenge_b64 = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode("ascii")
    return verifier, challenge_b64


def start_oauth_flow(client_id: str) -> tuple[str, int, str, str, OAuthResult]:
    """Start OAuth flow: create server, generate URLs.

    Returns: (auth_url, port, code_verifier, state, result_holder)
    """
    port = _find_free_port()
    redirect_uri = f"http://{_REDIRECT_HOST}:{port}"
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _generate_pkce()

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    result = OAuthResult()

    return auth_url, port, code_verifier, state, result


def wait_for_callback(port: int, state: str, result: OAuthResult,
                      timeout: float = 120) -> OAuthResult:
    """Start local HTTP server and wait for Google's redirect.

    Blocks until callback received or timeout.
    """
    _CallbackHandler.result = result
    _CallbackHandler.expected_state = state

    server = http.server.HTTPServer((_REDIRECT_HOST, port), _CallbackHandler)
    server.timeout = timeout

    # Handle one request (the callback)
    server.handle_request()
    server.server_close()

    return result


def exchange_code_for_tokens(auth_code: str, client_id: str,
                             code_verifier: str, port: int,
                             client_secret: str = "") -> dict:
    """Exchange authorization code for tokens (id_token, access_token)."""
    redirect_uri = f"http://{_REDIRECT_HOST}:{port}"

    params = {
        "code": auth_code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    if client_secret:
        params["client_secret"] = client_secret
    data = urllib.parse.urlencode(params).encode("utf-8")

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google token exchange failed (HTTP {e.code}): {body}")


def _warmup_relay(relay_url: str, timeout: float = 60) -> None:
    """Ping relay / to wake Render free-tier dyno from sleep.

    Render spins down idle services; cold start can take 30–60s.
    We hit the cheap / endpoint so the expensive /auth/login call
    arrives on a warm server.
    """
    try:
        req = urllib.request.Request(
            f"{relay_url.rstrip('/')}/",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except Exception as e:
        log.debug("Relay warmup ping failed (non-fatal): %s", e)


def send_token_to_relay(id_token: str, relay_url: str) -> dict:
    """Send Google id_token to relay server's /auth/login endpoint.

    Returns: {token, uid, email, display_name, referral_code, balance}
    """
    data = json.dumps({"google_token": id_token}).encode("utf-8")

    req = urllib.request.Request(
        f"{relay_url.rstrip('/')}/auth/login",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    # Render free-tier cold start can take 30-60s; 15s timeout was too short.
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"中繼伺服器拒絕登入 (HTTP {e.code}): {body}")
    except Exception as e:
        raise RuntimeError(f"中繼伺服器連線失敗：{type(e).__name__}: {e}")


def save_auth(auth_data: dict):
    """Save auth data to disk."""
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(
        json.dumps(auth_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_auth():
    """Remove saved auth."""
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()


def full_login_flow(client_id: str, relay_url: str,
                    on_status=None, client_secret: str = "") -> dict:
    """Run complete Google OAuth → Relay login flow (blocking).

    on_status: optional callback(str) for progress updates.
    Returns auth_data dict on success.
    Raises Exception on failure.
    """
    if on_status:
        on_status("正在準備登入...")

    auth_url, port, code_verifier, state, result = start_oauth_flow(client_id)

    # Kick off relay warmup in parallel — Render free-tier cold start
    # takes 30–60s. By firing this while the user is still on Google's
    # consent screen, /auth/login lands on a warm server.
    threading.Thread(
        target=_warmup_relay,
        args=(relay_url,),
        daemon=True,
    ).start()

    if on_status:
        on_status("已開啟瀏覽器，請完成 Google 登入...")

    # Open browser
    webbrowser.open(auth_url)

    # Wait for callback (blocks up to 120s)
    wait_for_callback(port, state, result, timeout=120)

    if result.error:
        raise RuntimeError(f"Google 登入失敗：{result.error}")
    if not result.auth_code:
        raise RuntimeError("未收到授權碼（逾時或取消）")

    if on_status:
        on_status("正在驗證授權...")

    # Exchange code for tokens
    tokens = exchange_code_for_tokens(
        result.auth_code, client_id, code_verifier, port,
        client_secret=client_secret,
    )

    id_token_str = tokens.get("id_token")
    if not id_token_str:
        raise RuntimeError("Google 未回傳 id_token")

    if on_status:
        on_status("正在連線到市場伺服器...")

    # Send to relay
    relay_result = send_token_to_relay(id_token_str, relay_url)

    # Save locally
    auth_data = {
        "token": relay_result["token"],
        "uid": relay_result["uid"],
        "email": relay_result["email"],
        "display_name": relay_result["display_name"],
        "referral_code": relay_result.get("referral_code", ""),
        "balance": relay_result.get("balance", 0),
    }
    save_auth(auth_data)

    if on_status:
        on_status("登入成功！")

    return auth_data
