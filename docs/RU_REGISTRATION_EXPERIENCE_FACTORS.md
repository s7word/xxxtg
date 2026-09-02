# 俄语圈 Telegram 注册经验与成功相关因素

> 时间：2026-09-02  
> 方法：外网俄语手册 / 新闻 / 论坛 / 接码站营销文 + 对照本仓已有 vault 样本与实验报告。  
> **本轮未租号。** 实验数字一律引用既有报告，不以本轮新测为准。  
> 对照：[RU_COUNTRY_PASSRATE_RESEARCH.md](./RU_COUNTRY_PASSRATE_RESEARCH.md)、[RU_DOLLAR_WALL_RESEARCH.md](./RU_DOLLAR_WALL_RESEARCH.md)、[VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md](./VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md)、[API_ID_PUBLISHED_FLOOD_CAUSES.md](./API_ID_PUBLISHED_FLOOD_CAUSES.md)、[COUNTRY_PASSRATE_50_RESULTS.md](./COUNTRY_PASSRATE_50_RESULTS.md)、[COUNTRY_PASSRATE_50_ROUND2_RESULTS.md](./COUNTRY_PASSRATE_50_ROUND2_RESULTS.md)、[PROXY_AND_DEVICE_FITNESS.md](./PROXY_AND_DEVICE_FITNESS.md)（移动 vs 住宅 **未系统测过**；设备字段达标/缺口表）。  
> 注：上述对照文档可能在其它分支；本文自洽，不依赖它们才能读懂。

**先读结论：** 俄语农场并不相信「换一个公开 api_id」或「付 1 美元就能过」。他们押的是一套**身份栈**：用泄露的官方 Android **api_id=4** 冒充移动端 → 用 AntiSafety 的 **Push + SafetyNet** 当「真机证明」→ **SIM-based 新号**（扔掉会把码打进 App 的回收号）→ **号国住宅/移动代理 + 时区/语言/机型对齐**。Desktop（api_id=2040）明确不能建新号；api_id=6 被当成「像现网官方、于是进付费税区」的路径，能躲就躲。这与本仓策略（**4 主攻、6 后备、暂不 Payment**）同向。他们和我们的分歧不在方向，而在：**Push 被他们写成「SMS 不来时的收码通道」；本仓实测 Push 主要只决定能不能过 `API_ID_PUBLISHED_FLOOD`，过了之后仍是 `SentCodeTypeApp`，没有 SMS。**

可信度标尺：

| 档 | 含义 |
|----|------|
| **高** | 官方文档 / 源码 / 可复核新闻 / 本仓 RPC 实测 |
| **中** | 俄语注册机行业默认（Soft Expert 手册），内部自洽，但营销腔重、无公开原始数据 |
| **低** | 接码站成功率百分比、代理商「90% 通过」、单条论坛经验 |

---

## 1. 他们到底相信什么在起作用

按俄语圈自己的优先级，不是按我们仓库的优先级。

### 1.1 第一层：客户端身份（他们叫 «пара API»）

