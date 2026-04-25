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
        "desc_zh": (
            "用系統預設程式開啟**本地檔案或資料夾**。只接受主人電腦上的實體路徑 — "
            "絕對不是網站 / App 名稱。要開網站請用 surface.open_url。"
        ),
        "desc_en": (
            "Open a LOCAL file or folder with the OS default handler. "
            "Only accepts real paths on disk — NOT website names or app "
            "names. For websites use surface.open_url instead."
        ),
        "payload": {"path": "string — 絕對路徑，必須在使用者 home 目錄下"},
        "example": {"path": "C:/Users/me/Documents/note.md"},
        "policy_note": "URL、不存在的路徑、使用者 home 之外的路徑都會被拒絕",
    },
    "surface.open_url": {
        "desc_zh": (
            "在預設瀏覽器打開一個網址。YouTube、GitHub、Google、任何 .com .tw "
            "都用這個，不要用 open_path 亂湊路徑。"
        ),
        "desc_en": (
            "Open a URL in the default browser. YouTube, GitHub, Google, any "
            "website — use this, never fabricate a local path for a web "
            "service with open_path."
        ),
        "payload": {"url": "string — 必須是 http:// 或 https:// 開頭的完整網址"},
        "example": {"url": "https://youtube.com"},
        "policy_note": "只允許 http/https，javascript:/data:/file:// 等一律拒絕",
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
    "voice.listen": {
        "desc_zh": (
            "打開麥克風錄幾秒，丟雲端 STT 轉成文字。轉好後會自動進「螢幕觀察」"
            "旁邊的語音欄，下一輪對話你就看得到。"
        ),
        "desc_en": (
            "Record N seconds from the mic and transcribe via cloud STT. "
            "Transcript lands in the Context Bus voice channel."
        ),
        "payload": {
            "duration_s": "number — 秒數，1~60 之間，不確定就填 5",
            "language": "string (optional) — 'zh' / 'en'；不填讓模型自動判斷",
        },
        "example": {"duration_s": 5, "language": "zh"},
        "policy_note": "會錄音 → 送到雲端 — 確認周圍沒有其他人私密對話",
    },
    "voice.speak": {
        "desc_zh": "用 TTS 把一段文字唸出來（喇叭會發聲）",
        "desc_en": "Speak the given text aloud via TTS",
        "payload": {
            "text": "string — 要念的內容，最多 1000 字",
        },
        "example": {"text": "提醒主人該休息囉"},
        "policy_note": "會從喇叭發出聲音；如果主人戴著耳機就只有主人聽到",
    },
    "chain.run": {
        "desc_zh": (
            "一次提案多個動作串起來跑（最多 5 步）。每一步都是上面其他的 action type，"
            "payload 要塞完整。一次批准、一次執行整串。適合「開檔案然後 focus 到它」這種組合。"
        ),
        "desc_en": (
            "Propose a multi-step chain (up to 5 steps). Each step is one of the "
            "other action types with its payload fully specified. One approval = "
            "whole chain runs."
        ),
        "payload": {
            "steps": (
                "list — 每個元素為 {action_type, payload, title}，"
                "按順序執行，任一步失敗會跳過後面"
            ),
        },
        "example": {
            "steps": [
                {
                    "action_type": "surface.open_path",
                    "payload": {"path": "C:/Users/me/proj"},
                    "title": "開啟專案資料夾",
                },
                {
                    "action_type": "surface.focus_window",
                    "payload": {"title_match": "proj"},
                    "title": "切過去",
                },
            ],
        },
        "policy_note": (
            "每一步都會跑自己原本的政策檢查；任何一步被擋整條就被擋。"
            "不能把 chain.run 嵌在另一個 chain.run 裡。"
        ),
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
=== ⚡ 動作提案協議（最高優先：主人剛剛叫你動手了，你必須照做）===

主人剛才的訊息看起來在叫你做事（開檔案、切視窗、唸出文字、看螢幕、開網站等）。
**這時你的工作不是聊天，是提案一個動作。** 提案進入「待同意」佇列，主人按同意才執行——你沒執行權，但**你必須提案**。

下面是規則 + 白名單 + 嚴格格式：

1. 主人明確要你做事 → **必須**提案（不要光講話、不要列系統狀態、不要假裝沒看到）
2. 真的閒聊（問你看法、講心情）→ 不要提案
3. 一次訊息最多 1 個動作；不確定參數→用文字回問
4. 只能用下面白名單裡的 action type
5. 開網站 → `surface.open_url`（必須完整 https:// URL）
   開本地檔案 → `surface.open_path`
   唸文字 → `voice.speak`
   錄音 → `voice.listen`
   看螢幕 → `vision.interpret_screen`

**回覆格式（嚴格遵守，否則我抓不到你的提案）**：
你的回覆 = 一句簡短自然語言 + 一個 <action> JSON 區塊：

<action>
{"type":"<從白名單挑>","payload":{...},"title":"簡短中文動作標題","reason":"為什麼要做"}
</action>

⚠ 重要：JSON 必須完整包在 `<action>` 和 `</action>` 兩個標籤之間。不要拆行寫成 `stance.xxx`，不要漏標籤。

白名單 action types：
<<CATALOG_LIST>>

範例：

主人：唸出「今天天氣真好」
Slime：好，我幫你提案唸出這句話。
<action>
{"type":"voice.speak","payload":{"text":"今天天氣真好"},"title":"唸出文字","reason":"主人要求"}
</action>

主人：幫我開 YouTube
Slime：好，我提案在瀏覽器打開 YouTube。
<action>
{"type":"surface.open_url","payload":{"url":"https://www.youtube.com"},"title":"開啟 YouTube","reason":"主人要求"}
</action>

主人：看一下我螢幕有什麼錯誤
Slime：好，我提案截圖讓 VLM 看。
<action>
{"type":"vision.interpret_screen","payload":{"prompt":"找出畫面上的錯誤訊息或紅色警告"},"title":"看螢幕找錯誤","reason":"主人要求"}
</action>

⚠ 再次提醒：本訊息最後**必須有 `<action>{...}</action>` 區塊**，否則你就只是閒聊，主人會以為你壞掉。
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


# Fallback regex for when the LLM forgets (or mangles) the <action>
# tags but still emits a JSON object with the right shape. Seen live:
# Gemini flash writes things like
#   stance.interpret_screen\n{"type": "vision.interpret_screen", ...}
# where the tag got tokenized into garbage but the JSON is still
# correct. Matching bare `{"type": ...}` objects with a non-greedy
# body lets us recover those without forcing the LLM to be perfect.
# The DOTALL flag keeps multi-line JSON (common when the LLM pretty-
# prints) working.
_BARE_JSON_RE = re.compile(
    r'\{\s*"type"\s*:\s*"[^"]+"\s*,\s*"payload"\s*:\s*\{.*?\}\s*(?:,[^{}]*?)?\}',
    re.DOTALL,
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


def _coerce_block(body: str, span: tuple[int, int]) -> Optional[ActionProposal]:
    """Shared body → ActionProposal converter. Returns None if the
    body is malformed, unknown, or wrong-shape — caller just skips.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = _try_repair_json(body)
        if data is None:
            return None
    if not isinstance(data, dict):
        return None
    action_type = data.get("type")
    payload = data.get("payload") or {}
    if not action_type or not isinstance(payload, dict):
        return None
    if action_type not in CATALOG:
        return None
    return ActionProposal(
        action_type=action_type,
        payload=payload,
        title=data.get("title") or action_type,
        reason=data.get("reason") or "使用者要求",
        span=span,
        raw_json=body,
    )


def parse_action_blocks(text: str) -> list[ActionProposal]:
    """Extract all action proposals from LLM text.

    Two passes:
      1. Strict: `<action>…</action>` wrapped JSON. What the prompt
         asks the LLM for; what a well-behaved LLM emits.
      2. Lenient fallback: bare `{"type": "...", "payload": {...}}`
         anywhere in the text. Real Gemini flash runs were emitting
         valid JSON but mangling the <action> tag into noise like
         "stance.interpret_screen" — the JSON still parses and still
         references a known action type, so we salvage it instead of
         dropping the whole action.

    The fallback is scoped carefully: only JSON objects whose "type"
    field names a key in CATALOG count. Arbitrary {"type":"..."}
    objects in chat (someone pasting a schema example) won't
    accidentally submit actions — only the handful of registered
    action types do.

    Malformed / unknown / wrong-shape blocks are silently dropped.
    Better to show the user the LLM's natural-language reply minus
    one broken block than to error out on tag parsing.
    """
    proposals: list[ActionProposal] = []
    covered: list[tuple[int, int]] = []  # spans already claimed by strict pass

    # Pass 1: strict <action>...</action>
    for m in _ACTION_BLOCK_RE.finditer(text or ""):
        body = m.group("body")
        prop = _coerce_block(body, m.span())
        if prop is not None:
            proposals.append(prop)
            covered.append(m.span())

    # Pass 2: bare JSON fallback. Skip anything already inside a span
    # the strict pass claimed so we don't double-count.
    def _overlaps(span: tuple[int, int]) -> bool:
        for cs, ce in covered:
            if span[0] < ce and span[1] > cs:
                return True
        return False

    for m in _BARE_JSON_RE.finditer(text or ""):
        if _overlaps(m.span()):
            continue
        body = m.group(0)
        prop = _coerce_block(body, m.span())
        if prop is not None:
            proposals.append(prop)
            covered.append(m.span())

    # Keep deterministic order: earliest-first so the splicing logic
    # in parse_and_submit produces a predictable reply.
    proposals.sort(key=lambda p: p.span[0])
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
