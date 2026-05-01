"""Day 1 smoke test for 5888 wallet staging integration.

Covers the three reasons 5888 whitelisted for slime on staging:
  1. s2sEnsureUser      — create/lookup user, seed test balance
  2. s2sSpend            reason=slime_evolve   (2 pt, idempotency check)
  3. s2sSpend            reason=slime_list_fee (10 pt, tiered)

Plus s2sGetBalance between spends so failures point at the right step.

Note: the old generic `smoke_test` reason is no longer whitelisted —
any spend with that reason will now come back 403 SITE_NOT_AUTHORIZED.

Credentials load order:
  1. env var WALLET_KEYS_FILE (path to keys json) — useful for CI
  2. ~/.hermes/wallet_5888_keys.json (default for local dev)

WALLET_ENV selects the block inside the json; defaults to "staging".

Optional:
    SMOKE_TEST_GOOGLE_SUB — reuse an account across runs
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Make sentinel importable
sys.path.insert(0, str(Path(__file__).parent))

from sentinel.wallet.client import WalletClient, WalletError
from sentinel.wallet.market_rules import (
    SPEND_TYPE_EVOLVE, SPEND_TYPE_LIST_FEE, EVOLVE_COST, listing_fee,
)

# ── Load credentials ─────────────────────────────────────────────
keys_path = Path(
    os.environ.get("WALLET_KEYS_FILE")
    or Path.home() / ".hermes" / "wallet_5888_keys.json"
)
if not keys_path.exists():
    print(f"[FAIL] credentials file not found: {keys_path}")
    sys.exit(2)

env_name = os.environ.get("WALLET_ENV", "staging")
data = json.loads(keys_path.read_text(encoding="utf-8"))
block = data.get(env_name)
if not block:
    print(f"[FAIL] block '{env_name}' missing from {keys_path}")
    sys.exit(2)

required_keys = ("site_id", "api_key", "hmac_secret", "endpoints")
missing = [k for k in required_keys if not block.get(k)]
if missing:
    print(f"[FAIL] block '{env_name}' is missing fields: {missing}")
    sys.exit(2)

SITE_ID = block["site_id"]
API_KEY = block["api_key"]
HMAC_SECRET = block["hmac_secret"]
ENDPOINTS = block["endpoints"]

client = WalletClient(
    endpoints=ENDPOINTS,
    site_id=SITE_ID,
    api_key=API_KEY,
    hmac_secret=HMAC_SECRET,
)

TEST_GOOGLE_SUB = os.environ.get(
    "SMOKE_TEST_GOOGLE_SUB",
    f"slime_smoketest_{uuid.uuid4().hex[:12]}",
)
# Firebase Auth rejects re-linking the same email to a new googleSub
# with PROVIDER_ALREADY_LINKED, so keep the email unique per googleSub.
# If SMOKE_TEST_EMAIL is set (e.g. reusing an account across runs), use it.
TEST_EMAIL = os.environ.get(
    "SMOKE_TEST_EMAIL",
    f"{TEST_GOOGLE_SUB}@slime.local",
)

# Unique idempotency suffix per run. Each spend gets its own key so
# 5888 doesn't collapse repeat runs into duplicate=true.
TS = int(time.time())
EVOLVE_KEY = f"slime_evolve:smoketest_{TS}"
LIST_FEE_KEY = f"slime_list_fee:smoketest_{TS}"

# 100 pt price → tier-2 listing fee (10 pt) per market_rules.listing_fee.
LIST_FEE_PRICE_TIER = 100
LIST_FEE_AMOUNT = listing_fee(LIST_FEE_PRICE_TIER)

print("=" * 60)
print(f"5888 Wallet {env_name.capitalize()} - Smoke Test (Day 1)")
print("=" * 60)
print(f"Keys file       : {keys_path}")
print(f"Env block       : {env_name}")
print(f"Site ID         : {SITE_ID}")
print(f"ensureUser URL  : {ENDPOINTS.get('s2sEnsureUser', '?')}")
print(f"googleSub       : {TEST_GOOGLE_SUB}")
print(f"email           : {TEST_EMAIL}")
print(f"evolve key      : {EVOLVE_KEY}")
print(f"list-fee key    : {LIST_FEE_KEY}")
print(f"list-fee amount : {LIST_FEE_AMOUNT} (price tier {LIST_FEE_PRICE_TIER})")
print()


def _fail(step: str, err: "WalletError|Exception") -> None:
    print(f"  [FAIL]{step} FAILED: {err}")
    if isinstance(err, WalletError):
        print(f"    http_code  = {err.http_code}")
        print(f"    error_code = {err.error_code}")
    sys.exit(1)


# ── Step 1: s2sEnsureUser ─────────────────────────────────────────
print("[1/5] s2sEnsureUser ...")
try:
    result = client.ensure_user(
        google_sub=TEST_GOOGLE_SUB,
        email=TEST_EMAIL,
        display_name="Slime Smoke Test",
    )
    uid = result.get("uid", "")
    balance = result.get("balance", 0)
    is_new = result.get("isNewUser", False)
    print(f"  [OK]uid          = {uid}")
    print(f"  [OK]balance      = {balance}")
    print(f"  [OK]isNewUser    = {is_new}")
except WalletError as e:
    _fail("ensureUser", e)

print()

# ── Step 2: s2sGetBalance (before spends) ─────────────────────────
print("[2/5] s2sGetBalance (pre-spend) ...")
try:
    result = client.get_balance(uid)
    balance_before = result.get("balance", 0)
    print(f"  [OK]balance = {balance_before}")
except WalletError as e:
    _fail("getBalance", e)

# If balance is below what the rest of the test needs, stop here rather
# than failing on an insufficient-balance branch and making it look like
# a wiring bug.
needed = EVOLVE_COST + LIST_FEE_AMOUNT
if balance_before < needed:
    print()
    print(f"  [WARN]balance {balance_before} < {needed} (evolve {EVOLVE_COST} + "
          f"list_fee {LIST_FEE_AMOUNT}).")
    print(f"  [WARN]Top up via s2sResetTestBalance or s2sGrant, then re-run.")
    print()
    print("=" * 60)
    print("PARTIAL PASS - ensureUser + getBalance OK, spends need balance")
    print("=" * 60)
    sys.exit(0)

print()

# ── Step 3: slime_evolve ──────────────────────────────────────────
print(f"[3/5] s2sSpend reason={SPEND_TYPE_EVOLVE} amount={EVOLVE_COST} ...")
try:
    result = client.spend(
        uid=uid, amount=EVOLVE_COST,
        reason=SPEND_TYPE_EVOLVE, idempotency_key=EVOLVE_KEY,
    )
    balance_after_evolve = result.get("balanceAfter", 0)
    duplicate = result.get("duplicate", False)
    print(f"  [OK]balanceAfter = {balance_after_evolve}")
    print(f"  [OK]duplicate    = {duplicate}  (should be False on first call)")
    if duplicate:
        print("  [FAIL]EXPECTED duplicate=False on fresh idempotency key")
        sys.exit(1)
except WalletError as e:
    _fail("spend(slime_evolve)", e)

# Replay same key -> expect duplicate=true, balance unchanged.
print(f"      replay same key -> expect duplicate=true ...")
try:
    result = client.spend(
        uid=uid, amount=EVOLVE_COST,
        reason=SPEND_TYPE_EVOLVE, idempotency_key=EVOLVE_KEY,
    )
    dup2 = result.get("duplicate", False)
    bal2 = result.get("balanceAfter", 0)
    print(f"  [OK]duplicate    = {dup2}")
    print(f"  [OK]balanceAfter = {bal2}  (unchanged)")
    if not dup2:
        print("  [FAIL]EXPECTED duplicate=true on replay - idempotency BROKEN")
        sys.exit(1)
    if bal2 != balance_after_evolve:
        print(f"  [FAIL]EXPECTED balance to stay {balance_after_evolve}, got {bal2}")
        sys.exit(1)
except WalletError as e:
    _fail("spend(slime_evolve replay)", e)

print()

# ── Step 4: slime_list_fee ────────────────────────────────────────
print(f"[4/5] s2sSpend reason={SPEND_TYPE_LIST_FEE} amount={LIST_FEE_AMOUNT} ...")
try:
    result = client.spend(
        uid=uid, amount=LIST_FEE_AMOUNT,
        reason=SPEND_TYPE_LIST_FEE, idempotency_key=LIST_FEE_KEY,
    )
    balance_after_list = result.get("balanceAfter", 0)
    duplicate = result.get("duplicate", False)
    print(f"  [OK]balanceAfter = {balance_after_list}")
    print(f"  [OK]duplicate    = {duplicate}")
    if duplicate:
        print("  [FAIL]EXPECTED duplicate=False")
        sys.exit(1)
except WalletError as e:
    _fail("spend(slime_list_fee)", e)

print()

# ── Step 5: final balance sanity check ────────────────────────────
print("[5/5] s2sGetBalance (post-spend) ...")
try:
    result = client.get_balance(uid)
    final_balance = result.get("balance", 0)
    expected = balance_before - EVOLVE_COST - LIST_FEE_AMOUNT
    print(f"  [OK]balance = {final_balance}")
    print(f"    expected >= {expected} (balance_before - evolve - list_fee)")
    # Allow >= because commissions could flow back in (they shouldn't for
    # slime_evolve/list_fee spends on test accounts with no upline, but
    # we don't want to fail the smoke if 5888 adds bonuses later).
    if final_balance < expected:
        print(f"  [FAIL]balance short by {expected - final_balance}")
        sys.exit(1)
except WalletError as e:
    _fail("getBalance(final)", e)

print()
print("=" * 60)
print("FULL PASS - all 3 whitelisted reasons work, idempotency verified")
print("=" * 60)
print(f"Start balance: {balance_before}")
print(f"Final balance: {final_balance}")
print(f"Spent:         {balance_before - final_balance}")
