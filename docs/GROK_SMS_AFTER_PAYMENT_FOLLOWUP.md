# Payment 后 SMS：离真相还有多远

> 时间：2026-09-02  
> 分支：`cursor/grok-sms-after-payment-88d6`  
>
> **概念对照**：[OFFICIAL_AND_PAYMENT_EXPLAINED.md](./OFFICIAL_AND_PAYMENT_EXPLAINED.md)
> B.3 / C.5。本文实验把「SentCodeTypeSms ≠ 真短信」写成工作假设；解释文把它标成**原则性误判**若当作接近成功。  
> 上一轮：`docs/GROK_IQ_SPRINT_RESULTS.md`（H5/H10：resend → `SentCodeTypeSms`，smsbower 100% NO_CODE）  
> 脚本：`backend/scripts/run_grok_sms_after_payment.py`  
> 原始报告：`data/ab_reports/grok_sms_after_payment_*.json`

## 1. 诚实评估：离真相多近

**协议层我们已经走到官方 Paid auth 文档里「下一步」的门口，但还没有证据表明这扇门通向可完成的注册。**

上一轮稳定复现：

```
sendCode → SetUpEmailRequired (next_type=CodeTypeSms timeout=90)
  → account.verifyEmail
  → auth.sentCodePaymentRequired (telegram_premium.one_week.auth / USD $1.00)
  → auth.resendCode(phone, phone_code_hash)
  → auth.sentCode type=SentCodeTypeSms next_type=CodeTypeCall timeout=90
  → 轮询接码 120s → NO_CODE
```

iq 与 ma 都如此。`SentCodeTypeSms` 是本项目在 api_id∈{4,6} 约束下**唯一**出现过的带外短信 constructor。

### 这条 SMS constructor 是真发到运营商，还是 Telegram 空壳？

**当前证据更支持「付费墙未解除时的形式上 SMS」，而不是「已经交给运营商、只是我们等得不够久」。** 但还不能 100% 判死刑，所以本轮才做延长等待 / 二次 resend / 换号源。

| 观察 | 更像空壳 | 更像真短信没送到虚拟号 |
|------|----------|------------------------|
| resend 立刻返回 `SentCodeTypeSms`，且带 `next_type=CodeTypeCall timeout=90` | 服务端愿意改 constructor | 也符合官方 resend 语义 |
| smsbower 在 120s 内 **20+ 次 0 码**（上一轮 H5+H10） | 没有随机迟到 | 号段可能被 Telegram 屏蔽 |
| 同一平台的 **Email 验证码能稳定收到** | 接码 API 本身没坏 | 只说明邮箱通道通，SMS 通道仍可能被拦 |
| 假 Play 收据 → `PLAYMARKET_RECEIPT_INVALID` | 付费墙是硬门 | — |
| 官方文档：Paid auth **仅官方 App 内购后才能继续** | resend 不是文档里的过墙步骤 | 文档没写「禁止 resend」 |

**不能当成已经能注册。** constructor 从 Payment 变成 SMS，只证明 `auth.resendCode` 在这个状态下**可调用且返回了 SMS 类型**；不证明短信离开了 Telegram 网关。

### 为何虚拟号 100% NO_CODE？

按可能性排序（本轮实验就是为了把前几项降级或坐实）：

1. **付费墙未付，SMS 是空壳 / 不投递。** 官方路径是 `payments.assignPlayMarketTransaction` / `assignAppStoreTransaction`，然后 `auth.checkPaidAuth`。resend 不在这条链上。这是先验最高的解释。
2. **虚拟号段被 Telegram 屏蔽。** 高成本国家（iq/ma）的接码号经常收不到 TG 短信。换 Grizzly/5SIM 或不同 providerId 才能测。
3. **120s 不够 / 取消过早。** 上一轮默认 `30×4s=120s`。SMS constructor 自己的 timeout 是 90s（之后 next_type 是 **Call**，虚拟号更不可能收到）。把窗口拉到 180–300s **只能排除「晚到的 SMS」**，不能推翻空壳。上一轮 H10 多用 `reghelp_reuse` Token，**没有**被 180s 退款窗口截断，所以「等了满 120s」这一点是成立的。
4. **需要 Firebase。** 全程 0 次 `SentCodeTypeFirebaseSms`。`auth.requestFirebaseSms` 只适用于 Firebase constructor；对 `SentCodeTypeSms` 调用没有文档依据。本轮明确跳过。
5. **phone_code_hash 传错。** 代码用的是 Payment 对象上的 `phone_code_hash`，resend 成功返回了新 SentCode（含新 hash）。若 hash 错，RPC 应是 `PHONE_CODE_HASH_EMPTY` / `PHONE_CODE_EXPIRED`，而不是 SMS constructor。此项**基本排除**。
6. **必须先等 timeout 再 resend。** 官方：`next_type` 在 `timeout` 秒后才允许 resend。Payment 对象**没有** next_type/timeout；Email 阶段有 `timeout=90 next_type=Sms`，但我们走的是 verifyEmail 而不是那次 resend。本轮假设 F 专门等 90s 再 resend。

