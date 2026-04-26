# Changelog

所有重要變更都會記錄在這裡。格式基於 [Keep a Changelog](https://keepachangelog.com/)。

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
