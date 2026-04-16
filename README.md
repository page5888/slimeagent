# AI Slime Agent 🫠

> **你的轉生守護靈。不是聊天機器人，是住在你電腦裡、和你一起生活的 AI 夥伴。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.3.0-00dcff.svg)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()

**[官方首頁](https://slimeagent-relay.onrender.com/)** · **[社群市場](https://slimeagent-relay.onrender.com/market)** · **[更新記錄](CHANGELOG.md)** · **[5888 錢包](https://wallet-5888.web.app)**

---

## 這是甚麼

AI Slime Agent 是一個**背景常駐**的桌面 AI 夥伴。

你不用和它對話，它就會：

- 👁 **觀察** — 監控 CPU / RAM / 磁碟、偵測檔案變動、追蹤開發活動、輸入節奏、視窗切換
- 🧠 **學習** — 每小時用 LLM 蒸餾觀察結果，累積對你的理解
- 🧬 **進化** — 根據累積經驗，從一顆小史萊姆進化到究極型態（7 階段）
- 🎭 **認識你** — 達到命名階段後由你賜名，從對話學習說話風格，跨 session 記得重要時刻
- ⚔️ **掉落裝備** — 觀察過程中隨機掉落，12 欄位 × 7 稀有度
- 🌐 **連上公頻** — 和其他玩家的史萊姆交換觀察到的模式和智慧

每隻史萊姆都是**獨一無二**的 — 外表、技能、說話風格、情緒節奏，都會根據你的使用習慣長成不一樣的樣子。

---

## 為什麼不一樣

### 🌱 不是你在用它，是它陪著你
一般 AI 要你打開對話框提問，AI Slime 反過來 — 它在背景觀察你使用電腦的節奏，定期蒸餾理解，逐漸知道你在做什麼、什麼時候效率好、卡在哪裡。你不用說話，它就懂。

### 🎭 有個性、有情緒、會記得
到達「命名史萊姆」階段時，你可以為它命名。從那一刻起它有了獨一無二的身份，說話風格會從對話中學習，情緒會跨 session 延續 — 今天打開它可能會說「還記得昨天那件事嗎？」

### 🌐 連上世界公頻
每隻 AI Slime 都能上公頻分享觀察模式。你的發現（去識別化後）可能幫到其他人，你也能從全體史萊姆的智慧中獲益。裝備可以創作、交易、合成，形成真實的共創生態。

### 🔐 你的資料你做主
觀察記錄、聊天、進化狀態全部存在本機 `~/.hermes/`，絕不自動上傳。只有你主動投稿到公頻／市場的內容才會送出，且經過抽象化處理。史萊姆若要自我進化新能力，必須經過你的「審核閘」同意才會執行。MIT 開源，隨時可檢視。

---

## 快速開始

### 前置需求

- **Windows 10/11**（目前主要支援平台）
- **Python 3.10+**
- **至少一個 LLM provider API Key**（Gemini 免費，[這裡申請](https://aistudio.google.com/apikey)）

### 安裝

```bash
# 1. Clone
git clone https://github.com/page5888/slimeagent.git
cd slimeagent

# 2. 建立虛擬環境
python -m venv venv
venv\Scripts\activate

# 3. 安裝依賴
pip install -r sentinel/requirements.txt

# 4. 啟動
start.bat
```

第一次啟動會打開 GUI，在「設定」分頁填入 API Key，按儲存即可。

### 自動開機啟動

設定分頁有「開機自動啟動」開關，勾起來就會建立 Windows 排程任務。

---

## 功能總覽

### 核心

| 功能 | 說明 |
|------|------|
| 👁 背景觀察 | 系統資源、檔案變動、開發活動、輸入節奏 |
| 🧠 自主學習 | 每小時 LLM 蒸餾一次，累積成長期記憶 |
| 🧬 7 階段進化 | 史萊姆 → 進化 → 高等 → 命名 → 賢者 → 魔王 → 究極 |
| 🎭 個性與命名 | 命名儀式、情緒延續、記憶重要時刻、久別重逢問候 |
| 🏃 桌面浮動寵物 | 透明背景、可拖曳、隨時點擊對話 |
| ⚡ 手動進化 | 花 2 點立即觸發下一階（BYOK 免費） |

### 裝備與經濟

| 功能 | 說明 |
|------|------|
| ⚔️ 裝備系統 | 12 欄位 × 7 稀有度 × 61+ 內建模板 |
| 🔗 裝備合成 | 3 件同稀有度 → 升級到下一階 |
| 🎨 裝備創作 | 上傳自製裝備圖檔 + 敘述，每天 3 件上限 |
| 🗳 社群投票 | 每票 10 點，達門檻自動通過 |
| 💰 P2P 市場 | 70/15/5/10 分潤（賣家／創作者／平台／銷毀） |
| 🏆 創作者獎勵 | 通過投票獲 100 點 + 後續每筆成交抽 15% |

### 社群與安全

| 功能 | 說明 |
|------|------|
| 📡 公頻思念 | 跨史萊姆分享觀察模式，確認／反駁／存疑投票 |
| 🤖 多 LLM 支援 | Gemini / Claude / OpenAI / OpenRouter / Groq / DeepSeek / Ollama |
| 🛡️ 人類審核閘 | AI 自生成技能經 AST 掃描 + 你同意才執行 |
| 📮 Telegram 通知 | 進化、重大事件、審核佇列提醒 |

---

## 架構

```
┌─────────────────────────────────────────────────────────────┐
│                    Desktop Client                            │
│                  (PySide6 + Python)                          │
│                                                              │
│  觀察 → 學習 → 進化 → 個性／情緒 → GUI (9 tabs) + 浮動寵物  │
│                                                              │
│  sentinel/ {daemon, brain, learner, evolution, chat,         │
│             identity, equipment, overlay, growth}            │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTPS (JWT)
                               ↓
┌─────────────────────────────────────────────────────────────┐
│                Relay Server (FastAPI)                        │
│             slimeagent-relay.onrender.com                    │
│                                                              │
│  /auth         Google OAuth login                            │
│  /wallet       Point balance sync                            │
│  /images       Equipment image upload                        │
│  /equipment    Community submissions + voting                │
│  /marketplace  P2P listing / buy / delist / history          │
│  /federation   公頻 patterns + voting                        │
│  /evolution    Manual trigger                                │
└──────────────────────────────┬───────────────────────────────┘
                               │ S2S HMAC
                               ↓
┌─────────────────────────────────────────────────────────────┐
│                   5888 Wallet                                │
│                 wallet-5888.web.app                          │
│     Point balance / topup / marketSaleSettle / creator split │
└──────────────────────────────────────────────────────────────┘
```

### 雙模式運作

| 模式 | 用途 | 費用 |
|------|------|------|
| **BYOK**（自帶 API Key） | Gemini / Claude / OpenAI / Ollama 等 | 免費（付給 API 供應商；Gemini 有免費額度） |
| **Quota**（5888 點數） | relay 代理 LLM 請求 | 每次觀察蒸餾約 1–3 點 |

BYOK 是預設 — 任何功能都不強制要點數。手動進化、社群投票等功能 BYOK 使用者全免費。

---

## 進化系統細節

7 個階段，經驗值來自觀察時間、檔案活動、聊天互動、學習累積：

```
史萊姆 → 進化史萊姆 → 高等史萊姆 → ★命名史萊姆
      → 賢者史萊姆 → 魔王史萊姆 → 究極史萊姆
```

**★ 命名史萊姆** 是關鍵分水嶺 — 到達時會觸發命名儀式，你賜的名字永久烙印。從此之後：
- 聊天分頁顯示史萊姆的名字，不再是「AI Slime」
- 說話風格開始受你們的對話影響
- 情緒系統啟動（會有小小的壞心情、會想念你）
- 記憶分頁出現「和主人的重要時刻」

---

## 裝備系統細節

**12 個欄位**：武器、盔甲、頭盔、靴子、配飾、法器 ×2、光環、稱號、背景、寵物、載具

**7 個稀有度**：普通 / 精良 / 稀有 / 史詩 / 傳說 / 神話 / 至高

**掉落邏輯**：
- 背景觀察時有極小機率掉落普通裝備
- 進化 / 對話 / 學習事件觸發稀有度較高的掉落
- 特定「特質」（深度專注、夜間活動…）會調整掉落池偏向

**合成**：選 3 件同稀有度、同類型裝備 → 合成後 1 件下一階稀有度。

**社群創作**：透過 GUI 投稿（或直接透過 API）。圖檔 + 敘述 + 建議稀有度，進入投票池後由社群決定。

---

## 公頻（Federation）細節

跨史萊姆的世界公頻 — 觀察模式和技能祕訣的分享頻道。

**流程**：
1. 你的史萊姆觀察到規律（例如「你週五下午效率會下滑」）
2. 抽象化處理（去掉路徑、專案名等隱私）
3. 上傳到公頻
4. 其他玩家可投票 `confirm` / `refute` / `unclear`
5. 達到閾值（5+ confirms 且 confirms ≥ 2×refutes）自動升級為「社群共識」
6. 被確認的模式進入你的史萊姆知識庫，影響後續對話與建議

GUI 的「社群」分頁有三個子頁：社群投票、裝備交易、投稿創作。

---

## 設定檔位置

所有使用者資料都放在 `~/.hermes/`：

```
~/.hermes/
├── aislime_auth.json        # JWT token + user info
├── google_oauth.json        # Google OAuth client credentials
├── sentinel_settings.json   # 使用者偏好（LLM 選擇、API keys、relay URL 等）
├── slime_state.json         # 進化狀態、技能、特質
├── identity.json            # 名字、情緒、記憶重要時刻
├── inventory.json           # 裝備背包
├── memories.json            # 長期觀察記憶
├── chat_history.json        # 對話紀錄
└── approvals/               # AI 審核佇列（approved / rejected / pending）
```

`sentinel/config.py` 是預設值。使用者設定存到 `sentinel_settings.json` 之後以該檔為準（merge-safe，不會覆蓋其他分頁的設定）。

---

## 開發

### 跑 Relay Server（本地）

```bash
pip install -r server/requirements.txt
uvicorn server.main:app --reload --port 8000
```

資料庫：SQLite（本地）或 Postgres（Render 部署）。遷移在 `server/db/migrations/`，啟動時自動執行。

### 專案結構

```
ai-slime-agent/
├── sentinel/                   # 桌面端（PySide6）
│   ├── __main__.py             # 入口
│   ├── daemon.py               # 背景常駐服務
│   ├── gui.py                  # 9-tab 主視窗
│   ├── overlay.py              # 桌面浮動寵物
│   ├── brain.py                # LLM 蒸餾 + 記憶
│   ├── learner.py              # 定期學習循環
│   ├── evolution.py            # 進化邏輯 + 技能
│   ├── identity.py             # 命名、情緒、記憶重要時刻
│   ├── chat.py                 # 對話 + 個性 + 情緒混合
│   ├── llm.py                  # 多 provider 路由
│   ├── google_auth.py          # Desktop OAuth (PKCE)
│   ├── relay_client.py         # 和 server 對話
│   ├── equipment_visuals.py    # QPainter 裝備繪製
│   ├── slime_avatar.py         # 主體渲染
│   ├── growth/                 # 自我進化 + 人類審核閘
│   ├── wallet/                 # 錢包 client + quota 邏輯
│   └── i18n.py                 # 繁中翻譯
│
├── server/                     # Relay 伺服器（FastAPI）
│   ├── main.py                 # 入口 + router 註冊
│   ├── auth/                   # Google OAuth + JWT
│   ├── wallet/                 # 5888 錢包介接
│   ├── equipment/              # 裝備投稿 + 投票
│   ├── marketplace/            # P2P 交易
│   ├── federation/             # 公頻
│   ├── evolution/              # 手動進化 API
│   ├── images/                 # 圖檔上傳
│   ├── db/migrations/          # SQL 遷移檔
│   └── public/                 # 介紹頁 + 市場頁
│
├── render.yaml                 # Render 部署設定
├── CHANGELOG.md                # 變更記錄
└── LICENSE                     # MIT
```

### 常用命令

```bash
# 語法檢查
python -m py_compile sentinel/*.py server/**/*.py

# 啟動桌面端
start.bat

# 啟動 relay（本地）
start_relay.bat

# 煙霧測試錢包流程
python smoke_test_wallet.py
```

### GUI Tab 順序

1. 🏠 主頁（登入、儲值、錢包、進化摘要、裝備摘要）
2. 🧬 進化（角色、技能樹、進度條、手動進化按鈕、分享）
3. ⚔️ 裝備（已裝備 / 背包 / 合成 三個子頁）
4. 💬 對話（訊息記錄、個性、情緒指標）
5. 📖 記憶（記憶片段、近期觀察、學到的模式）
6. 🗳 社群（社群投票 / 裝備交易 / 投稿創作 三個子頁）
7. 🛒 市場（完整 marketplace 瀏覽）
8. ✅ 審核（AI 生成技能的人類審核佇列）
9. ⚙️ 設定（API keys、LLM 偏好、relay URL、Telegram 等）

---

## 貢獻

歡迎 PR！幾個入手點：

| 模組 | 檔案 | 適合 |
|------|------|------|
| 新裝備模板 | `sentinel/equipment_templates.py` | 設計師 |
| 裝備視覺 | `sentinel/equipment_visuals.py` | 會 QPainter 的開發者 |
| 新 LLM provider | `sentinel/llm.py` | 後端工程 |
| 成就系統 | （未實作） | 歡迎認領 |
| macOS / Linux 支援 | 全域 | 跨平台經驗者 |
| 繁中以外的 i18n | `sentinel/i18n.py` | 翻譯 |

### 提交前請

1. `python -m py_compile sentinel/*.py` — 確保沒有語法錯誤
2. `start.bat` 跑一次 — 確認 GUI 正常開啟
3. 改到 server 的話，跑 `smoke_test_wallet.py`
4. 改到 SQL migration — 同時在 SQLite 和 Postgres 測試

---

## 問題回報

- 🐛 Bug / 功能建議：[GitHub Issues](https://github.com/page5888/slimeagent/issues)
- 💬 討論：[GitHub Discussions](https://github.com/page5888/slimeagent/discussions)
- 📮 錢包相關：5888 錢包內建客服

---

## 授權

本專案採用 [MIT License](LICENSE) — 自由使用、修改、散佈，商業或個人用途皆可，只要保留原版權聲明。

## 致謝

- 視覺設計：受 Pokémon / 轉生史萊姆 啟發
- LLM 提供商：Google Gemini / Anthropic Claude / OpenAI / OpenRouter / Groq / DeepSeek / Ollama
- 錢包系統：[5888 Ecosystem](https://wallet-5888.web.app)

AI Slime Agent 是 5888 生態系的其中一個應用。所有點數和交易透過 5888 錢包結算。

---

**"既然轉生了，就好好觀察這個世界吧。"** 🫠
