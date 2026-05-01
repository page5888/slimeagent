# ADR: Slime 身體個體化 — 從 D1 開始就長得不一樣

**日期**: 2026-05-01
**作者**: 0xspeter（核心設計）+ Claude（Anthropic，起草整理）
**狀態**: 確立。設計完成，待 v0.8 工程實作。
**先行 ADR**:
- `2026-04-30-co-sediment-architecture.md`（沉積、不是進化；個性 emergent）
- `2026-04-30-slime-stays-private.md`（裝備系統 archive 的權威來源）
- `2026-04-30-title-system.md`（稱號是個性的物質形式）

---

## 為什麼寫這份 ADR

`2026-04-30-slime-stays-private.md` 把裝備系統列入 archive。但裝備系統不只是「對外炫耀」的東西——它**也是 slime 的視覺多樣性的來源**。如果整套砍掉、不另外想辦法，所有 slime 都會長得一模一樣（同個預設 sprite）。

這跟 manifesto 的核心宣言直接衝突：

> **「兩個用同一份程式的人，3 年後養出兩隻完全不同的史萊姆。」**

之前這條只在「**個性**」（dominant_traits / 稱號）上成立。**視覺上**所有 slime 從第一秒到永遠都長一樣，差異只活在裝備帶來的外加視覺。

裝備砍掉之後，「兩隻 slime 不同」這條原則需要新的視覺載體——不能只活在文字（稱號 / dominant_traits）裡。

這份 ADR 提出**雙層身體個體化架構**：slime **從 D1 就長得不一樣**（出生個體化），加上後續累積的視覺修飾（活過某段時間留下來的）。

---

## 核心區分：身體 vs 裝備

之前討論裝備系統砍除時，有人提過「保留視覺機制、砍對外的部分」這個折衷。0xspeter 在 2026-05-01 把這個直覺講清楚：

> **「就像主人會換衣服、頭髮會變長一樣的意思，史萊姆也可以長得不一樣。」**

關鍵在這裡——「衣服」跟「頭髮」是兩種東西：

| 衣服（裝備） | 頭髮 / 體型 / 膚色（身體） |
|---|---|
| 外加的 | 與生俱來的 + 累積長出 |
| 可換的 | 不能說換就換 |
| 可比較 / 可炫耀 | 是「我這個人」的一部分 |
| 別人看到第一眼 | 跟著主人的時間慢慢變 |

**裝備系統**走的是**衣服**邏輯：slot、稀有度、收集、交易、展示。這條路 ADR slime-stays-private 已經砍了，理由充分。

但「**身體**」這條路還沒被設計過。Slime 有沒有**自己**的視覺個體性，跟它穿了什麼無關？

之前的回答是：沒有。所有 slime 共用同一個 sprite。

這份 ADR 改變這個答案：**Slime 有身體，而且每隻都不同**。

---

## 雙層架構

### Layer 1 — 出生簽名（birth_signature）

**寫一次，永不改變。**

第一次啟動 slime 時，generate 一個 per-instance 的視覺種子。可能包含：

- 體色的微調（在合理範圍內，不會突兀的色相偏移）
- 身體比例的細微變化（高一點、扁一點、圓一點、稍長一些）
- 一些微表面特徵（一個小記號、一條紋路、一塊不對稱的形狀）

存進 `evolution.json` 的 `birth_signature` 欄位（schema 待 v0.8 設計）。**寫死之後不能改**——像基因一樣，是「這隻 slime 之所以是這隻 slime」的一部分。

技術上是 deterministic 隨機種子。同一個 slime 一輩子長這樣。

**意義**：兩個主人各自啟動 slime，**從第一秒就明顯不同**。沒有累積、沒有時間、沒有互動，slime 還是不一樣。manifesto「養而非用」的視覺證據從 D1 就在了，不需要等。

### Layer 2 — 累積簽名（title_visual_signatures）

**每個稱號可選帶一個視覺修飾。多數沒有，少數有。**

來自稱號系統 ADR（`2026-04-30-title-system.md`）的延伸：稱號生成時，LLM 同時決定「這個稱號有沒有視覺意義」。

