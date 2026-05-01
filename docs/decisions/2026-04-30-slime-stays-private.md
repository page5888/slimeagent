# ADR: Slime 是私人的 — 砍除所有對外機制

**日期**: 2026-04-30
**作者**: 0xspeter（決定者）+ Claude（Anthropic，起草整理）
**狀態**: 確立。執行中。
**先行 ADR**:
- `2026-04-29-emergent-milestones.md`
- `2026-04-29-slime-voice-anchors.md`
- `2026-04-30-co-sediment-architecture.md`

---

## 為什麼寫這份 ADR

過去 24 小時連續做了三個砍除決定：

1. **公頻 federation 砍除** — Slime 之間不互通
2. **裝備市場砍除** — Slime 不商業化
3. **裝備系統砍除** — Slime 是個人的，不是炫耀的

這三個決定共用同一條原則，值得統一寫成一份 ADR。

---

## 三個砍除的原始 vision 跟現在哲學的衝突

### 公頻 Federation

**早期 vision**：

讓不同主人的 Slime 之間分享觀察到的模式，形成「智能集合體 / 共同進化」。
網路效應：越多人用 Slime，Slime 群體越強。

**跟新哲學的衝突**：

- 共同沉積架構說「兩個用同一份程式的人，3 年後養出兩隻完全不同的 Slime」
- 如果 A 主人的 Slime 學到的東西能傳給 B 主人的 Slime，B 的 Slime 就有 A 的痕跡，不再純粹是 B 養出來的
- 公頻跟「每隻 Slime 都不同」本質互斥

### 裝備系統

**早期 vision**：

12 欄位 × 7 稀有度的裝備，Slime 在觀察過程中掉落。
社群創作裝備 → 社群投票 → 裝備市場交易 → 創作者抽成。
建立一個 Slime 圍繞的經濟生態。

**跟新哲學的衝突**：

- 裝備是用來展示的（對外的東西）
- 主人的 Slime 戴稀有裝備 = 對其他主人炫耀
- Slime 變成可比較、可交易的物件
- 這跟「Slime 是這個主人專屬的、不是炫耀物」根本衝突

### 裝備市場 + 創作者獎勵

**早期 vision**：

70/15/5/10 分潤（賣家/創作者/平台/銷毀），建立創作經濟。

**跟新哲學的衝突**：

- 平台抽 5% = Slime 變成「平台」
- manifesto 紅線之一：Slime 不是平台
- 商業激勵會讓創作者優化「會賣的裝備」，不是「對主人有意義的裝備」
- 這個結構必然往「炫耀性、稀缺性、社交資本」這條路滑

---

## 共用原則：Slime 不向外

把這三個砍除濃縮成一條原則：

> **Slime 跟主人之間是封閉的、純粹的、個人的。**
>
> **任何「向外」的機制都不該綁進 Slime 核心。**

具體判斷：

- 對其他主人展示 → 砍
- Slime 之間分享 → 砍
- 對市場交易 → 砍
- 對社群投票 → 砍
- 對創作者獎勵 → 砍

---

## 但「分享」不是壞事 — 只是不該綁 Slime

這個原則不是禁止主人交流，只是把交流移出 Slime 系統：

| 場景 | 是否合法 | 為什麼 |
|---|---|---|
| 主人想跟其他主人交流養 Slime 經驗 | ✅ | 主人之間自己社群，不走 Slime |
| 主人手動分享自己 Slime 的故事到 Twitter | ✅ | 主人的權利，Slime 不參與決定 |
| 主人想看別人怎麼養 | ✅ | 看別人發的內容，Slime 之間不互通 |
| 主人手動分享 Slime 的自畫像 | ✅ | 主詞是主人，不是 Slime |
| Slime 自動上傳資料到公頻 | ❌ | 主詞是 Slime，違反原則 |
| Slime 之間分享觀察模式 | ❌ | 違反「個人的 Slime」 |
| Slime 戴裝備給別人看 | ❌ | Slime 變成展示物 |

判斷標準：

> **「對外」這個動作的主詞是誰。**
>
> **主詞是主人 → 永遠合法**（主人有完全處置權）
>
> **主詞是 Slime → 違反「Slime 不向外」**

---

## 對 manifesto 的影響

這份 ADR 提案兩條 manifesto v1.2 候選原則：

### 候選原則 14：Slime 永遠只服務一個主人

```
一隻 Slime，一個主人。沒有第三方。

兩隻 Slime 不會互相認識。
不會交換記憶。不會比較成長。不會合併進集合體。

每隻 Slime 是一座獨立的島。
這座島的全部內容，屬於它的主人。
```

### 候選原則 15：Slime 不向外

