# 调研拓展：网上资料 × 代码审计 × 新思路 / 问题清单

> 生成：2026-09-02 · 分支 `cursor/research-expansion-88d6`  
> 本轮**没有租号**，纯资料 + 对照官方 Android 源码（DrKLO/Telegram `LoginActivity.java` / `ConnectionsManager.java`）+ 本仓库与本机 Telethon 1.44.0（layer **227**）。  
> 实验数字一律引用既有文档，不以本轮新测为准。  
> 概念基线仍以 [OFFICIAL_AND_PAYMENT_EXPLAINED.md](./OFFICIAL_AND_PAYMENT_EXPLAINED.md) 为准；本文补它没写到的**协议步骤差**和**半官方指纹**。

读完应能回答：

1. 官方 App 过 Paid auth 时，**到底调了哪些 RPC、返回值长什么样**（不是我们猜的 `checkPaidAuth` 一条龙）。  
2. 我们自称 api_id=6 官方 Android，但 InitConnection / CodeSettings 有哪些位和真机**系统性不一致**。  
3. 哪些「绕过」说法只对**已有账号登录**成立，对**新号注册**无效。

---

## 0. 与旧文档的关系（先咬死）

仍成立、本文不推翻：

- iq/id/pe/ma 上 **api_id=6** 走完 email 后 PaymentRequired **100%**（survey 18/18 + H3/H8）。  
- 假 Play 收据 → `PLAYMARKET_RECEIPT_INVALID`。  
- Payment 后 `auth.resendCode` → `SentCodeTypeSms` **接码 0 码**（空壳工作假设）。  
- 无 Push 发 4/6 → `API_ID_PUBLISHED_FLOOD`；有 Push 仍可能 FLOOD。  
- vault 历史成功 = **api_id=4 + Push + 12.7.3 + 非 emu**，不是 api_id=6。

本文新增、旧文没写清或写错的：

| 旧表述 | 校正 |
|--------|------|
| 过墙 = `assignPlayMarketTransaction` **然后** `auth.checkPaidAuth(form_id)` | **两条官方付款链**：Play/App Store IAP 走 `canPurchaseStore` → 商店 SDK → `assign*Transaction`，成功返回 **`Updates` + `updateSentPhoneCode`**（里面才是下一跳 sent_code）。`checkPaidAuth` 是 **PaymentForm / Stars 发票**那条链的轮询，`form_id` 来自 `payments.sendPaymentForm`，不是 Play 收据。 |
| `CodeSettings.token` 在 Android 上填 REGHelp Push「接近官方」 | 官方文档写明 token/app_sandbox **仅官方 iOS Firebase**。官方 **Android 源码 sendCode 从不设这两个字段**；FCM token 是登录**之后** `registerForPush`。Android + token = 半官方杂交。 |
| InitConnection 设备字符串够了 | Telethon **硬编码** `lang_pack=''`，注释 `"langPacks are for official apps only"`。官方 Android 发 `lang_pack="android"`。我们握手里 `langpack.getLanguages(lang_pack=android)` 是**事后 RPC**，改不了已发出的 InitConnection。 |
| [GROK_IQ_SPRINT_PLAN.md](./GROK_IQ_SPRINT_PLAN.md)「当前 CodeSettings 未设 allow_firebase / unknown_number」 | **过时**。`code_delivery.py` 默认已 `allow_firebase=true`、`unknown_number=true`；H4 已测 0 次 FirebaseSms。 |

---

## 1. 网上资料（按主题）

可信度：高 = core.telegram.org / 官方源码 / TDLib；中 = 库维护者 / 可复核的 issue；低 = 营销博客、YouTube、Reddit 个案。

### 1.1 `sentCodePaymentRequired` / Paid auth / store product

