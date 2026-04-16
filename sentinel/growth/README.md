# sentinel/growth — 成長系統

**誠實狀態**：PR 1 of 5. 這是地基，不是成品。讀 `__init__.py` 的模組 docstring 有最新狀態。

## 為什麼多寫這個模組

既有的 `evolution.py` / `slime_avatar.py` / `self_evolution.py` 處理「史萊姆會變」。但有三個洞：

1. **沒有 human-in-the-loop**  `self_evolution.py` 的 `maybe_evolve()` 每 10 次學習就叫 LLM 寫新技能，直接落地到 `skills/` 然後就能跑。沒有人類點過頭。
2. **safety 可以被繞**  `_is_code_safe()` 用字串比對 dangerous pattern，像 `os . system`（有空格）或 `getattr(__builtins__, 'eval')` 這類反射攻擊能過。
3. **沒有 capability 階梯**  剛誕生的史萊姆跟存活 3 個月的史萊姆，理論上都能觸發 self_modify。應該要綁 evolution tier。

`growth/` 模組補這三個洞，同時鋪長期路（吸收、聯邦學習）。不改寫既有程式，只插檢查點。

## 檔案對應

| 檔案 | 狀態 | 用途 |
|------|------|------|
| `capability.py` | 實裝 | 權限階梯 — 什麼 tier 可以做什麼 |
| `safety.py` | 實裝 | AST-based code scanner，取代字串比對 |
| `approval.py` | 實裝 | 人類核准佇列 — pending → approved/rejected |
| `absorption.py` | 資料模型實裝、渲染待 PR 2 | 裝備 → 永久身體部位 |
| `federation.py` | **設計稿 only** | 跨使用者學習協議，實作在 PR 5 |

## 五個 PR 的全景

| PR | 範圍 | 狀態 |
|----|------|------|
| **1（這個）** | growth/ 地基：capability + safety + approval + absorption 資料模型 + federation 設計稿 + self_evolution.py 接上 approval gate | 進行中 |
| 2 | GUI 整合：approval queue 顯示在設定頁、絕收物 render 到 avatar、marketplace 拒絕已吸收 item | 下一個 |
| 3 | 記憶結晶：階段晉升時用 LLM 從最近 event log 摘 5 個重要時刻，變成永久記憶碎片 | 之後 |
| 4 | Skill runtime sandbox：通過核准的 skill 用 subprocess + 資源限制跑，不污染主 process | 之後 |
| 5 | Federation 實作：relay 加 `/federation/patterns` endpoints，客戶端跑真投票 | 之後 |

## 使用方式（今天能用的部分）

```python
# 檢查 slime 是否有權限做某件事
from sentinel.growth import can_perform, Capability
decision = can_perform(Capability.PROPOSE_SKILL)
if not decision.allowed:
    log.info("Skipped: %s", decision.reason)
    return

# 掃描 LLM 寫的 code
from sentinel.growth import scan_code
report = scan_code(llm_generated_source)
if not report.safe:
    for f in report.blocking:
        log.warning("%s: %s", f.rule, f.message)
    return  # 不能送核准，直接丟

# 送到核准佇列
from sentinel.growth import submit_for_approval
from sentinel.growth.approval import SKILL_GEN
approval = submit_for_approval(
    kind=SKILL_GEN,
    title="深夜提醒休息",
    reason="觀察到使用者常在凌晨 2 點後仍在寫 code",
    target_path=str(SKILLS_DIR / "late_night_reminder.py"),
    source=llm_generated_source,
    safety_findings=[asdict(f) for f in report.findings],
    proposer_tier="Demon Lord Seed",
)
# 通知使用者：approval.id 等著你點

# 使用者點「同意」
from sentinel.growth.approval import approve
approve(approval.id, approver="peter_via_telegram")
```

## 「不能自殺」的具體保證

1. `safety.py` 拒絕 import `sentinel.self_evolution` / `sentinel.growth.safety` / `sentinel.growth.approval` — 生出來的 skill 不能改寫安全系統。
2. `safety.py` 拒絕 `os.remove` / `shutil.rmtree` / `os._exit` / `sys.exit` — 不能刪檔也不能強制退出。
3. `approval.py` 只接受寫入 `target_path`，由呼叫端指定；self_evolution.py 呼叫時會白名單 `skills/` 跟 `MODIFIABLE_FILES`。核准通過才寫。
4. PR 4 會加 subprocess sandbox + CPU/RAM 限制。

## 對使用者的承諾

- **史萊姆永遠不會自己部署 code**。LLM 寫的每一行都要你同意。
- **史萊姆永遠不能改寫安全系統**。改寫嘗試會被 AST scanner 擋。
- **使用者隨時能 reject 或 rollback**。approval 可以 reject，既有 `self_evolution.rollback_to_snapshot()` 可以回前一版，極端狀況 `rollback_to_core()` 回出廠。
- **聯邦學習 opt-in per pattern**。沒有全域開關，每個 pattern 要你個別點過才上傳。