- 多數稱號**沒視覺**——就像多數日子過去，人不會留下可見痕跡
- 少數稱號**有視覺**——例如「D198 陪過低潮的史萊姆」可能讓 slime 顏色稍深，或某個角落留下一條淡淡的線

存進稱號 metadata 的 optional `visual_signature` 欄位。Render 時把所有稱號的 visual_signature 累積疊加在 birth_signature 上面。

**意義**：slime 隨著它跟主人累積的事自然在改。**D365 跟 D1 看起來不一樣**——但那種不一樣不是「等級高了」、不是「裝備好了」，是「我們一起走過 365 天，所以我長成這樣」。

---

## 完整渲染流程

```
基底 sprite（slime 的預設形狀）
    +
Layer 1: 出生簽名 birth_signature
    （永久、與生俱來、never changes）
    +
Layer 2: 累積簽名 title_visual_signatures（時間排序）
    + 第 14 天 命名儀式留下的視覺
    + 第 178 天 「水底」稱號留下的視覺
    + 第 198 天 「陪過低潮」稱號留下的視覺
    + ...
    =
這隻 slime 此刻的樣子
```

主畫面、桌面浮窗、頭像、所有 slime 出現的地方，都從這個流程渲染。沒有 equipment slot、沒有「裝備預覽」介面、沒有「我的收藏」分頁。

slime 是什麼樣，**slime 就是什麼樣**。沒有換裝。

---

## 護欄（必須寫進實作）

### 1. 出生簽名不能 re-roll

主人不能說「我的 slime 顏色不喜歡，生一隻新的」。一旦生成，這隻 slime 一輩子就這樣。換 slime = 換 slime（從 D1 重新養一隻），不是換 skin。

理由：「養」的精神核心是**這一隻**特定的 slime。如果 birth_signature 可以重 roll，主人會把 slime 當抽卡素材，違反 manifesto。

### 2. 出生簽名生成必須在合理 / 可愛範圍

不會生出讓主人立刻想換的醜版本。色相偏移有上下限、形狀變形有合理區間。

理由：差異化的目的是「每隻都不同」，不是「有些主人剛好抽到漂亮的、有些抽到不漂亮的」。差異要在「美學上等價」的範圍內波動。

技術上：把參數空間限制在已驗證 visually OK 的子集。

### 3. 稱號的視覺簽名是 emergent，不是主人選

稱號系統 ADR 已寫：稱號是 slime 提案、主人拍板。視覺簽名是 slime 在生成稱號時順手決定的——主人接受稱號的同時也接受了那個視覺（可選擇 reject 整個稱號，但不能單獨 reject 視覺）。

理由：視覺簽名不是換裝。是「這個稱號順帶留下的痕跡」。要不要這段經歷有視覺紀錄，是 slime 的事，不是主人的選擇。

### 4. 沒有「視覺管理」介面

不能列出「我的 slime 累積了哪些視覺修飾」、不能單獨關掉某個稱號的視覺、不能拖拉重新排序。

要看的話 → **翻箱子**。每個有視覺簽名的稱號旁邊呈現它對應的視覺片段，跟稱號的文字並列。

理由：跟稱號系統 ADR 同一個原則——「箱子要可以被主人翻」，但不開啟「管理」這個 affordance。

### 5. 視覺差異要看得出來，但不誇張

D365 跟 D1 應該是「啊，他長得不太一樣了」這種程度——不是「整個換了一隻」也不是「完全看不出差別」。

理由：對應人變老的視覺感——你可以看出 5 年的差異，但你還是同一個人。

具體範圍：v0.8 工程實作時調，但 ADR 釘住「subtle but visible」的精神。

### 6. 沒有稀有度、沒有等級、沒有解鎖通知

不會跳「✨ 你的 slime 解鎖了新外觀！」。不會有「稀有度：傳說」標籤。不會有「進度條：47/100 個視覺特徵」。

