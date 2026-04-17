"""Phase 2 one-shot: replay creator_reward_ledger → 5888 s2sGrant.

Background
----------
Phase 1 left creator payouts LOCAL-ONLY — voter payments went to the 5888
platform pool via s2sSpend(slime_creator_reward), and creator credits were
logged to creator_reward_ledger with status='pending'. No actual 5888
balance movement happened on the creator side.

Phase 2 (as of 2026-04-16, staging-ready): 5888 whitelisted two grant
reasons — `slime_creator_reward_settle` (per-vote tips) and
`slime_creator_approval` (the +100pt approval bonus). We replay pending
rows through s2sGrant using `<reason>:<ledger_id>` as the idempotency
key, so retries are safe to re-run.

What this script does
---------------------
1. SELECT pending rows from creator_reward_ledger (optionally limited)
2. For each row, look up creator's wallet_uid (skip if missing)
3. Route by voter_spend_key prefix:
     - "slime_creator_reward:..."    → reason=slime_creator_reward_settle
     - "slime_creator_approval:..."  → reason=slime_creator_approval
4. Call wallet.grant(creator_uid, amount, reason, "<reason>:<ledger_id>")
5. On success → UPDATE status='settled', settled_at, settle_tx_id
6. On failure → log, leave status='pending' (next run will retry)

Flags
-----
  --dry-run   : list what WOULD be called; no API calls, no DB writes
  --limit N   : only process first N rows (default: all)
  --env staging|production : pick keys block (default: staging)

Exit codes
----------
  0 : all pending drained successfully (or dry-run completed)
  1 : some rows failed — safe to re-run after investigating
  2 : configuration / precondition error (bad creds, missing table, ...)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Make sibling packages importable when invoked as `python scripts/…`
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from sentinel.wallet.client import WalletClient, WalletError
from sentinel.wallet.market_rules import (
    GRANT_TYPE_CREATOR_REWARD_SETTLE,
    GRANT_TYPE_CREATOR_APPROVAL,
)

log = logging.getLogger("phase2_replay")


def _route_reason(voter_spend_key: str) -> str:
    """Map a ledger row's voter_spend_key prefix to the 5888 grant reason."""
    if voter_spend_key.startswith("slime_creator_reward:"):
        return GRANT_TYPE_CREATOR_REWARD_SETTLE
    if voter_spend_key.startswith("slime_creator_approval:"):
        return GRANT_TYPE_CREATOR_APPROVAL
    raise ValueError(
        f"Unknown voter_spend_key prefix: {voter_spend_key!r} — "
        f"cannot route to a whitelisted grant reason"
    )