[Telegram Soft Expert — 参数生成器](https://ru.telegramexpert.pro/manuals/generator-parametrov) 把建号身份写死成三套：

| 他们的选项 | api_id | 俄语圈说法 | 用途 |
|------------|--------|------------|------|
| Android（标准） | **4** / hash `014b35…5103` | «стандартное приложение Телеграм на Андроид» | **唯一推荐的建号身份** |
| Android X | **21724** | 比标准 App 能同时登更多号 | 多账号客户端，**不是** 4 的替代建号门票 |
| Desktop | **2040** | «создать новый тут не получится» | **只能登已有号 / 转 TDATA**，禁止拿来注册 |

Lolz / Zelenka 帖 [7435060](https://zelenka.guru/threads/7435060/) 另外散播一整张泄露表（6 / 8 / 2040 / 21724 / Webogram 2496 等）。论坛里有人把 **4 的 hash 错安到 api_id=6 上**——和本仓曾经的 invalid 组合是同一类错误。

行业默认：**建号用 4，不要用现网 Play 的 6，不要用 Desktop。**  
本仓 vault 可用样本：+91 成功账号 **9/10 为 api_id=4** + 同一 hash + app_version 12.7.3 + Push，**不是** 6 的 Email→Payment 链。见 [VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md](./VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md)。

### 1.2 第二层：真机证明（Push + SafetyNet，他们叫 эмуляция устройства）

[通用注册器手册](https://ru.telegramexpert.pro/manuals/universalnyiy-registrator) 与 [手动 SIM 注册](https://ru.telegramexpert.pro/manuals/ruchnaya-registratsiya-sim) 把 Antisafety.net 写成注册机标配：

- **Push-токен**：模拟「用户第一次打开官方 App 并同意通知」。手册原文写它 **«полезно в ситуациях, когда SMS-коды не приходят»**（SMS 不来时靠它完成确认）。
- **SafetyNet-токен**：假装设备未 root、应用来自 Play。
- **Flood 时换 Push**：token 被 flood 就丢进 резерв，冷却后再用，最多复用约 5 次。
- **临时邮箱**：部分流程要求 email；推荐 Gmail 档「премиум-почта」，普通域名易进黑名单。

这与本仓「公开 api_id 必须 attach Push，否则必 `API_ID_PUBLISHED_FLOOD`」一致。差别是：他们把 Push 同时当成 **过闸门票** 和 **收码通道**；我们只验证了前者。官方文档里 `CodeSettings.token` **仅官方 iOS Firebase**；官方 Android 源码 sendCode **不设** 该字段，FCM 是登录之后才 `registerForPush`。所以俄语圈这套「Android + Push token 建号」本身就是半官方杂交——能过 published 闸，不等于能拿到 Firebase SMS。

### 1.3 第三层：号码质量（比选国更被他们反复强调）

手册和接码站把号码分成三档，俄语口头禅几乎不变：

| 档 | 俄语说法 | 他们的判断 |
|----|----------|------------|
| 最好 | реальная SIM / SIM-based виртуальный номер / свежий номер | 运营商真实移动号，没用过或很少用过 |
| 能用 | приватные одноразовые активации | 付费接码里的「私有一次性」 |
| 废 | VoIP、бесплатные публичные SMS-сайты、многократно использованные | 免费公共池、回收号 |

[Soft Expert 2026 注册文](https://ru.telegramexpert.pro/posts/telegram-registration-2026-guide) 写：Telegram 已能区分廉价 VoIP 和真实 SIM；免费公共服务「почти не работают」。

注册器里有一条对**本仓当前失败形态**最关键的开关：

> **«Отклонить номер, если код отправлен в приложение»**  
> 码被打进 App = 该号上**已有 Telegram 账号**（常带 2FA）。立刻扔号，不要继续。

这正好对应本仓大量 `SentCodeTypeApp`：不是「差一点就能出 SMS」，而是号池在卖**已注册过的回收号**。俄语圈把这类号当消耗品过滤，不拿来「再试一次握手」。

### 1.4 第四层：网络与指纹对齐（Region Gateway）

[封禁 / Region Gateway 2026](https://ru.telegramexpert.pro/posts/telegram-banned-guide-2026-fix-account-ban) 把注册瞬间的核心核对写成：

1. 号码 MCC（国家码）  
2. IP 地理  

+44 号配俄亥俄数据中心 IP → «высокорисковое несоответствие»，号可能被永久拉黑。配套纪律：

- 住宅或 4G/5G 移动 IP，不要 AWS/DO 数据中心  
- **sticky**（固定）IP，不要注册过程中旋转  
- **1 账号 : 1 IP**（FAQ 写安全上限 1 IP 配 1–3 号）  
- 时区、系统语言、App 语言、机型字符串与号国一致  
- [参数生成器](https://ru.telegramexpert.pro/manuals/generator-parametrov) 特别强调时区填错会导致 «масс-баны»

接码站 [SMSCode](https://smscode.gg/ru/blog/kupit-virtualnyj-nomer-dlya-telegram) 唱反调：号国不必等于你人在哪，功能不受影响。这是**卖号文**——他们说的是「账号注册完之后能不能用」，不是「sendCode 会不会被 Gateway / 反欺诈加分」。两套话术不要混：

- **OTP 能不能进接码面板**：更取决于号是不是 VoIP/回收，以及 Telegram 选了 SMS 还是 App。  
- **号会不会秒封 / 会不会 FLOOD**：俄语农场坚信号国 IP + 时区对齐。本仓选国实验证明换国能改变 **App vs FLOOD 窗口**，**不能**把 App 变成 SMS。

### 1.5 他们明确不相信的事

| 俄语圈基本不讨论 / 直接否定 | 含义 |
|------------------------------|------|
| 用 Desktop / Web 建新号 | 手册写死做不到；2023 政策后第三方 SMS 建号被禁 |
| 伪造 1$ / Stars 收据过 Paid auth | 策略是**别进**官方付费流（换身份/换国/换号），不是破解 Payment |
| 自建 api_id 走 SMS 量产新号 | 官方要求第三方走 App/QR/邮箱；商务 SMS 要 `sms@telegram.org #enableSMS` |
| 「关掉检查就能当官方 App」 | 他们反而加 AntiSafety，不减凭证 |

---

## 2. 经验帖 / 手册摘要（带来源）

### 2.1 Telegram Soft Expert（行业默认手册，中等可信）

这是俄语注册机圈的「教科书」。营销绑定 IPFoxy / ProxyCove / AntiSafety，数字（「90%+ 独特性」）不可当通过率。但 **api_id 表、Desktop 不能建号、Push/SafetyNet、扔 App 投递号** 与本仓观测同向。

| 文 | 他们说了什么 | 链接 |
|----|----------------|------|
| 参数生成器 | 建号默认 **api_id=4**；X=21724；Desktop=2040 **不能新建**；时区/厂商/App 版本/语言必须像真机；时区用相对 EET 的秒偏移 | [ru.telegramexpert.pro/manuals/generator-parametrov](https://ru.telegramexpert.pro/manuals/generator-parametrov) |
| 通用注册器 | AntiSafety 出 Push+SafetyNet；SMS 不来时靠 Push；Flood 换 token、进 резерв、冷却复用；过滤 2FA/FloodWait/已进 App 的号；可强制语音；并发别爆接码 API | [universalnyiy-registrator](https://ru.telegramexpert.pro/manuals/universalnyiy-registrator) |
| 手动 SIM 注册 | 同一套 эмуляция；Push 被写成「某些国家 SMS 几乎不来时的**唯一**确认方式」 | [ruchnaya-registratsiya-sim](https://ru.telegramexpert.pro/manuals/ruchnaya-registratsiya-sim) |
| 注册 2026 | 过号靠 SIM-based；VoIP/免费池废；设备/模拟器/IP/号质都被打分；新号有发言限制，要 прогрев | [telegram-registration-2026-guide](https://ru.telegramexpert.pro/posts/telegram-registration-2026-guide) |
| Ban / Region Gateway 2026 | IP 国 ≠ 号国 → Gateway；数据中心 IP 是红区；国家难度：id/vn **低**，in/us/gb **中**，**ru 高**；sticky 住宅 IP；不要买「别人炒过的号」再换 IP 登 | [telegram-banned-guide-2026-fix-account-ban](https://ru.telegramexpert.pro/posts/telegram-banned-guide-2026-fix-account-ban) |
| IP 声誉 2026 | 注册第一眼看 IP；注册用移动/住宅；养号用 sticky；1 号 1 出口；莫斯科号配印度 IP 本身就可疑 | [telegram-ip-reputation-…](https://ru.telegramexpert.pro/posts/telegram-ip-reputation-account-registration-warmup-2026) |
| 多账号 / 养号 | VoIP 与虚拟号「часто заранее помечены」；实体 SIM 更安全；号国与连接国断裂是红旗 | [how-to-run-multiple-…](https://ru.telegramexpert.pro/posts/how-to-run-multiple-telegram-accounts-without-ban) |

PirateCPA 对 Expert 的评测（2026-03）重复同一栈：AntiSafety 模拟真机、接 10 家 SMS、分「真 SIM 手工 / 接码自动 / 任意 API」。  
来源：[piratecpa.net/…obzor-telegram-expert…](https://piratecpa.net/2026/03/ot-registraczii-do-nakrutki-obzor-telegram-expert-softa-kotoryj-avtomatiziruet-vse-v-telegram-i-spasaet-akkaunty-ot-bana/)

### 2.2 DTF / 哈萨克财经 / BitBrowser（付费墙，中高可信）

| 文 | 要点 | 链接 |
|----|------|------|
| DTF 2025-08 | 肯尼亚 / 南非 / 尼日利亚等注册要 **Premium**；动机 = 非洲 A2P SMS 贵 + 虚拟号刷号的经济过滤器 | [dtf.ru/…platnaya-registratsiya-keniya-yuar](https://dtf.ru/id1251970/3981024-telegram-platnaya-registratsiya-keniya-yuar) |
| inbusiness.kz 2025-11 | 德 / 巴西 / 非洲多国，登录也可能付约 **1–1.5 USD**（报道约 650 坚戈），附一周 Premium | [inbusiness.kz/…dengi-za-avtorizaciyu](https://inbusiness.kz/ru/last/telegram-nachal-brat-s-polzovatelej-dengi-za-avtorizaciyu) |
| BitBrowser RU 2026-07 | 把用户看到的「SMS Fee」对齐官方 `auth.sentCodePaymentRequired`；**唯一官方确认原因是该国/运营商 SMS 成本高**；代理/虚拟号/换 IP「100% 触发 Fee」**没有**官方证据；Paid auth **只出现在官方客户端**；QR/Passkey/其它设备上的 App 码是登录替代，不是无号建号 | [bitbrowser.net/ru/blog/telegram-sms-fee](https://www.bitbrowser.net/ru/blog/telegram-sms-fee) |

官方 TL 原文支持 BitBrowser 的核心句：Paid auth **only usable by official clients**。[core.telegram.org/api/auth#paid-auth](https://core.telegram.org/api/auth#paid-auth)

本仓：iq/ma/id/pe + **api_id=6** → Email 后 **Payment 100%**。俄语圈建议是 **别用 6 去高成本国**，不是去伪造收据。

### 2.3 接码站营销（低～中，只能当选国方向）

[SMSCode 2026 Telegram 指南](https://smscode.gg/ru/blog/kupit-virtualnyj-nomer-dlya-telegram) 自称（2026-03）：

| 国家 | 他们宣称成功率 | 怎么读 |
|------|----------------|--------|
| kz +7 | 93–96% | 俄语圈**第一推荐国**（看起来像独联体、比 ru 便宜） |
| ua | 93–96% | 同档，更贵 |
| ru | 96%+ | 最贵；Expert 反而标 **高难度** |
| in | 87% | 便宜大池；本仓历史 **唯一成功国**，近几轮 App/FLOOD |
| pk | 88% | 南亚候补 |
| id | 86% | 与 Expert「低难度量产」同向 |
| us | 72% | 「Telegram 限制 VoIP」——回避 |

这些百分比多半是「接码面板收到了某条 SMS」，**不是** `auth.sendCode` 返回 `SentCodeTypeSms` 并完成注册。SMSCode 还写：Telegram 会先尝试 **来电**，虚拟号通常接不了，要等 15–20 秒再点「发 SMS」——这是真人官方 App 的交互，MTProto 自动化经常对不上。

同站英文 [Best countries 2026](https://smscode.gg/blog/best-countries-for-virtual-numbers-2026) 甚至把 **俄罗斯** 写成 Telegram 验证「 consistently the top performer」。这与 Expert「ru 高难度」+ 2025 秋俄运营商拦国际 OTP（见 §2.5）**互相打架**。接码站会把「能买到 ru 号」说成「Telegram 最爱 ru」，不可采信为策略。

### 2.4 4PDA / 普通用户（中高：政策层，不是农场技巧）

[4PDA · Nagram X 帖](https://4pda.to/forum/index.php?showtopic=1104292&st=1900)（2026-03 用户回复，可复核）：

- 不能无号注册，也不能用邮箱建新号。  
- **「Регистрацию на номер можно делать только в ориг. Телеграм。」**  
- 第三方客户端登录时，码会打到「另一台已登录的 Telegram」，没有已有会话就进不去。

这是 2023 年「新用户须先在官方移动端创建」政策的用户侧回声，不是农场秘籍。含义：泄露 api_id=4/6 是在 **sendCode 层冒充官方移动端身份**，不是变成 Play 签名的官方 APK。

哈萨克媒体 [NewTimes.kz 2026 注册说明](https://newtimes.kz/obshchestvo/223045-kak-zaregistrirovatsia-i-udalit-akkaunt-v-telegram-v-2026-godu-instruktsiia) 给普通人的步骤仍是：从 App Store / Play 装官方 App，用能控制的手机号。码可能来 SMS，也可能来已打开的另一台设备。

### 2.5 Habr / 「代码杜罗夫」——俄本土 SMS 被拦（高，但是**另一议题**）

| 文 | 要点 | 链接 |
|----|------|------|
| Habr 转 Forbes 2025-10-31 | 俄运营商对 **新用户** 的 Telegram/WhatsApp 国际 OTP SMS/来电被拦；Beeline 尤明显；MTS/MegaFon 当时仍有部分可达 | [habr.com/ru/news/962208](https://habr.com/ru/news/962208/) |
| Habr 评论 961854 | 评论区强调：邮箱/Passkey/其它设备上的码是 **已有号登录** 的退路；**初次注册仍要 SMS/来电** | [habr.com/…/961854/comments](https://habr.com/ru/companies/femida_search/news/961854/comments/) |
| Habr Q&A 1355108 | 虚拟号常因「号上已有 Telegram」导致码走 Bot/App 而不走 SMS；建议 VPN **与 SIM 同国**，且别先登 Web | [qna.habr.com/q/1355108](https://qna.habr.com/q/1355108) |
| Habr sandbox：my.telegram.org | 从俄申请 api_id 时，VPN 会导致号国与 IP 国不一致被拒；hosts 直连让反欺诈看到「俄号+俄 IP」 | [habr.com/ru/sandbox/284878](https://habr.com/ru/sandbox/284878/) |

**不要把这条和 Paid auth 混在一起。** 俄本土是监管拦运营商 A2P；Paid auth 是 Telegram 对高 SMS 成本国收一周 Premium。Expert 把 ru 标高难度，两条原因叠在一起。本仓主战场是虚拟号 + api_id=4，不是俄实体卡。

### 2.6 「为什么收不到 SMS」俄语实用文（中，真人场景）

| 文 | 他们列的原因 | 链接 |
|----|----------------|------|
| Hidemium 2026-08 | 号输错、运营商过滤、**虚拟号被拒**、请求太频、码其实在**其它已登录设备**、VPN/代理/地区限制 | [hidemium.io/ru/blog/fixing-the-telegram-verification-code-issue](https://hidemium.io/ru/blog/fixing-the-telegram-verification-code-issue/) |
| vc.ru 经验帖 | 先关 VPN 再要码（针对真人在家用 ru 号） | [vc.ru/…ne-prihodit-kod…](https://vc.ru/niksolovov/1392705-ne-prihodit-kod-v-telegram-kak-reshit-problemu-za-paru-minut) |
| SMSCode | 卡在「来电尝试」、号已注册、号段被临时封、中间运营商故障 | 见 §2.3 |

农场文和普通用户文在「码在另一台设备」上完全一致——这就是 `SentCodeTypeApp`。

### 2.7 代理 / 指纹外围（低～中，养号 > 建号）

Pressaff、Coronium、Neironica 等俄语 SEO 文重复：1 号 1 住宅代理、指纹与 GEO 一致、注册后 5–14 天 прогрев、不要同一 IP 连开几十个。这些对 **session 存活** 有行业共识，对 **sendCode 出 SMS** 没有公开对照实验。本仓近几轮失败发生在 sendCode 当时，还没走到养号。

`API_ID_PUBLISHED_FLOOD` 在俄语公开网上几乎不被当术语讨论；出现在官方 [obtaining_api_id](https://core.telegram.org/api/obtaining_api_id) 与 TDLib issue。俄语圈用口语 **флуд / flood токена / API flood**，Soft Expert 的 «Заменить push, если он получил flood» 就是在应对同一类闸，只是他们归因到 **token** 而不是 api_id 数字。

---

## 3. 成功相关因素表

「与本仓是否一致」只看 **已发生的 RPC / vault 样本**，不看愿望。

| 因素 | 俄语圈说法 | 可信度 | 与本仓实验是否一致 |
|------|------------|--------|----------------------|
| **api_id=4 + 配对 hash 建号** | 标准 Android；不要用 6 去量产 | **高**（手册写死 + vault 样本） | **一致。** 历史成功全是 4；6 在 iq/ma/id/pe 进 Payment |
| **api_id=6 = 官方税区** | 现网 Play 身份，容易被当成该收 Premium 的客户端 | **高** | **一致。** 关 `official_client_emulation` 但仍用 6，iq 照样 Email→Payment |
| **Desktop 2040 / Web 不能建号** | «создать новый не получится» | **高**（政策 + 手册） | **一致（原则）。** 未拿 2040 建号；也不该测 |
| **无 Push 用公开 ID** | 他们直接上 AntiSafety，不会裸发 | **高** | **一致。** G3 无 Token 必 FLOOD |
| **有 Push 仍可能 FLOOD** | «Заменить push, если flood»；token 进 резерв 冷却 | **中高** | **一致。** G1 / 选国填满波：已 attach 仍 FLOOD；窗口约十几分钟翻盘 |
| **Push = SMS 不来时的收码通道** | 手册原话 | **中（机制说混了）** | **部分一致。** Push 能帮过 published 闸；**不能**把 `SentCodeTypeApp` 变成 SMS。官方 Android 建号并不靠 CodeSettings.token 收 SMS |
| **SafetyNet / Play Integrity** | 真机、非 root、来自 Play | **中**（官方确有 Integrity；第三方 token 是否被认是另一回事） | **方向一致，质量未过关。** 本仓 REGHelp/AntiSafety 形态对了仍常被拒 |
| **SIM-based > VoIP > 免费池** | 2026 主旋律 | **高** | **方向一致。** 本仓用的是付费接码虚拟号，不是免费 VoIP；仍大量 App，说明「付费」≠「新鲜 SIM」 |
| **回收号 → 码进 App，应丢弃** | 注册器开关原文 | **高** | **高度一致。** 选国探针 ph/vn/id = App×10–12，0 SMS |
| **号国住宅/移动代理** | Region Gateway；数据中心红区 | **中** | **部分一致。** pk 先代理超时后 FLOOD；换国改变 App/FLOOD 窗口。**没有**「对齐了就出 SMS」的证据 |
| **时区 / lang / 机型对齐号国** | 填错会 масс-бан；Beginner 必填 | **中** | **弱一致。** follow-up：T4（对齐）与 T0（空 lang/tz）同场都 FLOOD → **窗口/Token >> 握手字段**。vault 成功样本确有 `hi-in`/`en-in` 与真机型 |
| **1 号 1 sticky IP** | 共享 IP 继承邻居垃圾声誉 | **中**（养号逻辑强，建号证据弱） | **未单独 AB。** 不与成功矛盾 |
| **选 kz / ph / vn / id，避 ke/za/ng、避 ru** | Expert 难度表 + DTF Paid 点名 + SMSCode 捧 kz | **中**（选国方向） | **选国方向部分应验，SMS 不应验。** ph/vn/id 探针能 App、in 同场 FLOOD；全程 0 SMS。kz **租不到**。ke/za/ng 未测（按调研回避） |
| **in 性价比对照** | SMSCode 87%；Expert「号段大量回收」 | **中** | **一致于「回收」。** 历史 vault 成功是旧窗口；近几轮 in = App 或 FLOOD |
| **注册间隔 5–20 分钟** | SMSCode：连开会触发限制 | **中** | **同构。** R1 探针后 10 分钟填满翻 FLOOD；R2 拉开间隔把 App 窗从 ~6 分拉到 ~20 分，**仍无 SMS** |
| **新号要 прогрев 再群发** | 全行业 | **中**（存活，不是 sendCode） | **未走到。** 本仓卡在收码前 |
| **付 1$ 可过墙** | 新闻有，农场不当主策略 | **高**（墙存在）/ **高**（假收据无效） | **一致。** 真墙；MTProto 假收据 `PLAYMARKET_RECEIPT_INVALID` |
| **换更多公开 api_id（8/10/21724…）** | 论坛会贴表；Expert 建号仍押 4 | **低（当主策略）** | **不一致于「再换 ID」。** 本仓结论：停扩公开 ID |

---

## 4. 常见失败原因（俄语圈清单 × 本仓对照）

按出现频率，不是按严重性。

### 4.1 码根本没有变成 SMS

| 俄语圈怎么叫 | 实际是什么 | 本仓对应 |
|--------------|------------|----------|
| код ушёл в приложение | 号上已有会话，`SentCodeTypeApp` | 选国探针主形态；resend 不可用 |
| номер уже использовался / с 2FA | 回收号 | 注册器建议直接 reject |
| VoIP / бесплатный номер | line-type 不是 mobile，SMS 可能根本不发 | 接码营销也承认 us VoIP 最差 |
| Telegram 先打电话 | 虚拟号接不了语音，真人要点「改发 SMS」 | 自动化若不等这一步会误判「没码」 |
| флуд | 请求太频或 token/IP 窗口关了 | `API_ID_PUBLISHED_FLOOD` 或 PHONE_NUMBER_FLOOD |
| оператор режет SMS | 俄 Beeline 等拦国际 OTP；部分国家运营商拦 TG | 与 Paid auth 不同；ru 因此「又贵又难」 |

### 4.2 sendCode 直接被拒

| 俄语圈怎么叫 | 实际是什么 | 本仓对应 |
|--------------|------------|----------|
| flood токена / API flood | 公开 api_id 无被接受的平台凭证，或 token 过期/签名不被认 | 无 Token = 必中；有 Token 仍可能中（主战场） |
| Region Gateway | 号国 ≠ IP 国，或数据中心 ASN | 手册主因；本仓未单独打出该错误码，但代理超时/错国出口会表现为连不上或窗口差 |
| PHONE_NUMBER_BANNED | 号段/该号被拉黑 | 接码站会换号退款 |
| PHONE_NUMBER_APP_SIGNUP_FORBIDDEN | 2023 政策：该客户端身份不许 SMS 建号 | 第三方/Desktop 路径；用 4 是在赌「被当成官方移动端」 |

### 4.3 过了闸却进税区

| 俄语圈怎么叫 | 实际是什么 | 本仓对应 |
|--------------|------------|----------|
| платная регистрация / SMS Fee | `auth.sentCodePaymentRequired` | **api_id=6** + 高成本国 100%；**api_id=4 本仓未撞上**（不是因为换国，是因为身份不是 6） |
| нужна почта | `SetUpEmailRequired` | 6 的前置；4 近几轮几乎不进这条 |

### 4.4 俄语圈常误判、本仓已踩过

| 误判 | 为什么错 |
|------|----------|
| 「关掉本地拒绝裸发就不会 FLOOD」 | 本地拦截根本没发 RPC；关掉只会真打到 Telegram，同一 400 |
| 「再换一个泄露官方 ID」 | 4 已是农场标准建号身份；8/10/2040/17349 要么同闸要么政策禁止建号 |
| 「对齐时区就能出 SMS」 | follow-up：对齐与不对齐同场 FLOOD |
| 「SMSCode 93% = 注册成功率」 | 那是接码面板统计 |
| 「ru 号 Telegram 最爱」 | 接码站文案；Expert + 2025 监管都说 ru 难 |
| 「付费虚拟号 = 真 SIM」 | 付费池里仍大量回收号（App 投递就是证据） |

---

## 5. 对当前策略的启示（api_id=4 主攻、6 后备、暂不 Payment）

俄语资料 **支持维持这条策略**，不支持推翻。

### 5.1 应该继续做的

1. **钉死 api_id=4 + 配对 hash `014b35…5103` + 官方 Android 设备串 + 强制 Push。** 这就是 Soft Expert 的默认建号身份，也是 vault 成功样本。  
2. **6 只留作「真要走 Paid / 短期会员」的后备，默认关。** 俄语圈把 6 当税区；本仓已在多国验证 Email→Payment。  
3. **不要做 Payment / 假收据 / resendCode 空壳 SMS。** 农场手册完全不走这条；BitBrowser 也写没有通用免费绕过。  
4. **把 `SentCodeTypeApp` 当成号池问题，不是握手问题。** 俄语注册器的默认动作是 **丢号**，不是改 lang_pack。  
5. **Token 被 FLOOD 就冷却/换发，不要同一窗口内填满。** 他们有 резерв；我们 R1/R2 已经看到 10–20 分钟窗口。继续烧同一 Push 只会抬 FLOOD 计数。  
6. **代理国 = 号国、sticky、避免数据中心。** 即使它不是 SMS 开关，它也是他们和 Habr「申请 api_id」都承认的反欺诈信号；pk 超时说明出口质量会先于协议把任务打死。

### 5.2 不应该再烧的

| 别做 | 俄语圈 + 本仓共同理由 |
|------|------------------------|
| 再扫 8/10/2040/17349/21724 当建号主 ID | Expert 建号不用它们；2040 明文不能新建；21724 是 X 不是 4 |
| iq/ma + api_id=6 冲 Payment | DTF/官方文档/本仓 100% 墙 |
| 把预算砸进 ke/za/ng/de/br「试试会不会免费」 | 已被点名 Paid auth |
| 用 us VoIP / 免费公共号 | 全俄语源回避 |
| 指望 kz 营销 93% 在无库存时硬租 | 本仓两轮 getNumber 全 noNumber |
| 为「时区没对齐」再开一轮纯握手 AB | follow-up 已否证它对 FLOOD 窗口的决定性 |

### 5.3 俄语圈多写、本仓仍缺的（按杠杆排序，不是再租 50 号）

这些是他们**相信**且我们**尚未单独证伪**的点；其中 1–2 才可能改变 4 路径，3–4 是存活问题。

1. **号码「新鲜且非 App 投递」的筛选**（最高杠杆）。没有这层，换国只是换一个也会 App 的池。  
2. **Push/SafetyNet 的签发质量**（与 FLOOD 窗口绑定）。他们为 flood token 做了冷却队列；我们已证明「带了仍拒」是主战场。  
3. **SIM-based vs 接码 VoIP 的线型**——接码面板标价高不等于 HLR 是 mobile。  
4. **注册后的 1:1 sticky IP 与 прогрев**——对「注完立刻拿去群发」才关键；对「先收到 SMS」不是第一因。

### 5.4 一句话策略

> 俄语圈和本仓已经站在同一侧：**用 4 冒充官方移动端，避开 6 的税，不付那 1 美元。**  
> 他们能批量出号（至少他们的产品靠卖这个故事活着）时，多出来的不是新的 api_id，而是 **更新的号 + 更像官方包签名的 Push/Integrity + 更狠的 App 投递过滤 + 更慢的节奏。**  
> 当前 4 路径的失败形态（App 或 FLOOD、0 SMS）按他们的分类，分别叫「号已经有号了」和「token/窗口 flood」——**不是**「该切 Payment」。

---

## 6. 来源索引

**手册 / 农场**

- https://ru.telegramexpert.pro/manuals/generator-parametrov  
- https://ru.telegramexpert.pro/manuals/universalnyiy-registrator  
- https://ru.telegramexpert.pro/manuals/ruchnaya-registratsiya-sim  
- https://ru.telegramexpert.pro/posts/telegram-registration-2026-guide  
- https://ru.telegramexpert.pro/posts/telegram-banned-guide-2026-fix-account-ban  
- https://ru.telegramexpert.pro/posts/telegram-ip-reputation-account-registration-warmup-2026  
- https://ru.telegramexpert.pro/posts/how-to-run-multiple-telegram-accounts-without-ban  
- https://piratecpa.net/2026/03/ot-registraczii-do-nakrutki-obzor-telegram-expert-softa-kotoryj-avtomatiziruet-vse-v-telegram-i-spasaet-akkaunty-ot-bana/  
- https://zelenka.guru/threads/7435060/ （泄露 api_id 表，hash 有错配，仅作「论坛在传什么」）

**新闻 / 付费墙**

- https://dtf.ru/id1251970/3981024-telegram-platnaya-registratsiya-keniya-yuar  
- https://inbusiness.kz/ru/last/telegram-nachal-brat-s-polzovatelej-dengi-za-avtorizaciyu  
- https://www.bitbrowser.net/ru/blog/telegram-sms-fee  
- https://habr.com/ru/news/962208/  
- https://habr.com/ru/companies/femida_search/news/961854/comments/  
- https://core.telegram.org/api/auth#paid-auth  
- https://core.telegram.org/api/obtaining_api_id  

**接码 / 普通用户**

- https://smscode.gg/ru/blog/kupit-virtualnyj-nomer-dlya-telegram  
- https://4pda.to/forum/index.php?showtopic=1104292&st=1900  
- https://qna.habr.com/q/1355108  
- https://hidemium.io/ru/blog/fixing-the-telegram-verification-code-issue/  
- https://newtimes.kz/obshchestvo/223045-kak-zaregistrirovatsia-i-udalit-akkaunt-v-telegram-v-2026-godu-instruktsiia  

**本仓（实验，非本轮）**

- vault：+91 成功样本 api_id=4 + Push + 12.7.3  
- 选国 R1：租 44，App 10，SMS 0，真实 FLOOD 22，Payment 0  
- 选国 R2：租 51，App 12，SMS 0，真实 FLOOD 26，Payment 0  
- G1/G3：4 + 有/无 Push；无 Token 必 FLOOD，有 Token 仍可能 FLOOD  
- api_id=6：iq/ma/id/pe Email→Payment 100%
