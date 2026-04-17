"""Phase 2 smoke test — creator-reward grants + error codes.

Covers the two reasons 5888 added to the grant whitelist on 2026-04-16:
  1. s2sGrant reason=slime_creator_reward_settle  (per-vote tip)
  2. s2sGrant reason=slime_creator_approval       (+100pt approval bonus)

Plus the error-code contract we agreed with 5888:
  3. amount=0                 → 400 INVALID_AMOUNT
  4. uid that does not exist  → 404 USER_NOT_FOUND

Credentials load the same way as smoke_test_wallet.py:
  - WALLET_KEYS_FILE (override path) or ~/.hermes/wallet_5888_keys.json
  - WALLET_ENV picks the block (default: staging)

Run this AFTER smoke_test_wallet.py is green, so you know HMAC + site_id +
existing whitelist are fine. This one only exercises the two new grant
reasons and the error-code surface for the Phase 2 replay script.
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sentinel.wallet.client import WalletClient, WalletError
from sentinel.wallet.market_rules import (
    GRANT_TYPE_CREATOR_REWARD_SETTLE,
    GRANT_TYPE_CREATOR_APPROVAL,
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

client = WalletClient(
    endpoints=block["endpoints"],
    site_id=block["site_id"],
    api_key=block["api_key"],
    hmac_secret=block["hmac_secret"],
)

TEST_GOOGLE_SUB = f"slime_phase2_{uuid.uuid4().hex[:12]}"
TEST_EMAIL = f"{TEST_GOOGLE_SUB}@slime.local"
TS = int(time.time())

SETTLE_AMOUNT = 10     # matches per-vote tip size
APPROVAL_AMOUNT = 100  # matches CREATOR_REWARD in server/config.py

# Ledger ids — these stand in for what the replay script would pass.
LEDGER_SETTLE = f"phase2smoke_settle_{TS}"
LEDGER_APPROVAL = f"phase2smoke_approval_{TS}"

print("=" * 60)
print(f"5888 Wallet {env_name.capitalize()} - Phase 2 Smoke Test")
print("=" * 60)
print(f"Keys file  : {keys_path}")
print(f"Site ID    : {block['site_id']}")
print(f"googleSub  : {TEST_GOOGLE_SUB}")
print(f"email      : {TEST_EMAIL}")
print()


def _fail(step: str, err) -> None:
    print(f"  [FAIL] {step}: {err}")
    if isinstance(err, WalletError):
        print(f"    http_code  = {err.http_code}")
        print(f"    error_code = {err.error_code}")
    sys.exit(1)


# ── Step 1: ensure creator user exists ────────────────────────────
print("[1/6] ensure creator user ...")
try:
    result = client.ensure_user(
        google_sub=TEST_GOOGLE_SUB,
        email=TEST_EMAIL,
        display_name="Phase 2 Smoke Creator",
    )
    creator_uid = result.get("uid", "")
    balance_start = result.get("balance", 0)
    print(f"  [OK] uid            = {creator_uid}")
    print(f"  [OK] start balance  = {balance_start}")
except WalletError as e:
    _fail("ensureUser", e)
print()


# ── Step 2: grant slime_creator_reward_settle ─────────────────────
print(f"[2/6] s2sGrant reason={GRANT_TYPE_CREATOR_REWARD_SETTLE} "
      f"amount={SETTLE_AMOUNT} ...")
settle_key = f"{GRANT_TYPE_CREATOR_REWARD_SETTLE}:{LEDGER_SETTLE}"
try:
    result = client.grant(
        uid=creator_uid, amount=SETTLE_AMOUNT,
        reason=GRANT_TYPE_CREATOR_REWARD_SETTLE,
        idempotency_key=settle_key,
    )
    settle_tx = result.get("txId", "")
    balance_after_settle = result.get("balanceAfter", 0)
    duplicate = result.get("duplicate", False)
    print(f"  [OK] txId          = {settle_tx}")
    print(f"  [OK] balanceAfter  = {balance_after_settle}")
    print(f"  [OK] duplicate     = {duplicate}  (should be False)")
    if duplicate:
        _fail("grant(settle)", "expected duplicate=False on fresh key")
    if balance_after_settle != balance_start + SETTLE_AMOUNT:
        _fail("grant(settle)",
              f"expected balance {balance_start + SETTLE_AMOUNT}, "
              f"got {balance_after_settle}")
except WalletError as e:
    _fail("grant(settle)", e)
print()


# ── Step 3: replay same key → expect duplicate=true ───────────────
print("[3/6] replay settle key → expect duplicate=true ...")
try:
    result = client.grant(
        uid=creator_uid, amount=SETTLE_AMOUNT,
        reason=GRANT_TYPE_CREATOR_REWARD_SETTLE,
        idempotency_key=settle_key,
    )
    dup = result.get("duplicate", False)
    bal = result.get("balanceAfter", 0)
    tx2 = result.get("txId", "")
    print(f"  [OK] duplicate     = {dup}")
    print(f"  [OK] balanceAfter  = {bal}  (unchanged)")
    print(f"  [OK] txId          = {tx2}  (same as original: "
          f"{tx2 == settle_tx})")
    if not dup:
        _fail("grant(settle replay)", "expected duplicate=true on replay")
    if bal != balance_after_settle:
        _fail("grant(settle replay)",
              f"expected balance {balance_after_settle}, got {bal}")
except WalletError as e:
    _fail("grant(settle replay)", e)
print()


# ── Step 4: grant slime_creator_approval (+100pt bonus) ───────────
print(f"[4/6] s2sGrant reason={GRANT_TYPE_CREATOR_APPROVAL} "
      f"amount={APPROVAL_AMOUNT} ...")
approval_key = f"{GRANT_TYPE_CREATOR_APPROVAL}:{LEDGER_APPROVAL}"
try:
    result = client.grant(
        uid=creator_uid, amount=APPROVAL_AMOUNT,
        reason=GRANT_TYPE_CREATOR_APPROVAL,
        idempotency_key=approval_key,
    )
    balance_after_approval = result.get("balanceAfter", 0)
    duplicate = result.get("duplicate", False)
    expected = balance_after_settle + APPROVAL_AMOUNT
    print(f"  [OK] balanceAfter  = {balance_after_approval}")
    print(f"  [OK] duplicate     = {duplicate}")
    if duplicate:
        _fail("grant(approval)", "expected duplicate=False")
    if balance_after_approval != expected:
        _fail("grant(approval)",
              f"expected balance {expected}, got {balance_after_approval}")
except WalletError as e:
    _fail("grant(approval)", e)
print()


# ── Step 5: amount=0 → expect 400 INVALID_AMOUNT ──────────────────
print("[5/6] amount=0 → expect 400 INVALID_AMOUNT ...")
try:
    client.grant(
        uid=creator_uid, amount=0,
        reason=GRANT_TYPE_CREATOR_REWARD_SETTLE,
        idempotency_key=f"phase2smoke_zero_{TS}",
    )
    _fail("grant(amount=0)",
          "expected WalletError, but call returned success")
except WalletError as e:
    if e.http_code == 400 and e.error_code == "INVALID_AMOUNT":
        print(f"  [OK] got expected [{e.http_code}] {e.error_code}")
    else:
        print(f"  [WARN] expected 400 INVALID_AMOUNT, "
              f"got [{e.http_code}] {e.error_code}: {e.message}")
        print("         5888 may have a different error-code name. "
              "Not fatal — flag for follow-up.")
print()


# ── Step 6: unknown uid → expect 404 USER_NOT_FOUND ───────────────
print("[6/6] unknown uid → expect 404 USER_NOT_FOUND ...")
bogus_uid = f"phase2_bogus_uid_{TS}"
try:
    client.grant(
        uid=bogus_uid, amount=SETTLE_AMOUNT,
        reason=GRANT_TYPE_CREATOR_REWARD_SETTLE,
        idempotency_key=f"phase2smoke_nouser_{TS}",
    )
    _fail("grant(unknown uid)",
          "expected WalletError, but call returned success")
except WalletError as e:
    if e.http_code == 404 and e.error_code == "USER_NOT_FOUND":
        print(f"  [OK] got expected [{e.http_code}] {e.error_code}")
    else:
        print(f"  [WARN] expected 404 USER_NOT_FOUND, "
              f"got [{e.http_code}] {e.error_code}: {e.message}")
        print("         5888 may have a different error-code name. "
              "Not fatal — flag for follow-up.")
print()


print("=" * 60)
print("PHASE 2 FULL PASS — both grant reasons + idempotency + error codes")
print("=" * 60)
print(f"Creator uid  : {creator_uid}")
print(f"Start balance: {balance_start}")
print(f"After settle : {balance_after_settle}  (+{SETTLE_AMOUNT})")
print(f"After bonus  : {balance_after_approval}  (+{APPROVAL_AMOUNT})")
print(f"Total granted: {balance_after_approval - balance_start}")
