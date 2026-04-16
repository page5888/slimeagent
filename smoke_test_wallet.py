"""Day 1 smoke test for 5888 wallet staging integration.

Runs the 3-step checklist from NEW_SITE_ONBOARDING.md §11:
1. s2sEnsureUser
2. s2sGetBalance
3. s2sSpend (x2 to verify idempotency)

Credentials passed via env vars:
    WALLET_API_BASE, WALLET_SITE_ID, WALLET_API_KEY, WALLET_HMAC_SECRET
"""
import os
import sys
import time
import uuid
from pathlib import Path

# Make sentinel importable
sys.path.insert(0, str(Path(__file__).parent))

from sentinel.wallet.client import WalletClient, WalletError

API_BASE = os.environ["WALLET_API_BASE"]
SITE_ID = os.environ["WALLET_SITE_ID"]
API_KEY = os.environ["WALLET_API_KEY"]
HMAC_SECRET = os.environ["WALLET_HMAC_SECRET"]

client = WalletClient(API_BASE, SITE_ID, API_KEY, HMAC_SECRET)

# googleSub: pass SMOKE_TEST_GOOGLE_SUB env to reuse an account,
# otherwise generate a fresh UUID each run for fully repeatable tests.
TEST_GOOGLE_SUB = os.environ.get(
    "SMOKE_TEST_GOOGLE_SUB",
    f"slime_smoketest_{uuid.uuid4().hex[:12]}",
)

# Idempotency key: time-stamped so each run actually exercises spend + dedup.
# A hardcoded key would return duplicate=true from the second run onward
# without actually verifying anything.
TS = int(time.time())
IDEMPOTENCY_KEY = f"5888_slime_staging_smoketest_{TS}"

print("=" * 60)
print("5888 Wallet Staging — Smoke Test")
print("=" * 60)
print(f"API base   : {API_BASE}")
print(f"Site ID    : {SITE_ID}")
print(f"googleSub  : {TEST_GOOGLE_SUB}")
print(f"idempoKey  : {IDEMPOTENCY_KEY}")
print()

# ── Step 1: s2sEnsureUser ─────────────────────────────────────────
print("[1/3] s2sEnsureUser ...")
try:
    result = client.ensure_user(
        google_sub=TEST_GOOGLE_SUB,
        email="smoketest@slime.local",
        display_name="Slime Smoke Test",
    )
    uid = result.get("uid", "")
    balance = result.get("balance", 0)
    referral = result.get("referralCode", "")
    is_new = result.get("isNewUser", False)
    print(f"  ✓ uid          = {uid}")
    print(f"  ✓ balance      = {balance}")
    print(f"  ✓ referralCode = {referral}")
    print(f"  ✓ isNewUser    = {is_new}")
except WalletError as e:
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

print()

# ── Step 2: s2sGetBalance ─────────────────────────────────────────
print("[2/3] s2sGetBalance ...")
try:
    result = client.get_balance(uid)
    balance_before = result.get("balance", 0)
    updated = result.get("updatedAt", "")
    print(f"  ✓ balance   = {balance_before}")
    print(f"  ✓ updatedAt = {updated}")
except WalletError as e:
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

print()

# ── Step 3a: s2sSpend (first call) ────────────────────────────────
print("[3a/3] s2sSpend (first call, amount=10) ...")
try:
    result = client.spend(
        uid=uid, amount=10, reason="smoke_test",
        idempotency_key=IDEMPOTENCY_KEY,
    )
    balance_after = result.get("balanceAfter", 0)
    commissions = result.get("commissions", [])
    tier = result.get("tier", "")
    is_first = result.get("isFirstPurchase", False)
    duplicate = result.get("duplicate", False)
    print(f"  ✓ balanceAfter    = {balance_after}")
    print(f"  ✓ tier            = {tier}")
    print(f"  ✓ isFirstPurchase = {is_first}")
    print(f"  ✓ commissions     = {commissions}")
    print(f"  ✓ duplicate       = {duplicate}")
except WalletError as e:
    if e.is_insufficient_balance:
        print(f"  ⚠ INSUFFICIENT_BALANCE (balance {balance_before} < 10)")
        print(f"  ⚠ Skip spend test — need to top up first")
        print()
        print("=" * 60)
        print("PARTIAL PASS — ensureUser + getBalance works, spend needs topup")
        print("=" * 60)
        sys.exit(0)
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

print()

# ── Step 3b: s2sSpend (idempotency check) ─────────────────────────
print("[3b/3] s2sSpend (same key — should be duplicate) ...")
try:
    result = client.spend(
        uid=uid, amount=10, reason="smoke_test",
        idempotency_key=IDEMPOTENCY_KEY,
    )
    duplicate = result.get("duplicate", False)
    balance_after2 = result.get("balanceAfter", 0)
    print(f"  ✓ duplicate    = {duplicate}")
    print(f"  ✓ balanceAfter = {balance_after2}")
    if not duplicate:
        print("  ✗ EXPECTED duplicate=true — idempotency BROKEN")
        sys.exit(1)
    if balance_after2 != balance_after:
        print(f"  ✗ EXPECTED balance to stay at {balance_after}, got {balance_after2}")
        sys.exit(1)
except WalletError as e:
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

print()
print("=" * 60)
print("FULL PASS — all 3 steps succeeded, idempotency verified")
print("=" * 60)
print(f"Final balance: {balance_after2}")
