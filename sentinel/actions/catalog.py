"""Action catalog + LLM protocol for proposal blocks.

The LLM and the slime share a simple text-based protocol so every
provider works without per-provider tool-calling glue:

  The system prompt tells the LLM:
    - What action types exist (name + description + payload shape)
    - How to emit a proposal: wrap JSON in <action>…</action>
    - When NOT to use them (observation-only, not asked, unsafe)

  The LLM replies in natural language, optionally with 0-N
  <action>…</action> blocks embedded.

  We strip the blocks out of the reply, feed each JSON object to the
  Phase C1 approval queue via `submit_action()`, and record outcomes
  so the chat handler can splice a "I queued this — go approve it"
  sentence back into the final reply.

Why not native tool calling?
----------------------------
Gemini/OpenAI/Anthropic each support function calling but with
different schemas, and our multi-provider fallback would need a
parallel implementation per provider. A text protocol works
everywhere — Ollama, local models, older API versions — and costs
<1 KB of system-prompt tokens. The drop in reliability vs. native
tool calling is small because the LLM has a structured template to
copy from; when it messes up we log, skip, and the chat still flows.

Catalog additions are intentionally gated to surface.* primitives
(Phase C2) for this first cut. Phase D follow-ups will add
higher-level composed actions (open workspace, inspect error, …)
as workflows built on top of surface primitives.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("sentinel.actions")


# ── Action type registry ──────────────────────────────────────────
#
# Keys are the action_type strings registered via
# sentinel.surface.handlers (or future modules). Each entry must
# match what `register_action_handler` knows about; mismatch → the
# LLM can propose something nothing will execute.
#
# Adding a new entry here exposes the action to the LLM. If you want
# an action handler that's NOT user-proposable from chat (e.g. a
# purely internal-automation action), register it in approval but
# don't add here.

CATALOG: dict[str, dict[str, Any]] = {
    "surface.open_path": {
        "desc_zh": "用系統預設程式開啟一個檔案或資料夾",
        "desc_en": "Open a file or folder with the OS default handler",
        "payload": {"path": "string — 絕對路徑，必須在使用者 home 目錄下"},
        "example": {"path": "C:/Users/me/Documents/note.md"},
        "policy_note": "URL 和使用者 home 之外的路徑會被拒絕",
    },
    "surface.focus_window": {
        "desc_zh": "把符合標題片段的視窗帶到前景",
        "desc_en": "Bring a window with matching title to front",
        "payload": {"title_match": "string — 標題子字串，不分大小寫"},
        "example": {"title_match": "Visual Studio Code"},
        "policy_note": "無政策；視窗切換是使用者看得到的動作",
    },
    "surface.list_windows": {
        "desc_zh": "列出所有可見視窗（唯讀）",
        "desc_en": "Enumerate visible windows (read-only)",
        "payload": {},
        "example": {},
        "policy_note": "無副作用",
    },
    "surface.get_clipboard": {
        "desc_zh": "讀取剪貼簿內容（唯讀）",
        "desc_en": "Read clipboard text (read-only)",
        "payload": {},
        "example": {},
        "policy_note": "無副作用",
    },
    "surface.set_clipboard": {
        "desc_zh": "寫入文字到剪貼簿",
        "desc_en": "Write text to the clipboard",
        "payload": {"text": "string — UTF-8, 最多 100KB"},
        "example": {"text": "要貼的內容"},
        "policy_note": "會覆蓋現有剪貼簿內容",
    },
    "surface.take_screenshot": {
        "desc_zh": "截全螢幕到 PNG 檔",
        "desc_en": "Capture full-screen PNG",
        "payload": {"out_path": "string (optional) — 不給會自動放 temp"},
        "example": {},
        "policy_note": "無副作用",
    },
    "vision.interpret_screen": {
        "desc_zh": "截取現在的螢幕，傳給多模態 LLM 根據你給的 prompt 分析",
        "desc_en": "Take a screenshot and have a VLM analyse it with your prompt",
        "payload": {
            "prompt": "string — 要 VLM 看什麼，例：『找錯誤訊息』『認出目前 UI』",
        },
        "example": {"prompt": "告訴我主人的螢幕上有沒有紅色錯誤提示"},
        "policy_note": "會把整張螢幕截圖傳到雲端 VLM — 主人會在批准卡片看到警告",
    },
}


# ── Prompt formatting ─────────────────────────────────────────────
#
# This block is appended to the chat system prompt when the user
# *explicitly asked the slime to do something*. We don't add it to
# every turn because it nudges the LLM toward proposing actions even
# on purely conversational messages ("the model that was offered
# tools keeps finding reasons to use them" — known behavioral bias).

PROMPT_INSTRUCTIONS_ZH = """
=== 動作提案協議（僅限主人明確要求你做事時使用）===
如果主人要你**動手做某件事**（開檔案、切視窗、讀/寫剪貼簿...），你可以用下面的格式**提案**一個動作。提案會進入主人的「待同意」分頁，只有主人按下同意才會真的執行——你自己沒有執行權。