async def _fetch_pending(db, limit: int | None):
    """Pull pending ledger rows joined with creator.wallet_uid.

    LEFT JOIN so we still see rows where the creator never linked a wallet
    (can't settle them, but we want to surface them in output).
    """
    sql = (
        "SELECT l.id AS ledger_id, "
        "       l.creator_id, "
        "       l.submission_id, "
        "       l.amount, "
        "       l.voter_spend_key, "
        "       u.wallet_uid AS creator_wallet_uid "
        "  FROM creator_reward_ledger l "
        "  LEFT JOIN users u ON u.id = l.creator_id "
        " WHERE l.status = 'pending' "
        " ORDER BY l.created_at ASC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return await db.execute_fetchall(sql)


async def _mark_settled(db, ledger_id: str, settle_tx_id: str) -> None:
    await db.execute(
        "UPDATE creator_reward_ledger "
        "   SET status = 'settled', "
        "       settled_at = CURRENT_TIMESTAMP, "
        "       settle_tx_id = ? "
        " WHERE id = ?",
        (settle_tx_id, ledger_id),
    )
    await db.commit()


async def _build_wallet_client(env: str) -> WalletClient:
    """Load credentials for the requested env block."""
    # Let server.config pick up WALLET_ENV before it imports
    os.environ.setdefault("WALLET_ENV", env)
    from server import config

    if not config.WALLET_ENDPOINTS or not config.WALLET_HMAC_SECRET:
        raise RuntimeError(
            f"5888 wallet not configured for env={env!r}. "
            f"Check ~/.hermes/wallet_5888_keys.json or WALLET_* env vars."
        )
    return WalletClient(
        endpoints=config.WALLET_ENDPOINTS,
        site_id=config.WALLET_SITE_ID,
        api_key=config.WALLET_API_KEY,
        hmac_secret=config.WALLET_HMAC_SECRET,
    )


async def replay(dry_run: bool, limit: int | None, env: str) -> int:
    from server.db.engine import init_db, get_db, close_db

    await init_db()
    db = await get_db()

    rows = await _fetch_pending(db, limit)
    if not rows:
        print("No pending rows — nothing to replay.")
        await close_db()
        return 0

    print(f"Found {len(rows)} pending ledger row(s) "
          f"(env={env}, dry_run={dry_run}, limit={limit}).")
    print()

    wallet = None if dry_run else await _build_wallet_client(env)

    settled = 0
    skipped_no_wallet = 0
    failed: list[tuple[str, str]] = []

    for row in rows:
        ledger_id = row["ledger_id"]
        creator_id = row["creator_id"]
        creator_uid = row["creator_wallet_uid"] or ""
        amount = row["amount"]
        spend_key = row["voter_spend_key"]

        try:
            reason = _route_reason(spend_key)
        except ValueError as e:
            print(f"  [SKIP] ledger={ledger_id[:8]}… — {e}")
            failed.append((ledger_id, str(e)))
            continue

        idempotency_key = f"{reason}:{ledger_id}"
        label = f"ledger={ledger_id[:8]}… creator={creator_id[:8]}… " \
                f"amount={amount} reason={reason}"

        if not creator_uid:
            print(f"  [SKIP no_wallet] {label} — creator has no wallet_uid")
            skipped_no_wallet += 1
            continue

        if dry_run:
            print(f"  [DRY-RUN] would s2sGrant({creator_uid}, {amount}, "
                  f"{reason}, {idempotency_key})")
            continue

        try:
            resp = wallet.grant(  # type: ignore[union-attr]
                uid=creator_uid,
                amount=amount,
                reason=reason,
                idempotency_key=idempotency_key,
            )
            tx_id = resp.get("txId", "")
            duplicate = resp.get("duplicate", False)
            balance_after = resp.get("balanceAfter", "?")
            tag = "duplicate" if duplicate else "new"
            print(f"  [OK {tag}] {label} → txId={tx_id} "
                  f"balance_after={balance_after}")
            await _mark_settled(db, ledger_id, tx_id)
            settled += 1
        except WalletError as e:
            print(f"  [FAIL] {label} → [{e.http_code}] "
                  f"{e.error_code}: {e.message}")
            failed.append((ledger_id, f"{e.error_code}: {e.message}"))

    print()
    print("=" * 60)
    print("Phase 2 replay summary")
    print("=" * 60)
    print(f"  Total pending    : {len(rows)}")
    print(f"  Settled          : {settled}")
    print(f"  Skipped (wallet) : {skipped_no_wallet}")
    print(f"  Failed           : {len(failed)}")
    if dry_run:
        print(f"  (dry-run — no DB writes, no API calls)")
    if failed:
        print()
        print("  Failures (safe to re-run):")
        for lid, msg in failed[:20]:
            print(f"    {lid}  {msg}")
        if len(failed) > 20:
            print(f"    … and {len(failed) - 20} more")

    await close_db()
    return 1 if failed else 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 2 creator-reward replay.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be done, no API calls or DB writes.")
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the first N rows.")
    p.add_argument("--env", default="staging",
                   choices=("staging", "production"),
                   help="Which credentials block to use.")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()

    try:
        return asyncio.run(replay(
            dry_run=args.dry_run,
            limit=args.limit,
            env=args.env,
        ))
    except RuntimeError as e:
        print(f"[FATAL] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
