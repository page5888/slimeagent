# Architecture Decision Records (ADR)

每一份檔案是一個**重要轉折**的決策紀錄。

格式：`YYYY-MM-DD-主題.md`，按日期排序。

不是規格、不是 todo、不是會議筆記 — 是**「我們決定了什麼，為什麼這樣決定」**的史料。

---

## 為什麼有這個資料夾

manifesto 是**沉澱物**（最終答案）。  
ADR 是**當下的決策**（為什麼選這條路）。

兩者分開存：
- **manifesto** 寫「Slime 是什麼」 — 不變、緩慢、終局
- **ADR** 寫「為什麼今天往左不往右」 — 即時、具體、可追溯

3 年後回看，manifesto 可能改過 2-3 次，但 **ADR 不應該改** — 它是時光膠囊。  
你那時為什麼這樣決定，就這樣定下來了。

---

## 對 Claude Code 的意義

每個新 Claude Code session 開起來，**最該先讀的就是這個資料夾**。

- 讀 manifesto 知道**目的地**
- 讀 ADR 知道**走過什麼路、為什麼沒走那條**

這樣每個新 session 都不會走回已經明確排除過的方向。

---

## 寫一份新的 ADR 時

1. 檔名：`YYYY-MM-DD-主題.md`
2. 開頭三行：
   ```
   日期、觸發、狀態
   ```
3. 結構建議：
   - **背景**：發生什麼事
   - **抓到的問題**：跟 manifesto / 既有原則的衝突
   - **決定的方向**：怎麼選
   - **要做的事 / 不要做的事**：具體落地
   - **給未來的我自己**：3 年後重看時要記得的東西

4. commit message 格式：`docs(adr): YYYY-MM-DD <主題>`

---

## 既有的 ADR

- [`2026-04-29-emergent-milestones.md`](2026-04-29-emergent-milestones.md) — 從 scripted 轉向 emergent 的時間里程碑
- [`2026-04-29-slime-voice-anchors.md`](2026-04-29-slime-voice-anchors.md) — Slime 調性錨點（具體 vs 抽象，三個親筆示範）
