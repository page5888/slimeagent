# AI Slime Agent 🫠

> 你的轉生守護靈。在背景默默觀察你的電腦使用習慣，逐漸學習並進化，成為只屬於你的 AI 夥伴。

**[官方首頁](https://slimeagent-relay.onrender.com/)** · **[社群市場](https://slimeagent-relay.onrender.com/market)** · **[5888 錢包](https://wallet-5888.web.app)**

---

## 這是甚麼

AI Slime Agent 是一個**背景常駐**的桌面 AI 夥伴。

你不用和它對話，它就會：

- 👁 **觀察** — 監控 CPU / RAM / 磁碟、偵測檔案變動、追蹤你的開發活動
- 🧠 **學習** — 定期用 LLM 蒸餾觀察結果，累積對你的理解
- 🧬 **進化** — 根據累積經驗，從一顆小史萊姆進化到究極型態（共 7 階段）
- ⚔️ **掉落** — 觀察和學習時隨機掉落裝備，12 個欄位 × 7 個稀有度

每隻史萊姆都是**獨一無二**的 — 外表、技能、說話風格，都會根據你的使用習慣長成不一樣的樣子。

---

## 快速開始

### 前置需求

- Windows 10/11（目前主要支援平台）
- Python 3.10+
- Gemini API Key（Google 免費提供，[這裡申請](https://aistudio.google.com/apikey)）

### 安裝步驟

```bash
# 1. Clone 這個 repo
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

第一次啟動會打開 GUI，在「設定」分頁填入 Gemini API Key，然後就可以看著你的史萊姆甦醒。

### 想要自動開機啟動？

GUI 裡有「開機自動啟動」開關，勾起來就會建立 Windows 排程任務。

---

## 架構

```
┌─────────────────────────────────────────────────────────────┐
│                       Desktop Client                         │
│                     (PySide6 + Python)                       │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  觀察者  │→ │  學習者  │→ │  進化器  │→ │   GUI    │   │
│  │ sentinel │  │ learner  │  │ evolver  │  │   gui    │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│       ↓             ↓              ↓                         │
│   system info   LLM distil    personality                    │
└──────────────────────────────┬───────────────────────────────┘
                               │  HTTPS (JWT)
                               ↓
┌─────────────────────────────────────────────────────────────┐
│                   Relay Server (FastAPI)                     │
│              slimeagent-relay.onrender.com                   │
│                                                              │
│  /auth         Google OAuth login                            │
│  /wallet       Point balance sync                            │
│  /equipment    Community submissions + voting                │
│  /marketplace  P2P listing / buy / delist                    │
│  /federation   公頻 pattern sharing                          │
│  /evolution    Manual trigger (2 pts)                        │
└──────────────────────────────┬───────────────────────────────┘
                               │  S2S HMAC
                               ↓
┌─────────────────────────────────────────────────────────────┐
│                     5888 Wallet                              │
│                 wallet-5888.web.app                          │
│          Point balance / topup / marketSaleSettle            │
└──────────────────────────────────────────────────────────────┘
```

### 雙模式運作

| 模式 | 用途 | 費用 |
|------|------|------|
| **BYOK**（自帶 API Key） | 填入自己的 Gemini / OpenAI / Claude Key | 免費（付給 API 供應商） |
| **Quota**（5888 點數包） | 用 5888 錢包點數 | 透過 relay 代理 LLM 請求，扣點 |

BYOK 是預設模式 — 任何功能都不強制要點數。

---

## 功能

### 進化系統

7 個階段，經驗值來自觀察時間、檔案活動、聊天互動。也可以手動觸發（花 2 點）：

```
史萊姆 → 進化史萊姆 → 高等史萊姆 → 超級史萊姆
      → 賢者史萊姆 → 魔王史萊姆 → 究極史萊姆
```

### 裝備系統

- **12 個欄位**：武器、盔甲、頭盔、靴子、配飾、法器 ×2、光環、稱號、背景、寵物、載具
- **7 個稀有度**：普通 / 精良 / 稀有 / 史詩 / 傳說 / 神話 / 至高
- **61+ 內建模板**，加上社群創作的無上限
- 合成：3 件同稀有度 → 升級到下一階

### 社群市場

- **裝備投稿**：每天最多 3 件，附圖 + 敘述 + 稀有度
- **社群投票**：每票 10 點，達到稀有度門檻自動通過
- **創作者獎勵**：通過後獲得 100 點
- **P2P 交易**：70% 賣家 / 15% 創作者 / 5% 平台 / 10% 系統

### 公頻（Federation）

跨史萊姆的「世界公頻」— 觀察模式和技能祕訣的分享頻道。你在自己電腦上發現的規律（例如「週五下午效率會下滑」），匿名化後丟到公頻，其他史萊姆可以投票 `confirm / refute / unclear`，達到閾值後會被升級為「社群共識」。

---

## 設定檔位置

所有使用者資料都放在 `~/.hermes/`：

```
~/.hermes/
├── aislime_auth.json        # JWT token + user info
├── google_oauth.json         # Google OAuth client ID/secret
├── sentinel_settings.json    # 使用者偏好
├── slime_state.json          # 史萊姆進化狀態
├── inventory.json            # 裝備背包
├── memories.json             # 學習記憶
└── chat_history.json         # 對話紀錄
```

專案根目錄的 `sentinel/config.py` 是預設值，使用者改過之後會存到 `sentinel_settings.json`。

---

## 開發

### 跑 Relay Server（本地）

```bash
pip install -r server/requirements.txt
uvicorn server.main:app --reload --port 8000
```

資料庫用 SQLite（本地）或 Postgres（Render 部署）。遷移檔在 `server/db/migrations/`。

### 專案結構

```
ai-slime-agent/
├── sentinel/          # 桌面端（PySide6 GUI + 觀察引擎）
│   ├── __main__.py   # 入口
│   ├── gui.py        # 主視窗
│   ├── brain.py      # LLM 呼叫
│   ├── learner.py    # 定期蒸餾
│   ├── evolution.py  # 進化邏輯
│   ├── equipment_visuals.py  # 裝備繪製
│   └── relay_client.py        # 和 server 對話
│
├── server/            # Relay 伺服器（FastAPI）
│   ├── main.py       # 入口
│   ├── auth/         # Google OAuth + JWT
│   ├── equipment/    # 社群裝備提交 + 投票
│   ├── marketplace/  # P2P 交易
│   ├── federation/   # 公頻
│   └── public/       # 介紹頁 + 市場頁
│
└── render.yaml       # Render 免費方案部署
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

---

## 貢獻

歡迎 PR！幾個入手點：

- **裝備模板**：`sentinel/equipment_templates.py` — 加新的武器、背景
- **裝備視覺**：`sentinel/equipment_visuals.py` — QPainter 繪製
- **成就系統**：尚未實作，歡迎認領
- **跨平台**：目前主要支援 Windows，macOS / Linux 只有部分測試

提交前請：

1. 執行 `python -m py_compile` 確保沒有語法錯誤
2. 跑一次 `start.bat` 確認 GUI 正常開啟
3. 如果改到 server，跑 `smoke_test_wallet.py`

---

## 授權與致謝

- 視覺設計：受 Pokémon / 轉生史萊姆 啟發
- LLM 提供商：Google Gemini（免費）/ OpenAI / Anthropic
- 錢包系統：[5888 Ecosystem](https://wallet-5888.web.app)

AI Slime Agent 是 5888 生態系的其中一個應用。所有點數和交易透過 5888 錢包結算。

---

## 問題回報

- 🐛 Bug / 功能建議：[GitHub Issues](https://github.com/page5888/slimeagent/issues)
- 💬 討論：[GitHub Discussions](https://github.com/page5888/slimeagent/discussions)
- 📮 錢包相關：5888 錢包內建客服

---

**"既然轉生了，就好好觀察這個世界吧。"** 🫠