### 与官方文档对齐

来源：[auth.sentCodePaymentRequired](https://core.telegram.org/constructor/auth.sentCodePaymentRequired)、[User Authorization](https://core.telegram.org/api/auth)、[auth.resendCode](https://core.telegram.org/method/auth.resendCode)。

- **Paid auth**：官方客户端在 SMS 成本高的国家会收到 `auth.sentCodePaymentRequired`，必须买短期 Premium（本项目观测 `telegram_premium.one_week.auth` / $1）。
- **过墙 RPC**：`payments.assignPlayMarketTransaction` / `assignAppStoreTransaction`，再用 `auth.checkPaidAuth(form_id)` 查询。没有真实收据会被 400 拒绝（已探测）。
- **resendCode**：根据**上一次** sendCode/resend 的 `next_type` 换通道。返回值可以是普通 `auth.sentCode`，也可以再次是 `sentCodePaymentRequired`。文档**没有**说「Payment 后 resend 可以跳过内购」。
- **Firebase**：仅 `SentCodeTypeFirebaseSms` + Play Integrity / iOS push secret → `auth.requestFirebaseSms`。失败则 resend 并带 `reason`。
- **缺信**：`auth.reportMissingCode` 仅官方 App；本轮假设 B 会探测一次。
- **第三方**：文档写明非官方 App 在部分条件下不能走 SMS/Call，应联系 `sms@telegram.org` `#enableSMS`。我们用的是泄露官方 api_id=6，服务端把会话当成官方 Android，所以会进 Paid auth；这不是 bug。

**结论先行：** 我们离「看清墙的形状」已经很近（Email → $1 墙 → resend 改 constructor 全是可复现事实），离「不付钱完成注册」仍然远。本轮把等待、二次 resend、换平台、换国、等 timeout 全部打穿之后仍然 0 码，**把「空壳 SMS」从猜想升级为工作假设**。下一步只剩真实 IAP 或放弃 official api_id=6。

## 2. 代码上检查并改了什么（最小 diff）

审查 `registrar.resolve_sent_code_channel` 与 `wait_for_code`：

| 点 | 上一轮行为 | 本轮 |
|----|------------|------|
| 等待时间 | 固定 30×4s=120s | 可配 `sms_poll_attempts` / `sms_poll_interval_seconds` |
| REGHelp 截断 | 新 Token 会把窗口砍到 180s 退款内；reuse 不砍 | `sms_poll_bypass_push_window` 可强制不砍 |
| 取消时机 | OTP 超时才 cancel，没有在 constructor 一出来就释放 | 未改早取消；只加了结束时 `elapsed` / `final` 日志 |
| phone_code_hash | 已从 Payment/SentCode 透传，resend 成功 | 缺 hash 则拒绝 resend；日志打截断 hash |
| Email 是否必须 | 现路径是 verifyEmail 后才到 Payment | 新增 `resend_before_email_verify` 对照 |
| Payment 后 resend 次数 | 1 次、wait=0 | `payment_resend_max`（1–3）、`payment_resend_wait_seconds` |
| Firebase | 只在 FirebaseSms 时 requestFirebaseSms | SMS constructor 明确日志「不调用 requestFirebaseSms」 |
| 接码原始响应 | 只打「第 N 次轮询」 | 每 5 次 + 非 WAIT 打 `getStatus raw=`，超时带 `last_getStatus` |

默认值保持旧行为（30×4s、resend 1 次、不 bypass），实验脚本再覆盖。

## 3. 实验设计

| ID | 猜想 | 配置要点 | 号数 |
|----|------|----------|------|
| A | 等 240s + 2s 轮询就能收到 | official6 + Payment resend×1，smsbower iq | 3 |
| B | 立刻 resend 两次 + reportMissingCode | resend_max=2，180s | 3 |
| C | 换接码平台 | Grizzly 优先，否则 5SIM；同 A | 3 |
| D | 非 Payment 的 api_id=4 resend 能否出 SMS | vault 4 + force_resend，ma | 2 |
| E | 换国 ma，同 A | official6 ma | 3 |
| F | Payment 后先等 90s 再 resend | `payment_resend_wait_seconds=90` | 3 |

成功判据：接码平台至少一次返回可解析验证码；若能 signUp 则记 session 路径（脱敏）。

## 4. 余额 / 充值

实验前（2026-09-02 09:23 UTC）：

| 平台 | 余额 | 库存（Telegram） | 本轮是否够用 |
|------|------|------------------|--------------|
| **SMS Bower** | **23.746 USD** | iq 19 万 / ma 24 万 | 够。参考价 iq≈$1、ma≈$1.70 |
| **Grizzly SMS** | **29.8 USD** | iq 10 万 @ $0.53 | 够，C 组用了它 |
| **5SIM** | **25.12 RUB**（约 $0.27） | iq 无货；ma 有货 | **不够**当主路径，未使用 |

实验后：smsbower **23.602 USD**（约 −$0.14，多数租号 cancel 后退回；主要是邮箱小额消耗），Grizzly 仍 **29.8**（C 组 3 号应已退订）。

**现在不需要充值。** 若还要继续烧 official email→Payment 链路，建议 smsbower 保持 ≥15 USD；5SIM 若要用请至少充 **200–300 RUB**。不要为「再等一次 SMS constructor」盲目加钱。

## 5. 实测结果

报告：`data/ab_reports/grok_sms_after_payment_20260902_092328.json`  
租号 **19**，注册成功 **0**，接码平台真正读到验证码 **0**。  
全程 getStatus 原始响应只有 `STATUS_WAIT_CODE`（订单活着、从未出码）。

| ID | 猜想 | 国家 | 平台 | 租号 | constructor | 等待 | 收码 | 结论 |
|----|------|------|------|------|-------------|------|------|------|
| **A** | 等 240s + 2s 密轮询 | iq | smsbower | 3 | Email→Payment→**Sms×3** | **271–272s** NO_CODE | 0 | **否证「等久一点」** |
| **B** | 立刻 resend 两次 + reportMissingCode | iq | smsbower | 4 | Sms×3 | 203–205s NO_CODE | 0 | 第 2 次 resend 被 **FLOOD_WAIT 95s** 拒绝（对齐 timeout=90）；`reportMissingCode` 返回 **True** 仍无短信 |
| **C** | 换接码平台 | iq | **grizzlysms** | 3 | Sms×3 | 213–221s NO_CODE | 0 | **否证「smsbower 号段独有」**；Grizzly 同样 `STATUS_WAIT_CODE` |
| **D** | 非 Payment 的 api_id=4 resend | ma | smsbower | 2 | 无 sendCode | — | 0 | **FLOOD**（已 attach Push）。ma 上 4 与 iq/jo 一样不能发码 |
| **E** | 同 A 换 ma | ma | smsbower | 4 | Sms×3 | 271–274s NO_CODE | 0 | **否证「iq 号段独有」** |
| **F** | Payment 后先等 90s 再 resend | iq | smsbower | 3 | Sms×2；**Call×1**（verifyEmail 直出 Call，跳过 Payment） | 202–204s NO_CODE | 0 | 等 timeout 再 resend **仍是 Sms 空壳**；偶发 Call 虚拟号也收不到 |

### 关键日志（脱敏）

B 组二次 resend（hash 前后相同，说明 hash 传递正确）：

```
PaymentRequired 后 resend max=2 hash=ecea8181…len=18
→ SentCodeTypeSms next_type=CodeTypeCall timeout=90  （同一 hash）
→ auth.reportMissingCode mnc=99 返回 True
→ 立刻第二次 resend → FLOOD_WAIT 95s
→ 继续轮询第一次 SMS 180s → last_getStatus='STATUS_WAIT_CODE'
```

F 组等 90s 再 resend：同样立刻得到 `SentCodeTypeSms`，再 180s `STATUS_WAIT_CODE`。

F 组另 1 号：`verifyEmail` 直接返回 `SentCodeTypeCall`（无 Payment）。虚拟号无法接语音，180s 仍 NO_CODE。这是稀有变体，不是可用注册路径。

phone_code_hash：Payment 对象与 resend 返回的 SMS **共用同一 hash**。RPC 成功改 constructor，排除「hash 传错所以没发出去」。

`auth.requestFirebaseSms`：本轮 0 次 FirebaseSms constructor，未调用（日志明确跳过）。

## 6. 结论与下一步

**离真相：协议形状已经看清；可完成注册仍然远。**

Payment 后的 `SentCodeTypeSms` 现在可以更有把握地称为 **付费墙未解除时的形式上 SMS**：

- 不是「等 120s 太短」（A/E 等到 270s+）。
- 不是「smsbower 一家的问题」（C 换 Grizzly 同样 0 码）。
- 不是「没等官方 timeout」（F 等 90s 再 resend 仍空壳）。
- 不是「hash 传错」（同一 hash，resend 成功）。
- 第二次立刻 resend 被服务端按 timeout 拒绝，说明 Telegram 把这条 SMS **当作已下发的通道**在计时，但接码平台从未看到短信 —— 更像网关没真正投递给虚拟号，或投递被策略丢掉。

官方过墙步骤仍是真实 Play/App Store 内购（`assignPlayMarketTransaction` + `checkPaidAuth`）。假收据已 400。本项目不会伪造收据。

**建议停止**在 iq/ma 上继续烧 official api_id=6 的 Email→Payment→resend 链路来「碰运气收码」。余额还够，但边际信息量已经接近 0。

若还要继续，只剩高成本选项：

1. **真实 $1 IAP**（用户在官方 Android 完成 `telegram_premium.one_week.auth`），再把收据交给 `assignPlayMarketTransaction`。自动化做不了这一步。
2. 换时段重跑 **+91 vault api_id=4 非 official**（历史唯一成功样本）；当前 iq/ma 上 api_id=4 是 FLOOD。
3. 非虚拟号 / 实体 SIM（本仓库接码源覆盖不到）。

不要再测：自建 api_id、无 Push 的 4/6、Firebase 开关、再延长等待、再换 smsbower/Grizzly 的 iq/ma 虚拟号。
