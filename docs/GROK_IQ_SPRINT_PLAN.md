# Grok 自主长程注册冲刺计划（iq 主战场）

> 生成：2026-09-02 · 分支 `cursor/grok-autonomous-iq-sprint-88d6`  
> 约束：api_id **仅 4 或 6**；目标 = **至少 1 个完整注册成功**  
> 脚本：`backend/scripts/run_grok_autonomous_sprint.py`

## 0. 已内化、不再重复的事实

| # | 事实 | 对冲刺的含义 |
|---|------|----------------|
| 1 | 自建 my.telegram.org api_id → 100% App | **禁止**再用 35337905 等自建 ID 当主路径 |
| 2 | 只有 api_id=4 / 6 才可能走 SMS / Firebase | 全部假设只用这两个 |
| 3 | official api_id=6 + Push → iq/id/pe 走完 email 后 **100% PaymentRequired** | H4/H5 只做「firebase 能否改通道」与「Payment 后探测」，不指望付钱 |
| 4 | official api_id=4 + Push on iq → 常 FLOOD；in 上 vault 官方模拟 100% App | 对照已有；本轮测 **非 official** 的 api_id=4 |
| 5 | vault 成功 +91：api_id=4 + 014b35 hash + Push + 12.7.3 + **非 official** | **H1 是最高先验假设**；vault 模式 **从未在 iq 上跑过** |
| 6 | SMS Bower Email 可用；REGHelp Email SERVICE_DISABLED | email 链路只用 smsbower |
| 7 | official 必须 attach Push（PR #44） | 4/6 一律 Push；不再做无 Token 裸发 |
| 8 | +91 号池老号多 | in 只作最后对照，主战场 iq |

当前 `CodeSettings` **未设置** `allow_firebase` / `unknown_number`（Telethon 已支持）。官方 Android 客户端会设 `allow_firebase=true`；接码号不是本机 SIM，`unknown_number=true` 更接近真实。这是本轮最值得改的协议位。

## 1. 可测假设（8 条）

| ID | 假设 | 配置要点 | 成功判据 | 失败则 |
|----|------|----------|----------|--------|
| **H1** | iq + **非 official** + api_id=4 + Push + firebase/unknown + 强制 resend + 每号新设备/代理 | vault 快照；`pin_app_version_substr=12.7.3`；`hunt_*=1` | SMS / Firebase 收码并 signUp；或至少 **非 FLOOD** | 若 FLOOD → 跳过其余 api4，转 H3 |
| **H2** | 同 H1，但打开 flashcall / missed_call，观察通道是否离开 App | 仅当 H1 为 App 且非 FLOOD | 出现 Sms/Call/Firebase | 放弃 CodeSettings 花活 |
| **H3** | iq + **非 official** + **api_id=6** + Push + firebase/unknown | `telegram_android`；emu=false | 不进 email/Payment，走 SMS/Firebase | 若仍 Email→Payment，说明墙跟 api_id=6 绑定而非 official 旗标 |
| **H4** | iq + **official** api_id=6 + **allow_firebase=true**（历史实验从未开此位） | 官方模拟；期望 Firebase 取代 SetUpEmailRequired | FirebaseSms + requestFirebaseSms 后收到短信 | 仍 Payment → H5 |
| **H5** | PaymentRequired 后：`auth.resendCode` + `payments.assignPlayMarketTransaction` 探测（**无真实收据**） | probe=`both` | resend 翻到 SMS/Firebase；Play RPC 只记录错误码 | 记「无 API 级绕过」 |
| **H6** | 号池：提高 `max_price` + 每号新设备，筛 SMS 友好 iq 号 | H1 配置 + max_price=1.5 | SentCodeTypeSms > 0 | 认定 iq smsbower 当前批次 App-only |
| **H7** | 换国 **jo**，保持 H1/H3 中 iq 上「能 sendCode 且非 Payment」的配置 | 10 号 | 收到 SMS 并注册 | 转 H8 |
| **H8** | 换国 **ae**（或 ma，视 smsbower 库存） | 10 号 | 同上 | 写穷尽结论 |

不测：自建 api_id、无 Push 的 4/6、REGHelp Email、真钱内购。

## 2. 代码改动（为实现假设所必需）

1. `CodeSettings`：`allow_firebase` / `unknown_number` / flashcall / missed_call 走配置。
2. App 且 `next_type=None` 时仍 `auth.resendCode`（`force_resend_on_app`）。
3. PaymentRequired 后可选 resend + Play Market 探测（假收据，只记 RPC）。
4. `pin_app_version_substr`：优先抽 12.7.3 指纹。
5. 自主冲刺脚本：分批跑、分析 sent_code、自适应下一批、成功即停。

## 3. 预算与批次

- 总租号预算 **30–50**；smsbower；threads 5–8；每任务 2 次取号。
- 主国家 **iq**；iq 全失败再 jo / ae 各约 10 号。
- 每批写入 `data/ab_reports/grok_autonomous_sprint_*.json`。
- 成功：脱敏记录 phone 尾号、api_id、配置快照、session 路径。

## 4. 自适应规则（脚本内）

```
H1 成功 → 停
H1 FLOOD → 跳过 H2/H6 的 api4，进 H3
H1 App 且 sendCode 成功 → H2 小样本，再 H6
H3 非 Payment 且非 App → 加码同配置
H4 出现 Firebase → 加码 H4
H4/H3 出现 Payment → 跑一次 H5，之后不再烧 official email
iq 租号 ≥28 且 0 SMS → H7、H8
任意 SMS 收码 → 立即用该配置加码直到成功或预算尽
```

## 5. 复现

```bash
python3 backend/scripts/run_grok_autonomous_sprint.py \
  --budget 42 --threads 6 --max-attempts 2
```