規則：
1. 只在主人**明確要求**時提案。閒聊、問你對某件事的看法、問「你覺得怎麼樣」都不要提案。
2. 一次訊息最多 1 個動作提案，寧可先說「我打算做 X，對嗎」。
3. 不確定參數（例如使用者說「開那個檔案」但你不知道是哪個）→ 用文字回問清楚，不要亂填 path。
4. 不要提案不在下列白名單內的 action type。

格式（放在你的自然語言回覆任何位置，一次訊息最多一個）：
<action>
{"type":"<action_type>","payload":{...},"title":"簡短中文動作標題","reason":"為什麼要做（給主人看的）"}
</action>

白名單 action types：
<<CATALOG_LIST>>

例子：
主人：幫我開 ~/Documents/note.md
Slime：沒問題，我幫你提案打開這個檔案，去「待同意」點同意就會開。
<action>
{"type":"surface.open_path","payload":{"path":"/Users/peter/Documents/note.md"},"title":"開啟 note.md","reason":"主人要求打開此檔案"}
</action>
""".strip()


def _catalog_list_text() -> str:
    """Render the catalog into bullet lines for the system prompt."""
    lines = []
    for action_type, spec in CATALOG.items():
        lines.append(f"- `{action_type}` — {spec['desc_zh']}")
        if spec.get("payload"):
            payload_str = ", ".join(f"{k}: {v}" for k, v in spec["payload"].items())
            lines.append(f"    payload: {{{payload_str}}}")
        note = spec.get("policy_note")
        if note:
            lines.append(f"    注意：{note}")
    return "\n".join(lines)


def format_catalog_for_prompt() -> str:
    """Return the action-protocol prompt block ready to concatenate
    into the chat system prompt. Caller decides when to include it
    (e.g. only when user asked for help doing something) vs. leave
    out (for pure conversation)."""
    return PROMPT_INSTRUCTIONS_ZH.replace("<<CATALOG_LIST>>", _catalog_list_text())


# ── Parsing ───────────────────────────────────────────────────────


# Non-greedy match so back-to-back blocks don't merge. DOTALL so the
# JSON body can span lines (LLMs sometimes pretty-print).
_ACTION_BLOCK_RE = re.compile(
    r"<action>\s*(?P<body>\{.*?\})\s*</action>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class ActionProposal:
    """One parsed <action> block. Raw JSON body kept for debugging."""
    action_type: str
    payload: dict
    title: str = ""
    reason: str = ""
    # Raw match span in the original LLM text — useful for splicing
    # out the block when rendering the final user-visible reply.
    span: tuple[int, int] = (0, 0)
    raw_json: str = ""


def _try_repair_json(body: str) -> Optional[dict]:
    """Second-chance parse when strict json.loads failed.

    Real-world LLM output on Windows paths often emits single
    backslashes ("C:\\Users\\foo") which are valid Python strings but
    invalid JSON (\\U, \\f, \\t, etc. are all interpreted as escape
    sequences). Rather than drop an otherwise-correct proposal because
    of a path-format quirk, we run a conservative repair: escape any
    backslash that isn't already part of a valid JSON escape sequence.

    Returns the parsed dict on success, None if even the repaired
    string doesn't parse.
    """
    # JSON allows: \" \\ \/ \b \f \n \r \t \uXXXX. Anything else after
    # a single backslash is invalid. Escape every backslash that isn't
    # followed by one of these.
    repaired = re.sub(
        r'\\(?!["\\/bfnrtu])',
        r'\\\\',
        body,
    )
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def parse_action_blocks(text: str) -> list[ActionProposal]:
    """Extract all <action>…</action> proposals from LLM text.

    Malformed blocks (not valid JSON, missing required fields, unknown
    action_type) are skipped with a log line. We prefer "quietly drop
    the bad one" over "fail the whole chat reply" — better to show the
    user the LLM's natural-language reply minus one broken block than
    an error about tag parsing.

    Before giving up on a JSON parse error we try one repair pass for
    the common Windows-path backslash case (see _try_repair_json).
    """
    proposals: list[ActionProposal] = []
    for m in _ACTION_BLOCK_RE.finditer(text or ""):
        body = m.group("body")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            data = _try_repair_json(body)
            if data is None:
                log.warning(f"action block not valid JSON: {e!r}; body={body[:100]}")
                continue
        action_type = data.get("type")
        payload = data.get("payload") or {}
        title = data.get("title", "")
        reason = data.get("reason", "")
        if not action_type or not isinstance(payload, dict):
            log.warning(f"action block missing type/payload: {data}")
            continue
        if action_type not in CATALOG:
            log.warning(f"action block references unknown type: {action_type}")
            continue
        proposals.append(ActionProposal(
            action_type=action_type,
            payload=payload,
            title=title or action_type,
            reason=reason or "使用者要求",
            span=m.span(),
            raw_json=body,
        ))
    return proposals


# ── Submission ────────────────────────────────────────────────────


@dataclass
class ProposalOutcome:
    """Result of trying to queue a parsed proposal.

    Shape is deliberately simple so the chat handler can branch on it
    without importing approval internals:
      - queued: True  → approval id is usable, user will see in tab
      - queued: False → denied or errored; `message` is user-facing
    """
    proposal: ActionProposal
    queued: bool
    approval_id: str = ""
    message: str = ""
    findings: list[dict] = field(default_factory=list)


def submit_parsed_action(prop: ActionProposal) -> ProposalOutcome:
    """Submit a single parsed proposal through the Phase C1 queue.

    Returns a ProposalOutcome. Catches the PolicyDenied and generic
    Exception cases so the caller can produce a human-friendly
    response without touching approval internals.
    """
    try:
        from sentinel.growth import submit_action, PolicyDenied
    except Exception as e:
        return ProposalOutcome(
            proposal=prop, queued=False,
            message=f"動作提案模組載入失敗：{e}",
        )

    try:
        approval = submit_action(
            action_type=prop.action_type,
            title=prop.title,
            reason=prop.reason,
            payload=prop.payload,
        )
    except PolicyDenied as e:
        return ProposalOutcome(
            proposal=prop, queued=False,
            message=f"政策拒絕：{'; '.join(f['msg'] for f in e.findings)}",
            findings=e.findings,
        )
    except ValueError as e:
        # Unknown action_type, etc — shouldn't happen because we
        # filtered in parse_action_blocks, but defense-in-depth.
        return ProposalOutcome(
            proposal=prop, queued=False,
            message=f"動作不存在：{e}",
        )
    except Exception as e:
        log.exception("submit_action raised unexpectedly")
        return ProposalOutcome(
            proposal=prop, queued=False,
            message=f"提案失敗：{e}",
        )

    return ProposalOutcome(
        proposal=prop, queued=True,
        approval_id=approval.id,
        message=f"已排入『待同意』（編號 {approval.id}）",
    )


def parse_and_submit(llm_text: str) -> tuple[str, list[ProposalOutcome]]:
    """Full pipeline: text → parsed proposals → submitted → cleaned text.

    Returns (user_visible_text, outcomes):
      - user_visible_text: the LLM's reply with every <action>…</action>
        block replaced by a short status sentence ("我提案了 X，去待
        同意確認 ✓" / "政策拒絕這個動作 ✗").
      - outcomes: ordered list matching the original block order so
        caller can inspect per-proposal results (useful for Telegram
        reply formatting that differs from chat window).

    If no action blocks are found, returns (original_text, []).
    """
    if not llm_text:
        return llm_text, []
    proposals = parse_action_blocks(llm_text)
    if not proposals:
        return llm_text, []

    outcomes = [submit_parsed_action(p) for p in proposals]

    # Replace each block (last-first, so earlier spans stay valid) with
    # a short status sentence. Iterating in reverse span order keeps
    # the absolute offsets of earlier blocks unchanged as we edit.
    out = llm_text
    for outcome, prop in sorted(
        zip(outcomes, proposals),
        key=lambda pair: pair[1].span[0],
        reverse=True,
    ):
        start, end = prop.span
        if outcome.queued:
            replacement = (
                f"【已提案 {prop.title}｜到「待同意」分頁按同意就會執行】"
            )
        else:
            replacement = f"【無法提案 {prop.title}：{outcome.message}】"
        out = out[:start] + replacement + out[end:]

    return out, outcomes
