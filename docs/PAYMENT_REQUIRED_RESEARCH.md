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

固定：`official_client_emulation=true`，`smsbower_only`，`push_required`，每实验 2 号。

| 实验 | 变量 | 结果（post-verifyEmail sent_code） |
|------|------|-----------------------------------|
| A | api_id=6 baseline | _待填_ |
| B | api_id=4 | _待填_ |
| C | telegram_x 21724 | _待填_ |
| D | telegram_9 / 9.6.7 | _待填_ |
| E | device_max=1 proxy_max=1 | _待填_ |

详细 JSON：`data/ab_reports/payment_bypass_ab_*.json`

## 根因假设排序（实验前）

1. **official 路径 + 国家 SMS 成本策略**（文档支持）— 最可能
2. **api_id=6 官方身份** vs custom api_id（历史 A/B 强烈支持）
3. 设备指纹重复 / IP 质量 — E 实验可否定或部分支持
4. app_version 新旧 — D 实验
5. Telegram X 独立策略 — C 实验
6. CodeSettings / Push attach — official 模式已固定 attach，变量已控

## 建议路径

若需 **自动化注册成功率**，关闭 `official_client_emulation`，使用 `balanced + custom api_id`（历史 iq/co/pe 数据支持）。

若业务 **必须官方 Push/Firebase 链路**，需接受 PaymentRequired 或接入真实 Play 内购（`payments.assignPlayMarketTransaction`），无免费绕过。
