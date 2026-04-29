"""Day-1 safety net (manifesto 三大守則, 第一守則).

Slime is a toy, not a therapist. But the manifesto is explicit:
   "Slime 知道自己會被當成處方使用，特別是被那些找不到處方的人。"

So we don't pretend the risk doesn't exist. This package holds the
small set of guardrails that fire BEFORE the LLM gets the message —
not as legal cover, but as the line we said we'd never cross from
day 1.

Currently shipped:
  - crisis.check_crisis: keyword-tier scan for self-harm / suicide
    intent in user input. On match, the chat handler skips the LLM
    and surfaces a hand-off card pointing at human resources.

Out of scope here (separate concerns):
  - PII redaction (lives in chat / federation submission paths)
  - Action-policy guardrails (lives in growth.approval policy hooks)
"""
from sentinel.safety.crisis import (
    check_crisis,
    format_handoff_html,
    CrisisMatch,
)

__all__ = ["check_crisis", "format_handoff_html", "CrisisMatch"]
