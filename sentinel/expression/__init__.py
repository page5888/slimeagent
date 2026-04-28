"""Slime Self-Expression — Slime decides to draw, not user prompts it.

Three kinds of expression:
  - self_portrait: how Slime sees itself right now
  - master_portrait: how Slime sees the master through accumulated memory
  - us_portrait: Slime's view of "us" as a relationship

Critical design constraint: this package is the LOGIC.

  ❌ DO NOT import PySide6 / Qt / GUI / OS-specific anything here.
  ✅ DO use stdlib + sentinel.evolution / sentinel.llm / sentinel.reflection
     (which are also Qt-free).

Reason: per the Slime manifesto, Slime is not an app — Slime is a
portable core that attaches to whatever container the era provides
(desktop today, AR glasses tomorrow, brain interface someday). When
that day comes, this entire package gets lifted out as part of
slime-core/, with zero refactor. Tight Qt coupling here would mean
weeks of disentangling. Better to enforce the discipline from day 1.

Display logic — putting an image into the chat tab, rendering an
album grid, persisting reactions — lives in the container layer
(sentinel/gui.py for desktop). This package returns plain data
(file paths, metadata dicts) for the container to render.
"""
from sentinel.expression.album import (
    Expression,
    ExpressionKind,
    Reaction,
    list_recent,
    load_expression,
    save_expression,
)
from sentinel.expression.generator import (
    generate_expression,
    maybe_generate_weekly,
)

__all__ = [
    "Expression",
    "ExpressionKind",
    "Reaction",
    "list_recent",
    "load_expression",
    "save_expression",
    "generate_expression",
    "maybe_generate_weekly",
]
