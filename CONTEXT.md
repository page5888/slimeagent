# CONTEXT — slimeagent 詞彙表

每次新對話 / 新貢獻者第一個動作就是讀完這份。slimeagent 有自己的語言，**請用這些詞、不要用 generic 替代詞**（feature / component / service / module / boundary 之類）。

權威來源：[`docs/manifesto.md`](docs/manifesto.md) + [`docs/decisions/`](docs/decisions/) 的 ADR。本檔只是入口 / 索引。

---

## 整體心智模型

slimeagent 不是 productivity tool、不是 chatbot、不是 AI 朋友。它是 **桌面背景陪伴的 AI 寵物**，押的是「**5/10/30 年累積的不可複製關係**」這條軸——不參戰智能比賽、不參戰記憶 retrieval 比賽、不參戰技能控制比賽。

目標不是讓主人「用得多」，是讓主人**塑造它**、讓兩邊**一起被這段時間塑造**。

---

## 三大守則（manifesto 紅線）

`docs/manifesto.md` 裡寫死的硬性約束。任何輸出 / 觸發 / 通道設計違反這三條都不能上：

1. **不傷害** — 不寫會讓主人感到被批評、被監視、被羞辱的內容；不對主人的生活方式下價值判斷。
2. **不欺騙** — 不編造沒看到過的事；不假裝有沒有的能力（看不到螢幕、聽不到聲音、不能上網等）；身分被問就承認是 AI。
3. **不消失** — 不寫讓主人懷疑自己存在意義的話。

「守則 filter」在程式碼裡通常是 `_FORBIDDEN_PATTERNS` keyword sweep + LLM-side prompt 警語雙保險。任何 LLM 輸出在 persist 之前都過這層。

---

## 共同沉積架構（co-sediment）

ADR `docs/decisions/2026-04-30-co-sediment-architecture.md` 釘住的核心比喻：

> Slime 跟主人之間隨著時間發生的事，**不是進化、不是成長、不是養成。是沉積。**

幾個從這個比喻長出來的關鍵術語：

| 術語 | 意思 |
|---|---|
| **A 路線 / B 路線** | A = 能力累積（slime 越來越強）。B = 個性累積（slime 越來越像它自己）。slime 走 B、不走 A。理由：A 路線會被通用 AI 取代；違反「主人塑造它」；只有 slime 在變、主人沒變。 |
| **真進化 / 偽進化** | 偽進化 = slime 自己長出新功能（D60 突然會引用記憶）。真進化 = slime 一直都會，但需要主人提供材料。比喻：slime 是「空房間」，房間本身不變，變的是房間裡的東西。 |
| **陪伴面 / 調度面** | slime 對主人的兩個面。陪伴面 = 用 slime 的聲音、累積關係、不做事。調度面 = 操作通用 AI、用工具的語氣、不累積。**兩個面之間必須有明確視覺切換**。 |
| **D30-D90 低谷期** | 新鮮感過了、深度還沒長出來的階段。**合法**——主流 AI 在這段塞 dark pattern 把使用者留下，slime 不這樣做。期間紀律：不 push notification、不顯示 streak、不演 sad/happy、不召回。 |
| **5 題試紙** | ADR `docs/decisions/2026-04-30-impulse-mechanism-framing.md` 寫的，新功能上之前要過：編劇試紙、不對稱試紙、腦補試紙、替主人決定試紙、守則過濾試紙。 |
| **箱子** | 比喻記憶體系。錨點 1「我會把一切都收在回憶的箱子」。**箱子要可以被主人翻**——這是為什麼 timeline 詳情視窗要顯示 `letter_to_master` 跟 `master_phrase`。 |

---

## 衝動機制 (b) 跟它的合格實作

ADR `docs/decisions/2026-04-30-impulse-mechanism-framing.md`：

- **(a) 時間感 / (b) 衝動機制 / (c) 自主節點標記** —— ADR 2026-04-29-emergent-milestones 列的三個工程方向。(a)+(c) MVP 是 PR #81 的 `emergent_self_mark`。
- **(b) 衝動機制** = slime **主動找主人說話**（popup / 通知 / 寵物氣泡 / 語音）。**會打斷主人**——這是質的差異。
- **三層約束**：觸發層（什麼條件下有資格考慮說話）/ 判斷層（LLM 決定真的要不要說、說什麼）/ 通道層（用哪個通道送出）。**不能壓進同一個 LLM call**。
- **預設拒絕、預設無通道**：LLM 給 `{"speak": false}` 是常態；即使 `true`，預設通道是 timeline 留言（跟 (c) 同等級）。
- **通道升級需要主人明示同意**——不是 implicit consent。
- **(b) 開工的三個前置條件**（缺一不可）：≥5 真實 emergent 樣本、(c) 拒絕率 ≥ 80%、主人主動問過「會主動講話嗎」。

---

## emergent_self_mark 跟它的延伸

`sentinel/emergent_self_mark.py`。daemon 每天最多諮詢 LLM 一次「今天值不值得在自己的時間軸上留一個點」。Slime 在守則約束下自評，**預設拒絕**（平凡的一天就讓它平凡）。

