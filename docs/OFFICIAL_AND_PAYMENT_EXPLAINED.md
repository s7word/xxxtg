# official 模式与 Payment 墙：给判断用的说明

> 写给用户读，不是实验报告。  
> 生成：2026-09-02 · 分支 `cursor/official-payment-explained-88d6`  
> **本轮没有跑租号实验**，只读代码、本仓库已有报告、[core.telegram.org](https://core.telegram.org/api/auth) 与公开资料。  
> 实验数据一律来自既有文档（见文末交叉引用）。

读完这份，你要能自己回答两件事：

1. 我们说的「模式=official」到底改了什么、服务端看见的又是什么。  
2. Payment / Paid auth 是不是一条**原则上付了 Play/App Store 内购才能走完**的墙。

---

## A. 「official 模式」在我们代码里到底是什么

先记住一句：**Telegram 服务端看不到我们配置里的开关名字。**  
它只看见：`api_id` / `api_hash`、设备字符串、`CodeSettings`、有没有 Push token。

我们控制台里的「official」其实是**三件不同的本地事**叠在一起。混用这三个词，就会把「关了一个旗标」误当成「不再被当成官方 App」。

### A.1 三个开关分别干什么

| 本地开关 | 它改不改服务端身份 | 实际作用 |
|----------|-------------------|----------|
| `official_client_emulation` | **间接改**（因为它锁死官方 api_id） | 编排旗标：强制用模板官方凭证、强制每轮 attach Push、日志打 `[official]` / `模式=official`。**不**把这个布尔值发给 Telegram。 |
| `api_credential_mode=official` | **改**（选用哪套 api_id） | 凭证策略：始终用设备模板里的官方 `api_id/hash`（`telegram_android`→6，`telegram_android_public`→4）。`custom` 才用自建 ID；`auto` 在没 Push 时才可能回退自建。 |
| 设备模板 `api_id=4` vs `6` | **这才是服务端认的客户端种类** | 4 = 早期泄露 Android 公开 ID；6 = 当前 Play Store 官方 Android。配对 hash 写死在 `OFFICIAL_API_CREDENTIALS`。 |

代码依据：

- `official_client_emulation=true` 时，`resolve_effective_credentials` **无视** `api_credential_mode`，把 mode 当成 `official`，因此**不会**回退到自建 api_id。见 `backend/app/services/device_profile.py`。
- 同时 `resolve_code_delivery_plan` 把通道策略抬成 `push_required`，猎号连续 App **不得**跳过 Push。见 `backend/app/services/code_delivery.py`。
- 日志里的 `模式=official` 只是 `emulation_label_for()`：旗标开了就写 official，否则写 balanced / sms_first / push_required。

因此会出现这种「看起来矛盾、其实不矛盾」的配置（vault 冲刺就用过）：

```text
official_client_emulation = false
api_credential_mode       = official
active_app_type           = telegram_android_public   → api_id=4
```

本地日志**不会**写「官方客户端模拟」，但 sendCode 仍带着泄露官方 api_id=4。服务端仍按官方 Android 身份处理。

### A.2 api_id=4 vs 6、Push attach、CodeSettings

**api_id / api_hash**

| api_id | 我们仓库里的来源 | 配对 hash（前缀） | 服务端大致待遇（已有实验） |
|--------|------------------|-------------------|---------------------------|
| **6** | `telegram_android`，现网官方 Android | `eb06d4…581e` | iq/id/pe/ma：常 `SetUpEmailRequired` → 验证邮箱后 **PaymentRequired 100%**。关 `official_client_emulation` **挡不住**（H3/H8）。 |
| **4** | `telegram_android_public`；凭证库成功 +91 账号几乎全是这个 | `014b35…5103` | 无 Push → 几乎必 `API_ID_PUBLISHED_FLOOD`。有 Push：in 上常 **App**；iq/jo/ma 本窗口常 **FLOOD**。不是「绕过内购」的可靠开关。 |
| **21724** | Telegram X（官方变体） | `3e0cb5ef…dc16` | 同属泄露黑名单。本仓库一轮对照未能稳定 sendCode，**不能**当「免 Payment」证据。 |
| **自建**（my.telegram.org） | `api_credential_mode=custom` | 你自己的 hash | 文档与 2023-02 起政策：第三方通常**不能**走 SMS/Call，多走 App / 其它非短信通道。用户已禁止把它当主 SMS 路径。 |

混用 4 的 id 和 6 的 hash → Telethon 直接 `api_id/api_hash combination is invalid`。代码已用 `normalize_official_api_credentials()` 纠正。

**Push attach（`CodeSettings.token`）**

官方文档原文把 `token` 写成「仅官方 iOS 用于 Firebase / APNS」。我们在 Android 指纹上仍把 REGHelp / AntiSafety 签发的 Push token 填进去。经验规律：

- 泄露 ID（4/6/21724…）**不带** token → `API_ID_PUBLISHED_FLOOD`（原则级：公开 ID 无平台凭证）。
- **带了** token 仍可能 FLOOD（api_id=4 在 iq：G1 已 attach 仍被拒）。那是「Token 未被当成合法平台签署」，不是「没申请 Push」。
- 带 token 等于告诉服务端「有一条可推送通道」，会**提高**走 `SentCodeTypeApp` 的机会，不是提高 SMS。

**CodeSettings 其它位**（`allow_firebase` / `unknown_number` / `allow_app_hash` / 闪信）

这些是**通道协商**，不是付款。官方说明见 [codeSettings](https://core.telegram.org/constructor/codeSettings)。

- `allow_firebase`：官方 Android 为 true，**可能**触发 `SentCodeTypeFirebaseSms`；本仓库在 iq/ma 上打开后 **0 次**打出 FirebaseSms。
- `unknown_number`：号码不是本机 SIM 时设 true（接码号符合这个语义）。
- `allow_app_hash`：只协商短信正文里的 Android SMS Retriever hash，**不**选择 App vs SMS。
- 闪信 / 漏接电话：接码网关通常收不到，默认关。

Email / Firebase / Payment 的**处理代码**在 `registrar.resolve_sent_code_channel` 里对**所有模式**都会跑——只要服务端返回了那种 constructor。official 旗标并不是「才去处理 Email」。旗标的作用是让你更容易**走到**这些 constructor（因为锁死了官方 api_id）。

### A.3 与「真·官方 Android APK」差在哪里

| 能力 | 真机 Play 版官方 APK | 我们的 official 模拟（Telethon + 指纹库 + REGHelp） |
|------|---------------------|------------------------------------------------------|
| 用官方 `api_id=6` + 配对 hash | 能（包里写死） | **能模拟** |
| 设备型号 / SDK / app_version 字符串 | 真机 | **能模拟**（指纹库采样） |
| Push token | Google/FCM 或厂商通道，系统签发 | **能模拟形态**（第三方网关签发的 token，不是 Google 原签） |
| Play Integrity / SafetyNet 真机证明 | Google 对这台设备、这个 `org.telegram.messenger` 包签名出具 | **不能模拟成真**。REGHelp 的 integrity 接口是另一路网关；本仓库 Firebase 路径几乎没被服务端点名 |
| Play Billing 内购 `telegram_premium.one_week.auth` | 用户 Google 账号在 Play 商店付钱，得到 purchaseToken | **不能模拟** |
| 把 IAP 收据交给 `payments.assignPlayMarketTransaction` | 官方 App 专用 | RPC **能发出去**；假收据已被打回 `PLAYMARKET_RECEIPT_INVALID` |
| Google 账号绑定的真实 IAP 收据 | 有 | **没有**。本项目明确不伪造收据 |

所以「official 模拟」更准确的说法是：

> **我们在 MTProto 上把自己介绍成官方 Android（api_id + 指纹 + 一枚 Push token），从而走进官方客户端才会看到的流程（邮箱、Paid auth）。我们没有、也无法出示 Google 认为有效的真机证明和内购收据。**

这不是「模拟失败所以被当第三方」。被当第三方时，文档写的是走 App / QR / 邮件，**不应**收到 `auth.sentCodePaymentRequired`。我们在 api_id=6 上**稳定收到** PaymentRequired——说明服务端把会话当成**官方 App 足够格去收这道墙**。

### A.4 对照：凭证库成功账号路径

扫描结论见 [VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md](./VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md)：可用样本里 +91 成功账号 **9/10 为 api_id=4**，带 Push，app_version **12.7.3**，且注册时 **不是** `official_client_emulation=true` 那条 Email→Payment 链路。

| 维度 | 凭证库成功路径（历史 +91） | 当前「模式=official」典型（telegram_android） |
|------|---------------------------|-----------------------------------------------|
| `official_client_emulation` | **false** | **true** |
| `api_credential_mode` | custom / balanced，或 official + public 模板 | official |
| api_id | **4** | **6** |
| api_hash | `014b35…5103` | `eb06d4…581e` |
| Push | JSON 里有 `device_token` | 强制 REGHelp attach |
| app_version | 12.7.3 (67502/67509) | 指纹库随机（常 12.9.x） |
| sendCode 后（历史成功时） | 最终能收码并注册（当时号池允许） | iq/id/pe/ma：Email → **PaymentRequired** |
| 近年同配方重放 | in：常 **100% App**，0 SMS；iq：常 **FLOOD** | email 后内购墙 100%（已有样本） |

**勘误（相对 vault 冲刺文）：**  
[VAULT_MODE_SPRINT.md](./VAULT_MODE_SPRINT.md) 写「无 Payment 墙是因为关掉了 official 模拟」。更准确：那一轮用的是 **api_id=4 + in 号池**，服务端给了 App，根本没走到 Email。  
[GROK_IQ_SPRINT_RESULTS.md](./GROK_IQ_SPRINT_RESULTS.md) **H3** 已经否证「只关旗标」：`official_client_emulation=false` + **api_id=6** 在 iq/ma 仍然 Email→Payment。

墙跟的是 **api_id=6 这份官方 Android 身份**，不是配置文件里那个布尔值。

---

## B. 「Payment / Paid auth」在 Telegram 协议里是什么

### B.1 官方怎么说

[auth.sentCodePaymentRequired](https://core.telegram.org/constructor/auth.sentCodePaymentRequired) 与 [User Authorization — Paid auth](https://core.telegram.org/api/auth)：

> Official apps **may** receive this constructor, indicating that due to the high cost of SMS verification codes for the user's country/provider, the user must purchase a Telegram Premium subscription in order to proceed with the login/signup, **using a flow only usable by official clients**.

要点：

- 这是 `auth.SentCode` 的**另一种 constructor**，和普通 `auth.sentCode`（带 `type=SentCodeTypeSms` 等）平级。它**没有** `type` / `next_type` / `timeout`。
- 字段：`store_product`、`phone_code_hash`、客服邮箱、`premium_days`、`currency`、`amount`。
- 本仓库观测到的产品几乎全是：`store_product=telegram_premium.one_week.auth`，`premium_days=7`。
- 过墙 RPC（文档写明 **official apps only**）：
  - [`payments.canPurchaseStore`](https://core.telegram.org/method/payments.canPurchaseStore) — 买之前检查
  - [`payments.assignPlayMarketTransaction`](https://core.telegram.org/method/payments.assignPlayMarketTransaction) / `assignAppStoreTransaction` — 把商店收据交给 Telegram
  - [`auth.checkPaidAuth`](https://core.telegram.org/method/auth.checkPaidAuth) — 用 `form_id` 查登录付款是否完成
- 用途对象是 [`inputStorePaymentAuthCode`](https://core.telegram.org/constructor/inputStorePaymentAuthCode)（「为登录验证码付款」），不是普通给好友送 Premium，也不是 Stars。

TDLib 对应状态：[`authorizationStateWaitPremiumPurchase`](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1authorization_state_wait_premium_purchase.html) → `checkAuthenticationPremiumPurchase` → `setAuthenticationPremiumPurchaseTransaction`。

公开侧证：

- [t.me/tginfoen](https://t.me/s/tginfoen/2076)（Android 11.9 beta）：可能限制部分用户收 SMS，并提示买 **一周 Premium**。
- [Telegram-iOS #2168](https://github.com/TelegramMessenger/Telegram-iOS/issues/2168)：真用户遇到 SMS Fee 屏；维护者回复 **This is server-controlled**（服务端策略，不是客户端 bug）。
- Durov 公开说过 Telegram 每月约花费 **1000 万美元**做号码认证短信（[t.me/durov/347](https://t.me/s/durov/347) 一带）。Paid auth 与「SMS 太贵」的叙事一致。

### B.2 完整官方客户端过墙步骤（概念级）

真机官方 App 在高 SMS 成本国家，概念上是：

```text
auth.sendCode
    │
    ├─ 可能先 SentCodeTypeSetUpEmailRequired
    │     account.sendVerifyEmailCode → 用户邮箱收码
    │     account.verifyEmail
    │     返回新的 auth.SentCode
    │
    └─ auth.sentCodePaymentRequired
          store_product = telegram_premium.one_week.auth
          amount/currency = 例如 USD、100（= $1.00）
              │
              ▼
       Play Billing / App Store 购买该 store_product
       （真·Google/Apple 账号，真·钱，真·收据）
              │
              ▼
       payments.assignPlayMarketTransaction(receipt, purpose=AuthCode)
         或 assignAppStoreTransaction
              │
              ▼
       auth.checkPaidAuth(phone, phone_code_hash, form_id)
              │
              ▼
       这才回到普通 auth.sentCode（SMS / Firebase / Call …）
       再 auth.signIn / signUp
```

[`auth.resendCode`](https://core.telegram.org/method/auth.resendCode) 的文档定义是：按**上一次** sendCode/resend 的 `next_type` 换通道。PaymentRequired **没有** `next_type`。文档**没有**写「Payment 之后 resend 可以跳过内购」。

Firebase 是另一条官方-only 路：`SentCodeTypeFirebaseSms` + 真 Play Integrity → `auth.requestFirebaseSms`。失败才带 `reason` 去 resend。本仓库在 Payment 国几乎从未收到 FirebaseSms constructor。

### B.3 为什么「Payment 后 resend 返回 SentCodeTypeSms」被判为空壳

既有实验（未在本轮重跑）稳定复现：

```text
PaymentRequired
  → auth.resendCode(同一个 phone_code_hash)
  → auth.sentCode type=SentCodeTypeSms  next_type=Call  timeout=90
  → 接码平台轮询 120–270s → 一直 STATUS_WAIT_CODE，0 条验证码
```

见 [GROK_SMS_AFTER_PAYMENT_FOLLOWUP.md](./GROK_SMS_AFTER_PAYMENT_FOLLOWUP.md)、[GROK_IQ_SPRINT_RESULTS.md](./GROK_IQ_SPRINT_RESULTS.md) H5/H10。

**「空壳」在这里的意思：**  
协议层愿意改 constructor 的名字，让客户端以为「现在走短信了」；**没有证据**表明运营商短信离开了 Telegram 网关、到达了虚拟号。

| 若是真短信 | 我们应该看到 | 实际 |
|------------|--------------|------|
| 投递成功 | 接码平台至少一次非 WAIT 的码 | smsbower **与** Grizzly 均为 0 |
| 只是慢 | 拉长到 270s 应出现迟到码 | A/E 组否证 |
| 只是没等官方 timeout | 等 90s 再 resend 应不同 | F 组仍是 Sms + NO_CODE |
| hash 传错 | RPC 应 `PHONE_CODE_HASH_EMPTY` / `EXPIRED` | 同一 hash，resend 成功 |
| 二次立刻 resend | 若通道已「下发」，应按 timeout 限流 | 确实 `FLOOD_WAIT ~95s`（服务端把这条 SMS **当作已下发在计时**） |

最后一条特别关键：Telegram **在计时器上把 SMS 当真**，接码平台 **从未看见短信**。更像「网关没对虚拟号投递 / 策略丢掉」，而不是「我们轮询写错了」。

**「真发短信」的证据标准（请用这个，不要用 constructor 名字）：**

1. 接码 API 返回可解析 OTP（或实体 SIM 短信应用里出现 Telegram 短信）；并且  
2. 该 OTP 能通过 `auth.signIn`（或至少报 `PHONE_CODE_INVALID` 而不是 `PHONE_CODE_EXPIRED` 空号）。  

只满足「RPC 返回了 `SentCodeTypeSms`」= **未达标准**。

### B.4 货币字段：USD $1 是什么

官方：`amount` 是货币**最小单位的整数**，不是小数。例：`$1.45` → `amount=145`（[currencies.json 的 exp](https://core.telegram.org/constructor/auth.sentCodePaymentRequired)）。

本仓库：`currency=USD`、`amount=100` → 展示 **USD $1.00**。  
这是 **Google Play / App Store 上那一档一周 Premium（auth 专用 sku）的标价**，不是 Stars，不是 Fragment TON，也不是「短信通道手续费」这种独立商品。付的是 Premium 订阅（文档写明买完才能继续登录/注册）。

有的国家会返回当地货币等价，产品 ID 仍是 `telegram_premium.one_week.auth`。

---

## C. 原则性问题 checklist

分类约定：

- **原则问题**：协议/产品设计如此，改我们的重试、等待、指纹、号商，**不能**把「官方路径」走完。  
- **工程问题**：实现、号池、时段、Token 质量，理论上还能试，只是贵或窗口不稳。  
- **未验证**：资料或逻辑指向这里，但本仓库没有干净实验。

### 1. 不付真实 IAP，能否走完 PaymentRequired 国家的「官方路径」注册？

**判断：原则问题 — 官方路径原则上不可能。**

依据：Paid auth 文档写明须官方客户端内购；过墙 RPC 是 Play/App Store 收据；假收据 → `PLAYMARKET_RECEIPT_INVALID`（已探测）；resend 不在文档链上，且实验为 0 码。

这句话的边界要咬死：

- 「官方路径」= 继续以 api_id=6（或其它被当成官方移动端的身份）面对已经下发的 `sentCodePaymentRequired`，还不付钱。  
- **不是**在问：换一个非官方 api_id、换国、换号，能不能在别的通道（App）碰运气。那是另一堵墙（App-only / 2023 年起第三方禁 SMS）。

### 2. official 模拟会不会原则上被识别成非官方（缺 Play Integrity / 收据）？

**判断：原则问题，但是「最差的那种被识别」。**

两层身份：

| 层 | 服务端怎么待我们（api_id=6 + Push） | 含义 |
|----|-------------------------------------|------|
| 进不进 Paid auth | 稳定当作官方 App：**会**给 PaymentRequired | 模拟「够像」官方，才会收这道只有官方能收的墙 |
| 过不过 Integrity / 内购 | 没有真 Play Integrity、没有真收据 → 过不了 | 模拟「不够真」，付不了这道墙要求的 Google 证明 |

所以不是「被识破后改走第三方 App 通道、因此免付费」。而是：**被收官方的税，又拿不出官方的收据。** 这是设计上的死胡同，不是再调 `app_version` 能解开的。

Play Integrity 真机证明、Play Billing 收据 **原则上**不能由 Telethon + 虚拟号 + 第三方 Push 网关伪造（伪造收据属欺诈，本项目不做）。

### 3. api_id=4/6 泄露 ID 与 FLOOD / App-only 的原则关系

**判断：FLOOD（无 Push）是原则问题；带 Push 之后走 App / FLOOD / Payment 是策略+工程。**

原则：

- 4 和 6 都在 `PUBLISHED_API_ID_BLOCKLIST`。公开泄露 ID、无合法平台凭证 → `API_ID_PUBLISHED_FLOOD`。这是服务端对「滥用官方 ID 的第三方」的固定闸。  
- 2023-02-18 起 Telegram 邮件政策：第三方应用登录码走 Telegram App，**不再**为第三方发 SMS（Telethon 维护者原文：[issue #3835](https://github.com/LonamiWebs/Telethon/issues/3835)）。用泄露官方 ID，是在赌「服务端仍把我当官方移动端」。

带 Push 之后（非原则、已观测）：

- api_id=6 → 高成本国 Email→Payment（官方待遇）。  
- api_id=4 → 有的国/时段 App，有的国 FLOOD；**不能**当成「4=免 Payment 的官方后门」。in 上 4 和 6 都出现过纯 App。

App-only（`SentCodeTypeApp`）的原则含义：验证码进了**该号码已有的 Telegram 会话**（老号/未注销会话）。换 Push、换设备救不了，要换号或换号源。这和 Payment 墙是**并列**的失败模式，不是同一堵墙的前后两级。

### 4. 虚拟号 vs 实体 SIM

**判断：有原则差异；「实体 SIM 在真机官方 App 里付 $1 能否注册」未在本仓库验证。**

原则差异：

| | 虚拟号（接码平台） | 实体 SIM + 真机官方 APK |
|--|-------------------|-------------------------|
| 运营商短信路由 | 号段常被 Telegram / 运营商过滤 | 正常消费卡，投递率完全不同 |
| Google Play 账号与 Billing | 自动化进程里没有 | 有，才能买 `one_week.auth` |
| Play Integrity | 无真机硬件证明 | 有 |
| `unknown_number=true` | 与「卡不在本机」语义一致，帮不上 IAP | 真机可 `current_number` |
| 我们接码 API | 只证明「平台有没有收到短信」 | 用户能直接看短信箱 |

因此：即使哪天 Payment 后的 Sms constructor **真的**出网，虚拟号仍可能 0 码（号段屏蔽）。这是第二道原则墙，不能用「再换一家接码」无限否定——但换号商只能**降低**「单平台号段」假设，不能证明实体卡也收不到。

本仓库：**未**用实体 SIM 做官方 IAP。若你要验证「官方路径在付钱后是否通」，唯一干净实验是真机 + 真 Play 内购，而不是再租虚拟号。

### 5. 把「看到 SentCodeTypeSms」当成「已接近成功」是不是原则性误判？

**判断：是原则性误判。**

`SentCodeTypeSms` 只表示 **auth.SentCode 的 type 字段叫 Sms**。它不蕴含：

- 短信已提交运营商；  
- 虚拟号能收到；  
- 付费墙已解除；  
- 离 `signUp` 只差轮询。

在 Payment 之后，它甚至可能表示「服务端给了一个形式上的下一通道，同时仍按 timeout 限流」，与「可完成注册」无关。  
**接近成功的唯一信号**是证据标准 B.3：平台或 SIM 出现 OTP，并能 signIn。

---

## D. 拓展思路（资料支撑）

标记：

- **已验证**：官方文档写明，或本仓库已有实验。  
- **未验证**：网上有说法，本仓库没测或测失败无法下结论。  
- **高成本**：要真钱、真机、真 IAP、或明显违规。

### D.1 第三方「代付 Premium auth / 一周解锁码」

网上能搜到 AccountBoy、NexSMS 等博客，卖「一周 Premium 解锁 SMS Fee」（例：[accountboy.com/news-detail/1054](https://www.accountboy.com/en-us-usd/news-detail/1054)、[nexsms.net/blog](https://nexsms.net/blog/en/archives/30/)）。

| 项 | 说明 |
|----|------|
| 状态 | **未验证**（本仓库未买、未接） |
| 协议现实 | 过墙必须有 **Play/App Store 收据** 或官方 `sendPaymentForm`。第三方「解锁码」要么是真 IAP 收据转移、要么是骗局、要么是给**已有账号**充 Premium（对**未完成登录**的 `inputStorePaymentAuthCode` 对不上） |
| 风险 | 违反 Telegram / Google 条款；收据欺诈；账号被冻；钱货两空。**不建议**当产品路径 |
| 高成本 | 即使有人真能代付，每号 ~$1 + 手续费，且仍要解决虚拟号收不到 SMS 的第二堵墙 |

### D.2 Fragment / Stars 能否付 auth PaymentRequired？

**通常不能。协议层已验证（文档不同对象）；本仓库未尝试用 Stars 去 checkPaidAuth。**

| 机制 | 干什么 | 和 Paid auth 的关系 |
|------|--------|---------------------|
| `sentCodeTypeFragmentSms` | 登录码发到 fragment.com，用钱包去看码 | **另一种发码类型**，不是 PaymentRequired 的付款方式 |
| Telegram Stars | 付费私信、礼物、给**已有用户**买 Premium（含 Fragment 赠送） | Stars 的 API 是 `allow_paid_stars` / payments Stars，**不是** `assignPlayMarketTransaction` |
| `inputStorePaymentAuthCode` | 为**当前这次登录**买一周 Premium sku | 绑的是商店内购收据 |

官方 Paid auth 章节列出的未登录可用方法里，没有「用 Stars 余额支付 sentCodePaymentRequired」。  
Fragment 要先能登录才能买东西，用它来**完成尚未登录的 auth**，逻辑上是循环。

### D.3 非官方客户端（Telegram X、Plus）注册政策

| 客户端 | 身份 | 资料 | 对我们的含义 |
|--------|------|------|--------------|
| 官方 Android / iOS | api_id 6 / iOS 对应 ID | 可走 Firebase、Paid auth、IAP | 真机付钱的正路 |
| **Telegram X** | 官方变体，api_id **21724** | 营销文常说「换 X 就不收费」；官方仍可能按官方 App 处理 | 本仓库对照 **未成功 sendCode** → **未验证**是否免 Payment。即使免 Payment，泄露 ID 仍要 Push，否则 FLOOD |
| Plus Messenger 等 fork | 非官方，自建或盗用官方 ID | Play 页写明 unofficial | 2023 政策下第三方 **默认无 SMS**；社区「换旧版 APK / 换 X」属个案，[PAYMENT_REQUIRED_RESEARCH.md](./PAYMENT_REQUIRED_RESEARCH.md) 评为低～中可靠度 |
| Telethon / Pyrogram 默认 | 你的 my.telegram.org api_id | 维护者：不能再用库来 **sign up**（见下） | 与「自建 ID → App-only」一致 |

旧版 APK、换 IP、换 Telegram X：网上重复出现，**无批量公开数据**。本仓库：旧版 9.6.7、全新设备+代理 **未去掉** PaymentRequired（已验证，见 Payment 调研 A/D/E）。

### D.4 换国 / 换号池：只改变「进不进 Payment」，还是也会进 App-only？

**两者都会变。已验证。**

同一套 api_id=6 official：

- iq / id / pe / ma：走完 email → **PaymentRequired 100%**（survey 18/18 + 后续冲刺）。  
- in（+91）：同一时期 official 4/6 都出现过 **100% App**，没进 Payment。

同一套 api_id=4 + Push：

- in：App 或偶发 FLOOD（窗口不稳）。  
- iq / jo / ma：本窗口以 **FLOOD** 为主。

所以换国不是「绕过 $1 墙就能 SMS」。更常见的是：**从 Payment 墙换到 App-only 墙，或换到 FLOOD 墙。** 三堵墙都不是注册成功。

### D.5 开源社区对 PaymentRequired 的讨论摘要

| 来源 | 链接 | 摘要 |
|------|------|------|
| 官方文档 | [core.telegram.org/api/auth](https://core.telegram.org/api/auth) Paid auth 节 | 仅官方客户端；须买 Premium 才能继续 |
| Telethon 类型页 | [tl.telethon.dev … sent_code_payment_required](https://tl.telethon.dev/constructors/auth/sent_code_payment_required.html) | 只列出 TL 字段，**没有**高层「去付款」封装 |
| Telethon `CheckPaidAuthRequest` | [tl.telethon.dev … check_paid_auth](https://tl.telethon.dev/methods/auth/check_paid_auth.html) | 需要已经发生的 `form_id`（商店付款表单），不是 resend |
| Telethon 维护者（2023-02 SMS 政策） | [LonamiWebs/Telethon#3835](https://github.com/LonamiWebs/Telethon/issues/3835) | 「You can no longer use Telethon to sign up」「SMS 不会再给第三方」。高层 `send_code_request` 假定返回值有 `.type`，**PaymentRequired 没有 `.type`** |
| Pyrogram fork | [PyroTGFork `sent_code.py`](https://github.com/TelegramPlayGround/PyroTGFork/blob/f1dc08e/pyrogram/types/authorization/sent_code.py) | 遇到 `SentCodePaymentRequired` 直接 `raise Unauthorized`，注释 `TODO: CheckPaidAuth`，写明 **currently not supported** |
| Hydrogram / Electrogram 文档 | [hydrogram SentCodePaymentRequired](https://docs.hydrogram.org/en/latest/telegram/types/auth/sent-code-payment-required.html) | 复述官方「official apps may receive」 |
| MadelineProto | [LOGIN.html](https://docs.madelineproto.xyz/docs/LOGIN.html) | 新 api_id 默认无 SMS，须邮件 `sms@telegram.org` `#enableSMS` |
| 官方 iOS 仓库 | [Telegram-iOS#2168](https://github.com/TelegramMessenger/Telegram-iOS/issues/2168) | 真用户 SMS Fee；维护者：服务端控制 |

社区共识与我们实验一致：**库不实现、也不打算实现「不付钱过 Paid auth」。** 没有找到可信的「resend 即可跳过内购」技术帖。

---

## E. 请你用这些问题判断我们是否走入死胡同

不必写长文，勾选即可。你的目标若与某题的「死胡同」定义一致，就不要再烧同一条链路。

**1.** 当前目标是不是：**在不进行真实 Play/App Store 内购的前提下，把 api_id=6 官方路径在 iq/ma 这类国家注册成功**？  
- [ ] 是 → 按第 C.1 条，这是**原则死胡同**。  
- [ ] 否 → 写下真正目标（例如：只要任意国家能出 session / 只要研究墙的形状 / 愿意真机付 $1）。

**2.** 若必须「像官方 App」：你是否接受 **每号约 $1 的真 IAP + 真机/真 Google 账号**（自动化做不了 Billing）？  
- [ ] 接受 → 高成本验证，不是 MTProto 调参。  
- [ ] 不接受 → 官方路径关闭。

**3.** 看到日志 `SentCodeTypeSms` 时，你是否仍把它算作「快成功了」？  
- [ ] 是 → 请改用 B.3 的收码标准；否则会无限开「再等 30 秒」实验。  
- [ ] 否，必须接到 OTP。

**4.** vault 历史成功（+91、api_id=4、非 emu、有 Push）对你是否仍算主路径？  
- [ ] 是 → 主矛盾变成 **App-only / FLOOD 窗口 / 号池**（工程+号源），不是 Payment。  
- [ ] 否（你已判定 +91 不值得测）→ 不要用「再开 official 6」去替代它。

**5.** 换国时你期望的是哪一种？  
- [ ] 去掉 Payment **并且**出现可收 SMS  
- [ ] 只要不再 Payment，即使变成 App-only 也算进展  
- [ ] 不清楚  

（资料：换国经常是 Payment ↔ App ↔ FLOOD 三选一，不是自动变成 SMS。）

**6.** 虚拟号 0 码时，你是否认为「换实体 SIM + 官方 APK」才是下一验证，而不是再换接码商？  
- [ ] 同意（C.4）  
- [ ] 不同意，还想换号商碰 Payment→resend  

**7.** 第三方代付 / 买「一周解锁码」：  
- [ ] 明确禁止（合规）  
- [ ] 可以评估风险后小样本  
- [ ] 不关心条款  

**8.** 若原则问题成立，你希望仓库默认策略变成哪一个？  
- [ ] 关闭 api_id=6 official 编排，避免再进 Paid auth  
- [ ] 保留探测代码，但 UI 把 Payment 标成「需真机内购」而不是「继续猎号」  
- [ ] 仍要保留 Email→Payment→resend 当研究开关  

---

## 与旧文档的矛盾（以本文为准）

| 旧表述 | 出处 | 校正 |
|--------|------|------|
| 关掉 `official_client_emulation` 就不会进内购 | [PAYMENT_REQUIRED_RESEARCH.md](./PAYMENT_REQUIRED_RESEARCH.md) 结论段；[VAULT_MODE_SPRINT.md](./VAULT_MODE_SPRINT.md) | **不成立**。H3/H8：旗标 false + **api_id=6** 仍 100% Payment。服务端认 api_id，不认本地布尔值。 |
| 可完成注册就改用 `balanced + custom api_id` | 同上调研结论 | 自建 ID 按 2023 政策与本仓库历史 → **App-only**，不是 SMS 主路。用户已禁止把它当 SMS 方案。它只是「不进 Payment」的另一种失败。 |
| vault 冲刺无 Payment 是因为关了 official 模拟 | [VAULT_MODE_SPRINT.md](./VAULT_MODE_SPRINT.md) §4.3 | 主因是 **api_id=4 + in 号池给了 App**。同一旗标配 api_id=6 仍会 Payment。 |
| Payment 后出现 `SentCodeTypeSms` 表示接近成功 | 早期口头/日志直觉 | **否**。见 B.3 / C.5。跟进文已改口为「空壳」，以跟进文+本文为准。 |
| FLOOD 文案「缺少 Push Token」= 没申请到 Token | 更早 vault 对照 | **已勘误**（V3 03:44:48 已 attach 仍 FLOOD）。见 vault 对照文第 6 节。 |
| 仅 `official_client_emulation=true` 才会处理 Email/Payment | schemas 描述容易读成这样 | registrar **凡收到该 constructor 都会处理**。旗标只让你更容易收到它们。 |

保留不变、仍然成立的结论：

- iq/id/pe/ma 上 **api_id=6 走完 email 后 PaymentRequired 100%**。  
- 假 Play 收据 **400**。  
- Payment 后 resend 的 Sms **接码 0**（两平台、两国、拉长等待）。  
- 无 Push 发 4/6 → FLOOD。  
- 换 app_version / 全新 IP+设备 **去不掉** Payment。

---

## 交叉引用

| 文档 | 角色 |
|------|------|
| [PAYMENT_REQUIRED_RESEARCH.md](./PAYMENT_REQUIRED_RESEARCH.md) | 官方 Paid auth 摘录 + A–E 对照（保留 100% Payment 数字；结论段「关旗标」已过时） |
| [GROK_IQ_SPRINT_RESULTS.md](./GROK_IQ_SPRINT_RESULTS.md) | H1–H10：旗标≠身份；resend Sms 0 码；假收据 400 |
| [GROK_SMS_AFTER_PAYMENT_FOLLOWUP.md](./GROK_SMS_AFTER_PAYMENT_FOLLOWUP.md) | 空壳 SMS 工作假设；证据标准 |
| [VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md](./VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md) | 成功账号 = api_id=4 + Push + 非 emu |
| [VAULT_MODE_SPRINT.md](./VAULT_MODE_SPRINT.md) | in 上 vault 配方 100% App |
| 代码 | `code_delivery.py`、`device_profile.py` `resolve_effective_credentials`、`registrar.py` `resolve_sent_code_channel` / `_probe_assign_play_market` |

官方：

- https://core.telegram.org/api/auth  
- https://core.telegram.org/constructor/auth.sentCodePaymentRequired  
- https://core.telegram.org/method/payments.assignPlayMarketTransaction  
- https://core.telegram.org/method/auth.checkPaidAuth  
- https://core.telegram.org/constructor/inputStorePaymentAuthCode  
- https://core.telegram.org/constructor/codeSettings  
