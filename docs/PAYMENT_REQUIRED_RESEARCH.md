# PaymentRequired 调研摘要

> 生成时间：2026-09-02 · 分支 `cursor/payment-bypass-research-88d6`  
> 实验脚本：`backend/scripts/run_payment_bypass_ab.py`

## 官方文档要点

[auth.sentCodePaymentRequired](https://core.telegram.org/constructor/auth.sentCodePaymentRequired) 与 [User Authorization — Paid auth](https://core.telegram.org/api/auth) 说明：

- **仅官方客户端**可能收到此 constructor（第三方 api_id 通常走 `auth.sentCode` + 普通 `SentCodeType*`）。
- 触发条件：用户所在 **国家/运营商 SMS 成本高**，服务端要求通过 **App Store / Play Store 购买 Telegram Premium（短期）** 才能继续登录/注册。
- 产品字段 `store_product` 如 `telegram_premium.one_week.auth`；完成内购需 `payments.assignPlayMarketTransaction` / `payments.assignAppStoreTransaction`（**仅官方 App 可用**）。
- 第三方开发者若需 SMS 授权应联系 `sms@telegram.org`（`#enableSMS`），而非模拟官方内购。

## 社区/博客共识（非官方）

| 来源 | 说法 | 可靠度 |
|------|------|--------|
| [core.telegram.org](https://core.telegram.org/api/auth) | 无 API 级「跳过内购」；Paid auth 是官方路径设计 | 高 |
| Reddit / 营销博客 | 换 Telegram X、旧版 APK、干净住宅 IP 可能改变验证流 | 低～中（个案，无批量数据） |
| [AccountBoy / NexSMS 等](https://www.accountboy.com/en-us-usd/news-detail/1054) | 第三方「一周 Premium 解锁码」可过支付墙 | 商业宣传，非 Telegram 官方 |
| REGHelp / 本项目历史 | `balanced + custom api_id` 在 iq/co/pe 等多走 **SentCodeTypeApp**，**不触发 PaymentRequired** | 高（自有 A/B 日志） |

**结论：没有可靠的「规避内购」API 技巧**；社区方法本质是换客户端形态或换号段/IP，或真付钱。自动化若坚持 `official_client_emulation`，服务端把请求识别为官方 Android/iOS，Paid auth 是预期行为而非 bug。

## api_id 对照

| api_id | 来源 | 与 PaymentRequired |
|--------|------|---------------------|
| **6** | 当前 Play Store Telegram Android | official 模拟默认；实测 email 验证后高概率 PaymentRequired |
| **4** | 早期泄露 Android 公开 ID | 仍在 PUBLISHED 黑名单；有 Push 时可发码，但是否免 PaymentRequired 需实测 |
| **21724** | Telegram X (TDLib) | 官方变体；社区称验证流不同，需实测 |
| **自建** (如 35762565) | my.telegram.org 注册 | 第三方路径，文档称不应收到 PaymentRequired |

## 本项目 official 路径流程

```
auth.sendCode → SentCodeTypeSetUpEmailRequired
  → SMS Bower verifyEmail
  → auth.sendCode (再次) → SentCodePaymentRequired  ← 100% 观测点
```

非 official（`balanced + custom api_id`）同国家常见：

```
sendCode → SentCodeTypeApp（或 SMS），无 PaymentRequired
```

## A–E 控制变量实验

固定：`official_client_emulation=true`，`smsbower_only`，`push_required`，目标每实验 2 号（iq）。

### 本次实测（2026-09-02）

| 实验 | 变量 | 租号 | 完成 email | verifyEmail 后 sent_code | email→Payment 率 |
|------|------|------|------------|---------------------------|------------------|
| **A** | api_id=6 baseline | 6 | 2 | PaymentRequired ×2 | **100%** (2/2) |
| **B** | api_id=4 | 2 | 0 | App ×1；黑名单跳过 ×1 | N/A（未到 email 后阶段） |
| **C** | telegram_x 21724 | 2 | 0 | 未到达 sendCode | N/A |
| **D** | telegram_9 / 9.6.7 | 1 | 1 | PaymentRequired ×1 | **100%** (1/1) |
| **E** | device_max=1 proxy_max=1 | 2 | 1 | PaymentRequired ×1 | **100%** (1/1) |

补跑 A（4 租号 / 2 完成 email）：`payment_bypass_ab_iq_20260902_033109.json`  
全矩阵：`payment_bypass_ab_iq_20260902_032747.json`

### 历史 official survey（iq + id + pe，同配置）

| 国家 | 完成 email | email→Payment | 率 |
|------|------------|---------------|-----|
| iq | 9 | 9 | 100% |
| id | 4 | 4 | 100% |
| pe | 5 | 5 | 100% |

合计 **18/18 = 100%**，产品均为 `telegram_premium.one_week.auth`（USD $1.00 或当地货币等价）。

### 关键对比

- **A / D / E**：凡走完 `SetUpEmailRequired → smsbower verifyEmail`，下一跳 **均为 PaymentRequired**。
- **B (api_id=4)**：首跳直接 **SentCodeTypeApp**，未进入 email 流程 — 与 api_id=6 路径不同，但**不是**「绕过内购」，而是号池/App 投递。
- **C (Telegram X)**：本轮未成功 sendCode（REGHelp Push 对 tg_x 可能更严），**无法验证**是否免 PaymentRequired。
- **E vs A**：全新设备+代理 **未改变** PaymentRequired（1/1）。

## 根因假设排序（证据更新）

| 排序 | 假设 | 证据 |
|------|------|------|
| 1 | **official 路径被服务端标记为「官方 App」→ Paid auth 是设计行为** | A/D/E 100%；文档明确「Official apps may receive sentCodePaymentRequired」 |
| 2 | **与 custom api_id / balanced 路径本质不同** | 历史 non-official iq → SentCodeTypeApp，0% PaymentRequired |
| 3 | **国家/号段 SMS 成本策略** | iq/id/pe 均 100%，与 IP 无关（E 未改善） |
| 4 | app_version 新旧 | D (9.6.7) 仍 PaymentRequired → **否定** |
| 5 | 设备指纹 / IP 重复 | E 全新设备+代理仍 PaymentRequired → **否定** |
| 6 | api_id=4 可规避 | B 走 App 非 Payment，但非可用注册路径 → **部分否定** |
| 7 | Telegram X 免内购 | C 未出码 → ** inconclusive** |

## 结论

**不存在可靠的 API 级「规避内购」办法。** 在 `official_client_emulation=true` 且完成 email 验证后，Telegram 对 iq/id/pe 等测试国 **系统性返回 PaymentRequired**，这与 core.telegram.org 的 Paid auth 设计一致，不是设备/IP 配置 bug。

若自动化目标为 **可完成注册**：应关闭 official 模拟，使用 `balanced + custom api_id`（历史数据支持 SentCodeTypeApp/SMS 路径）。

若必须 official 链路：仅能通过 **真实 Play/App Store 内购**（`payments.assignPlayMarketTransaction`）或放弃该号段。
