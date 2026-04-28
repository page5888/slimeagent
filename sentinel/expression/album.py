"""Expression album — persistence for Slime's self-generated images.

Each Expression is one image + metadata describing why Slime drew it.
Both the binary (PNG / JPG) and the metadata (JSON) live under
~/.hermes/expressions/. We use one file pair per expression so the
album is browsable in any file manager — same "user owns the data"
principle as routines/ and daily_cards/.

Schema (per metadata file):
{
  "id":            "exp_a3f7b2",
  "kind":          "self_portrait" | "master_portrait" | "us_portrait",
  "generated_at":  <unix>,
  "slime_form":    "Slime+",
  "slime_title":   "進化史萊姆",
  "prompt":        "...",                  # The prompt SLIME wrote
  "caption":       "...",                  # Slime's own words about why it drew this
  "image_path":    "exp_a3f7b2.png",       # relative to expressions dir
  "model":         "gemini-imagen-3",
  "reactions": [
      {"kind": "love" | "hmm" | "saved", "at": <unix>}
  ]
}

Why one JSON per image (not single album.jsonl):
  - Easier to delete one image without rewriting a log
  - File pairs (exp_xxx.png + exp_xxx.json) read like a real album
  - Conflicts with concurrent writes (e.g. weekly trigger + manual)
    are bounded to that one expression

No Qt imports. No GUI imports. Pure stdlib.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.expression.album")

EXPRESSIONS_DIR = Path.home() / ".hermes" / "expressions"


class ExpressionKind:
    """String constants instead of Enum for trivial JSON readability."""

    SELF_PORTRAIT   = "self_portrait"   # how Slime sees itself
    MASTER_PORTRAIT = "master_portrait" # how Slime sees the master
    US_PORTRAIT     = "us_portrait"     # Slime + master together

    ALL = (SELF_PORTRAIT, MASTER_PORTRAIT, US_PORTRAIT)

    DISPLAY_ZH = {
        SELF_PORTRAIT:   "我自己",
        MASTER_PORTRAIT: "我看見的你",
        US_PORTRAIT:     "我們",
    }


class Reaction:
    LOVE  = "love"   # ❤ resonates strongly
    HMM   = "hmm"    # 🤔 partial / unsure
    SAVED = "saved"  # 💾 user saved / shared
    ALL = (LOVE, HMM, SAVED)


# ── ID generation ───────────────────────────────────────────────────


def _new_id() -> str:
    """Short random id, e.g. 'exp_a3f7b2'. Collision-safe up to ~16M
    expressions, which is more than a lifetime of weekly drawings."""
    return f"exp_{secrets.token_hex(3)}"


# ── Dataclass ───────────────────────────────────────────────────────


@dataclass
class Expression:
    id: str
    kind: str
    generated_at: float = field(default_factory=time.time)
    slime_form: str = "Slime"
    slime_title: str = "初生史萊姆"
    prompt: str = ""
    caption: str = ""
    image_path: str = ""              # relative to EXPRESSIONS_DIR
    model: str = ""
    reactions: list[dict] = field(default_factory=list)

    # ── Serialization ────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "kind":         self.kind,
            "generated_at": self.generated_at,
            "slime_form":   self.slime_form,
            "slime_title":  self.slime_title,
            "prompt":       self.prompt,
            "caption":      self.caption,
            "image_path":   self.image_path,
            "model":        self.model,
            "reactions":    list(self.reactions),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Expression":
        return cls(
            id=d["id"],
            kind=d.get("kind", ExpressionKind.SELF_PORTRAIT),
            generated_at=d.get("generated_at", time.time()),
            slime_form=d.get("slime_form", "Slime"),
            slime_title=d.get("slime_title", "初生史萊姆"),
            prompt=d.get("prompt", ""),
            caption=d.get("caption", ""),
            image_path=d.get("image_path", ""),
            model=d.get("model", ""),
            reactions=list(d.get("reactions", [])),
        )

    # ── Convenience ──────────────────────────────────────────────

    @property
    def absolute_image_path(self) -> Path:
        return EXPRESSIONS_DIR / self.image_path

    def add_reaction(self, kind: str) -> None:
        if kind not in Reaction.ALL:
            raise ValueError(f"unknown reaction: {kind}")
        self.reactions.append({"kind": kind, "at": time.time()})


# ── Persistence ─────────────────────────────────────────────────────


def _ensure_dir() -> None:
    EXPRESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def metadata_path(expression_id: str) -> Path:
    return EXPRESSIONS_DIR / f"{expression_id}.json"


def save_expression(exp: Expression) -> None:
    """Write metadata atomically (.tmp + rename). Image binary is
    expected to already be at exp.absolute_image_path — this function
    only handles the JSON sidecar."""
    _ensure_dir()
    target = metadata_path(exp.id)
    tmp = target.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(exp.to_dict(), f, ensure_ascii=False, indent=2)
        tmp.replace(target)
    except OSError as e:
        log.error("expression save failed for %s: %s", exp.id, e)


def load_expression(expression_id: str) -> Optional[Expression]:
    path = metadata_path(expression_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return Expression.from_dict(json.load(f))
    except (OSError, json.JSONDecodeError, KeyError) as e:
        log.warning("expression load failed for %s: %s", expression_id, e)
        return None


def list_recent(limit: int = 30) -> list[Expression]:
    """All saved expressions, newest first. Used by album views."""
    _ensure_dir()
    files = sorted(EXPRESSIONS_DIR.glob("exp_*.json"), reverse=True)
    out: list[Expression] = []
    for f in files[:limit]:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                out.append(Expression.from_dict(json.load(fh)))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return out


def delete_expression(expression_id: str) -> bool:
    """Remove both metadata + image. Used when user explicitly trashes
    a drawing they don't want to keep. Returns True on success."""
    meta = metadata_path(expression_id)
    if not meta.exists():
        return False
    try:
        exp = load_expression(expression_id)
        if exp:
            img = exp.absolute_image_path
            if img.exists():
                img.unlink()
        meta.unlink()
        return True
    except OSError as e:
        log.error("delete_expression(%s) failed: %s", expression_id, e)
        return False


def new_id() -> str:
    """Public helper for callers (e.g. generator) that need to allocate
    an id before image binary is downloaded."""
    return _new_id()