| 来源 | 要点 | 可信度 |
|------|------|--------|
| [auth.sentCodePaymentRequired](https://core.telegram.org/constructor/auth.sentCodePaymentRequired) | 仅官方 App；SMS 成本高 → 必须买短期 Premium。constructor 页仍画 **`#e0955a3c`（无 premium_days）**，字段表却列了 `premium_days`。 | 高（文档自身打架，见 §4） |
| [api/auth Paid auth](https://core.telegram.org/api/auth) | 现行 schema **`#f8827ebf` + premium_days**。列出 `canPurchaseStore`、`assignPlayMarketTransaction` / `assignAppStoreTransaction`、`getPaymentForm` / `sendPaymentForm`。 | 高 |
| [inputStorePaymentAuthCode](https://core.telegram.org/constructor/inputStorePaymentAuthCode) | 页上 **`#9bb2636d` 无 premium_days**；layer changelog 与 Telethon / 官方 Android 实际是 **`#3fc18057` + premium_days**。 | 高（又一次文档滞后） |
| [payments.assignPlayMarketTransaction](https://core.telegram.org/method/payments.assignPlayMarketTransaction) | 官方 App only；未登录可调；入参 `receipt:DataJSON` + `purpose`。 | 高 |
| [payments.canPurchaseStore](https://core.telegram.org/method/payments.canPurchaseStore) | **买之前必须调**。可能 `PREMIUM_CURRENTLY_UNAVAILABLE`。 | 高 |
| [auth.checkPaidAuth](https://core.telegram.org/method/auth.checkPaidAuth) | `form_id` = **传给 `payments.sendPaymentForm` 的表单 ID**，不是 Play `purchaseToken`。 | 高 |
| [api/premium](https://core.telegram.org/api/premium) | 商店订阅流 `assignPlayMarket*` **不对第三方开放**（和 Paid auth 同一句话）。Stars / PremiumBot 发票是**已登录用户**买 Premium，不是 `inputStorePaymentAuthCode`。 | 高 |
| [api/stars](https://core.telegram.org/api/stars) | 同上：store-based subscription **currently not available to third-party apps**。 | 高 |
| [TDLib WaitPremiumPurchase](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1authorization_state_wait_premium_purchase.html) | `checkAuthenticationPremiumPurchase` → 商店内购 → `setAuthenticationPremiumPurchaseTransaction`。对应 MTProto：`canPurchaseStore` + `assign*Transaction`。 | 高 |
| [t.me/tginfoen](https://t.me/s/tginfoen/2076)、[Telegram-iOS #2168](https://github.com/TelegramMessenger/Telegram-iOS/issues/2168) | 真用户 SMS Fee；维护者：**server-controlled**。 | 高 |
| [tech-ish 2025-11 Kenya](https://tech-ish.com/2025/11/27/telegram-charging-one-week-premium-subscription-for-sms-verification/) | 肯尼亚等国新设备登录也收一周 Premium；提到 P2PL（志愿者代发 OTP）。 | 中（新闻，不是协议） |

**官方 Android 过墙（源码，不是猜测）** — `LoginActivity.java` `VIEW_PAY` / `LoginPayView`：

```text
sendCode / verifyEmail
  → TL_auth_sentCodePaymentRequired
  → 切到 VIEW_PAY（源码在这里 return，绝不 resendCode）
  → Play Billing queryProductDetails(store_product)
  → 用商店标价填 inputStorePaymentAuthCode
       (currency/amount 来自 offer，premium_days 来自 PaymentRequired)
  → payments.canPurchaseStore(purpose)
  → 若已有未消耗购买：assignPlayMarketTransaction(receipt=purchase.getOriginalJson(), restore=true)
  → 否则 launchBillingFlow
  → 成功：response instanceof Updates
       从中取出 updateSentPhoneCode.sent_code
       fragment.open(phone, sent_code)   ← 真正的下一通道
  → 另一条：PaymentFormActivity 才 startPoll → auth.checkPaidAuth(form_id)
```

关键：Play 收据被接受后，**下一跳 sent_code 塞在 Updates 里**，不是 `resendCode`，也不是我们探测用的假 JSON。

### 1.2 `API_ID_PUBLISHED_FLOOD` 与 layer / 设备

| 来源 | 要点 | 可信度 |
|------|------|--------|
| [obtaining_api_id](https://core.telegram.org/api/obtaining_api_id) | 开源样例 api_id 仅测试；发布用会 `API_ID_PUBLISHED_FLOOD`。 | 高 |
| [auth.exportLoginToken 错误表](https://core.telegram.org/method/auth.exportLoginToken) | 明确列出该错误：api_id 已公开。 | 高 |
| Telethon 源码 `telegrambaseclient.py` | InitConnection **`lang_pack=''`**，注释官方语言包不对第三方。layer=227。 | 高（本机 1.44.0） |
| [initConnection](https://core.telegram.org/method/initConnection) | 公开 TL 只有 device/system/app/lang + 可选 `params.tz_offset`。 | 高 |
| 官方 `ConnectionsManager.native_init` | 额外传入 **installer、packageId、timezoneOffset、regId/fingerprint**。这些走 native，**不在公开 initConnection 字段里**。Play 安装 installer=`com.android.vending`，package=`org.telegram.messenger`。 | 高（源码） |
| UnifyPort 等 SEO 文 | 「立刻停用泄露 ID、去 my.telegram.org 申请」——对**第三方产品**对；对我们「必须用 4/6 才可能 SMS」是死胡同建议。 | 低～中 |

**没有**公开资料表明「换 layer / 改 SDK 字符串」能单独解除 PUBLISHED_FLOOD。本仓库：无 Token 必 FLOOD；有 Token 在 api_id=4 上仍可能 FLOOD（G1）。FLOOD 看的是 **api_id 黑名单 + 平台凭证是否被承认**，不是 layer 数字。

### 1.3 Firebase SMS / Play Integrity 与注册

官方原文（[api/auth](https://core.telegram.org/api/auth)）必须整句留下：

> In some conditions when signing up or logging in using an SMS code/call, **only** `auth.sentCodeTypeFirebaseSms` may be used.  
> Currently, **only mobile official apps** can make use of Firebase SMS authentication: this means that in some conditions, **only the official applications can receive a login/signup code via SMS/call**.

流程：

1. `CodeSettings.allow_firebase=true` 才可能收到 `SentCodeTypeFirebaseSms`。  
2. Android：把 `play_integrity_nonce` 交给 **Play Integrity API**（project_id 来自 constructor），JWS → `auth.requestFirebaseSms.play_integrity_token`。旧路径 SafetyNet `safety_net_token`。  
3. **`requestFirebaseSms` 返回 true 之后短信才发**；false 或拿不到 token → **必须** `auth.resendCode(reason=…)`。`reason` 文档写明 **Official clients only**。  
4. 官方 Android 源码里的 reason 字符串（完整性失败回退 SMS 的正式入口）：

| reason | 何时 |
|--------|------|
| `PLAYINTEGRITY_TOKEN_NULL` | Integrity 返回空 token |
| `PLAYINTEGRITY_REQUESTFIREBASESMS_FALSE` | RPC 返回 false |
| `PLAYINTEGRITY_EXCEPTION_*` | Integrity SDK 抛错 |
| `SAFETYNET_*` / `GOOGLE_PLAY_SERVICES_NOT_AVAILABLE` | 旧 SafetyNet / 无 GMS |

iOS：`CodeSettings.token` = APNS device token，等 push 里的 `ios_push_secret`。

**和注册的原则关系：** 在「只允许 FirebaseSms」的国家/条件下，没有真机 Play Integrity（包名 `org.telegram.messenger`、Play 签名、云项目号匹配）→ **原则上收不到运营商短信**。这比 Payment 墙更早：我们在 iq/ma **0 次**收到 FirebaseSms constructor，说明服务端根本没把我们放进这条「官方 SMS」漏斗，而是放进 Email→Payment 漏斗。

REGHelp `/integrity/getToken` 是**另一家网关**的 attestation，不是 Google 对官方包出具的 JWS。即便哪天收到 FirebaseSms，用它调 `requestFirebaseSms` 也极可能 false，然后应走官方 **带 reason 的 resend**——我们现在**根本没写这条回退**。

### 1.4 「绕过」声称（全部标注可信度）

| 声称 | 来源例子 | 对**新号注册** | 可信度 | 原则障碍？ |
|------|----------|----------------|--------|------------|
| Payment 后点 resend / 等一会就有 SMS | 无官方帖；我们自己测过 | **否**。官方源码 Payment 后进 VIEW_PAY，不 resend。我们 resend 得到空壳 Sms。 | 已否证 | 是（未付款） |
| 假收据 / 改 JSON | — | 已测 `PLAYMARKET_RECEIPT_INVALID` | 已否证 | 是（欺诈 + 无效） |
| 关 official 旗标 | 旧内部结论 | H3：api_id=6 仍 Payment | 已否证 | 认的是 api_id |
| 自建 api_id | [Telethon #3835](https://github.com/LonamiWebs/Telethon/issues/4047) 维护者：2023-02-18 起第三方无 SMS | App-only，不是 SMS | 高 | 是（政策） |
| 换 Telegram X | 营销/Reddit | 本仓库 21724 **未能稳定 sendCode**；即便发出去仍是官方变体，可能照收 Payment | 低 | 未验证能否免 Payment |
| 旧版 APK 11.9 / 11.7.3 / WhatsApp 收码 | [lilys.ai](https://lilys.ai/en/notes/telegram-20260128/telegram-sms-fee-fix)、YouTube | 要求**同一号码已有 WhatsApp**。接码虚拟号没有。本仓库 **9.6.7 仍 Payment**。 | 低（登录旧号个案）；注册无效 | 对虚拟号是 |
| 换干净住宅 IP / 设备 | Reddit | 实验 E：全新设备+代理 **仍 Payment** | 已否证（Payment 国） | 对 Paid auth 是 |
| 第三方「一周 Premium 解锁码」 | AccountBoy / NexSMS | 协议上必须商店收据或 sendPaymentForm。解锁码要么骗局、要么收据转移（违规）、要么给**已有账号**充 Premium | 商业宣传 | 合规禁止 |
| Stars / Fragment 付 auth | 博客混淆 | 不同 constructor / 不同 purpose；Fragment 要先能登录 | 文档已区分 | 是 |
| QR / Passkey / 「另一台设备收码」 | Kenya 新闻、opentele QR 文 | **已有 session** 的登录。新号没有已登录设备。QR 的 `exportLoginToken` 对泄露 api_id 还会 PUBLISHED_FLOOD | 中（登录）；注册无效 | 对注册是 |
| `force_sms` / 立刻 resend 逼出 SMS | [TechnetExperts](https://www.technetexperts.com/telethon-force-sms-verification-code/) | Telethon 已废弃 force_sms；App→resend 对**老号**有时有用，对 Payment 国空壳、对无 next_type 的 App 也救不了 | 低（过时） | 部分 |
| `sms@telegram.org` `#enableSMS` | 官方 / MadelineProto | 给**自己的** my.telegram.org api_id 开 SMS，不是给泄露官方 ID 开后门 | 高 | 与「只用 4/6」目标冲突 |
| P2PL 志愿者代发 OTP | 新闻 | 已登录 Premium 用户给别人发码；不是注册 API | 中 | 自动化用不上 |

**社区库态度（与我们一致）：**

- Telethon 高层 `send_code_request` 假定返回值有 `.type`；`SentCodePaymentRequired` **没有** `.type`。  
- [PyroTGFork](https://github.com/TelegramPlayGround/PyroTGFork/blob/f1dc08e/pyrogram/types/authorization/sent_code.py)：遇到 PaymentRequired 直接 `raise Unauthorized`，`TODO: CheckPaidAuth`，**currently not supported**。

没有找到任何可复核的「不付钱过 Paid auth」技术帖。

---

## 2. 本地代码 / 配置审计（比旧文档更深一层）

### 2.1 Payment / resend / email / push：和官方步骤对不上的地方

对照 `registrar.resolve_sent_code_channel`、`_probe_assign_play_market`、`_complete_firebase_sms`、`_maybe_resend_to_sms`。

| 官方步骤 | 我们 | 矛盾 / 未实现 |
|----------|------|----------------|
| Payment → **VIEW_PAY**，禁止 resend | `payment_required_probe=resend/both` 时立刻 `auth.resendCode` | 官方不会这么做。实验已证明空壳 Sms。默认 `off` 还好；打开就是在走**文档未定义路径** |
| 先 `canPurchaseStore` | **从未调用** | 缺官方强制前置。错误码（`PREMIUM_CURRENTLY_UNAVAILABLE` 等）我们看不到 |
| Play Billing 真收据 `purchase.getOriginalJson()` | 假 JSON：`GROK_PROBE_NOT_A_PURCHASE` | 已验证 400；不是 bug，是没收据 |
| `assignPlayMarket` 成功 → **`Updates` / `updateSentPhoneCode`** | 只记「意外返回的 TL 类型名」，**不解析 Updates 里的 sent_code** | 即便哪天意外成功，也会当成失败快退 |
| `purpose.restore=true` 仅当已有未消耗购买 | 探测不设 restore | 小差；无真购买时无意义 |
| 发票链才 `checkPaidAuth(form_id)` 轮询 | 代码里 **零次** `CheckPaidAuthRequest` / `CanPurchaseStoreRequest` | 旧文档把两条链写成一条，实现两条都没走完 |
| Email：`sendVerifyEmailCode` + `verifyEmail` | 有，smsbower 邮箱 | 与官方同构；verify 后 100% Payment（已测） |
| Firebase：Integrity → `requestFirebaseSms`；失败 **resend(reason=PLAYINTEGRITY_*)** | 缺 nonce/version/token 就 **跳过并按已有短信通道轮询** | **逻辑自相矛盾**：FirebaseSms 在 `requestFirebaseSms==true` 之前**不会发短信**。我们既没发 Integrity，也没带 reason 的官方回退 |
| App 且无 next_type：等用户点 resend（有 timeout 才允许） | `force_resend_on_app` **默认 true**，无 next_type 也立刻 resend | 比官方更吵；容易 FLOOD_WAIT；不能把 App 变成 SMS |
| Push：登录后 `account.registerDevice` | 把 REGHelp token 塞进 **iOS** `CodeSettings.token` | 见 2.2 |

`auth.resendCode.reason`：Telethon 1.44 已支持该可选字段；我们 `_maybe_resend_to_sms` **从不传 reason**。文档写明 reason 仅官方、且用于 Integrity 失败。Payment 后 resend 不带 reason，官方也不会在 VIEW_PAY 调它。

### 2.2 CodeSettings：官方 Android 会设的位 vs 我们

官方 `LoginActivity` 构造 `TL_codeSettings`（约 3084–3151 行）vs `registrar._build_code_settings`：

| 字段 | 官方 Android | 我们 | 是否漏 / 错 |
|------|--------------|------|-------------|
| `allow_app_hash` | = `has Google Play services` | 按平台：Android true / iOS false | 近似。官方与 firebase **绑在同一个 hasServices** |
| `allow_firebase` | 同上，但 `forceDisableSafetyNet` 或空 `SAFETYNET_KEY` 时 **强制 false** | 默认 true（配置） | 位开了，但 **从未收到 FirebaseSms** → 服务端不认我们是可走 Firebase 的官方端 |
| `allow_flashcall` / `allow_missed_call` | 要有 SIM + 权限 | 默认 false | 与接码环境一致，合理 |
| `current_number` | 仅当 flashcall：与本机 SIM `PhoneNumberUtils.compare` | 写死 `False`（序列化=不置位） | flashcall 关时与官方等价 |
| `unknown_number` | **只在 `allow_flashcall` 块里**设；能读到本机号码则为 false | 默认 **true（无 flashcall 也置位）** | **官方不会这样发**。语义是「有 SIM 但读不出号」，不是「虚拟号」 |
| `logout_tokens` | 最多 20 条 future_auth_token（登录/登出留下的） | **不发** | 新设备注册官方也可以为空；不是主因 |
| `token` / `app_sandbox` | **不设**（iOS Firebase 专用） | push_required 时填 REGHelp token，`app_sandbox=False` | **最大协议级偏差**。文档：Used only by official iOS apps |
| （无）FCM | 登录后另 RPC 注册 | 未登录就把「推送凭证」混进 sendCode | 半官方 |

结论：漏的不是「再开一个布尔就能 SMS」。漏的是 **Android 不该带 token、InitConnection 不该空 lang_pack、Firebase 失败不该假装已经在等短信**。`allow_firebase` 我们已经开过，H4 无效。

### 2.3 设备层 vs api_id=6：「半官方」清单

`TelegramClient(...)` 只传了 `device_model / system_version / app_version / lang_code / system_lang_code`。Telethon 随后：

```python
# telethon/client/telegrambaseclient.py
self._init_request = functions.InitConnectionRequest(
    ...
    lang_pack='',  # "langPacks are for official apps only"
    query=None,
    proxy=init_proxy
    # params 缺省 None → 不发 tz_offset
)
```

| 信号 | 真机 Play 版官方 APK (api_id=6) | 我们 telegram_android + api_id=6 |
|------|--------------------------------|----------------------------------|
| api_id / hash | 6 / `eb06d4…581e` | 能对齐（`OFFICIAL_API_CREDENTIALS`） |
| `lang_pack` | `"android"` | **`""`（Telethon 写死）** |
| `params.tz_offset` | native timezoneOffset | 配置里有 `tz_offset`，**从不放进 InitConnection**（只打日志） |
| installer | `com.android.vending` | **发不出**（不在公开 TL） |
| package | `org.telegram.messenger` | **发不出** |
| CodeSettings.token | 无 | REGHelp 第三方 token |
| Play Integrity | GMS 真机 | REGHelp 网关；且 Payment 国 0 次 FirebaseSms |
| 握手 `langpack.getLanguages(android)` | 与 InitConnection 一致 | InitConnection 空包名、事后却要 android 语言包 → **自相矛盾** |
| 指纹库抽样 | 真机 ROM | 可抽到非 12.x / 非 android lang_pack 行；`telegram_android` 还会继承库里的 api_id 再 `apply_official_api_id` 纠正 |

服务端因此可以同时：

- 凭 **api_id=6** 把我们当官方，**收 Paid auth 的税**（已观测）；  
- 凭 **空 lang_pack + iOS token + 无 Integrity**，不给我们 FirebaseSms（已观测 0 次）。

这就是旧文说的「最差的那种被识别」，这里补上**可核对的字段级证据**。

`telegram_x`：`lang_pack=android_x`、api_id=21724。即便以后改 Telethon lang_pack，X 与主版 Android 仍不是同一身份；历史未 sendCode 成功。

### 2.4 配置默认值是否仍推向死胡同

**Schema 默认**（`AppConfigModel`，未覆盖时）：

| 键 | 默认 | 风险 |
|----|------|------|
| `active_app_type` | `telegram_android` → **api_id=6** | 只要凭证策略走到官方 ID，就是 Payment 身份 |
| `api_credential_mode` | `auto` | 有 Push 时用模板 6；无 Push 才可能回退自建（自建又 App-only） |
| `official_client_emulation` | `false` | 关旗标**不够**，见 H3 |
| `code_delivery_mode` | `balanced` | 泄露 ID 仍强制 Push |
| `code_settings_allow_firebase` | `true` | 已测无 FirebaseSms |
| `code_settings_unknown_number` | `true` | 与官方 flashcall 绑定语义不符 |
| `force_resend_on_app` | `true` | 在 App-only 号池上空转 RPC |
| `payment_required_probe` | `off` | 合理；打开 resend 只会烧空壳 |

**当前服务器 `data/config.json`（不入库，只作现场审计）：**

```text
active_app_type            = telegram_android     → api_id=6
api_credential_mode        = official
official_client_emulation  = true
code_delivery_mode         = push_required
code_settings_allow_firebase / unknown_number = true
payment_required_probe     = off
pin_app_version_substr     = ""                   → 不钉 12.7.3
custom_api_id              = 35337905             → official 模式根本用不到
target_country             = it                   → 未在本轮测；不能假设免 Payment
```

这是一条**组合死胡同**：旗标 + official 凭证 + 主版 Android 模板 = 稳定走进 Email→Payment。UI `useConfig.js` 里 emu 默认 false，**挡不住**已经写进 config.json 的 true，也挡不住 `telegram_android` 模板。

前端设置页仍把 `official_client_emulation` 画成可完成注册的开关，文案未标明「api_id=6 ⇒ Paid auth」。

---

## 3. 拓展思路清单

每条：来源、已验证/未验证、原则障碍？、代价。  
**本轮未租号。** 标「0–2 次验证」的才值得以后极小探测。

| # | 思路 | 来源 | 状态 | 原则障碍？ | 代价 |
|---|------|------|------|------------|------|
| A | **InitConnection `lang_pack="android"`**（构造 Client 后改 `client._init_request.lang_pack`，必要时补 `params.tz_offset`） | Telethon 源码注释；官方 InitConnection；opentele 指纹文 | **未验证**是否改变 iq 的 Email/Payment/FLOOD | 可能只是更像官方 → **更容易 Payment**，不是绕过。但可能是「进入 FirebaseSms 漏斗」的前提 | 工程小；验证 0–2 次 sendCode |
| B | **Android 不 attach CodeSettings.token**（只靠 lang_pack + firebase 位） | 官方 Android sendCode 源码；token 文档 iOS-only | **未验证**。无 Token 的 4/6 历史必 FLOOD；未知空 lang_pack 是否是 FLOOD 主因之一 | 若 FLOOD 仍在，说明 Token 是 PUBLISHED 闸，去掉会更糟 | 0–2 次：api_id=6 + lang_pack=android + **不** attach token |
| C | **收到 FirebaseSms 后走官方失败回退**：`requestFirebaseSms` 失败则 `resendCode(reason=PLAYINTEGRITY_REQUESTFIREBASESMS_FALSE)` | 官方 LoginActivity；[auth.resendCode](https://core.telegram.org/method/auth.resendCode) `reason` | 代码**未实现**；Payment 国 **0 次 FirebaseSms**，当前进不去 | 若永远收不到 FirebaseSms，这条是死的。若 A/B 能打出 FirebaseSms，这才是官方「Integrity 失败 → 真 SMS」路径 | 先依赖 A/B；实现小 |
| D | **Play 链补全探测（仍无真收据）**：`canPurchaseStore` → 假 `assignPlayMarket` → 若 Updates 则解析 `updateSentPhoneCode` | 官方 LoginPayView | 缺 canPurchaseStore / 缺 Updates 解析。假收据仍应 400 | **不能**无收据过墙。只补齐观察面，避免以后真 IAP 时把成功当失败 | 0 租号可单测 RPC；真 IAP 要真机真钱 |
| E | 真机官方 APK + 真 Play 内购 `telegram_premium.one_week.auth` | 文档 + 源码唯一过墙路径 | 本仓库**未做** | 对「官方路径不付钱」是原则墙；对「付钱后虚拟号能否收到」仍是第二堵墙 | 高：$1 + 真 Google 账 + 真机；自动化做不了 Billing |
| F | 钉死 vault：api_id=4 + 12.7.3 + Push + **lang_pack=android** + 非 emu，换时段/号源（+91 或非 Payment 国） | [VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md](./VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md) | 历史成功已验证；**当前 iq FLOOD / in App-only** | 不是 Payment；是号池+窗口。原则：App-only 换号救，FLOOD 换窗口/Token 质量 | 中（号钱）；不要再烧 iq api_id=6 |
| G | 实体 SIM + 官方 App（可付钱或看是否仍 Payment） | 旧文 C.4 | 未验证 | 虚拟号段屏蔽是第二原则墙 | 高，出自动化范围 |
| H | Telegram X / 21724 + 修 Push | 社区「X 不收费」 | sendCode 未稳定 | 即使发出去仍可能官方待遇 | 先修 Push 再 0–2 次；勿当主路 |
| I | 旧 APK / WhatsApp 收码 | YouTube | 9.6.7 已 Payment；虚拟号无 WhatsApp | 对注册虚拟号是 | 不要再测 |
| J | Stars / Fragment / 代付解锁码 | 博客 | 协议对象不对；代付违规 | 是 / 合规禁止 | 不做 |
| K | QR / Passkey / 多设备 | 新闻、Telethon qr_login | 只服务已有账号 | 对**注册**是 | 不做（目标若是注册） |
| L | `#enableSMS` 自建 api_id | 官方邮件政策 | 与「只用 4/6」冲突；历史自建 App-only | 政策墙 | 除非目标改成第三方 App 登录已有号 |
| M | `unknown_number` 改回与官方一致（仅 flashcall 时置位） | LoginActivity | 未验证；H 已开此位无 Firebase | 低优先级指纹卫生 | 几乎免费，信息量可能为零 |
| N | installer/package 仿真 | native_init | **公开 MTProto 没有这两字段** | 原则上 Telethon 发不出去 | 除非改 C 层/自定义连接，代价大且像对抗 |

**明确不要再做：** iq/ma 上 api_id=6 Email→Payment→resend 碰运气；再延长空壳 SMS 等待；再换 smsbower/Grizzly 同国虚拟号；无 Push 裸发 4/6；自建 ID 当 SMS 方案。

---

## 4. 发现问题清单

### 4.1 实现 / 逻辑

1. **Payment 探测链不完整且认错成功信号**（`registrar._probe_assign_play_market`）：无 `canPurchaseStore`；无 `checkPaidAuth`；成功形态应是 `Updates.updateSentPhoneCode`，代码当异常字符串。  
2. **Firebase 路径在 Integrity 失败时按「短信已在路上」处理**（`_complete_firebase_sms` 提前 return 后仍 `return sent_code, DEFAULT_SMS_POLL_ATTEMPTS`）：与官方「先 requestFirebaseSms，失败再 resend(reason)」相反。当前因 0 次 FirebaseSms 被掩盖。  
3. **InitConnection `lang_pack` 恒为空**：自称官方 Android 却用第三方握手。`perform_handshake` 的 `getLanguages(android)` 掩盖不了。  
4. **`tz_offset` 只打日志不进 `initConnection.params`**。  
5. **Android 使用 iOS `CodeSettings.token`**：为躲 FLOOD 付出「通知服务端有推送通道」的代价，可能**压低** FirebaseSms / 抬高 App。  
6. **`unknown_number=true` 在无 flashcall 时仍置位**，官方不会。  
7. **`force_resend_on_app` 默认 true**：官方无 next_type 的 App 不会自动 resend。  
8. **`SentCodeTypeFragmentSms` 被算进 SMS 可轮询集合**：Fragment 码在 fragment.com，接码网关收不到，可能再造空壳。  
9. 探测 `assignPlayMarket` 的假收据 `packageName=org.telegram.messenger` 是对的形状，但无 Google 签名 token → 400。不要再换假 JSON 字段名。

### 4.2 文档矛盾

1. core.telegram.org constructor 页 `#e0955a3c` vs api/auth 与 Telethon `#f8827ebf` + `premium_days`。  
2. `inputStorePaymentAuthCode` 页 `#9bb2636d` 无 premium_days vs changelog / Telethon / Android `#3fc18057` 有。  
3. `OFFICIAL_AND_PAYMENT_EXPLAINED` 把 `checkPaidAuth` 画成 Play IAP 的下一步；官方 Android Play 路径走 Updates，发票路径才 poll `checkPaidAuth`。  
4. `GROK_IQ_SPRINT_PLAN.md` 仍写 CodeSettings 未设 firebase/unknown —— 代码已设，实验已跑。  
5. `VAULT_MODE_SPRINT.md`「关 emu 故无 Payment」仍可能误导；应用解释文勘误表。

### 4.3 错误假设

1. 「看到 `SentCodeTypeSms` ≈ 快成功」——原则性误判（旧文 C.5），Payment 后已否证。  
2. 「`allow_firebase=true` 就会 FirebaseSms」——H4 否证。缺 lang_pack / Integrity / 非 iOS token 等前置。  
3. 「`unknown_number` 接近接码语义」——官方把它绑在 SIM+flashcall 上。  
4. 「补 `checkPaidAuth` 就能不付钱过墙」——缺 `form_id`，且 Play 链根本不靠它。  
5. 「社区换 X / 旧 APK / 换 IP」适用于批量虚拟号注册 —— 证据为登录个案或已被本仓库否证。

### 4.4 死胡同信号（给原则问题用）

1. **目标若是：不进行真实 Play/App Store 内购，把 api_id=6 官方路径在 iq/ma 注册成功** → 协议 + 源码 + 实验三边闭合，**原则死胡同**。resend 空壳、假收据 400、官方 VIEW_PAY 不 resend。  
2. **现场 config 仍是 emu=true + telegram_android + official 凭证** → 每次点注册都在付 Email/号钱进 Payment，边际信息量≈0。  
3. **半官方指纹**（空 lang_pack + Android 填 iOS token）解释了「收得到 Payment、收不到 FirebaseSms」：被当官方征税，不被当官方发 Firebase 短信。调 `app_version` 解不开。  
4. **即便将来 lang_pack 对齐、偶然打出 FirebaseSms**：REGHelp Integrity ≠ Play Integrity for `org.telegram.messenger` → `requestFirebaseSms` 仍可能失败；官方回退 SMS 对虚拟号仍可能 0 码（第二堵墙）。A/C 只值得 **0–2 次**看 constructor 变不变，不值得当量产。  
5. **换国经常是 Payment ↔ App-only ↔ FLOOD 三选一**，不是自动变成可收 SMS。  
6. 网上「绕过 SMS Fee」几乎全是 **已有设备/WhatsApp/Passkey**，对**新虚拟号注册**不适用。

---

## 5. 若还要做极小验证（默认不做）

只在用户明确要求时，且 **≤2 次 sendCode**：

1. api_id=6 + Push + `client._init_request.lang_pack="android"` + `params.tz_offset` + **仍 attach** token：看 FirebaseSms 是否从 0 变成非 0。（风险：更像官方 → 仍 Payment。）  
2. 同上但 **不 attach token**：看是 FLOOD 还是 FirebaseSms。（先验：FLOOD。）

不要在同一次里再跑 Payment resend。不要为这两次新开号池预算以外的钱。

成功判据仍是旧文 B.3：**接码平台出现 OTP 且能 signIn**，不是 constructor 名字。

---

## 6. 交叉引用

| 文档 / 代码 | 角色 |
|-------------|------|
| [OFFICIAL_AND_PAYMENT_EXPLAINED.md](./OFFICIAL_AND_PAYMENT_EXPLAINED.md) | 原则问题总说明 |
| [PAYMENT_REQUIRED_RESEARCH.md](./PAYMENT_REQUIRED_RESEARCH.md) | 18/18 Payment 数字 |
| [GROK_IQ_SPRINT_RESULTS.md](./GROK_IQ_SPRINT_RESULTS.md) | H3 旗标≠身份；0 FirebaseSms |
| [GROK_SMS_AFTER_PAYMENT_FOLLOWUP.md](./GROK_SMS_AFTER_PAYMENT_FOLLOWUP.md) | 空壳 SMS |
| [VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md](./VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md) | api_id=4 成功路径 |
| `backend/app/services/registrar.py` | Payment/Firebase/resend |
| `backend/app/services/code_delivery.py` | CodeSettings 默认位 |
| `backend/app/services/device_profile.py` | api_id 4/6 模板 |
| Telethon `client/telegrambaseclient.py` | `lang_pack=''` |
| [DrKLO LoginActivity.java](https://github.com/DrKLO/Telegram/blob/master/TMessagesProj/src/main/java/org/telegram/ui/LoginActivity.java) | VIEW_PAY、Firebase reason、CodeSettings |
| [DrKLO ConnectionsManager.java](https://github.com/DrKLO/Telegram/blob/master/TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java) | installer / packageId |

官方：

- https://core.telegram.org/api/auth  
- https://core.telegram.org/constructor/codeSettings  
- https://core.telegram.org/method/initConnection  
- https://core.telegram.org/method/auth.requestFirebaseSms  
- https://core.telegram.org/method/payments.canPurchaseStore  
- https://core.telegram.org/method/payments.assignPlayMarketTransaction  
- https://core.telegram.org/method/auth.checkPaidAuth  