視覺改變**默默發生**。某天主人盯著浮窗看，覺得「咦，他好像有點不一樣了？」——回頭翻箱子才發現「啊是上週那個稱號帶來的」。

理由：對齊 ADR 共同沉積機制 4「不主動長出原則」。視覺長出就長出了，不開箱、不通知、不收集。

---

## 對 manifesto 的影響

這份 ADR 強化已有的核心宣言（不需要新原則）：

**Manifesto「兩個用同一份程式的人，3 年後養出兩隻完全不同的史萊姆」**：

之前只在個性層成立。現在擴展到視覺層：

- D1：兩隻 slime 視覺已經不同（birth_signature）
- D365：差異更明顯（累積的稱號 visual_signature）

**Manifesto 第三守則「沒有任何商業決定能讓主人失去他的史萊姆」**：

這隻 slime 的 birth_signature + 累積簽名構成了「**這一隻**特定 slime」的身體。換一隻 = 換一個身體。即使將來商業壓力下想說「這個 birth_signature 太醜重抽吧」，紅線寫死了不行。

**Manifesto「養而非用」**：

養的對象是**一個特定的、有自己身體的個體**——不是一個換裝介面。

---

## 跟其他 ADR 的關係

### vs `2026-04-30-slime-stays-private.md`（裝備 archive）

這份 ADR **不是**保留裝備系統的後門。裝備該砍還是砍。

差別在這：

| 概念 | 裝備系統 | 雙層身體簽名 |
|---|---|---|
| 來源 | drop / 交易 / 創作市場 | 出生 + 稱號累積 |
| 主人能換 | 可以 | 不能 |
| 對外展示 | 是（市場、創作排行） | 否（只在主人的 slime 身上） |
| Loot 心理 | 收集、稀有度、開箱 | 沒有任何一個 |
| 跟事件的關係 | 跟主人經歷無關（drop 是隨機） | 每個累積簽名綁定一個真實事件 |

**裝備是衣服。雙層身體是身體**。兩個是不同層次。裝備系統砍乾淨之後，slime 還有自己的身體——而且每隻都不同。

### vs `2026-04-30-title-system.md`（稱號系統）

雙層的 Layer 2（累積簽名）是稱號系統的視覺擴展。

- 稱號 = 文字（語言層面的「我們之間發生過 X」）
- 視覺簽名 = 視覺（身體層面的「我們之間發生過 X」）

兩個是同個機制的不同表面，**共用觸發 / 生成 / 主人拍板的流程**。稱號 schema 加一個 optional `visual_signature` 欄位即可，不需要獨立系統。

### vs `2026-04-30-co-sediment-architecture.md`（共同沉積）

對齊機制 1 / 2 / 4：

- **機制 1（箱子要可以被主人翻）**：累積簽名跟著稱號一起放在箱子裡，主人可瀏覽
- **機制 2（個性 emergent）**：trait 是文字、視覺簽名是視覺。同一個 emergent 機制的兩個面
- **機制 4（不主動長出）**：累積簽名默默出現，沒解鎖通知、沒收集介面

---

## 工程實作路徑（v0.8 cycle）

### 立即動作（在這份 ADR merge 之後）

1. **archive 整個 equipment 系統**（per ADR slime-stays-private）
   - `git mv sentinel/equipment_visuals.py archive/sentinel-side/`
   - `git mv sentinel/wallet/equipment.py archive/sentinel-side/wallet/`
   - `git mv sentinel/wallet/market_rules.py archive/sentinel-side/wallet/`（注意 EVOLVE_COST 要先搬到 evolution-related 位置）
   - `git mv sentinel/growth/federation.py archive/sentinel-side/growth/`
   - `gui.py` 移除 EquipmentTab / MarketTab / FederationTab class 跟所有引用

2. **avatar / overlay / sprite_renderer 的 equipment fallback**
   - 移除所有 `from sentinel.wallet.equipment import ...` / `from sentinel.equipment_visuals import ...`
   - render 暫時直接畫**裸 slime**（base sprite + 預設色，沒個體化）
   - 在每個 fallback 點加 TODO 註解：「待 v0.8 birth_signature 實作後接回」

