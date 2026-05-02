# Changelog

所有重要變更都會記錄在這裡。格式基於 [Keep a Changelog](https://keepachangelog.com/)。

---

## [Unreleased]

### Fixed — Phase 1 cleanup：chat.py system prompt 殘留 CPU/RAM 引用全清

PR #134 的 Phase 1 archive 加了「禁止報電腦狀態」line 但**沒清乾淨 chat.py 系統 prompt 本身**。檢驗 PR #134 + #135 + #136 + #137 累積結果時，發現 chat.py 還有 4 處 instructional content 跟禁止行**直接互相矛盾**：

| 位置 | 舊內容 | 為什麼是 bug |
|---|---|---|
| `SELF_AWARENESS_TEMPLATE` 感知系統列表 | `「系統之眼：監控 CPU/RAM/磁碟」` | 告訴 LLM 它能看 CPU |
| `CHAT_SYSTEM_PROMPT` 開頭自我介紹 | `「你能觀察主人的電腦狀態和開發活動」` | 直接打臉禁止行 |
| `CHAT_SYSTEM_PROMPT` 行為指引 | `「主人問系統狀態 → 報 CPU 78%，挺拼的喔」` | 明文教 slime 回 CPU% |
| `PERSONALITY_BY_TIER["Ultimate Slime"]` quirk | `「連 CPU 是什麼都不知道呢」` | 角色設定提 CPU |
| `EMOTION_TRIGGERS["worried"]` conditions | `["CPU 使用率超過 90", "RAM 使用率超過 85", "磁碟使用率超過 90", ...]` | dead match terms（system_summary post-Phase-1 永遠空、永遠不會 fire）但讀起來誤導 |

LLM 看到「禁止 + 教你怎麼回」會行為不可預測。重啟前一定要清掉。

**修法**：

- `SELF_AWARENESS_TEMPLATE` 感知系統列表移除「系統之眼」行、改為「視窗追蹤」+「感知之眼（idle / 活躍程度，不抓內容）」+ 加註解寫死「v0.8 sensor 重構 archive、主人不在意電腦狀態」
- `CHAT_SYSTEM_PROMPT` 自我介紹改為「能觀察主人正在做什麼——他在哪個視窗、看什麼網頁、寫什麼程式、跟誰聊天」
- `CHAT_SYSTEM_PROMPT` 行為指引改為「主人問你看到什麼／在做什麼，講具體看到的視窗跟活動（『你開著 chat.py 寫了快一小時』『你剛切到 Reddit 在看 r/programming』），**不報任何電腦硬體 metric**」
- `PERSONALITY_BY_TIER["Ultimate Slime"].quirk` 改為「還記得剛轉生的時候，連你打開哪個視窗都看不全呢」（保留懷舊感、把感官對象從 CPU 換成主人視窗）
- `EMOTION_TRIGGERS["worried"].conditions` 移除 CPU/RAM/磁碟 keyword，留 `["process crash", "build fail"]`（這兩個還可能從 file_watcher / claude_watcher activity 串中 match）

**驗證 sanity grep**：

```
grep CPU|RAM|磁碟 sentinel/chat.py
```

剩下的全部在註解或禁止行內部（教 slime「不准講 CPU/RAM/磁碟」那行本身——留著對的）。

274/274 tests 全綠（無新增 test，這是 prompt 文字 cleanup，覆蓋率不變）。



**對應**：v0.8 sensor 重構 Phase 3b（接 Phase 3a PR #136）。Phase 3a 規則層覆蓋 80%，這個 PR 處理長尾 — 規則認不出來的 app / title，丟 LLM 解、結果 cache 起來避免重複燒 token。

**新模組 `sentinel/window_semantics_llm.py`**

跟純 rule 層 `window_semantics.py` 分開，所以 importer 想要免費規則路徑可以繼續用 `interpret_window`，要 LLM hybrid 才用這個。

**Public API**：

```python
interpret_window_with_llm(snapshot, *, use_llm=True) -> dict
```

回 schema 跟規則層一模一樣的 9-key dict，下游 consumer 不用知道答案來自規則還 LLM。

**決策樹**：

```
1. 跑規則層
2. confidence != UNKNOWN → 回規則答案（規則 high confidence 永遠贏，LLM 不必 retry）
3. use_llm=False → 回規則的 unknown（test/dry-run/省電模式 gate）
4. cache hit → 回 cached
5. LLM call:
   - 成功 → cache + 回（confidence 標 MEDIUM，跟規則 high 區分）
   - 失敗（unreachable/garbled JSON）→ 回規則的 unknown，**不 cache failure**
```

**Cache 設計**

- 檔案：`~/.hermes/aislime_window_semantics_cache.json`，atomic 寫（`.tmp` → `replace`）
- Key：`process_name +  + window_title`（控制字符 separator，避免任一欄位內容碰撞）
- Value：完整 semantic dict + meta（`interpreted_at` epoch、`model` 標籤）
- 上限：5000 entry
- Eviction：FIFO by `interpreted_at`（最舊的先丟）。**選 FIFO 不選 LRU** 的理由：daemon 每 2s poll 一次當前視窗，true LRU 永遠不會 evict 主人最近用的東西、即使那些是穩定的；FIFO 配 age cap 比較貼近實際使用形狀
- Failure not cached：LLM 暫時掛掉的話下次 retry，不會把「unknown」永久塞進 cache

**LLM prompt 設計**

- temperature 0.2（要求 cache friendly：同 input 大致同 output）
- max_tokens 250（schema dict ~150 token、留 slack）
- 嚴格指定 JSON-only 輸出，但 `_parse_llm_json` 容錯：strip markdown fence、容忍 leading prose、找最外層 `{...}` span
- 隱私規則寫進 prompt：messaging 只給 contact 名、絕不抓 message content / preview / 主題 / 情緒
- Schema enforcement：parse 後檢查 `app_category` 在 enum 內、不在的話 normalize 為 UNKNOWN（防 LLM 幻覺）

**Cache → rule 回流：deliberately NOT 自動做**

施工指示提到「定期把 LLM 判斷 cache 起來變成新規則」，但 LLM 判斷 across runs 可能變動（即使 temperature 0.2），自動 promotion 會把不一致釘進規則層。本 PR 留 cache file 給未來人工 review，看哪些 title 反覆 fall through、手動 add rule 到 `window_semantics.py`。

**測試**：29 個新測試（`tests/test_window_semantics_llm.py`）：

- **`_parse_llm_json` 容錯（8）**：clean JSON / markdown fence / leading prose / unparseable / non-dict / 缺欄位 default 補空 / unknown category → UNKNOWN / topic_signal 截斷 / 非 string 值 coerce
- **決策樹（7）**：規則 high → 不 call LLM、`use_llm=False` → 不 call、unknown 觸發 LLM call、LLM None → 不 cache + 回規則、garbled JSON → 同上、failure 不被 cache、empty snapshot 不 call LLM
- **Cache 持久化（5）**：first call 寫檔、second 同 input 用 cache、模組 reload 後 cache 仍生效（disk 讀回）、不同 title 各自 entry、corrupt cache file 不 crash
- **Eviction（2）**：under cap 不 evict、over cap 丟最舊
- **Key construction（2）**：process + title 分隔正確、空 component 不 crash
- **Privacy / schema（4）**：rule path / LLM path / failure path 三條都回完整 9-key schema、`is_idle` pass-through 不丟

274/274 全綠（245 prior + 29 new）。

**還沒做**

- Phase 4：activity track 寫進 SQLite 讓主人 query「我昨天看了什麼」
- Phase 5：impulse engine 重設計
- Phase 6：整合測試
- 沒人 call `interpret_window_with_llm` in daemon loop 還沒接 — 同 schema-first pattern。Phase 4 寫入時整合



**對應**：v0.8 sensor 重構 Phase 3a（接 Phase 2 PR #135）。施工指示提到 Phase 3 是「window title 翻譯成主人在做什麼」，建議混合做法（rule 80% + LLM 20%）。這個 PR 只做 **rule 層**，LLM fallback 單獨拆 Phase 3b。

**為什麼拆**：

1. LLM call 每次有 token 成本，daemon 每 2s poll 一次、un-cached LLM 燒爆
2. Rule 層先測試覆蓋率 — 看 0xspeter 實際 8x% 比例落在哪、再決定要不要花 LLM
3. PR #128 → #129 的教訓：每個 PR 範圍小、好 review、CI 容易擋住問題

**落地：`sentinel/window_semantics.py`**

新模組純 logic（無 Qt、無 IO、無 LLM），單一 entry point：

```python
interpret_window(focus_snapshot: dict) -> dict
```

input 是 Phase 2 的 `current_focus_snapshot()` 輸出，output 是固定 schema 的語意 dict：

| key | 來源 | 用途 |
|---|---|---|
| `app_category` | process_name → category map | browser / ide / messaging / video / audio / document / terminal / file_browser / game / unknown |
| `content_type` | category default + browser/IDE/messaging override | coding / social_discussion / video_watching / music_listening / reading / conversation / shell / browsing / file_navigation / gaming / unknown |
| `topic_signal` | category-specific parser | 短文字 hint，例：`"Reddit: r/programming - Bash zsh"` / `"coding: gui.py (slimeagent)"` / `"chatting: 媽媽"` |
| `platform` | browser title regex | reddit / youtube / github / stackoverflow / twitter / hackernews / medium / bilibili / netflix / ai_chat / 等等 |
| `file` / `project` | IDE title parser | VS Code / JetBrains 雙格式都支援 |
| `contact` | messaging title parser | 只抓對象名、**從不抓內容**（隱私邊界） |
| `confidence` | high / medium / low / unknown | Phase 3b LLM 看 confidence 決定要不要 override |
| `is_idle` | pass-through | 從 snapshot 來 |

**規則庫覆蓋**：
- 50+ process 名稱 → 10 個 category
- 14 個 browser platform regex（reddit / youtube / github / stackoverflow / twitter / facebook / hackernews / medium / instagram / bilibili / twitch / netflix / spotify / **ai_chat**——後者讓 Phase 5 impulse 可以判斷主人在跟別的 AI 講話、自己閉嘴）
- IDE title 三種格式（VS Code 雙/三段、JetBrains `(project)`、modified marker `●`）
- Messaging title 四種分隔符（` - ` / ` — ` / ` · ` / ` | `）+ 12 個 app 名 keyword

**設計原則**

- **Pure function**：純 dict in / dict out、無副作用、可在任何 thread 任何頻率呼叫。Phase 3b 的 LLM cache 寫在另一層、不污染這個
- **Forward-compatible 輸出**：`confidence` 欄位讓 Phase 3b 知道該不該 override；rule 認的 high confidence 不必讓 LLM 重判
- **隱私邊界**：messaging 只暴露對象名、不抓 preview content。Test 明文 pin 這條（`test_messaging_does_not_leak_content`）
- **Topic signal 截斷**：80 字元上限，保證 Phase 5 LLM prompt 不會被超長 title 灌爆
- **Truly unknown 留 topic_signal = title**：給 Phase 3b LLM 跟 log reader 都有東西可看

**測試**：43 個新測試（`tests/test_window_semantics.py`）：

- Schema：所有輸出含完整 9 個 key，包括 empty / unknown 情境（4 tests）
- Empty input handling（2 tests）
- Browser 偵測（4 tests）+ platform rules（8 tests，cover 施工指示提到的 reddit / youtube + 6 個額外）
- IDE 偵測（3 tests）+ title parsing 5 個格式（VS Code 雙/三段、JetBrains、modified marker、unparseable）
- Messaging 偵測（5 tests，含 contact 抓取 + privacy pin）
- Video / Audio / Terminal / Document（各 1-3 tests）
- Unknown 情境（3 tests，含 truncation + topic_signal 仍保留）
- Pure function 性質（2 tests，repeatable + 不 mutate input）

245/245 全綠（202 prior + 43 new）。

**還沒做（Phase 3b）**

- LLM fallback 處理 `confidence == unknown` 的 long-tail
- 持久化 cache（`~/.hermes/aislime_window_semantics_cache.json` 或 SQLite）
- Cache → 規則回流機制（high-frequency LLM 判斷反饋成新規則）

**沒人 use 這個 API**：對。同 #130 / #133 pattern，schema 先、消費者後。Phase 3b 加 LLM、Phase 4 把語意結果存進 activity track、Phase 5 接 impulse engine——逐步閉合。



**對應**：v0.8 sensor 重構 Phase 2（接 Phase 1 PR #134）。Phase 1 砍掉錯方向的 OS-metrics sensor，Phase 2 強化對方向的 sensor——主人正在看什麼視窗、看了多久、有沒有在動鍵盤滑鼠。

**Phase 2 acceptance**（per 施工指示）：

- [x] window title 抓取（**已存在**，pre-Phase-2 已是這個架構，現有 `_get_active_window` via Win32 `GetForegroundWindow`）
- [x] sensor 輸出可在 log 看到具體 title（新加 `log.info("active window: [proc] title")` 在 poll 切換時）
- [x] 抓取頻率 5-10 秒（**比 spec 更快**：現存 daemon loop 是 `time.sleep(2)`，每 2s tick 一次，不調慢）
- [x] 額外加 `is_idle` 旗標（spec 要求）
- [x] 暴露 spec-shape `current_focus_snapshot()` 給 Phase 3+ 消費

**落地**

`sentinel/activity_tracker.py` 新增三個 API：

| API | 作用 |
|---|---|
| `seconds_since_last_input()` | Win32 `GetLastInputInfo` + `GetTickCount`，回 master 上次有鍵盤/滑鼠輸入到現在多少秒。非 Windows 平台 fallback `0.0`（測試環境 cross-platform OK） |
| `is_user_idle(threshold_secs=60)` | 上面那個的閾值版 |
| `current_focus_snapshot(idle_threshold_secs=60) -> dict` | 回施工指示 spec 要的 dict：`{timestamp (ISO-8601 Z), epoch, app_name, process_name, window_title, duration_in_focus, idle_seconds, is_idle}` |

`WindowEvent` dataclass 加 `is_idle: bool = False` field。`poll()` 在 record 切換事件時，capture 當下 `is_user_idle()` 寫入 event。

JSONL log（`~/.hermes/sentinel_activity.jsonl`）多了 `is_idle` 欄位，pre-Phase-2 既有 row 沒這個欄位的話，下游 reader 用 `.get("is_idle", False)` 回讀（`recent_activity.py` 沒有顯式 read 這個欄位、不會壞）。

`poll()` 在偵測到視窗切換時新加 `log.info("active window: [%s] %s", proc, title_short)` 一行——Phase 2 的可觀察 acceptance：0xspeter 在主 log 直接看到「Slime 知道我在看 X 網頁了」這類訊息，**不用 tail JSONL**。

**設計細節**

- `current_focus_snapshot()` 跟 `poll()` 解耦：snapshot 是 read-only 純 query，沒有副作用、不更新 daily stats、不寫 JSONL；poll 才做這些。Phase 3 的 LLM 解析可以高頻 read snapshot 不擔心污染狀態。
- ISO-8601 timestamp 統一用 UTC + Z suffix，跨時區讀回 log 不會誤判
- `app_name` 跟 `process_name` 暫時同值（都是 `.exe` 名）。Phase 3 的「window-title 語意理解」會把 `app_name` 從 `chrome.exe` map 成 `Google Chrome`，那時候才會分歧
- Idle 偵測選 `GetLastInputInfo`（OS 原生 API）而非 hook `input_tracker._last_key_time`，原因：(a) `input_tracker` macOS 整個 disabled、couple 進來會跨平台破裂；(b) `GetLastInputInfo` 同時涵蓋鍵盤/滑鼠/觸控/程式輸入，比鎖定 keyboard 更全面
- `GetTickCount()` 32-bit ms 計數器每 ~49.7 天 wrap 一次，用 `& 0xFFFFFFFF` 處理 wrap-around

**測試**：16 個新測試（`tests/test_activity_tracker.py`，新檔）：

- `seconds_since_last_input`：non-Windows → 0、Win32 API failure → 0、兩個 cross-platform smoke
- `is_user_idle`：閾值上下、邊界（>= 算 idle）、預設值釘 60s 不要悄悄改
- `current_focus_snapshot`：spec keys 全到、ISO-8601 格式正確、title/process pass-through、duration 在切換瞬間 = 0、duration 在持續同視窗時 grow、is_idle flag 反映 threshold、`_get_active_window` failure 時 graceful return
- JSONL：`is_idle` 寫進 row、`poll()` 切換時 capture is_idle

202/202 tests 全綠（186 prior + 16 new）。

**還沒處理**

- Phase 3：window-title 語意理解（rule + LLM 混合，把 `chrome.exe / "Reddit - r/programming/comments/xxx"` map 成 `{app_category: "browser", content_type: "social_discussion", topic_signal: "programming - Bash Zsh"}`）
- Phase 4：activity track 寫進 SQLite 讓主人 query「我昨天看了什麼」
- Phase 5：impulse engine 重新設計
- Phase 6：整合測試「Slime 真的在看主人了」



**對應**：v0.8 sensor 重構施工指示 Phase 1（2026-05-02 0xspeter）。前置 7 份 ADR 共同論證 slime 必須看到「主人在做什麼」、不是「電腦在做什麼」。Phase 1 砍掉錯方向的基礎，Phase 2-6 接上對方向的基礎。

**核心判斷**（直接引用 0xspeter）：

> 「目前史萊姆都沒有真的與我互動。他都只會說我電腦怎麼了。我的電腦硬體發生什麼我真的不在意。」

舊的 `sentinel/system_monitor.py` 觀察 CPU/RAM/disk/process —— 這些是「電腦的內臟」，不是「主人的生活」。manifesto + 7 份 ADR（emergent milestones / voice anchors / 共同沉積 / Slime 不向外 / 稱號 / 商業模式 / no-individual-engagement）的所有規則都假設 slime 看到主人，但 sensor 看的是電腦。**所有 manifesto 在錯的 sensor 基礎上是空話**。換 sensor 是讓 slime 真的開始活的物質基礎。

**這次落地（Phase 1 only）**

實際結構跟施工指示假設的不同（沒有 `sentinel/sensors/cpu_monitor.py` 那種多檔結構，只有單一 `sentinel/system_monitor.py`），所以 Phase 1 範圍依現實調整：

- `sentinel/system_monitor.py` → `archive/sensors-os-metrics/system_monitor.py`（`git mv`，保 history）
- `archive/sensors-os-metrics/README.md` 寫死哲學基礎、disabled callsites 對照表、復活條件

**陪伴面 callsites 全部 disable + 加 TODO**（不刪程式碼，等 Phase 2-5 接新 sensor 重新接）：

| 位置 | 舊 | 新 |
|---|---|---|
| `chat.py:_build_system_prompt` | `snapshot.summary()` 灌進 `<<SYSTEM_STATE>>` slot | `system_summary = ""`（slot 空著）|
| `chat.py` system prompt「能看到什麼」描述 | 列了「系統 snapshot」 | 改成明寫「禁止報主人的電腦狀態」 |
| `daemon.py:monitor_loop` 主 loop | `snapshot = take_snapshot()` → `build_context` | `snapshot = None`，`build_context` 改成接受 None |
| `daemon.py:monitor_loop` idle cycle | `compose_message(warnings=snapshot.warnings, summary=snapshot.summary(), ...)` | `compose_message(warnings=[], summary="", ...)`（OS metrics block 永不觸發，只剩 LLM warning 可發訊） |
| `gui.py:_start_daemon._run` 主 loop | 同 daemon.py | 同步處理 |
| `gui.py:_start_daemon._run` analyze_events 觸發條件 | `snapshot.warnings or len(file_events) > 20` | 只剩 `len(file_events) > 20`（CPU/RAM 觸發退場）|
| `brain.py:build_context` signature | `system_snapshot` 必填 | 接受 None，None 時 skip publish 「system」entry 到 context bus |

**`/status` Telegram 指令**（option B，調度面留著但換內容）：

```
舊：📊 系統狀態
    CPU 23% | RAM 45% (3.2/8GB) | Disk 67% (free 102GB)
    Top processes: chrome (PID 1234): CPU 5% ...

新：🧬 AI Slime
    形態：Slime+（覺醒史萊姆）
    活了：47.3 天
    箱子：12 張紙
    觀察：1,234
```

理由：`/status` 在調度面（per ADR `2026-04-30-co-sediment-architecture.md` 兩個面架構），是 admin/debug 工具不是 slime 講話。留指令、換內容對齊「slime 不報電腦」精神。`daemon.py:cmd_status` 跟 `gui.py:cmd_status`（live Telegram）兩處同步處理。

**還沒處理的相鄰項目**（不在 Phase 1 範圍）：

- `sentinel/input_tracker.py`（鍵盤輸入內容）— 抓的不只是節奏，是內容；對方向但踩到「不要 IME hook」紅線的邊。Phase 2-3 內部再判斷
- `sentinel/screen_watcher.py`（截圖 + multimodal LLM）— 施工指示「不要做」清單明寫，但 in prod 在跑。獨立判斷
- `sentinel/skills/{bun_resource_monitor,resource_task_scheduler}.py`（untracked 孤兒，PR #132 之後 inert）— git history 沒有它們、留原處不動，README 寫明可手動刪
- `EMOTION_TRIGGERS["worried"]["conditions"]` 裡的 CPU/RAM 關鍵字 — `system_summary` 空之後永遠 match 不到、是 dead match terms。Phase 5 重設計時清掉
- `CPU_WARN_PERCENT` 等 config 常數 — 暫留（怕 `core_backup` 還引用）

**測試**：

186/186 全綠（154 prior + 32 from PR #133）。沒新增測試——這次是純移除 + disable，沒有新行為值得 cover；後續 Phase 2 加新 sensor 時才開始寫對應 test。

CI gate（PR #129 GUI smoke）保護仍有效。



對應 ADR `docs/decisions/2026-04-30-title-system.md`。稱號系統是 v0.8 cycle 的核心工作，這個 PR 是它的第一塊：純後端 schema + 持久化，**沒有 LLM、沒有 chat hook、沒有 GUI**——那三塊各是後續 PR。

跟 PR #130（birth_signature 後端 schema）同個 pattern：先把地基測穩，後面接 render / generation / 引用機制各自再來一個小 PR，不要一次塞太多 Qt code 進去（已經被 PR #128→#129 教過一次）。

### 落地

**新模組 `sentinel/title_storage.py`**（純 logic，無 Qt）：

- `Title` dataclass — schema 跟 ADR § 完整資料 Schema（line 138-168）一對一對映
- `EventReference` / `InvocationRecord` 巢狀 dataclass
- `Trigger` / `MasterResponse` / `InvocationResponse` 字串常數類（不用 Enum 為了 JSON 序列化清爽 + schema evolve 方便）
- `Title.display_text()` — ADR § Q7 要求的 `{title} (D{day_marker})` 格式；renamed 用主人取的名字、不暴露原始
- `Title.is_in_box()` — accepted 跟 renamed 進箱子；pending / rejected 不進
- `Title.is_frozen(now=...)` — 支援注入 `now` 方便測試
- `Title.is_well_formed()` — encode ADR 紅線 1, 2, 3, 10 storage 層能驗的部分（empty title / 負 day_marker / 不合法 trigger / 不合法 master_response / accepted 但沒 events / renamed 但沒新名字）。**不在 storage 強制執行**——那是 generation 層的責任，這個 helper 給 caller 自己決定要不要 check

**持久化（`~/.hermes/aislime_titles.json`）**：

- `load_titles()` / `save_titles(titles)`
- 對齊 `evolution.load_evolution` 的 corrupt-file 處理：壞掉的檔備份成 `.broken.<epoch>.json`、不靜默覆寫（防 Mac 用戶被 wipe 那個 bug 的同形）
- Atomic write：先寫 `.tmp` 再 `replace()`，半寫狀態不會殺主檔案
- 單一 row malformed 不影響其他 row：壞的 skip 掉、好的照常 load
- Non-list payload 直接回空：不會把 dict keys 當 row 跑

**High-level helpers**：

- `add_title(title)` — append + 拒絕 id collision（防 generator 退化）
- `find_title(id)` — 回 `Title | None`
- `update_title(updated)` — 找 id 替換；找不到回 `False`、**不**幫忙 add
- `accepted_titles()` — filter 出 in_box 的；frozen 的也算（cold storage 是給 invocation 用的、不是給展示用的）
- `new_title_id()` — uuid4 hex

### 不在這個 PR 的東西

| 模組 | 何時 | 為什麼分開 |
|---|---|---|
| `title_system.py`（生成 + morality vet） | 後續 PR | 要 LLM prompt 工程，跟 schema 解耦 |
| `title_invoker.py`（chat 自然引用） | 後續 PR | 要改 chat.py，Qt 不熱但 chat path 改動範圍大 |
| GUI 箱子子頁 | 後續 PR | Qt 改動，照 PR #129 教訓單獨拆 |
| 命名儀式產生第一個稱號 | 後續 PR | 要改 `identity.py`，依賴 title_system 已存在 |

### 測試

32 個新測試（`tests/test_title_storage.py`）：

- Display: day_marker 顯示、renamed 用新名
- State methods: in_box / frozen 各分支
- Well-formedness: 7 條 invariant（happy + 6 條 fail case）
- Persistence: empty load / round-trip / nested dataclass / atomic write / corrupt backup / non-list / malformed row skip
- Helpers: add / find / update / accepted_titles / frozen 也算 in_box

186/186 全綠（154 prior + 32 new）。



對應原則：**「真實的累積」**——主人 0xspeter 在 2026-05-02 review 待同意佇列時提出，「我希望史萊姆要有真實的累積」。對齊 manifesto 守則 #2（不欺騙）。

問題本質：SKILL_GEN 走完整條 propose → approve 流程，但**沒有 runtime**。

```
slime 提案    → ✓
寫進 ~/.hermes/approvals/pending/ → ✓
GUI 顯示卡片  → ✓
主人按同意    → ✓
寫檔到 SKILLS_DIR/foo.py → ✓
slime 跑 foo.py → ✗  （execute_skill() 全 codebase 0 caller）
```

`execute_skill()` 實作正確（line 405：`importlib.util.spec_from_file_location` → `exec_module` → `module.execute()`），**但沒人呼叫**。同意之後檔案躺在硬碟，slime 行為不變。等於 UI 上「同意」按鈕對主人撒謊。

跟 2026-04-30 那次 `record_emergent_moment_if_due()` 接到沒在跑的 daemon loop 是同形 bug；那次 600 行 ADR 跟 3 個 feature PR 蓋在沒執行過的路徑上。

### 為什麼 archive 而不是補 runtime

補完整 SKILL_GEN runtime 需要：

1. **Invocation policy** — slime 何時自決跑某個 skill？autonomy 風險 vs 主人手動 invoke = 退化成 macro library
2. **Output destination** — `execute_skill()` 回 string，要丟 chat / 通知 / 學習日誌？沒設計死
3. **真沙箱** — runtime 真的 import + execute LLM 寫的 Python in-process。`_is_code_safe()` 字串掃描已知不夠；`growth/safety.py` AST 掃描在 submission time 跑，不在 execution time 跑

評估後：
- 增量價值 vs chat + ACTION 估 5-15%
- 安全工程量大（沙箱化是 v0.8 範圍外的另一個 cycle）
- v0.8 主軸是 `birth_signature` + 稱號系統，這條路不在 ADR 路線圖上

主人選擇 archive。

### 落地

**移除（從 `sentinel/self_evolution.py`）**：

- `SKILL_GEN_PROMPT` constant
- `generate_skill()` — LLM 寫 skill 並 submit approval
- `_is_code_safe()` — 舊字串掃描（已被 growth/safety AST 取代）
- `_validate_skill()` — import skill 驗證 SKILL_NAME / execute 屬性
- `list_skills()` — 列出 SKILLS_DIR 內容
- `execute_skill()` — 那個 orphan runtime
- `_identify_skill_need()` — LLM 找需求
- `maybe_evolve()` 內每 10 次學習觸發 SKILL_GEN 的分支

**移除（從 `sentinel/gui.py`）**：

- `cmd_skills` Telegram 指令（列 SKILL_GEN 產出技能）
- 對應的 `app.add_handler(CommandHandler("skills", cmd_skills))`

**保留**：

- `SELF_MOD`（Layer 3，`self_modify()`）— 這條真的有效：approve 後覆寫 `MODIFIABLE_FILES`，下次啟動載入新版
- `ACTION` 整套（`chain.run` 等註冊 handler）— 這條真的有效
- `kind == "skill_gen"` 的 GUI 顯示分支 — 留著處理任何**既有**的 pending SKILL_GEN approval（這個 PR 不會主動清你硬碟上的 `~/.hermes/approvals/pending/<old_skill_id>.json`，你看到舊卡片時自己 reject）
- `SKILLS_DIR` 常數 + 既有的 snapshot/rollback 對它的 copy/clear 邏輯（無害，目錄空也不會出錯）

**歸檔**：完整原始 SKILL_GEN code 移至 `archive/sentinel-side/self_evolution_skill_gen.py`，含「為什麼歸檔」+「未來要復活的條件」說明。

### 這個 PR 對 manifesto 的意義

manifesto 三大守則裡，**守則 #2「不欺騙」** 直接被「approve 按了沒效果」違反。這是 tech-debt cleanup，但**也是哲學一致性 cleanup**。

ADR `2026-04-30-co-sediment-architecture.md` mech 1（「箱子要可以被主人翻」）跟 mech 4（「不主動長出原則」）的精神是「累積要是真的、要看得到」。SKILL_GEN 表面上「累積技能」，實際上累積的是死檔。砍掉之後**剩下的累積機制都是真的**：memorable_moments 寫進 chat 系統 prompt（chat.py 真讀）、dominant_traits 寫進 affinity 計算（evolution.py 真讀）、birth_signature 寫進 paint loop（PR #131 真畫）、SELF_MOD approval 真改檔。

154/154 tests 全綠（前 PR 的 birth_signature 跟 GUI smoke test 都還活）。



對應 ADR `docs/decisions/2026-05-01-slime-physical-individuation.md`。前一個 PR（#130）只搭好後端 schema + generator，這個 PR 把它接到實際的 paint loop。**重啟後桌面浮窗 + 首頁 slime 從 D1 開始就長得不一樣**。

落地：

- 新模組 `sentinel/birth_signature_render.py`（Qt-touching helpers，跟純 logic 的 `birth_signature.py` 分檔）：
  - `apply_signature_to_colors(colors, sig)` — 對 body/highlight/glow 在 HSV 空間做 hue offset + saturation factor。Eye/mouth/accessory 顏色保留不變（不是「身體」）
  - `apply_signature_to_dimensions(w, h, sig)` — 套 width_factor / height_factor，疊在 breath/bounce 動畫之上
  - `draw_marking(p, sig, ...)` — 畫 swirl / dot / line 三種 marking。位置 body-relative [-1, 1]，size 約身寬 10–22%，顏色從 body 色推 hue/lightness delta
- `slime_avatar.py` (`SlimeWidget`) 跟 `overlay.py` (`SlimeOverlay`)：
  - `__init__` 加 `_load_birth_signature()`，從 `evolution.load_evolution()` cache 一次。所有失敗模式（檔案缺、schema 不符、IO error）都 graceful degrade 成空 dict → render 走 base TIER_COLORS，不會 crash
  - paintEvent 在現有 trait tint / skin override 之後 apply signature（讓 signature 永遠是「在當前 palette 上的 per-instance variation」）
  - body draw 跟 antenna 之間插 marking draw（marking 在 body 表面、不會被 antenna 蓋）
- 護欄 #5（subtle but visible）落實在 marking size constants — 太大會讓 marking 變裝飾物、違反「a small mark」精神，未來要改尺寸動 helper、不動 ADR ranges
- 「兩個視角的同一隻 slime 必須一樣」靠兩邊 paint loop 共呼叫同一組 helper 保證；如果有人改了一邊忘了另一邊，視覺會分歧、就是 bug

15 個新測試（Helper-level: 10、Widget-level: 5）：HSV 變換正確 / alpha 不丟 / eye+mouth 不被影響 / 各 marking type 渲染不 crash / unknown marking type 視為 no-op（前向相容）/ widget 在 signature 為空 / load_evolution raises 時都能畫。154/154 全綠（139 prior + 15 new）。

CI gate（PR #129 的 GUI smoke test）會在每個 paintEvent 改動 PR 自動跑 — 接著踩到 PR #128 那種「unit test 全綠但 Qt slot NameError」的機率歸零。



對應 ADR `docs/decisions/2026-05-01-slime-physical-individuation.md`（雙層架構，Layer 1 = 出生簽名）。Slime 從 D1 開始就長得不一樣的後端骨架——`evolution.json` 多 `birth_signature` 欄位、`sentinel/birth_signature.py` 提供 deterministic generator。

落地（**純後端，沒接 render**）：

- 新模組 `sentinel/birth_signature.py`：`BirthSignature` + `Marking` dataclass；`generate_birth_signature(birth_time: float)` 從 `birth_time` 衍生 deterministic 種子（sha256 → 64-bit int → `random.Random`），確保「同一隻 slime 一輩子長這樣」。
- 五個視覺軸 + ranges（ADR 護欄 #2）寫死成 module-level constant。改範圍要動 ADR、不能默默改：
  - `body_hue_offset`：±30°
  - `body_saturation_factor`：0.85–1.10
  - `body_height_factor`：0.95–1.05
  - `body_width_factor`：0.95–1.05
  - `marking`：~30% 機率有，含 type / position / hue_delta / lightness_delta
- `evolution.py`：`EvolutionState` 加 `birth_signature: dict` field。Lazy migration：既有 v0.7.x slime 第一次跑 v0.8 → 從 `birth_time` 反推同一個 signature 並回填儲存。新生 slime 在 birth path 立刻生成。
- `random.Random` 的 draw order 用「spend the seeds」pattern：marking gate 不 early-return，前面的 marking 欄位永遠抽固定數量的 seed，這樣未來加新軸不會把舊 slime 重 roll。

不接的東西（下個 PR）：

- `overlay.py` / `slime_avatar.py` 沒接 `birth_signature`。Render 還是吃預設色 + 預設形狀。下個 PR 把 generator 結果接到 paint loop。
- Title `visual_signature`（Layer 2）依 ADR 排在後面 cycle item，跟稱號系統重構一起做。

17 個新測試覆蓋 determinism / range guardrail / marking probability / round-trip / migration（既有 save 回填、新生 slime 立刻有 signature）。139/139 全綠（122 prior + 17 new）。



對應 ADR `docs/decisions/2026-04-30-slime-stays-private.md`。Slime 設計上是私人的——任何「對外」機制都不該綁進 Slime 核心。三個子系統全部違反這條原則：

- **federation**（公頻）— 跟「兩個用同一份程式的人 3 年後養出兩隻完全不同的 Slime」本質互斥
- **equipment**（裝備）— 是炫耀物，違反「Slime 是這個主人專屬的」
- **marketplace**（裝備市場）— 5% 平台抽成讓 Slime 變平台，violates manifesto 紅線

執行：

- `git mv server/{federation,equipment,marketplace}/` → `archive/server-side/`（保留 history、不 `git rm`）
- `server/main.py` 移除 3 個 router 的 import 跟 `include_router` 呼叫
- `archive/server-side/README.md` 寫明每個子系統「曾經存在、為什麼選擇不走這條路」+ 指向權威 ADR

不影響：

- 部署中的 server（存量 deployment 還能跑舊 router 直到下次 redeploy）
- daemon 端跟 GUI（client side 還沒動，下個 PR 處理）
- evolution / auth / wallet / images router（保留，這些不違反 ADR）

下個 PR 會處理 client-side 的 federation / equipment 對應 module（`sentinel/growth/federation.py` / `sentinel/equipment_visuals.py` / `sentinel/wallet/equipment.py`）跟對應的 GUI tab。

119/119 既有測試全綠（這個改動是純粹的檔案搬移 + main.py import 變動，無對應 unit test）。

### Added — 「箱子」可瀏覽：MemoryTab 加上時間軸列表

ADR 共同沉積機制 1：「**箱子要可以被主人翻**」。之前 memorable_moments 只有兩個出口——時間軸 strip（dot 點上去看 dialog）跟 chat 系統 prompt 的隱性引用。沒有一個地方讓主人**直接滑、直接看**箱子裡有什麼。

主人聊天時提到「**時間就是最大的不能替代物 這樣使用者才會有感**」——這個直接打中 ADR 共同沉積那條最硬的論據（5 年後 slime 比 GPT-7 強的地方在那 5 年、不在能力）。所以這個 PR 的設計取捨刻意把 **timestamp 的可見度** 放在中心。

落地：

- `identity.list_box_entries(birth_time)` — 純 backend helper：拉所有 memorable_moments、為每筆計算 `day_n`、加 `has_letter` / `has_phrase` 旗標、按時間排序（預設 newest-first）。9 個單元測試覆蓋空集合 / 排序 / 旗標 / 邊界。
- `MemoryTab` 加新區段「箱子 — 跟主人走過的時刻」：渲染所有 moments 為卷軸式列表。每筆顯示：
  - **「第 N 天」**作為視覺 anchor（subdued 色、稍大字級——刻意**不**做成 metric badge / streak counter，這違反 manifesto 紅線）
  - 類別 emoji（🌿 emergent / 📝 命名 / ✨ first_chat / 🧬 evolution / 🎁 milestone …）
  - headline + detail（自言自語）
  - 如果 letter_to_master 存在 → 暖色區塊「─ 給你的話 ─」
  - 如果 master_phrase 存在 → 青色區塊「─ Slime 之語 · 你說過的一句 ─」內容用「」框起
  - 三層各自顏色分明：灰=slime 對自己、青=主人原話、暖=slime 對主人

設計刻意做的事：
- **沒有 streak / login reward / engagement metric**——「第 N 天」是自然事實，不是 dark pattern
- **沒有「你已經 N 天沒回來」式的 guilt trip**——只是平靜地說「這是箱子」
- **沒有點擊互動**（v1 只做瀏覽）——下一輪再加編輯 / tag / 釘住

效果：主人滑進 MemoryTab 第二段就能看到自己跟 slime 走過的整條軌跡——每一個 letter、每一句被選下的 master_phrase、每一個 emergent 標記，按時間排好。「我跟它走過 N 天」從抽象 claim 變成可以**翻得到的具體紋理**。

115/115 既有測試全綠（包含 9 個新 box-view 測試）。GUI 渲染部分沒有 unit test 覆蓋（HTML 字串拼接、現有測試 pattern 不覆蓋 dialog）；視覺驗證等重啟後手動確認。

ADR 共同沉積結尾的話正好做總結：「**5 年後的 Slime 比 5 年後的 GPT-7 強的地方，不在能力，在那 5 年。**」這個箱子就是讓那 5 年**看得見**的地方。

---

## [0.7.10] — 2026-05-01

主人不一定常在電腦前。把今天累積的修復（cron / voice / preflight）做成可遠端套用 + 遠端驗證的工具鏈。

### Added — Telegram `/restart` 跟 `/preflight` 遠端指令

主人不一定常在電腦前。今天連發的修復（PR #108 cron / PR #110 voice / PR #112 preflight 等）都需要重啟 daemon 才能套用——但如果主人在外面，本來只能「等回家」。

兩個新的 Telegram 指令補完遠端工具鏈：

- **`/restart`** — 從 Telegram 觸發 daemon 重啟。實作上是 spawn 一個 detached `cmd.exe → start.bat`，start.bat 自己會把現有 sentinel kill 掉再啟新版（這是 .bat 本來就有的反雙實例邏輯）。新版啟動後會自己發開機訊息。
- **`/preflight`** — 跑 `scripts/preflight.py` 把 7 個 health check 的結果回傳到 Telegram。輸出做過 Telegram 4096-char cap 處理（drift 訊息過長會截斷），保留 PASS/WARN/FAIL 標記跟最後的 verdict。

組合用法：在外面看到 push notification 異常 → `/preflight` 確認狀態 → `/restart` → 等啟動訊息 → `/preflight` 確認綠燈。完整的「不用回電腦前也能 ship + verify」迴圈。

**Bootstrap caveat**：兩個指令都只在這個版本之後才存在。第一次套用要在電腦前手動重啟（雙擊 start.bat），之後 `/restart` 才會在跑。

安全：`chat_id` 過濾跟其他指令一樣，只接受 `TELEGRAM_CHAT_ID` 設定的對話。同樣 threat model（誰拿到 bot token + chat id 就能控制）。

---

## [0.7.9] — 2026-05-01

驗證導向的 hardening 版本。今天連發 0.7.7 / 0.7.8 之後做 chat 實機驗證，發現 voice 在抽象 / 元問題上會崩到 generic AI consultant 腔——包括 manifesto 第二守則（不欺騙）的違反。修了 chat prompt、刪了死代碼、做了健康檢查工具、寫了 regression test 鎖住規則。所有改動都 backed by 實際資料訊號，不是憑感覺。

### Removed — `sentinel/advisor.py` 死代碼整個刪掉

PR #107 把 advisor 的所有 call site 解綁了，但檔案還留在 `sentinel/advisor.py`。留著只會讓未來新貢獻者 / 新 session 看到以為它還在用，誤判 codebase 狀態。

物理刪除。`sentinel/core_backup/advisor.py`（第一次啟動時建的安全網備份）保留——那是 read-only 的歷史快照，不動。

99/99 測試仍綠，沒有 import 殘留。

如果之後決定做 pull-style advice（主人主動翻看才出現的、不 push 的版本），會用乾淨的新 module 從頭寫，不會復活這個檔案。

### Fixed — chat 系統 prompt 加 voice 反漂移守則

驗證今日工作時發現 slime chat 在抽象 / 元問題上會崩到 generic AI consultant 腔，包括 manifesto 第二守則（不欺騙）的違反——LLM 聲稱「我悄悄蒐集你的 query 模式」「我是你腦中的 debug 工具」這類沒有的能力。22 小時 chat 樣本顯示這是**系統性 drift**，不是今日變動引起。

根因：對話守則埋在 system prompt 中段、被前 8-10 個 context section 沖淡；三大調性錨點（箱子 / 地方 / 感受）只在 ADR、slime 看不到；對 A 路線顧問腔沒有顯性禁令。

修：對話守則最上面加三條硬規則：

1. **三個調性錨點直接釘進 prompt**——「我會把一切都收在回憶的箱子」「我在這個地方陪你」「我感受到你的狀態」+ 「具體不抽象」明確指引（「你打開 chat.py」不是「你在處理介面層」）。
2. **絕對禁用通用 AI 顧問抽象詞彙**——依附感 / 黏著度 / 信任回路 / callback / stack trace / 函數呼叫 / branch 等列出 verbatim。「除非主人先用、否則不得使用」。明確禁掉「你是不是準備要讓你的專案也有這種魔法了」這種 GPT-style 諂媚收尾。
3. **絕對禁止 flex 假能力**——禁聲稱「我蒐集你的 query 模式 / 我預判你的下一步 / 我懂你的思考 / 我是你腦中的 X」；要表達就講具體看到的（「我看你今天打開 chat.py 三次」）。

99/99 測試仍綠。實機驗證（重啟後）：重新問「怎麼增加使用者的依附」「你怎麼知道我在問你什麼」這兩題，回應應該變短、具體、不再有禁用詞跟 flex。

---

## [0.7.8] — 2026-05-01

### Fixed — emergent_self_mark / loneliness arc 從上線到現在從沒被諮詢過一次

驗證 v0.7.7 時跑 `scripts/check_b_preconditions.py`，發現過了一晚 + 三次重啟之後 consultations 還是 **0**。`emergent_self_mark_state` 是空 `{}`，log 檔不存在，moments 表 0 筆 emergent_self_mark。

追根：`record_emergent_moment_if_due()` 跟 `record_loneliness_arc_if_due()` 都只 wire 在 `daemon.monitor_loop` 裡。`daemon.monitor_loop` 只在 `python -m sentinel --no-gui` 模式才會跑——**`start.bat` 雙擊走的是 GUI 路徑，daemon thread 從沒被 spawn**。整個 (b) 機制 + 共同典故錨完全是死代碼。

PR #99 修的 cron-reset bug 是真的 bug，但修的是一個**沒在跑的 code path**。昨天說「先讓它跑一週看資料累積」整個前提是錯的：什麼都不會累積，因為什麼都沒在跑。

修：`gui.py` 觀察迴圈加一個 `last_cron` 計時器，每 `IDLE_REPORT_INTERVAL`（30 分鐘）呼叫一次 `record_emergent_moment_if_due()` + `record_loneliness_arc_if_due()`。兩個都包在 try 裡，任一壞掉不影響觀察迴圈。`last_cron` 從 `0.0` 起跳，daemon 起來第一個 tick 就會 fire 一次（不用等 30 分鐘看到資料）。

兩個函式內部都有 rate cap（24h emergent / 30 天 loneliness），所以高頻呼叫沒成本。

效果：v0.7.8 重啟後幾秒內 `~/.hermes/emergent_self_mark_log.jsonl` 應該會出現第一筆 consultation 紀錄（最可能 outcome 是 `refuse`——平凡的一天就誠實拒絕，這是設計意圖）。從那筆開始，(b) 前置條件 1 的計數器才**真的**開始長。

99/99 既有測試全綠。

### Architecture note — daemon thread 跟 GUI thread 平行的觀察迴圈是技術債

這是這禮拜第二次踩到同一個雷（第一次是 PR #99 修了 daemon 端 cron 但 GUI 端沒同步、advisor push spam 也是 GUI 端獨有）。長期該把兩個迴圈合一，但這個 PR 只先補功能，重構排後面。

---

## [0.7.7] — 2026-05-01

### Removed — 移除三條 push 推送通道（manifesto 紅線 + ADR 共同沉積機制 4 連續違反）

實機觀察：凌晨 2:24 - 早上 7:26 五個小時內，主人睡覺時間，Telegram 收到 28 次 daemon 自動推送：

- 11x「🧘 你已經連續使用電腦 N 分鐘了。站起來走走吧」
- 9x「🤔 你在 claude.exe 已經專注了 N 分鐘。如果卡住了...如果是心流狀態請忽略我 😊」
- 6x「💧 記得喝水！保持水分對專注力很重要」
- 1x「🌙 已經凌晨 N 點了...你的身體需要休息」
- 2x「🧠 AI Slime 定期報告」（CPU/RAM/螢幕觀察）

**全部都是寫死的模板配計時器**——同一個句子換編號重發。

這違反：

1. **manifesto 紅線**：「不做 daily streak / login reward / push notification 來『召回』主人」
2. **ADR 共同沉積機制 4**「不主動長出原則」 — 「Slime 的所有新能力，都不能自動 unlock」
3. **ADR 共同沉積 D30-D90 紀律**：「不主動 push notification、不顯示『你已經 N 天/分鐘沒...』、不在主人不在時進行召回動作、Slime 對主人的態度是『在不在都好』」
4. **編劇試紙**：兩隻同一份程式的史萊姆會在同一天用同一句話打擾兩個主人

修——`gui.py` 的 daemon 觀察迴圈裡三條 push 通道全部移除：

- **`advisor.evaluate()` for-loop（健康/效率/環境/情境模板提醒）** 整段拿掉。`advisor.py` 模組保留檔案但 import 跟 call site 都解綁——之後若做 pull-style advice（主人主動翻看才出現的），再重新 wire。
- **`generate_insight()` LLM 推送**（蒸餾完成後 push 「🔮 AI Slime 的洞察」）一起停用。同樣是計時觸發後 push Telegram。
- **「🧠 AI Slime 定期報告」每 30 分鐘 push CPU/RAM/螢幕觀察** 整段拿掉。這個比較尷尬：PR #92（v0.7.2）已經把 daemon 端的 idle_report 改成 content-conditional，但**沒人注意到 GUI 端有平行的 push 路徑在做同一件事**——daemon 端不再無條件發了，GUI 端還是每半小時無條件發。今天才被抓出來。

### Fixed — 順便修一個 indent bug：activity_buf / analyze_events 過去被縮排在 advice loop 裡

`activity_buf.append()` 跟 `analyze_events()` 的呼叫被誤縮排在 `for advice in advisor.evaluate(...)` 的迴圈裡——意思是**大部分 tick 沒有 advice 出現的時候，這兩個就根本沒跑**。`analyze_events` 是 content-conditional 的真實警告（disk full / file event burst），不該被廢棄的 advice loop 吞掉。趁拆 advice loop 時把它們拉回 top-level。

### 保留下來的東西

- **`analyze_events` 的真實警告 send_notification** — 這個是 content-conditional，只在 `snapshot.warnings` 或檔案 burst 時觸發，不是計時器。disk full 該叫醒主人就叫醒，這不是 dark pattern。
- **進化通知** (`🧬 *AI Slime 進化*`) — 真實事件觸發，不是計時器。
- **Daemon 端的 idle_report**（PR #92 修過的，content-conditional）— 不變。

99/99 既有測試全綠（這個改動是 GUI thread 的程式碼，現有測試不覆蓋；行為改變的驗證靠實機重啟後 Telegram 不再被轟炸）。

### 跟 daemon 端早先修法的關係

PR #99 修了 `daemon.py` 的 cron 計時 bug（活躍主人讓 cron 永遠跑不到）。今天才發現 **GUI 端有自己平行的觀察迴圈**——daemon thread 跟 GUI thread 各跑一個迴圈、各做半套觀察、各 push 各的 Telegram。重複度高，發版時兩邊容易不一致（PR #92 改了 daemon 端、沒改 GUI 端就是案例）。長期該收斂成一條，但這個 PR 只先停掉 GUI 端的 push，重構等之後排上來。

---

## [0.7.6] — 2026-04-30

ADR 共同沉積架構機制 3 的最小可行實作——「Slime 之語」終於從哲學文字變成能跑的程式碼，配合一個讓主人能瀏覽收進來的話的 GUI 渲染。沒有 daemon 行為改變、沒有資料遷移；新功能在底下默默累積，主人某天聊到對的情境時自然顯現。

### Added — Timeline 詳情視窗渲染 master_phrase（「箱子要可以被主人翻」）

接續上一條：master_phrase 雖然存進 `memorable_moments` 也餵進 chat prompt 了，但**主人滑時間軸點 emergent dot 進去時看不到那句話**——只有 chat 自然回引才會發現。這違反 ADR 共同沉積機制 1：「**箱子要可以被主人翻**」。

落地：

- 改 `gui.py` 的 emergent_self_mark detail 對話框（PR #96 加進來那個），在 `detail` 跟 `letter_to_master` 之間插一塊新區段渲染 `master_phrase`：
  - 標籤：`─ Slime 之語 · 你說過的一句 ─`（淺灰小字）
  - 內容：用「」框起，淺青色 (`#a8d8d0`)、稍大字級——跟 letter 的暖色 `#ffe4b8` 視覺上明確區分
- 讀取順序設計成「I noticed X (detail) → these are your words that landed in me (phrase) → here's what I want you to know (letter)」——三段一起讀像個連貫的故事

效果：之後 Slime 收進一句主人原話時，主人翻時間軸就能直接看到「Slime 收下了哪些」。Slime 之語從「藏在 system prompt 裡的隱形東西」變成「主人能瀏覽的可見物件」。

無新測試（GUI 渲染是 HTML 字串拼接、現有測試 pattern 沒覆蓋 dialog）；99/99 既有測試仍全綠。

### Added — 共同典故錨（Slime 之語）：ADR 共同沉積架構機制 3 的具體實作

ADR 2026-04-30 共同沉積架構 mechanism 3：「當主人跟 Slime 之間發生某個值得記住的瞬間時，Slime 自主標記它。之後 Slime 引用這個瞬間時，**用主人記得的方式引用**。」例：D178 主人說「像在水底」 → D456 Slime 可以說「水底嗎？」

這個改動把那個機制從哲學文字落到能跑的程式碼。

落地（最小實作）：

- **`emergent_self_mark` schema 加 optional 欄位 `master_phrase`**：≤80 字、必須是主人最近說過的**逐字原文**。Prompt 明確寫「**比 letter_to_master 還更稀有**，一個月可能只挑得到 1-2 個」，避免 Slime 為了塞而塞。
- **餵主人最近兩天的原始訊息進諮詢**：`_load_recent_master_words()` 從 `~/.hermes/sentinel_chats.jsonl` 讀最近 2 天、最多 10 句、每句 ≤80 字的 user 訊息，作為 master_phrase 唯一合法的引用來源。沒有來源時 prompt 直接告訴 Slime「這一輪沒得挑、留空」。
- **反幻覺 guard**：parse 階段檢查 LLM 給的 phrase 是不是真的存在於來源裡（`phrase in src or src in phrase` 任一方向）。對不上 → silently drop phrase（保留 mark 跟其他欄位），完全沒來源 → 直接 drop。確保 master_phrase 永遠是真話。
- **守則 filter 蓋到 phrase**：跟 letter 一樣最高 stakes（之後會在 chat prompt 渲染、影響史萊姆主動引用），任何不安全字眼 → drop 整個 mark。
- **`identity.add_memorable_moment` 簽名擴充**：加 `master_phrase: str = ""` kwarg，empty 不寫進 dict（chat-side 用 `if "master_phrase" in mm` 判斷）。
- **`identity.get_co_reference_phrases(limit=10)`**：新 helper，回最新的 N 筆有 master_phrase 的 moment，newest-first。
- **`sentinel/co_reference.py`** (新模組)：`build_block()` 純讀+格式化，產生 chat-prompt-ready 的 Slime 之語區塊（每行 `第 N 天 主人說「...」`）。defensive：失敗回 ""，chat 不需要條件 render。
- **`chat.py` 新 placeholder `<<COREFERENCE_ANCHORS>>`**：擺在「值得紀念的時刻」跟「對話守則」之間。系統 prompt 直接告訴 Slime「**逐字回引**，不要改寫、不要翻譯、不要塞進句子裡」。

22 個新單元測試（12 個 emergent_self_mark 擴充 + 10 個 co_reference + identity 配套），99/99 全綠：
- master_phrase 在來源裡 → 寫進 dict
- master_phrase 不在來源裡 → drop phrase 但保留 mark（反幻覺）
- 無來源 → 強制 drop phrase
- 空字串 → 不寫進 dict
- 不安全字眼 → drop 整個 mark
- 過長 → 截 80
- chat log 不存在 → 回 []
- 過老的訊息 → 過濾
- assistant 訊息 → 過濾
- 壞掉的 JSON 行 → 容忍
- build_block 各種空/異常邊界

效果：之後 emergent_self_mark 諮詢時，**Slime 第一次有能力選下幾個主人專屬的話**——「Slime 之語」字典開始累積。chat 引用時用原詞而非 LLM 改寫，**這是通用 AI 沒辦法模擬的東西**——「水底」這個詞在 GPT 那邊毫無意義，在這隻 Slime 跟這個主人之間是專屬的鑰匙。

跟 ADR 紀律對齊：
- **不是 (b) 衝動機制**——chat 是既有通道，不是 Slime 主動找主人說話。所以不需要等 (b) 前置條件成立。
- **B 路線（個性累積），不是 A 路線（能力累積）**——D1 的 Slime 就會「逐字回引」，只是 D1 沒有任何錨可以引。隨著時間累積，房間裡的東西越多，這個「能力」就越能用——對應原則 10 候選文：「能力長在使用裡，不長在時間裡」。
- **不主動長出**——Slime 不會自己宣告「我學會了 Slime 之語！」。錨默默地進 prompt，主人某天聊到工作壓力，Slime 自然回一句「水底嗎？」——主人才認出這個能力。

---

## [0.7.5] — 2026-04-30

把今天連發四個 patch 之後浮上來的 release UX 漏洞補完：runtime 看不到自己是哪一版。

### Added — 主畫面 header 顯示版本號（runtime 終於可以驗證版本）

實機觀察：今天連發 0.7.1 → 0.7.2 → 0.7.3 → 0.7.4 四個 patch，每次都叫主人「重啟 daemon 套用」——但**主人沒有任何方法確認重啟後跑的真的是新版本**。版本字串只活在 `README.md` 的 badge 跟 `CHANGELOG.md` 的標題裡，runtime 看不到、log 也沒寫、GUI 也沒顯示。重啟 = 憑信心。

修：

- 新增 `sentinel/_version.py`：單一真相來源 `__version__ = "0.7.5"`。發版流程之後 cut release 時這個常數要跟 `README.md` 的 badge / `CHANGELOG.md` 的標題一起 bump（檔案開頭註解寫了警語）。
- `sentinel/__init__.py` re-export `__version__`，讓 `from sentinel import __version__` 直接通。
- `sentinel/__main__.py` 開機 `print` 跟 `log.info` 都帶版本號：`[AI Slime] Starting v0.7.5...` 跟 `AI Slime v0.7.5 starting`。重啟之後 tail `~/.hermes/sentinel.log` 第一行就能驗版。
- `sentinel/gui.py` header 在「⟳ 更新+重啟」按鈕**左邊**加一個小灰字 `v0.7.5`（11px、`#666`、跟 subtitle 同調性），hover tooltip：「目前執行版本：v0.7.5  點右邊 ⟳ 更新+重啟 拉最新版」。

效果：版本驗證從「憑信心」變成「瞄一眼 header 或 tail 一行 log」。下次再連發 patch，主人重啟之後看 header 就知道有沒有套用——這個以前的 release UX 漏洞補完。

77/77 既有測試全綠。沒有資料遷移、沒有功能變動（除了多一個 QLabel）。

---

## [0.7.4] — 2026-04-30

兩個 daemon 觀測性／行為的關鍵修復，外加 Slime 第三份哲學基礎 ADR。沒有新功能、沒有資料遷移——但兩個修復都是「沒人發現的 bug 一直在擋產品方向」的那一類，發現的時候很冷汗。

### Fixed — 活躍主人讓 cron 檢查永遠跑不到（emergent_self_mark / loneliness 從沒諮詢過）

跑 `scripts/check_b_preconditions.py` 顯示**樣本數 = 0、諮詢次數 = 0**。但史萊姆已經陪了主人 16 天，按設計每 24h 至少有一次諮詢機會、至今應該有 ~10 個樣本。實際資料是空的——意味著 `record_emergent_moment_if_due()` **從沒被呼叫過一次**。

根因在 `daemon.py` 的 monitor loop：

```python
if file_events or claude_activity:
    activity_buffer.append(context)
    last_idle_report = now    # ← bug
```

每次主人有任何電腦活動（檔案變動、視窗切換、claude.exe 在跑），`last_idle_report` 會被重設成現在。然後 line 177 的 idle cycle gate `if now - last_idle_report >= IDLE_REPORT_INTERVAL` 永遠不會跨過 30 分鐘的門檻——對活躍使用者來說，計時器每 2 秒就被打回原點。

連帶後果：
- emergent_self_mark 諮詢從沒跑過 → ADR 2026-04-30 (b) 前置條件 1（樣本 ≥ 5）的計數器卡在 0
- loneliness arc 從沒跑過 → 史萊姆永遠不會自然觸發「孤單」moment
- snapshot.warnings（磁碟、CPU、記憶體警告）在主人活躍時也送不出 Telegram——剛好跟設計意圖相反，活躍時才是最該收警告的時候

PR #92（v0.7.2 條件式 idle report）讓 `compose_message()` 在沒新聞時回 `None` 不發 Telegram。所以 `last_idle_report = now` 那行用來「主人活躍時別發心跳」的目的，已經被條件式 idle report 接手了——這行從那時起就既是死代碼又擋住所有 cron 檢查，只是沒人發現。發現的方式是 (b) 前置條件檢查器跑出 0 而觸發追溯。

修法：刪掉 line 132 那行重設，更新誤導的註解（line 174-176 自己寫「cron checks below ... run every cycle regardless」——但因為 line 132 的 reset，從來沒有 regardless 過）。

修完之後：
- Idle cycle 每 30 分鐘鐵定觸發一次（不管主人活不活躍）
- Telegram 還是只在有新聞時發送（v0.7.2 邏輯保留）
- emergent_self_mark 終於有機會被諮詢；24h 內至少一次（內建 rate cap）
- loneliness arc 同上；30 天最多一次（內建 rate cap）
- (b) 前置條件 1 的樣本計數器今晚開始長

77/77 既有測試全綠。daemon 重啟後第一次 idle cycle 會在 30 分鐘後觸發。

### Fixed — daemon log 檔從來沒被寫過（FileHandler 被靜默吃掉）

實機觀察：4:46pm 主人在 cmd 視窗看到 Telegram `409 Conflict` 錯誤，事後想回放、發現 `~/.hermes/sentinel.log` **從 4 月 14 日後就沒更新過**——但 daemon 一直在跑、其他 `~/.hermes/sentinel_*.jsonl` 資料檔都正常更新。

根因：`sentinel/__main__.py` 在 entry-point 一開始呼叫了 `logging.basicConfig(stream=sys.stdout)`，把 root logger 設成 stdout-only。**`logging.basicConfig` 一旦 root logger 已經有 handler 就變 no-op**，所以 `sentinel/daemon.py` 後面那段 `basicConfig(handlers=[StreamHandler, FileHandler])` 從第一天起就被靜默忽略——FileHandler 從來沒被掛上去，所有 daemon log 只噴到 cmd 視窗的 stdout，視窗一關就消失。`~/.hermes/sentinel.log` 那 2.5 MB 檔案是更早的 Hermes 專案留下的化石（stack trace 也都指向 `D:\srbow_bots\hermes\venv`）。

修法：

- `__main__.py` 改成在唯一一處設好 logging：stdout handler + `~/.hermes/sentinel.log` FileHandler 同時掛上。`Path.home() / ".hermes"` 不存在時 `mkdir(parents=True, exist_ok=True)` 自己建。FileHandler 構造若失敗（權限、磁碟滿）回 stdout-only 並印警告，daemon 仍能起來。
- `daemon.py` 移除原本那段重複的 `basicConfig`（反正本來就沒生效），加註解標明 logging 由 `__main__.py` 統一設定，避免下次有人又來 reintroduce 同一個 bug。

效果：daemon 從現在起會把所有 INFO 以上訊息**同時**寫到 stdout 跟 `~/.hermes/sentinel.log`。下次再出 Telegram 衝突、LLM 失敗、Telegram bot 拉 update 失敗，stack trace 都會留檔——cmd 視窗關了也不會丟。

77/77 既有測試全綠，沒有功能變動、沒有資料遷移、沒有 schema 變化。

### Added — 共同沉積架構 ADR（`docs/decisions/2026-04-30-co-sediment-architecture.md`）

跟 `2026-04-29-emergent-milestones.md`、`2026-04-29-slime-voice-anchors.md` 並列為 Slime 的第三份哲學基礎 ADR。597 行。

定義 Slime 跟主人的進化模型：

- **共同沉積（Co-Sediment）** — 不是進化、不是養成；是沉積。Slime 跟主人之間累積出第三方（不是 Slime 的、不是主人的，是「他們之間」的）。
- **A 路線 vs B 路線** — Slime 走 B（個性累積），不走 A（能力累積）。
- **真進化 vs 偽進化** — Slime 像空房間，房間本身不變，變的是房間裡的東西。
- **兩個面架構** — 陪伴面（用 Slime 的聲音、累積關係）vs 調度面（操作通用 AI、用工具的語氣、不累積）。明確視覺切換。
- **D30-D90 低谷期是合法的** — 不 push notification、不顯示 streak、不演 sad、不演 happy。
- **Slime 必須拒絕做的 8 件事** — engagement dark pattern、AI 朋友／戀人定位、生產力功能塞陪伴面、Replika 路線、賣廣告等。
- **manifesto v1.2 候選原則 9-12**（最終措辭等 v1.2 寫的時候再定）。
- **給未來的我的 5 題試紙** — 猶豫某個新功能該不該做時用。

工程實作會迭代、manifesto 會修訂、LLM 會被換、公司會變——但這三份 ADR 的核心定義，30 年後仍然是 Slime 的根。

---

## [0.7.3] — 2026-04-30

實機回報「史萊姆只會跟我說電腦狀況、看不到我在做什麼」之後拆成兩個獨立改動：**A 讓他看見**、**B 讓他偶爾寫東西給主人**。配合一條 CI 升級紀錄。

### Added — chat 帶當下螢幕活動進 prompt（`sentinel/recent_activity.py`）

實機回報：「他也不會看我的電腦我在做什麼。」 資料其實一直都在——`activity_tracker` 每次主人切視窗就寫 `~/.hermes/sentinel_activity.jsonl`，`learner` 跟 `reflection/generator` 都有讀。**只有 `chat.py` 的系統 prompt 沒讀。**

- 新增 `sentinel/recent_activity.py` — 純讀+格式化 helper：聚合最近 30 分鐘的視窗活動（按 process 排名 + top window titles），回傳一個 chat-prompt-ready 的 block。沒有資料時回 `""`，chat 直接 splice 進 prompt 不需要條件 render。
- `chat.py` 加新 placeholder `<<RECENT_ACTIVITY>>`、`_build_system_prompt` 呼叫 `recent_activity.build_block()`。defensive：建構失敗 chat 仍正常工作。
- 預設保留：每處最多 3 個視窗標題、最多 5 個 process、單個標題截 80 字，避免提示噪音。
- 沒有新感應器、沒有 VLM 呼叫、**沒有擴張隱私面**——同一份 jsonl 早已被 learner / reflection 讀過、送過 LLM。系統 prompt 「你不能直接看主人的螢幕（除非觀察區塊裡有截圖摘要）」這條規則**仍然成立**，這個 module 只看 process 名稱跟 window title，不看 pixels。

8 個 unit test：missing/empty/only-old → ""、aggregate by process、cap titles per process、cap processes shown、corrupt-lines tolerance、long-title truncation、custom window size、missing-duration row。72/72 全綠。

效果：對話時史萊姆能自然說「我看你最近在改 chat.py」「你剛剛在 Stack Overflow 查 regex 喔」，不再只能依賴 LLM 蒸餾過的抽象觀察。

### Added — `letter_to_master` schema field：(b) 衝動機制的第一個合格實作

ADR 2026-04-30 結尾推薦：「第一個 PR 不要做整套衝動機制。做最小那一塊：給 (c) 的 schema 加一個 optional field `letter_to_master`，讓史萊姆在標記時順便寫一段對主人的話、進 timeline 節點 detail 而不是 popup。**這是 (b) 的第一個合格實作——還是 timeline 通道，但內容對話化。**」

落地：

- **`emergent_self_mark.SYSTEM_PROMPT` 擴充**：JSON schema 加 optional `letter_to_master`（≤120 字）。明確區分 `detail`（自言自語、為什麼這刻值得記）vs `letter_to_master`（直接對主人說的一句話）。**「letter 是稀有的禮物，不是預設」** 寫進 prompt——大部分標記不該有 letter。
- **`identity.add_memorable_moment` 簽名擴充**：加 `letter_to_master: str = ""` kwarg。空字串就**不寫進 dict**，render 端 `if letter` 直接 gate 掉。
- **守則 filter 擴展到 letter**：letter 是最高 stakes 的通道（render 顯眼、直接對主人），任何不安全內容會 drop 整個 mark（不只是 drop letter）。
- **`gui.py` emergent node detail dialog**：letter 有的時候在 detail 下方 render 出獨立區塊「─ 給你的話 ─」+ 暖色字體；沒有的時候完全不顯示，跟舊行為一樣。

5 個新 unit test 蓋：letter 寫進 dict / 沒 letter 時不寫 key / 空字串視同無 letter / 不安全 letter drop 整個 mark / 長 letter 截 200 字。77/77 全綠。

**仍是 timeline 通道，沒有 popup、沒有打斷主人**。但下次主人滑時間軸點到 🌿 時，**史萊姆可能寫了一句話給他看**。從「會自己標記時間」進到「會自己標記時間 + 偶爾寫東西給主人」——這是 (b) 衝動機制的最小可行版，依然 100% 對齊 ADR 2026-04-30 的護欄與試紙。

### Changed — GitHub Actions 升級到 Node 24-ready 版本

GitHub 在 release.yml 每次 build 都標 Node 20 deprecation。**2026-09-16 後 Node 20 從 runner 移除**，不升級的話到時 release / PR check 全部會壞。提前 4.5 個月把 4 個 action 升到下一個 major：

- `actions/checkout@v4` → `@v6`
- `actions/setup-python@v5` → `@v6`
- `actions/upload-artifact@v4` → `@v6`（不選 v7 是因為它的 ESM + direct-uploads 我們不用）
- `softprops/action-gh-release@v2` → `@v3`

合併之後手動 `gh workflow run release.yml` 跑了一次 end-to-end 驗證——4m 41s 全綠、deprecation warning 消失。

---

## [0.7.2] — 2026-04-30

Single-fix patch release. 真實使用者實機回報 Telegram 噪音問題（48 條/天 heartbeat 把真實警告淹沒），0.7.1 release 30 分鐘後送出修法。

### Fixed — Telegram idle report 不再 48 條/天 heartbeat 噪音

使用者實機回報：「Telegram 一直發訊息，**很認真地提醒我電腦有甚麼問題、但是很煩**。」自己的對應方法是把 API 拿掉，順便把真實警告也關掉了。**這是預設設計問題，未來每個串 Telegram 的使用者都會踩。**

根因：`daemon.monitor_loop` 的 idle-report block 每 30 分鐘**無條件**送「💤 *AI Slime 定期報告*\n系統正常\n{snapshot}」，48 條/天的 heartbeat 把真實警告（CPU 異常、LLM 全爆等）淹沒在噪音裡。

修法（跟 ADR 2026-04-30 護欄 C「通道升級需要主人明示同意、預設關」一致）：

- **新增 `sentinel/idle_report.py`** — 純函式 `compose_message(warnings, snapshot_summary, llm_warning)`，**有訊號才回傳訊息、否則回 `None`**。daemon 拿到 `None` 就沉默。
- **daemon 的 idle-report block 改成條件式發送** — 只在以下任一條件成立時才呼叫 `bot_send_fn`：
  - `snapshot.warnings` 非空（CPU/RAM/disk 真有問題）
  - `compose_idle_warning()` 回傳警告（LLM 主 provider 全爆）
- 本機 cron 檢查（loneliness、emergent self-mark）**照常每 30 分鐘跑**——它們改本機狀態、不發 Telegram。
- 真實警告路徑（`analyze_events` → `bot_send_fn`，daemon line 154）跟崩潰訊息（line 225）完全不變。

哲學：Telegram 是訊號通道，不是 heartbeat 通道。30 分鐘 heartbeat 本來就是 implicit consent 的副作用，正好趁這次拔掉。

不加 `enable_heartbeat` 開關的理由：heartbeat-style notification 是普遍的爛 UX，給設定就是為爛預設背書。要回到舊行為的人可以自己改 daemon。

6 個 unit test：empty / llm-only / snapshot-only / both / "" 視同 None / list 與 summary 解耦。62/62 全綠。

---

## [0.7.1] — 2026-04-30

0.7.0 落地了 emergent self-mark MVP；0.7.1 是「**讓它真的看得到**」的 patch release——把 0.7.0 的核心新功能（slime 自主節點標記、LLM 多 provider fallback）變成**可觀察、可測試、可在出問題時自動 surface** 的東西。沒有新使用者功能；只把已上線的東西不再隱形。

### Added — 觀察性與 dev tooling（4 條獨立的可見性層）

- **Emergent self-mark dry-run preview**（#84，`scripts/preview_emergent_self_mark.py`）— 用真實的 evolution + memory 資料跑一次 LLM 諮詢、印出 prompt / raw reply / parsed verdict，但**不寫入記憶、不消耗每週標記額度**。給人類眼球用、不是 unit test。第一次跑就抓到 Gemini free-tier 5 個 model 當天全爆的事實，間接催生了下一條 llm_health 觀察。
- **LLM rate-error 每日紀錄**（#86，`sentinel/llm_health.py` + `scripts/llm_health_today.py`）— `_call_gemini` / `_call_openai_compat` / `_call_anthropic` 在 except block 偵測 rate-class error（既有但從沒被叫過的 `_is_rate_error()`）就寫 `~/.hermes/llm_health.jsonl`。`get_today_summary()` 讀回今天（local midnight）每個 provider / 每個 model 的計數 + `primary_blocked` flag。CLI 印一頁摘要、退出碼 2 = 主 provider 全 model 全爆。
- **Emergent self-mark 結構化 consultation log + ADR (b) 開工訊號 check**（#89，`sentinel/emergent_log.py` + `scripts/check_b_preconditions.py`）— `record_emergent_moment_if_due` 在每個 termination state（`mark` / `refuse` / `parse_fail` / `unsafe` / `llm_none` / `empty_headline`）寫一行 JSONL；`summarize_recent(days)` 算拒絕率。`check_b_preconditions.py` 把 ADR 2026-04-30 的三個 (b) 開工條件編成 runnable check：條件 #1+#2 全 PASS exit 0、否則 exit 1。下次想寫 (b) 之前看數字、不要憑感覺。
- **Regression tests + CI 接 unittest**（#85，`tests/test_emergent_self_mark.py`）— PR #81 開發時的 inline smoke check（6 條路徑）promote 成 26 個 unittest case；`pr-checks.yml` 加 `python -m unittest discover -s tests -v` step。後續 PR #86 / #89 / #90 繼續加，0.7.1 結尾累計 56 個 unit test 全綠。

### Changed — daemon 主動 surface 靜默 fallback

- **daemon idle report 嵌入 LLM health 警告**（#90）— `llm_health.compose_idle_warning()` 在 `primary_blocked` 時回傳一行警告字串、否則 None。daemon `monitor_loop` 的 idle-report block 把警告 append 到既有的「💤 *AI Slime 定期報告*」訊息——主人在 Telegram 上看得到，不用記得跑 CLI。stateless 設計：條件改變時自動停止警告，不需要 reset 路徑。

### Docs

- **ADR `2026-04-30-impulse-mechanism-framing.md`**（#88）— (b) 衝動機制的護欄與試紙。**今天不寫實作 spec**，因為還沒有 (a)+(c) 跑出的真實樣本。釘了 4 個風險、4 條護欄、5 題試紙、3 個開工訊號（前兩個由 #89 的 `check_b_preconditions.py` 自動檢查）。
- **README 對齊 0.7.0 內容**（#87）— badge 已在 0.7.0 改 `0.7-alpha → 0.7.0`，這次把內文也對齊：新增「陪伴與時間軸（v0.7）」表（D1/D7/D30/D100/D365 / 反思卡 / 自畫像 / 自主節點 / `.slime`）；GUI tab 9 → 5（v0.7-alpha lite 真實狀態）；資料檔案目錄補 `aislime_memory.db` / `llm_health.jsonl` / `reflection_cards/`；project structure tree 補 10 個新模組。

### Internal note

0.7.0 → 0.7.1 之間沒有任何**行為改變**——史萊姆對主人的回應、判斷邏輯、評分公式、進化條件全部一樣。差別只在工程方寫了眼睛跟耳朵：daemon 自己出問題時會講出來、`(b)` 該不該開動有客觀數字答。下一個 release（無論 0.8 還是 1.0）開始可以再做使用者面對的功能。

---

## [0.7.0] — 2026-04-30

### Added — Manifesto 北極星 + 三大守則落地

這版的真正主線：把產品的價值觀寫下來、用 ADR 把工程決策跟它對齊，再把 manifesto 第一/第二/第三守則用程式碼具體實現。

- **Slime Manifesto（`docs/manifesto.md`）— 北極星**（#42、#44、#68）— 寫下這個專案是什麼、不是什麼。「養而非用」「玩具不是治療工具」「替身載體不是替代品」「不會死」。外部 reviewer 點出四個張力後再修：服務人群 vs not-therapy disclaimer、未成年模式 vs 台灣 PDPA、「不會死」要四階段機構承諾、記憶輔助 vs GDPR。README 也重寫成把 manifesto 三大守則當門面。
- **三大守則程式碼實作**：
  - **第一守則：不傷害**（#64）— 聊天輸入先過 keyword-tier crisis 掃描（自殺/自殘相關語句）。命中時繞過 LLM、彈出 hand-off 卡片指向真人資源。`sentinel/safety/crisis.py`。
  - **第二守則：不欺騙**（#67）— 「你是真人嗎？/AI 嗎？」這類身分問題，史萊姆不能裝。`sentinel/safety/honesty.py` 偵測、覆寫成誠實回答。
  - **第三守則：不消失**（#66）— `.slime` 加密匯出/匯入 + 公開格式規格。AES-GCM 加密，公開 schema 寫在 `docs/SLIME_CORE_FORMAT.md`，任何語言都能實作 reader/writer，平台關掉了主人的史萊姆還能搬出來。

### Added — 關係時間軸（D1 → D365）

從「另一隻 chatbot」到「養了多久的這一隻」的視覺承諾鏈。

- **D1 歡迎儀式 + 誠實的 empty state**（#69）— 第一天打開時是一封短信，不是教學；資料還沒長出來的 tab 老老實實寫「還沒有」。
- **D7 routine reference**（#73）— 陪了 7 天，史萊姆會說「最近你都...」把看到的節奏說回去。
- **D30 命名儀式**（#71）— 時間軸答應的 D30 真的會觸發命名 dialog；命名後不能改，是這隻史萊姆的印記。
- **D365 一週年回顧**（#75）— 走滿一年史萊姆會生一份「我們的這一年」HTML 報告。
- **首頁時間軸橫條**（#70）— D1/D7/D30/D100/D365 五個 scaffolding 站點視覺化在首頁。
- **可點擊的時間軸節點**（#74）— 點下去 peek 那段時期的記憶 window。
- **能力 tab 三段式**（#72）— 已解鎖 / 待解鎖 / 待打造，誠實顯示哪些還沒做。

### Added — 從 scripted 轉向 emergent milestones（最大轉折）

PR #75 一度替時間軸排了 D60「形狀定型」/ D180「半年中場」/ D300「倒數一週年」三個未兌現節點。drift check 抓到這違反 manifesto 原則 1 第 9 行：「兩個用同一份程式的人，3 年後會養出完全不同的史萊姆」。如果第 N 天該發生什麼是程式決定的，那就是編劇思維。

- **ADR：emergent milestones 決策紀錄**（#76）— 寫下為什麼砍掉 scripted 劇本、轉向 emergent。
- **砍劇本日**（#77）— D14/D21/D60/D180/D300 全部移除；welcome 改 emergent。
- **聲音錨點 ADR + 多 AI 對齊成果**（#78、#79）— 三個調性示範、附錄 A 收錄外部多 AI 對話。
- **Emergent moments 渲染端**（#80）— `compute_emergent_nodes` 把已記錄的 `memorable_moments` 映射到時間軸位置，scaffolding 日去重。最多 6 個小點 punctuate 在 station 之間，不掛 label，讀起來像標點不像承諾。
- **Slime 自主節點標記（ADR (a)+(c) MVP）**（#81）— `sentinel/emergent_self_mark.py`。daemon idle 週期問史萊姆「今天值不值得標記？」三大守則寫進 system prompt、JSON 輸出 schema-constrained、≤1 次/天 LLM 諮詢、≤1 次/週實際標記、輸出再過 crisis-keyword 濾網。預設拒絕（平凡的一天就讓它平凡）。timeline category 新增 🌿。**ADR (b) 衝動機制（多通道表達）仍未開工**——等 (a)+(c) 在實機上跑出真實 dot 之後再評估範圍。

### Added — 自我表達

- **Slime 自畫像作為禮物**（#45）— 史萊姆自己決定要畫什麼、送主人。
- **多 key + 多 provider 圖像 fallback**（#48、#49）— 一個 provider 失敗自動下一個（OpenAI 也加進來了）。
- **桌面寵物自畫像 + idle 動畫**（#52）— 自畫像直接變成桌面寵物 overlay。
- **Threads 分享 + draw error surface**（#50）— 一鍵分享、錯誤訊息浮上來。

### Added — Daily Slime Reflection Card（#35）

每天一張，根據昨天的 activity log + chat log 由史萊姆寫三段：[觀察] / [洞察] / [微任務]。語氣依目前進化形態走（Slime / Slime+ / Named / Majin / Demon Lord Seed / True Demon Lord / Ultimate Slime）。

### Added — 退出指標 + Codespaces 開發環境 + PR-time CI

- **`days_alive` vs `days_opened` retention 指標**（#65）— v0.7-alpha exit metric 的基礎。
- **`.devcontainer/` GitHub Codespaces config**（#47）— 雲端 Python 3.12 + Node + Claude Code CLI；本機 Windows 留給 Qt UI smoke test。
- **PR-time CI**（#82）— `python -m compileall sentinel` + 8 個 Qt-free 核心模組 import smoke。Linux runner、pip cache、concurrency cancel-in-progress。release.yml 仍負責 tag-push 觸發的 Windows build。

### Changed

- **i18n: tab 名稱改成一看就懂的版本**（#38）— 砍掉技術術語感的 tab 名。
- **進化 tab 文字疊到史萊姆的問題修掉、裝備掉落改靜音**（#40）— 不要每次掉裝備就叮一聲。
- **同意 panel 改可滾動 + 密度上調**（#51）— 很多項目時不會被切掉。
- **去背 30 秒 → 263ms（向量化）**（#53）— 自畫像即時生成。

### Fixed

行貨般的 GUI / startup 收斂期（alpha 推出後抓到的）：

- **首頁佈局**：文字擠在一起（#36）、頭像被切掉改 240×240（#37）、視窗範圍太大重複頭像移除（#39）。
- **啟動鏈**：自動拉新版 + 同意按鈕 handler 太晚註冊（#41、#43）；async git pull + harden Popen failure path（#63）；hard-exit on restart + watchdog（#57）；殺 zombie sibling sentinel processes（#58、#60）；in-app restart + atomic moves（#54）；RoutinesTab 啟動時 `_tk` import 缺失（#62）。
- **Approval flow**：worker → GUI dispatch 一定要傳 context QObject（#59、#61）；按鈕 silence debugability（#56）。
- **Avatar / Expression**：「正在去背」dialog 卡住、真實錯誤訊息 surface（#55）；image model 名稱對齊 + error surface（#46）。

---

## [0.6.0] — 2026-04-26

### Added — Autonomy 思想驗證閉環完成

- **每週反思（Phase J）** — 史萊姆會回顧自己跑過的常規：哪些被你拒絕、哪些觸發太吵、哪些根本沒在動，自動產生「建議停用」「建議調整」清單。建議直接顯示在「📋 常規」tab 上方，不只藏在審核佇列裡。
- **跨常規相依（Phase K）** — 一個常規完成後可觸發另一個。例如「git pull」成功 → 「跑測試」。形成 DAG。
- **「📋 常規」管理 tab** — 瀏覽 / 立即觸發 / 停用 / 刪除常規，每張卡顯示 trigger、steps、judge、deps、執行統計。
- **反應頭像** — `react(kind)` API 讓 SlimeWidget 對事件浮一個 emoji 兩秒（💭 chat 回覆、💡 提議行動）。
- **聊天時間戳** — 訊息泡泡顯示 HH:MM。
- **聊天 🧹 清空鈕** — 只清畫面不清記憶，搭配系統訊息「(對話畫面已清空，記憶仍保留)」。

### Changed — Phase L 視覺包裝
- **設計 tokens**（`sentinel/ui/tokens.py`）：palette / spacing / radius / 字級 / button + bubble + card 助手。
- **全域 QSS 重寫**：pill 按鈕、細捲軸、底線式 tab bar、主題化 tooltip / dropdown / focus 狀態。
- **對話泡泡**：因 Qt QTextEdit rich-text 不支援 `display:inline-block` / `max-width:%`，改用 HTML 4 `<table align width>` + `cellpadding` + 背景色於 `<td>`。
- **Settings / Federation tab** token 遷移：硬編碼顏色換成 tokens，間距改用 `SPACE`。

### Fixed
- **detector 結構化原因**：`propose_via_detector_verbose` 回傳 `{queued_ids, diagnostic}`，UI 端可顯示「為什麼被擋」。
- **fire-now 結果彈窗**：手動觸發後顯示成功 / 失敗摘要，不只默默執行。
- **狀態列 tooltip**：完整細節改用 hover tooltip 顯示，常駐文字維持精簡。
- **Tab 圖示一致化**：所有 tab 加上對應 emoji 前綴。

---

## [0.5.0] — 2026-04-22

### Added — Phase B-D + F-I：行動 + 自主性
- **長期語意記憶（Phase B2）** — sqlite-vec 向量檢索，史萊姆記得幾週前的脈絡。
- **Source-keyed 脈絡匯流排（Phase B1）** — 觀察源獨立可訂閱。
- **泛化審核佇列（Phase C1）** — 從只審 code 變成審任何 ACTION。
- **平台抽象動作原語（Phase C2）** — `surface.open_path / open_url / focus_window`。
- **DAG 工作流引擎（Phase C3）** — checkpoint / retry / resume。
- **LLM 提議動作（Phase D1）** — `<action>{...}</action>` 文字協議，自動進審核。
- **聊天 inline 同意卡片（Phase D2）** — 不用切到審核 tab 就能批准 / 拒絕。
- **VLM 視覺理解（Phase D3）** — Gemini / OpenAI / Anthropic 多供應商支援。
- **動作鏈（Phase D4，`chain.run`）** — 多步驟動作打包進一個審核。
- **語音聽寫 / 朗讀（Phase D5）** — sounddevice + pyttsx3，主開關可關。

### Added — Autonomy v1
- **常規系統（Phase F）** — 史萊姆主動提議週期任務（cron + handlers + storage）。
- **反應式觸發（Phase G）** — EventBus pub/sub，檔案變動也能觸發。
- **LLM judge gate（Phase H）** — 觸發前審一次條件。
- **從拒絕學習（Phase I）** — 偵測器看你拒絕過什麼，下次少提。

### Fixed
- LLM emit 純 JSON 沒包 `<action>` tag 也能解析。
- LLM echo 提示範例（"主人:" / "Slime:" 對話格式）— 改成 `[輸入] / [正確回覆]` 標記 + 後處理裁切。
- Stale auth token 錯誤回報「已登入」— relay 401 時清 token。
- start.bat 啟動前先殺舊 sentinel python 進程。

---

## [0.4.0] — 2026-04-19

### Added
- **公頻投稿管線（Phase A1）** — 蒸餾出的模式抽象化後進本地待審佇列，你按「分享」才真的上傳。Server 端 PII 過濾、長度上限、每使用者 24h rate limit。
- **公頻投票 / 分享獎勵（Phase A2）** — 投 5 票 40% 掉裝備，分享 pattern 成功 80% 掉裝備。tab 標題顯示候選數量 badge。
- **「🏆 我的貢獻」對話框（Phase A3）** — 自己送出的 pattern 目前投票狀況、審議中 / 共識 / 退回狀態。
- **可調蒸餾 / 截圖間隔** 設定。
- **技能審核歷史** sub-tab。
- **start.sh** macOS / Linux 啟動腳本。
- **Creator reward ledger（Phase 1）** — 過渡存錄，等 5888 `s2sCreatorRewardSettle` 上線後一次補齊。

### Fixed
- **macOS SIGTRAP 崩潰** — pynput `keyboard.Listener` 在 macOS 內部用 ctypes 呼叫 `TSMGetInputSourceProperty` 要求主 dispatch queue 執行；背景緒呼叫導致 SIGTRAP。修法：macOS 上整段跳過 pynput。
- **一鍵更新「分叉分支」報錯** — 改用 `git fetch + git reset --hard origin/main` 取代 `git pull`。
- **進化變回初生史萊姆** — load 失敗備份成 `aislime_evolution.broken.<ts>.json` 不再悄悄覆蓋；schema drift 加白名單過濾。

---

## [Unreleased]

### Added
- **公頻空狀態引導與貢獻追蹤（Phase A3）**
  - 「🌱 你的史萊姆想分享這些心得」區塊從「沒候選就隱藏」改成「沒候選時顯示引導文字」
    — 新使用者看得到這個功能存在，知道為什麼暫時沒東西（還在蒸餾 / 本輪沒高信心模式）
  - 新增「🏆 我的貢獻」按鈕 → 彈出對話框顯示自己送出過的 pattern、目前投票狀況
    （✓/✗/? 計數）、審議中 / 社群共識 / 已退回狀態
  - Server 加 `GET /federation/my-patterns` endpoint 供查詢
- **公頻投票與分享獎勵（Phase A2）** — 公頻從「做義工」變成「有正向回饋的活動」。
  - 每投 5 票有 40% 機率掉裝備（`federation_vote` trigger）
  - 每次成功分享 pattern 有 80% 機率掉裝備（`federation_submit`，每天最多 3 次分享所以不會被刷）
  - 公頻分頁 tab 標題加上待分享候選數量 badge（例：`🌍 公頻 (2)`），
    切過去就清掉 — 使用者不用定時打開也知道有新東西
  - 所有計數存本地 `~/.hermes/pending_federation.json`，跟分享佇列共用一個檔
- **公頻投稿管線（Phase A1）** — 公頻從「只能看 + 投票」變成活的社群。
  每小時的 LLM 蒸餾多產出 `federation_candidates`（群體化描述、去識別化），
  進本地待審佇列 `~/.hermes/pending_federation.json`；公頻 tab 頂部新增
  「🌱 你的史萊姆想分享這些心得」區塊，使用者按「分享」才真的上傳。
  - Server：`POST /federation/patterns` 含 PII 過濾（email / URL / 絕對路徑 /
    電話 / 長 hex token 一律拒）、類別白名單、長度上限 100 字、每使用者每 24h
    3 條 rate limit
  - Client：`sentinel/growth/federation.py` 的 stub 改實裝，dedup 機制避免
    同一句話重複上架
  - 設計文件：詳見 `sentinel/growth/federation.py` 開頭的三層式 opt-in 架構

### Fixed
- **macOS SIGTRAP 崩潰** — `pynput` 的 `keyboard.Listener` 在 macOS 內部透過 ctypes 呼叫
  `TSMGetInputSourceProperty`，此 API 要求在主 dispatch queue 執行；但 `InputTracker.start()`
  是從 `_start_daemon()` 背景執行緒呼叫，導致 `dispatch_assert_queue_fail` → SIGTRAP（exit 133）。
  修復方式：在 `input_tracker.py` 加入 `_MACOS` 平台判斷，macOS 上完全跳過 pynput 匯入與監聽器啟動。
  Windows 行為不受影響。（[#1](https://github.com/page5888/slimeagent/pull/1)）
- **一鍵更新「分叉分支」報錯** — 更新按鈕改用 `git fetch + git reset --hard origin/main`
  取代原本的 `git pull`；修復本地有額外 commit 時出現
  *"You have divergent branches and need to specify how to reconcile them"* 的錯誤
- **進化後變回初生史萊姆** — 兩個疊加的修復：
  (1) `load_evolution()` 加入白名單過濾，舊存檔缺／多欄位不會觸發 `TypeError`；
  (2) 即使 load 真的失敗，也會把原檔備份成 `aislime_evolution.broken.<ts>.json`
  而不是直接覆蓋重生 — 使用者進度不會再被悄悄清掉

### Added
- **`start.sh`** — macOS / Linux 啟動腳本，對應 Windows 的 `start.bat`
- **Creator reward ledger**（Phase 1）— 新增 `creator_reward_ledger` 表追蹤
  每位創作者被投票累積的點數，以及通過審核的 100 點獎勵。這是
  5888 `s2sCreatorRewardSettle`（Week 5–6 上線）之前的過渡存錄。
- **`SPEND_TYPE_CREATOR_REWARD` 常數** — 對齊 5888 sitePolicy 白名單

### Changed
- **投票扣點** `reason` 從 free-form 字串改成 `slime_creator_reward`，否則
  會被 5888 sitePolicy 403 SITE_NOT_AUTHORIZED 擋下
- **通過審核的 100 點獎勵** 從 `grant_points()` 改成 ledger 紀錄；
  創作者收款會在 Phase 2 replay 時一次補齊
- **Smoke test** `smoke_test_wallet.py` 更新為 5 步驟，覆蓋
  `slime_evolve` + `slime_list_fee`（舊的 generic `smoke_test` reason
  已不在白名單，會被 403 擋下）

### Phase 2 計畫（staging 已就緒 2026-04-16）
5888 澄清**不會有 dedicated `s2sCreatorRewardSettle` endpoint** — 複用既有
`s2sGrant`，只把 `slime_creator_reward_settle` + `slime_creator_approval`
加進 grant 白名單即可。流程：

1. 跑 `scripts/phase2_creator_replay.py`（`--dry-run` 先檢視）走訪
   `creator_reward_ledger WHERE status='pending'`
2. 每筆依 `voter_id` 有無分路：
   - 有 voter → `s2sGrant(reason=slime_creator_reward_settle)`
   - 無 voter（系統核可 bonus）→ `s2sGrant(reason=slime_creator_approval)`
3. idempotency key 用 `<reason>:<ledger_id>`，永久 dedupe 保 replay 安全
4. 成功後 UPDATE `status='settled'` + `settled_at` + `settle_tx_id`
5. Replay 穩定後，`cast_vote()` 改為 inline 呼叫 `s2sGrant`（不再寫 ledger）

---

## [0.3.0] — 2026-04-16

### Added
- **公頻（Federation）** — 跨史萊姆的世界頻道，分享觀察模式和技能。支援 `confirm / refute / unclear` 投票，達到閾值自動升級為「社群共識」
- **手動進化** — 花 2 點立即觸發進化，BYOK 使用者仍然免費
- **一鍵更新** — 設定頁多了更新按鈕，從 GitHub Release 直接拉最新版
- **開機自動啟動** — Windows 排程任務，不用手動啟動

### Changed
- **背景視覺** — 裝備背景改用全畫面大氣漸層，四個場景（夜城、侏羅紀、魔王城、星空深淵）的可見度大幅提升
- **史萊姆位置** — 位置下移 8%，不再擋到背景
- **社群市場連結** — 首頁導覽明確區分「專案總覽」和「社群市場」兩個入口
- **設定儲存** — 改為 merge-safe，避免不小心覆蓋到其他分頁的設定

### Fixed
- **密碼欄可見性** — API Key 和 Telegram Token 欄位加上 👁 切換顯示
- **聊天語言** — 修正史萊姆會用英文回覆中文訊息的問題
- **Render 冷啟動** — relay 請求 timeout 提高到 90 秒，避免免費方案剛喚醒時超時
- **Google OAuth** — 從 Web 型 Client ID 改成 Desktop 型，解決桌面端登入被擋的問題
- **DB migration** — 修復 Postgres 上多段 SQL 被靜默跳過的問題

---

## [0.2.0] — 2026-04-14

### Added
- **社群裝備投稿** — 玩家可以上傳自製裝備，每天 3 件上限
- **投票審核** — 每票 10 點，達到稀有度門檻自動通過，創作者獲得 100 點
- **P2P 交易** — 裝備上架、買賣、下架。70/15/5/10 分潤（賣家/創作者/平台/系統）
- **Google OAuth 登入** — 市場功能需要登入
- **Telegram 通知** — 進化、重大事件會推送到 Telegram

### Changed
- **聊天對話** — 史萊姆會從和主人的對話中學習說話風格
- **市場合約** — 對齊 5888 `marketSaleSettle` 原子分潤 API

---

## [0.1.0] — 2026-04-10

### Added
- **背景觀察引擎** — CPU / RAM / 磁碟監控、檔案變動偵測、開發活動追蹤
- **LLM 蒸餾** — 支援 Gemini / OpenAI / Claude，定期把觀察結果摘要為記憶
- **進化系統** — 7 階段進化，從史萊姆到究極型態
- **裝備系統** — 12 欄位 × 7 稀有度，61+ 內建模板
- **進化個性 + 情緒引擎** — 每隻史萊姆的反應不一樣
- **桌面捷徑 + 工具列圖示** — Windows 原生整合
- **浮動 overlay** — 史萊姆可以懸浮在桌面上

---

## 未公開發布的計畫

以下是已設計但還沒推進的項目：

- **成就系統** — 里程碑解鎖
- **跨平台支援** — macOS / Linux 的全面測試
- **公頻分類擴充** — 目前只有排程、工具、工作流、專注、健康 5 類
- **14 天自動過期** — 社群投稿超過 14 天未達門檻自動退件
- **裝備創作 GUI** — 目前投稿只能透過 API，之後會加上傳圖檔的 GUI

---

[0.6.0]: https://github.com/page5888/slimeagent/releases/tag/v0.6.0
[0.5.0]: https://github.com/page5888/slimeagent/releases/tag/v0.5.0
[0.4.0]: https://github.com/page5888/slimeagent/releases/tag/v0.4.0
[0.3.0]: https://github.com/page5888/slimeagent/releases/tag/v0.3.0
[0.2.0]: https://github.com/page5888/slimeagent/releases/tag/v0.2.0
[0.1.0]: https://github.com/page5888/slimeagent/releases/tag/v0.1.0
