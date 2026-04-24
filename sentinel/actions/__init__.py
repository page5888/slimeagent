"""Action proposals — how the slime suggests doing things in chat.

This package is the "vocabulary" layer of Phase D. When the user chats
with the slime ("幫我打開 auth 模組"), the LLM can emit an `<action>…
</action>` block alongside its reply. We parse those blocks and route
each through the Phase C1 approval queue → Phase C2 surface
primitives. The user sees the proposal in the 待同意 tab and decides.

The slime **never** executes an action on its own. This module only
knows how to:
  1. Advertise the available action catalog to the LLM
  2. Parse action blocks out of LLM text
  3. Submit each parsed block as an approval queue proposal
  4. Tell the caller what to splice into the final chat reply

Public API:
    from sentinel.actions import (
        format_catalog_for_prompt,
        parse_and_submit,
    )
"""
from sentinel.actions.catalog import (
    format_catalog_for_prompt,
    parse_action_blocks,
    submit_parsed_action,
    parse_and_submit,
    ActionProposal,
    ProposalOutcome,
)

__all__ = [
    "format_catalog_for_prompt",
    "parse_action_blocks",
    "submit_parsed_action",
    "parse_and_submit",
    "ActionProposal",
    "ProposalOutcome",
]
