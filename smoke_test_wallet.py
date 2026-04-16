"""Day 1 smoke test for 5888 wallet staging integration.

Covers the three reasons 5888 whitelisted for slime on staging:
  1. s2sEnsureUser      — create/lookup user, seed test balance
  2. s2sSpend            reason=slime_evolve   (2 pt, idempotency check)
  3. s2sSpend            reason=slime_list_fee (10 pt, tiered)

Plus s2sGetBalance between spends so failures point at the right step.

Note: the old generic `smoke_test` reason is no longer whitelisted —
any spend with that reason will now come back 403 SITE_NOT_AUTHORIZED.

Credentials passed via env vars:
    WALLET_API_BASE   — https://asia-east1-wallet-5888-staging.cloudfunctions.net
    WALLET_SITE_ID    — the slime-staging site id
    WALLET_API_KEY    — the slime-staging api key
    WALLET_HMAC_SECRET

Optional:
    SMOKE_TEST_GOOGLE_SUB — reuse an account across runs
"""
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

API_BASE = os.environ["WALLET_API_BASE"]
SITE_ID = os.environ["WALLET_SITE_ID"]
API_KEY = os.environ["WALLET_API_KEY"]
HMAC_SECRET = os.environ["WALLET_HMAC_SECRET"]

client = WalletClient(API_BASE, SITE_ID, API_KEY, HMAC_SECRET)

TEST_GOOGLE_SUB = os.environ.get(
    "SMOKE_TEST_GOOGLE_SUB",
    f"slime_smoketest_{uuid.uuid4().hex[:12]}",
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
print("5888 Wallet Staging — Smoke Test (Day 1)")
print("=" * 60)
print(f"API base        : {API_BASE}")
print(f"Site ID         : {SITE_ID}")
print(f"googleSub       : {TEST_GOOGLE_SUB}")
print(f"evolve key      : {EVOLVE_KEY}")
print(f"list-fee key    : {LIST_FEE_KEY}")
print(f"list-fee amount : {LIST_FEE_AMOUNT} (price tier {LIST_FEE_PRICE_TIER})")
print()


def _fail(step: str, err: "WalletError|Exception") -> None:
    print(f"  ✗ {step} FAILED: {err}")
    if isinstance(err, WalletError):
        print(f"    http_code  = {err.http_code}")
        print(f"    error_code = {err.error_code}")
    sys.exit(1)


# ── Step 1: s2sEnsureUser ─────────────────────────────────────────
print("[1/5] s2sEnsureUser ...")
try:
    result = client.ensure_user(
        google_sub=TEST_GOOGLE_SUB,
        email="smoketest@slime.local",
        display_name="Slime Smoke Test",
    )
    uid = result.get("uid", "")
    balance = result.get("balance", 0)
    is_new = result.get("isNewUser", False)
    print(f"  ✓ uid          = {uid}")
    print(f"  ✓ balance      = {balance}")
    print(f"  ✓ isNewUser    = {is_new}")
except WalletError as e:
    _fail("ensureUser", e)

print()

# ── Step 2: s2sGetBalance (before spends) ─────────────────────────
print("[2/5] s2sGetBalance (pre-spend) ...")
try:
    result = client.get_balance(uid)
    balance_before = result.get("balance", 0)
    print(f"  ✓ balance = {balance_before}")
except WalletError as e:
    _fail("getBalance", e)

# If balance is below what the rest of the test needs, stop here rather
# than failing on an insufficient-balance branch and making it look like
# a wiring bug.
needed = EVOLVE_COST + LIST_FEE_AMOUNT
if balance_before < needed:
    print()
    print(f"  ⚠ balance {balance_before} < {needed} (evolve {EVOLVE_COST} + "
          f"list_fee {LIST_FEE_AMOUNT}).")
    print(f"  ⚠ Top up via s2sResetTestBalance or s2sGrant, then re-run.")
    print()
    print("=" * 60)
    print("PARTIAL PASS — ensureUser + getBalance OK, spends need balance")
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
    print(f"  ✓ balanceAfter = {balance_after_evolve}")
    print(f"  ✓ duplicate    = {duplicate}  (should be False on first call)")
    if duplicate:
        print("  ✗ EXPECTED duplicate=False on fresh idempotency key")
        sys.exit(1)
except WalletError as e:
    _fail("spend(slime_evolve)", e)

# Replay same key → expect duplicate=true, balance unchanged.
print(f"      replay same key → expect duplicate=true ...")
try:
    result = client.spend(
        uid=uid, amount=EVOLVE_COST,
        reason=SPEND_TYPE_EVOLVE, idempotency_key=EVOLVE_KEY,
    )
    dup2 = result.get("duplicate", False)
    bal2 = result.get("balanceAfter", 0)
    print(f"  ✓ duplicate    = {dup2}")
    print(f"  ✓ balanceAfter = {bal2}  (unchanged)")
    if not dup2:
        print("  ✗ EXPECTED duplicate=true on replay — idempotency BROKEN")
        sys.exit(1)
    if bal2 != balance_after_evolve:
        print(f"  ✗ EXPECTED balance to stay {balance_after_evolve}, got {bal2}")
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
    print(f"  ✓ balanceAfter = {balance_after_list}")
    print(f"  ✓ duplicate    = {duplicate}")
    if duplicate:
        print("  ✗ EXPECTED duplicate=False")
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
    print(f"  ✓ balance = {final_balance}")
    print(f"    expected ≥ {expected} (balance_before − evolve − list_fee)")
    # Allow ≥ because commissions could flow back in (they shouldn't for
    # slime_evolve/list_fee spends on test accounts with no upline, but
    # we don't want to fail the smoke if 5888 adds bonuses later).
    if final_balance < expected:
        print(f"  ✗ balance short by {expected - final_balance}")
        sys.exit(1)
except WalletError as e:
    _fail("getBalance(final)", e)

print()
print("=" * 60)
print("FULL PASS — all 3 whitelisted reasons work, idempotency verified")
print("=" * 60)
print(f"Start balance: {balance_before}")
print(f"Final balance: {final_balance}")
print(f"Spent:         {balance_before - final_balance}")