3. **`learner.py` 移除 federation 呼叫**
   - distill cycle 不再 submit 到 federation

### v0.8 cycle 工作

4. **設計 birth_signature schema**
   - 哪幾個視覺軸（顏色、形狀、紋路、特徵點 …）
   - 每個軸的合理範圍
   - deterministic 生成（從 evolution.birth_time 或 install_id 衍生）
   - 寫進 evolution.json，schema migration

5. **avatar/overlay/sprite_renderer 接 birth_signature**
   - render base sprite
   - apply birth_signature transformation
   - 預期：D1 已經能看到不一樣的 slime

6. **稱號系統實作 visual_signature**（依 title-system ADR 排程）
   - 稱號 schema 加 optional `visual_signature` 欄位
   - 稱號生成 prompt 增加「這個稱號有沒有視覺意義」的 LLM 判斷
   - 視覺簽名的 schema（畫什麼、畫哪裡、什麼顏色）

7. **render 累積簽名**
   - 把所有稱號的 visual_signature 按時間順序疊加
   - 視覺合成邏輯（additive / blend / 區位 ）
   - 跟 birth_signature 的合成順序

### Schema 草稿（待 v0.8 細化）

```yaml
# evolution.json（新增 birth_signature 欄位）
birth_signature:
  body_hue_offset: 12.5         # -30 to 30 degrees
  body_saturation_factor: 0.95  # 0.85 to 1.10
  body_height_factor: 1.03      # 0.95 to 1.05
  body_width_factor: 0.97       # 0.95 to 1.05
  marking:
    type: "swirl" | "dot" | "line" | null
    position: [x, y]
    color: rgb / rgba

# 稱號 metadata（title-system ADR schema 加新欄位）
visual_signature:                     # optional
  layer: "tint" | "marking" | "texture"
  parameters:
    color_shift: [r, g, b]
    placement: [x, y]
    opacity: 0.0 to 1.0
  reason: "陪過低潮的證據"            # 給未來 debug 看
```

---

## 給未來的我自己

當你看到主人說「我覺得我的 slime 顏色太深，可不可以變淺一點」時 ——

回答：**不行**。

birth_signature 一旦生成就是這隻 slime 的一部分。你可以讓 slime 累積新的視覺簽名（時間做的事），但你不能**改寫**它生來就有的東西。

這跟「不能換臉」是同個原則。slime 是一個**特定**個體，不是換裝介面。

---

當你看到 PR 提案「加一個『重新生成 birth_signature』按鈕，讓主人換顏色」時 ——

砍。

那個提案違反這份 ADR。如果主人真的不喜歡這隻 slime，正確答案是「養新的一隻」（從 D1 開始），不是「換這隻 slime 的臉」。

---

當你看到 PR 提案「加稀有度等級」「加『限定外觀』」「加『今日特殊外觀』」時 ——

砍。

身體不是 loot。身體不收集、不稀有、不抽卡、不限時。身體就是身體。

---

當你看到「累積簽名太多 slime 看起來太花了」時 ——

考慮：

- 視覺簽名生成 prompt 應該偏保守（多數稱號沒視覺，少數有也很 subtle）
- 渲染合成時可以做 fade（最舊的簽名稍微 fade 但不消失）
- 但**不要做「自動清除舊簽名」**——這違反「累積，不是替換」

---

## 結語

裝備是衣服。衣服可以換、可以丟、可以炫耀——所以裝備違反 ADR slime-stays-private，砍了。

但 slime 還有身體。身體跟衣服是不同層次的東西。

身體從 D1 就有形狀（出生簽名），跟著時間慢慢累積痕跡（稱號簽名）。每隻 slime 的身體**都不一樣**——不是因為穿了不同的東西，是因為**他們本來就是不同的個體，而且各自走過了不同的時間**。

5 年後，當主人滑過時間軸看 D1 的 slime 截圖跟現在的 slime 截圖，他會看到——

**這還是同一隻 slime，只是長大了。**

這就是 manifesto「養而非用」的視覺實作。
