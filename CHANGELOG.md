# Changelog

所有重要變更都會記錄在這裡。格式基於 [Keep a Changelog](https://keepachangelog.com/)。

---

## [Unreleased]

### Fixed
- **macOS SIGTRAP 崩潰** — `pynput` 的 `keyboard.Listener` 在 macOS 內部透過 ctypes 呼叫
  `TSMGetInputSourceProperty`，此 API 要求在主 dispatch queue 執行；但 `InputTracker.start()`
  是從 `_start_daemon()` 背景執行緒呼叫，導致 `dispatch_assert_queue_fail` → SIGTRAP（exit 133）。
  修復方式：在 `input_tracker.py` 加入 `_MACOS` 平台判斷，macOS 上完全跳過 pynput 匯入與監聽器啟動。
  Windows 行為不受影響。（[#1](https://github.com/page5888/slimeagent/pull/1)）

### Added
- **`start.sh`** — macOS / Linux 啟動腳本，對應 Windows 的 `start.bat`

### Fixed (continued)
- **一鍵更新「分叉分支」報錯** — 更新按鈕改用 `git fetch + git reset --hard origin/main`
  取代原本的 `git pull`；修復本地有額外 commit 時出現
  *"You have divergent branches and need to specify how to reconcile them"* 的錯誤
- **進化後變回初生史萊姆** — `load_evolution()` 原本直接展開 JSON 全部欄位到
  `EvolutionState(**data)`；只要版本升級後 schema 新增了欄位，舊存檔就會觸發
  `TypeError` 被 `except` 靜默吞掉，重建初生狀態。修復方式：加入白名單過濾，
  只傳 dataclass 認識的欄位，新舊存檔版本互相相容（已同步自上游修法）
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

### Phase 2 計畫（Week 5–6，等 5888 `s2sCreatorRewardSettle` 上線）
1. 寫 replay script 走訪 `creator_reward_ledger WHERE status='pending'`
2. 每筆以 `slime_creator_reward_settle:{ledger_id}` 當 idempotency key 呼叫 settle endpoint
3. 確認成功後將該 row 標為 `status='settled'`，寫入 `settle_tx_id`
4. Replay 完成後，`cast_vote()` 改為 inline 呼叫 settle（不再寫 ledger）

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

[0.3.0]: https://github.com/page5888/slimeagent/releases/tag/v0.3.0
[0.2.0]: https://github.com/page5888/slimeagent/releases/tag/v0.2.0
[0.1.0]: https://github.com/page5888/slimeagent/releases/tag/v0.1.0