```
Slime 不主動把自己暴露給外面的世界。

主人想分享什麼是主人的權利 ——
但 Slime 自己永遠不主動上傳、不主動展示、不主動連網。

「對外」的動作主詞永遠是主人，不是 Slime。

這條原則砍除了：
- 公頻 federation
- 裝備市場
- 創作者獎勵
- 任何讓 Slime 變成「展示物」的機制
```

---

## 工程影響

### Code 層面要砍的

```
sentinel/equipment_visuals.py        — archive
sentinel/equipment_templates.py      — archive
sentinel/equipment*.py（其他相關）   — archive
server/federation/                   — archive
server/equipment/                    — archive
server/marketplace/                  — archive
server/db/migrations/（裝備相關）    — schema 保留（避免破壞舊資料）
smoke_test_creator_reward.py         — archive
```

### UI 層面要砍

5 個 freeze 的 tab 裡：

- ❌ 裝備 tab — 直接從 `gui.py` 移除 `addTab`（不只 freeze）
- ❌ 市場 tab — 同上
- ❌ 社群中的「裝備交易」「投稿創作」「投票」子頁 — 同上
- ❌ 審核 tab（裝備相關部分）— 同上

### Documentation 層面

- README 整段「裝備與經濟」章節 → 刪
- README 整段「裝備系統細節」 → 刪
- README 「公頻細節」 → 刪
- README 「為什麼不一樣」中提到「連上世界公頻」段落 → 刪
- CHANGELOG 加 entry：「v0.X 砍除裝備系統 / 公頻 / 市場，理由見本 ADR」

### 砍除順序

```
第一步（UI）：從用戶角度移除
  - gui.py 移除 addTab 行
  - 主畫面任何顯示裝備的元素移除
  - 浮窗上裝備視覺改為空白
  → 用戶看不到了，但 code 還在

第二步（Code）：移到 archive
  - 把要砍的檔案 git mv 到 archive/
  - 保留 git history，不 git rm
  - archive/README.md 寫：「這些東西曾經存在，我們選擇不走這條路。理由見 ADR 2026-04-30。」

第三步（Server）：router 拿掉
  - server/main.py 移除 federation/equipment/marketplace router 註冊
  - server/ 子目錄移到 archive
  - 資料庫 schema 不刪，讓舊資料保留

第四步（Documentation）：重寫
  - README 砍除相關段落
  - CHANGELOG entry
  - manifesto v1.2 收進原則 14、15
```

---

## 紅線

未來任何「讓 Slime 之間互動」「讓主人之間透過 Slime 互動」的功能提案，都要先過這份 ADR 的判斷：

> **「這個功能會讓 Slime 不再是個人的嗎？」**
>
> 是 → 砍提案
>
> 否 → 可考慮

具體紅線：

**❌ 永遠不能做的事**

- 任何「Slime 自動上傳資料給其他用戶看」的功能
- 任何「Slime 之間互通」的協議
- 任何「以 Slime 為單位」的市場、排行、競賽
- 任何讓 Slime 帶有「對外身份」的元素（裝備、稱號展示給別人）

**✅ 永遠可以做的事**

- 主人手動 export 自己 Slime 的東西到外面
- 提供「主人之間的社群」（獨立於 Slime 系統）
- 讓主人在外部社群分享自己 Slime 的故事

---

## 但有一個 edge case 要釐清

**「主人存在感」（車燈概念）** — 在外面顯示「還有 N 個主人也在養 Slime」這類訊息。

這算「對外」嗎？

**判斷**：不算違反這份 ADR。

**理由**：

- 顯示的是「主人們的數量」（去個人化的事實）
- 不顯示「誰養了什麼 Slime」「Slime 之間在說什麼」
- 純粹是「還有別人」這個事實
- 像晚上開車看到對向車燈 — 知道你不孤單，但不交流

這個機制要不要做、怎麼做，不在這份 ADR 範圍。
另開 ADR 討論。

---

## 給未來的我自己

當你看到 Slime 的某個新功能設計，猶豫該不該做時 ——

問：**這個功能的主詞是誰？**

- 主詞是**主人**在用 Slime 做某件事 → 留
- 主詞是 **Slime** 在向外展示 / 分享 / 連接 → 砍

當你看到競爭對手做了某個社交功能、市場功能、社群功能，擔心 Slime 沒這個會輸時 ——

回到這份 ADR。

Slime 不需要追上他們。
Slime 是另一種物種。

5 年後當所有 AI 都互相連結、共享資料、變成大集合體時，
**「完全只屬於你一個人的 AI」會變成奢侈品。**

這就是 Slime 從第一天就走這條路的原因。
