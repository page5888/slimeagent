# Changelog

所有重要變更都會記錄在這裡。格式基於 [Keep a Changelog](https://keepachangelog.com/)。

---

## [Unreleased]

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
