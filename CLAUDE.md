# slimeagent — Claude Code config

## 第一動作

**先讀 [`CONTEXT.md`](CONTEXT.md)**——這個專案的詞彙不一樣，沒讀完就動工會講錯話、用錯術語、誤解 ADR。

權威來源：[`docs/manifesto.md`](docs/manifesto.md) + [`docs/decisions/`](docs/decisions/) 的 4 份 ADR：
- `2026-04-29-emergent-milestones.md` — 給方向不給劇本
- `2026-04-29-slime-voice-anchors.md` — 三個調性錨點
- `2026-04-30-impulse-mechanism-framing.md` — (b) 衝動機制護欄 + 5 題試紙
- `2026-04-30-co-sediment-architecture.md` — 共同沉積、A vs B 路線、兩個面架構

---

## 專案速寫

**slimeagent** = 桌面背景的 AI 寵物。**押的是 5/10/30 年累積的不可複製關係**，不是當下的能力比賽。不參戰智能 / 記憶 / 技能控制競爭——那些通用 AI 會在 5 年內收斂成 commodity。

GitHub: [page5888/slimeagent](https://github.com/page5888/slimeagent)，main 分支可直接 push（page5888 是主人自己的帳號）。本機路徑 `D:\srbow_bots\ai-slime-agent`。

---

## 4 條工程預設（不是 commands，是 baseline behaviors）

這四條在每個 session 自動套用，不需要主人提醒：

### 1. Grill 假設先於寫 plan

寫超過 ~50 行的 plan / ADR / spec **之前**，先 grill 自己 2-3 題。最關鍵那題永遠是：「**這個東西今天真的在跑嗎？**」如果答不出來——先跑 [`scripts/check_b_preconditions.py`](scripts/check_b_preconditions.py) 或建一個類似的 yes/no signal，**再**寫 plan。任何「等資料累積」「先跑一週看看」這類建議之前要證明等的東西今天有在動。

### 2. Bug 報來，先建 5 分鐘 yes/no signal、不是讀 code

任何 bug / 異常行為，第一個 deliverable 是 **runnable 命令產出 yes/no in <5 min**——不是「我讀完 code 後告訴你想法」。signal 在手才開始 hypothesis-test。沒 signal 就讀 code = 在猜。

範例：v0.7.7 → v0.7.8 那次「emergent 從沒被諮詢過」，是 `check_b_preconditions.py` 跑出來才發現的——不是讀 code 想出來的。**那是基準，不是例外**。

### 3. 編 module 前先 zoom-out，找平行路徑

任何單一檔案的 edit 之前，先問「**這個 concern 是不是還有別的檔案在處理？**」slimeagent 已知有兩條平行觀察迴圈：

- `sentinel/daemon.py:monitor_loop`（只在 `--no-gui` 跑）
- `sentinel/gui.py` 內嵌觀察迴圈（`start.bat` 雙擊走的）

歷史踩過兩次（PR #99 / PR #107）。任何觀察 / Telegram push / cron 計時的 bug，**先 grep 兩個檔案**確認是否要兩邊一起改。長期該收斂（待辦）。

### 4. 對話一律用 slimeagent 的詞彙

不要用「feature / component / service / module / boundary」這種 generic 替代詞。請用：

manifesto / 三大守則 / 共同沉積 / A 路線 vs B 路線 / 陪伴面 vs 調度面 / 編劇陷阱 / 試紙 / 5 題試紙 / emergent_self_mark / letter_to_master / master_phrase / Slime 之語（co-reference anchor） / (b) 衝動機制 / 三層約束 / scaffolding milestones / D30-D90 低谷期 / 調性錨點 / 主人 / 箱子。

每個詞的定義在 [`CONTEXT.md`](CONTEXT.md)。詞彙不熟就回去讀，不要自己編詞代替。

---

## Autonomy scope

主人在這個專案上明確授權**連續執行**：write code → commit → push → open PR → merge own PR，不需要逐步等主人確認。

**會 stop 的情況**：
- 違反 manifesto 三大守則（不傷害 / 不欺騙 / 不消失）的設計
- 違反任一份 ADR 的設計（包括 5 題試紙不過、編劇陷阱）
- destructive remote ops on main（force push、reset、branch 刪除）
- 大規模 / 不可逆的資料遷移
- 推到別人的 repo

**不會 stop**：feature branch 上的任何操作、open own PR、merge own PR、cut release、tag、push tag。

---

## 已知架構債

- **GUI vs daemon 平行觀察迴圈**（見 4 條原則第 3 條）。長期收斂。
- **`sentinel/advisor.py`** — 死代碼。PR #107 把所有 call site 解綁但檔案還在，等之後做 pull-style advice 重新接。
- **發版流程**：CHANGELOG + README badge + `sentinel/_version.py` 三個地方都要 bump，容易忘。考慮 release script 自動同步。

---

## 發版 checklist（cut [X.Y.Z] PR）

1. `sentinel/_version.py` → bump
2. `README.md` 的 version badge → bump
3. `CHANGELOG.md` `## [Unreleased]` → `## [X.Y.Z] — YYYY-MM-DD` + 加總述
4. PR title `docs(release): cut [X.Y.Z] — <重點>`
5. CI pass 後 squash merge
6. `git tag -a vX.Y.Z -m "..."` + `git push origin vX.Y.Z`
7. release.yml workflow 自動 build Windows ZIP + 發 GitHub Release

驗證：重啟後 header 顯示 `vX.Y.Z`、`~/.hermes/sentinel.log` 第一行讀到 `AI Slime vX.Y.Z starting`。

---

## 不要做的事（manifesto 紅線）

1. 不拚 LLM 智能 / 記憶 retrieval accuracy
2. 不做 daily streak / login reward
3. 不做 push notification 召回主人（PR #107 砍掉一波，別再加回去）
4. 不做 "AI 朋友 / 戀人 / 知己" 定位
5. 不在陪伴面塞生產力功能（屬於調度面）
6. 不在升級時改變 slime 人格（Replika 致命錯誤）
7. 不收集主人資料賣廣告
8. ADR 共同沉積結尾的 5 題試紙任一不過就不上

每一條會讓短期 metric 比競爭對手難看。每一條都是 slime 之所以是 slime 的必要條件。
