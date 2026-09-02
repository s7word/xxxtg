# 俄语圈「1 美元注册墙」调研（Paid Auth）

> 时间：2026-09-02  
> 方法：优先俄语新闻 / DTF / inbusiness / Telegram Soft Expert / 官方 TL 文档 / DrKLO Android `LoginActivity`  
> **本轮未租号测试**，只吸收可能改变策略的关键信息。

---

## 1. 这堵墙在俄语媒体里叫什么

常见说法：

| 俄语表述 | 含义 |
|----------|------|
| платная регистрация / платная авторизация | 付费注册 / 付费登录 |
| оплата SMS / SMS стоит дорого | SMS 太贵，成本转嫁给用户 |
| нужна Premium подписка | 必须买一周 Premium 才能继续 |

**官方 TL 名：** `auth.sentCodePaymentRequired`（文档亦写作 PaymentRequired / Paid auth）  
链接：[constructor](https://core.telegram.org/constructor/auth.sentCodePaymentRequired) · [Paid auth 段落](https://core.telegram.org/api/auth#paid-auth)

官方原文要点（英）：

> Official apps may receive this constructor… due to the **high cost of SMS** for the user's country/provider, the user must purchase a **Telegram Premium** subscription… **flow only usable by official clients**.

这与我们实测（api_id=6 走完邮箱后 100% 进墙）一致：**不是 bug，是产品设计。**

---

## 2. 俄语报道里「为什么收 1 美元」——三条动机（可改变我们怎么选号）

来源综合：

- [DTF：Кения / ЮАР 等国要 Premium](https://dtf.ru/id1251970/3981024-telegram-platnaya-registratsiya-keniya-yuar)（2025-08）
- [inbusiness.kz：德 / 巴西 / 非洲，约 1–1.5 USD，附一周 Premium](https://inbusiness.kz/ru/last/telegram-nachal-brat-s-polzovatelej-dengi-za-avtorizaciyu)（2025-11）

### 动机 A：SMS 运营商成本高

非洲等地区对 A2P SMS 定价极高，Telegram 不愿再免费垫付。

### 动机 B：虚拟号 / 农场刷号

俄语与 Reddit 虚拟号圈反复提到：这些国家虚拟 SIM 被大量用于批量开号，  
**Premium 门槛 = 经济过滤器**——真人还能掏 1$，刷号不划算。

> **对我们的含义：** 用 smsbower/Grizzly 这类虚拟号 + 高成本国家（iq/ma/ke/za…），  
> **进 Payment 墙是预期结果**，不是「指纹差了一点」就能消掉。

### 动机 C：补偿形式 = 一周 Premium，不是「只买短信」

金额报道约 **1–1.5 USD**（哈萨克报道约 650 坚戈量级）；  
付完后服务端侧绑定短期 Premium，再继续发码。

用户侧投诉（俄语频道转述）：

- 扣款失败 / 扣了但没码
- 有人扣了 ~99 ₽ 后仍收到「普通 SMS」才登进去  

说明：**即使真人官方客户端付费，链路也不 100% 干净**——自动化假收据更不可能。

---

## 3. 真正过墙步骤（Android 源码，比 resendCode 重要）

`DrKLO/Telegram` → `LoginActivity.java`（本轮直接 grep）：

| 步骤 | 证据 |
|------|------|
| 收到 `TL_auth_sentCodePaymentRequired` | `setPage(VIEW_PAY, …)`，**不会**去 `resendCode` |
| 商品 / 天数 | `store_product`、`premium_days`（常见 7） |
| 发票类型 | `TL_inputInvoicePremiumAuthCode`（登录专用，不是普通礼物 Stars） |
| 结算 | Play Billing → `TL_payments_assignPlayMarketTransaction` |
| 成功后 | 会话继续登录 UI（下一跳 sent_code 来自支付成功回调 / Updates，不是空壳 Sms） |

**原则结论（再次确认）：**

1. `auth.resendCode` **不是**过墙 API。我们看到的 `SentCodeTypeSms` 空壳，俄语社区也没有把它当「成功捷径」。
2. Fragment / Stars / 第三方「代开一周 Premium」若不能产出 **Play/App Store 收据并 `assign*Transaction`**，对 **auth PaymentRequired** 无效（与普通给已有号开通 Premium 不是同一条链）。

---

## 4. 俄语「注册机」圈怎么避开这堵墙（行业惯例）

来源：[Telegram Soft Expert — Генератор параметров](https://ru.telegramexpert.pro/manuals/generator-parametrov)

| 他们默认 | 数值 | 含义 |
|----------|------|------|
| Android 注册 | **api_id=4** / hash `014b35…5103` | 「能建号」标准 Android，**不是**现网 Play 的 6 |
| Telegram X | **21724**（手册另有一处写 21724 变体 hash；Play X 常见 21724） | 多账号客户端，**≠4** |
| Desktop | 2040 | **不能新建号**，只能登已有号 / 转 TDATA |
| 时区 | 强调必须与号码国家一致，否则易批量封 | 我们可能漏的细节 |
| 机型 / app_version / 语言 | 要像真人手机 | 与 vault 成功样本一致 |

**关键信息（可能改变结果）：**

1. 俄语农场主流仍押 **api_id=4 建号**，把 **api_id=6** 留给「像官方、于是被收税」的路径——与我们 vault（+91 成功全是 4）一致。  
2. 他们几乎不讨论「用 MTProto 伪造 1$ 过墙」；策略是 **别进官方付费流**（选号段/国家/客户端身份），而不是破解 Payment。  
3. Desktop 路径建号被明确否定——别在 Desktop api_id 上浪费时间。

---

## 5. 国家维度：俄语报道点名的区域

| 区域 | 报道提及 | 与我们实验 |
|------|----------|------------|
| 肯尼亚 / 南非 / 尼日利亚 | DTF：要 Premium | 未测，但同属「高 SMS 成本 + 虚拟号重灾」 |
| 德国 / 巴西 / 非洲多国 | inbusiness | 登录也可能付费 SMS |
| 伊拉克 / 印尼 / 秘鲁 | 本仓库 official+6 | **100% Payment**（自测） |
| 俄罗斯本土 | 另有「运营商限 SMS / 邮箱登录」叙事（Habr 961856 等） | 与 Paid auth 不同议题，勿混淆 |

**策略含义：**  
「换国」只有在该国 **SMS 便宜 + 虚拟号池不脏 + 身份不是官方 6** 时才有意义。  
iq/ma 继续烧 official-6，俄语圈视角属于已知亏损区。

---

## 6. 可能改变我们结果的「关键信息」清单

| # | 信息 | 可信度 | 对我们意味着什么 |
|---|------|--------|------------------|
| 1 | Paid auth **仅官方客户端流程**（文档原话） | 高 | 坚持 api_id=6 = 自愿进税区 |
| 2 | 过墙 = **商店内购 + assignPlayMarketTransaction**，不是 resend | 高（源码） | 停掉「空壳 Sms」方向的投入 |
| 3 | 虚拟号是触发墙的原因之一（DTF） | 中高 | 同国实体卡 vs 虚拟号可能不同；自动化难用实体卡 |
| 4 | 俄语注册机默认 **api_id=4** 建号 | 高 | 与用户直觉一致；主攻 4+Push+完整指纹，而不是 6 |
| 5 | 时区 / 语言 / 机型要与号码国家一致 | 中（行业经验） | 查 vault 与当前 profile 是否错配 |
| 6 | 付费后仍可能无码 / 扣款异常 | 中（用户投诉） | 即便未来接真 IAP，也要准备失败率 |
| 7 | 第三方 SMS API 需 `sms@telegram.org #enableSMS` | 高（文档） | 自建 api_id 走 SMS 是商务通道，不是破解 |
| 8 | 仅官方移动端可用 FirebaseSms | 高 | 半官方指纹 → 收税但不给 Firebase，和我们观测一致 |

---

## 7. 建议你怎么一起判断（原则题）

1. **目标若是「伊拉克虚拟号 + 不付 1$ + api_id=6」**：俄语资料 + 官方文档 + 我们实验 → **原则死胡同**。  
2. **目标若是「像俄语农场一样用 api_id=4 批量建号」**：墙不是主矛盾；主矛盾是 **Push/Integrity/号池/App-only**——应继续抠 4 的细节，而不是 Payment。  
3. **目标若是「可以付 1$」**：必须上真机 Play Billing 或官方可验证收据；MTProto 假收据已证明无效。

---

## 8. 来源索引

- [core.telegram.org — Paid auth](https://core.telegram.org/api/auth#paid-auth)  
- [auth.sentCodePaymentRequired](https://core.telegram.org/constructor/auth.sentCodePaymentRequired)  
- [DTF — платная регистрация (KE/ZA)](https://dtf.ru/id1251970/3981024-telegram-platnaya-registratsiya-keniya-yuar)  
- [inbusiness.kz — платная авторизация ~1–1.5$](https://inbusiness.kz/ru/last/telegram-nachal-brat-s-polzovatelej-dengi-za-avtorizaciyu)  
- [Telegram Soft Expert — api_id 4 / 21724 / Desktop 不能注册](https://ru.telegramexpert.pro/manuals/generator-parametrov)  
- DrKLO `LoginActivity.java`：`VIEW_PAY` / `TL_inputInvoicePremiumAuthCode` / `assignPlayMarketTransaction`  
- 本仓库：`docs/OFFICIAL_AND_PAYMENT_EXPLAINED.md`、`GROK_SMS_AFTER_PAYMENT_FOLLOWUP.md`

---

## 9. 下一步（调研向，暂不强制开测）

1. 对照俄语农场 checklist：时区秒偏移、`lang_pack=android`、app_version 与 vault 对齐，做 **api_id=4 差异表**（工程清单，可后测）。  
2. 若坚持 iq：只评估 **实体 SIM + 真机官方 App + 真 1$** 是否愿意承担成本。  
3. 停止把 Payment 后 `SentCodeTypeSms` 当进度条。  