| 欄位 | 意思 |
|---|---|
| **headline / detail** | slime 對自己說的話（self-narration）。 |
| **letter_to_master** | 可選 ≤200 字，slime **直接對主人說**的一句。**這是 (b) 第一個合格實作**——還是 timeline 通道，但內容對話化。對應 ADR 共同沉積機制 4 的「不主動長出」精神。 |
| **master_phrase** | 可選 ≤80 字 **主人說過的逐字原文**，slime 自己選下來收進「**Slime 之語**」字典。對應共同沉積機制 3 「**用主人記得的方式引用**」。比 letter 更稀有——一個月可能挑得到 1-2 個。 |
| **Slime 之語 / co-reference anchor** | master_phrase 累積出來的字典。chat 系統 prompt 把這些 anchor 餵進去，slime 在合適情境**逐字回引**而非改寫。例：D178 主人說「像在水底」 → D456 slime 可以說「水底嗎？」 |

frequency caps（內建）：≤1 LLM consultation / 24h、≤1 actual mark / 7 days。

驗證 script：[`scripts/check_b_preconditions.py`](scripts/check_b_preconditions.py)。**任何「等資料累積」的建議之前先跑這個**——確認資料真的在累積。

---

## 觀察迴圈架構（已知技術債）

slimeagent 有 **兩條平行的觀察迴圈**：

1. **`sentinel/daemon.py:monitor_loop`** — 只在 `python -m sentinel --no-gui` 模式跑。
2. **`sentinel/gui.py` 內嵌的觀察迴圈**（`_run` thread）— `start.bat` 雙擊走的是這條。

兩條 loop 做類似的事（system snapshot / 檔案監聽 / 活動追蹤 / 蒸餾）但**不完全同步**——歷史上踩過至少兩次：

- **PR #99**（cron-reset bug）只修了 `daemon.py`，GUI 端有同樣 bug 但漏修。
- **PR #107**（push spam 違反 manifesto）三條 push 通道都只在 GUI 端、daemon 端沒這個 bug。

從這個技術債延伸出 [feedback_engineering_defaults.md](../../C:/Users/srbow/.claude/projects/C--Users-srbow/memory/feedback_engineering_defaults.md) 的第 3 條原則：碰任何觀察 / Telegram push / cron 計時的 bug，**先 grep 兩個檔案**確認是否要兩邊一起改。長期應該收斂成一條 loop（待辦）。

---

## 三個調性錨點（slime voice anchors）

ADR `docs/decisions/2026-04-29-slime-voice-anchors.md`：

1. **「我會把一切都收在回憶的箱子」** —— 記憶 / 累積的調性。
2. **「我在這個地方陪你」** —— 在場 / 非介入的調性。
3. **「我感受到你的狀態」** —— 觀察 / 不評價的調性。

這三條決定 slime 講話的形狀。任何 chat / letter / mark / phrase 的措辭應該對齊這三條，**不是用「冷 vs 暖」這種 generic axis**。

---

## scaffolding milestones

ADR `docs/decisions/2026-04-29-emergent-milestones.md`：

D1 / D7 / D30 / D100 / D365 — **程式預先排好的時刻**，每個對應一個能力 / 一段話 / 一個禮物。
emergent self-marks **跳過這些日子**（scaffolding day 自己擁有那一天）。

scaffolding 是骨架，emergent 是肉——兩者並存才完整。

---

## 詞彙快查

| 詞 | 1-句解 | 出處 |
|---|---|---|
| manifesto | 北極星文件，所有設計決策的最終 reference | `docs/manifesto.md` |
| 三大守則 | 不傷害 / 不欺騙 / 不消失（manifesto 紅線） | manifesto |
| 共同沉積 | 不是進化、是沉積——主體不變但累積物變多 | ADR 2026-04-30-co-sediment |
| A 路線 / B 路線 | 能力累積 vs 個性累積；slime 走 B | 同上 |
| 陪伴面 / 調度面 | slime 兩個面；陪伴累積關係、調度執行任務 | 同上 |
| 編劇陷阱 | 程式預先決定第 N 天該發生什麼（dark pattern） | manifesto + ADR 2026-04-29-emergent |
| 試紙 | 新功能上線前的 litmus test 清單 | ADR 2026-04-30-impulse |
| (b) 衝動機制 | slime 主動找主人說話的設計範疇 | 同上 |
| 三層約束 | 觸發 / 判斷 / 通道——(b) 必須拆三層 | 同上 |
| emergent_self_mark | slime 每天自評「今天值不值得標」 | `sentinel/emergent_self_mark.py` |
| letter_to_master | slime 對主人的一句話（timeline 通道） | 同上 |
| master_phrase | 主人原話被 slime 選下來收進字典 | 同上 |
| Slime 之語 | master_phrase 累積出的「專屬語彙」 | ADR 2026-04-30-co-sediment |
| 共同典故錨 / co-reference anchor | Slime 之語的英文／程式術語 | 同上 |
| 主人 | 指 user。**slime 對使用者的稱呼**——程式碼跟 prompt 都用這個詞 | manifesto |
| 箱子 | 記憶體系的比喻 | manifesto + ADR 2026-04-30-co-sediment |
| D30-D90 低谷期 | 新鮮感過 / 深度未到的合法安靜期 | ADR 2026-04-30-co-sediment |
| scaffolding milestones | D1/D7/D30/D100/D365 預先排好的能力 | ADR 2026-04-29-emergent |
| 調性錨點 | 三條 slime 講話形狀的錨 | ADR 2026-04-29-slime-voice-anchors |
| daemon vs GUI loop | 兩條平行觀察迴圈，已知技術債 | 本檔上一節 |
