# 俄语圈选国：Telegram 注册通过率调研

> 时间：2026-09-02  
> 方法：俄语手册 / DTF / 虚拟号站 / 4PDA 评论 + 本仓库历史 A/B  
> 本文件**先调研、少租号**。实测数字见 [COUNTRY_PASSRATE_50_RESULTS.md](./COUNTRY_PASSRATE_50_RESULTS.md)。

**先读结论：** 俄语农场并不押伊拉克/肯尼亚这种「便宜但必进 Paid auth」的号；他们押 **api_id=4 + SIM-based 虚拟号 + 号国代理**，国家上更常推 **哈萨克（kz）/ 菲律宾（ph）/ 越南（vn）/ 印尼（id）**，印度（in）当性价比对照。本轮实测就按这个名单，**不把 50 号砸进 iq/in 刚测过的 FLOOD 窗口**。

---

## 1. 俄语资料怎么说「哪国好过」

### 1.1 Telegram Soft Expert（注册机手册，行业默认）

来源：

- [Генератор параметров](https://ru.telegramexpert.pro/manuals/generator-parametrov) — 建号默认 **api_id=4 / hash 014b35…5103**，Desktop 不能新建号  
- [Регистрация 2026](https://ru.telegramexpert.pro/posts/telegram-registration-2026-guide) — 过号靠 **SIM-based**，VoIP / 免费公共池几乎废  
- [Бан / Region Gateway 2026](https://ru.telegramexpert.pro/posts/telegram-banned-guide-2026-fix-account-ban) — 明确按国给了难度

手册里的国家难度（他们自称「практические тесты」；营销腔很重，但方向和我们仓库一致）：

| 号码 | ISO | 他们的难度 | 备注 |
|------|-----|------------|------|
| +62 | **id** | **低** | 「适合 массовая работа」，IP 必须印尼住宅 |
| +84 | **vn** | **低** | 注册简单，但当地运营商常拦 Telegram |
| +91 | **in** | 中 | 号段大量回收，只要新鲜号 |
| +1 | us | 中 | 回收号多，VoIP 差 |
| +44 | gb | 中 | gateway 严，IP 必须英国 |
| +7 俄 | **ru** | **高** | 「официальный мессенджер，контроль строже」 |

他们**没有**公开一份可复制的 ISO 白名单文件；国家列表是接码平台 API 拉下来的。真正写进手册的策略是：

1. 身份用 **api_id=4**，不要走现网 Play 的 6（6 = 官方税区）  
2. 时区 / 语言 / 机型对齐号国  
3. 代理国家 = 号码国家，否则 Region Gateway  
4. 回避 VoIP 和免费公共池

这与 [RU_DOLLAR_WALL_RESEARCH.md](./RU_DOLLAR_WALL_RESEARCH.md) 一致。

### 1.2 DTF / 哈萨克财经：点名「要 Premium」的国

| 来源 | 点名 | 含义 |
|------|------|------|
| [DTF 2025-08](https://dtf.ru/id1251970/3981024-telegram-platnaya-registratsiya-keniya-yuar) | **肯尼亚 / 南非 / 尼日利亚** | 虚拟号刷号 + 高 A2P SMS → Premium 过滤器 |
| [inbusiness.kz 2025-11](https://inbusiness.kz/ru/last/telegram-nachal-brat-s-polzovatelej-dengi-za-avtorizaciyu) | **德国 / 巴西 / 非洲多国**，约 1–1.5 USD | 登录也可能付费 SMS |
| [BitBrowser RU](https://www.bitbrowser.net/ru/blog/telegram-sms-fee) | 独联体/俄运营商抬高国际 A2P | 解释 **为什么 ru 贵且难**，不是 kz 黑名单 |

**回避（调研层，不测或只作阴性对照）：** ke / za / ng / de / br，以及本仓库已 100% Payment 的 **iq + api_id=6**、**ma/pe/id + api_id=6**。

### 1.3 俄语虚拟号站（SMSCode 等，营销数字需打折）

[SMSCode 2026 Telegram 指南](https://smscode.gg/ru/blog/kupit-virtualnyj-nomer-dlya-telegram) 自称：

| 国家 | 他们宣称成功率 | 我们怎么读 |
|------|----------------|------------|
| **kz** +7 | 93–96% | 俄语圈**第一推荐国**：号看起来像独联体、比 ru 便宜、未进 DTF 付费名单 |
| **ua** | 93–96% | 同档，但号贵、库存不稳，本轮不当主预算 |
| **in** | 87% | 便宜大池；本仓库历史 vault **唯一成功国**，近几轮变 App/FLOOD |
| **pk** | 88% | 与 in 同类南亚池，作 kz/ph 无库存时的候补 |
| **id** | 86% | 与 Expert「低难度」同向 |
| us VoIP | 72% | 回避 |

这些百分比多半是「接码平台自己收到了 SMS」，**不是** Telegram `auth.sendCode` 走出 `SentCodeTypeSms` 并完成注册。只能当**选国方向**，不能当通过率。

哈萨克号段（同一文）：

- **Kcell** 700 / 701 / 702 / 766  
- **Beeline KZ** 705–708、771、775–778  

菲律宾侧（VirtualSMS / 农场口头禅）：**Globe / Smart / DITO** 及 TNT、TM、GOMO 等副牌。本轮接码平台未必能按运营商下单，报告里记实际租到的号段。

### 1.4 4PDA / Habr

- 4PDA 第三方客户端帖（Nagram / Forkgram / Telega）：**新号必须先在官方 App 收码**；虚拟号被描述为不可靠。这支持「api_id=4 农场路径 ≠ 官方 6 税区」，也支持「别在第三方 Desktop 上建号」。  
- 4PDA 哈萨克实体卡（Activ / Altel）出现在跨境支付场景：俄语用户把 **kz 实体 SIM** 当「能稳定收码的 +7」。虚拟号 kz 是这个偏好的廉价近似。  
- Habr 上俄本土「运营商限 SMS / 邮箱登录」是**另一议题**，不要和 Paid auth 混为一谈（见美元墙调研）。

### 1.5 本仓库历史（硬事实，压过营销表）

| 国家 | 栈 | 结果 | 文献 |
|------|----|------|------|
| **in** +91 | api_id=4 + Push + emu=false | vault 冲刺 **10/10 App**，0 SMS；更早 lod_user **有成功 session** | VAULT_MODE_SPRINT |
| **in** | 同栈，今日 follow-up | **6/6 FLOOD**，0 SMS | API4_FOLLOWUP |
| **iq** | api_id=6 ± emu | email → **Payment 100%** | GROK_IQ_SPRINT / PAYMENT_REQUIRED |
| **iq** | api_id=4 + Push | 窗口：App×2 → 20 分钟后 **FLOOD×5** | API4_DETAIL / FOLLOWUP |
| **id / pe / ma** | api_id=6 | **Payment 100%** | PAYMENT survey |
| **jo** | api_id=4 | FLOOD | GROK_IQ_SPRINT H7 |
| **ke / za / ng** | — | 未测；DTF 点名 Paid auth | 本文件 |

含义：

- **不要**再把主预算砸进 iq + api_id=6。  
- iq/in 刚测的 api_id=4 FLOOD 可作**窗口对照**，但不要占满 50。  
- in 仍值得 **小样本对照**（历史唯一成功国）。  
- id 在 **api_id=6** 上已死；本轮只测 **api_id=4**，看它是否仍是 Expert 说的「低难度量产国」。

---

## 2. 选国表

| 国家 ISO | 为何看好/看衰 | 来源 | 本轮是否纳入实测 |
|----------|----------------|------|------------------|
| **kz** | 俄语圈第一推：+7、Kcell/Beeline、宣称高通过、未进 Paid auth 点名 | Soft Expert 时区/代理纪律；SMSCode；4PDA +7 偏好 | **是（优选主样本）** |
| **ph** | 东南亚农场常客；Globe/Smart SIM-based；locale/代理目录已齐 | 农场惯例；VirtualSMS；本仓库 `ph` 指纹/姓名池 | **是（优选）** |
| **vn** | Expert 明文「低难度」 | Soft Expert ban-guide Q5 | **是（优选）** |
| **id** | Expert「低难度、适合量产」；**api_id=6 已被我们测成 100% Payment**，本轮只走 4 | Soft Expert；PAYMENT survey | **是（优选，仅 api_id=4）** |
| **in** | 历史 vault 成功；近几轮 App/FLOOD。作对照，不占满 | lod_user；VAULT；FOLLOWUP | **是（对照，小 N）** |
| pk | SMSCode 与 in 并列南亚池；指纹合成表较弱 | SMSCode | **候补**（kz/ph/vn/id 无库存时） |
| ua | 宣称高通过，但贵、库存飘 | SMSCode | **否**（成本） |
| ru | Expert「高难度」+ A2P 涨价 | Soft Expert；BitBrowser | **否** |
| iq | api_id=6 Payment 死胡同；api_id=4 刚全 FLOOD | 本仓库今日两轮 | **否**（不占 50） |
| ke / za / ng | DTF 点名 Premium | DTF | **否** |
| de / br | inbusiness 点名付费 SMS | inbusiness.kz | **否** |
| ma / pe / jo | 已测 Payment 或 FLOOD | GROK / survey | **否** |
| us | VoIP 过滤、回收号 | Soft Expert；SMSCode 72% | **否** |

### 本轮 3–5 个实测组合（国 × 主栈）

固定主栈：**api_id=4** + hash `014b35…5103` + Push attach + `official_client_emulation=false` + vault 机型 + `lang_pack=android` + 号国 tz。

| # | 组合 | 角色 | 计划 N |
|---|------|------|--------|
| 1 | **kz × smsbower**（无号则 Grizzly） | 优选 | 12 |
| 2 | **ph × smsbower** | 优选 | 12 |
| 3 | **vn × smsbower** | 优选 | 10 |
| 4 | **id × smsbower** | 优选（api_id=4 only） | 8 |
| 5 | **in × smsbower** | 历史成功对照 | 6 |
| C | **kz × T0**（不写 lang_pack/tz、不回放 vault） | 握手对照 ≤20% | 2 |

合计计划 **50**。api_id=6 **0 号**（Payment 已在 iq/ma/id/pe 证完，不再烧）。

每国先 **探针 4 号**；若 4/4 真实 `API_ID_PUBLISHED_FLOOD` 且 0 App/SMS，**停该国剩余额度**，把名额匀给仍有 App/SMS 信号的国。禁止把剩余 30 号倒进同一个 FLOOD 窗口。

---

## 3. 号段 / 运营商（调研，不是下单保证）

接码平台多数只能选国家，不能锁运营商。记录租到的前缀用于事后对照。

| 国 | 区号 | 常被提到的运营商 / 号段 |
|----|------|-------------------------|
| kz | +7 | Kcell 700–702、766；Beeline 705–708、771、775–778（**不是**俄 9xx） |
| ph | +63 | Globe / Smart / DITO；副牌 TNT、TM、GOMO |
| vn | +84 | Viettel / Vinaphone / Mobifone（Expert 警告当地可能拦 TG） |
| id | +62 | Telkomsel / XL / Indosat（Expert 要求印尼住宅 IP） |
| in | +91 | 历史成功批次；当前池 App-only / FLOOD |
| pk | +92 | Jazz / Telenor / Zong（候补） |

虚拟号质量纪律（各俄语手册重复）：

- 要 **SIM-based 一次性激活**，不要公开 VoIP  
- 号码国家 = 代理国家  
- 用过的 / 已进 spam 库的号直接退

---

## 4. 对实验的硬约束（写进脚本）

1. 主预算不进 iq + api_id=6 Payment。  
2. iq/in 今日 FLOOD 窗口不当 50 号主体；in 最多对照 6。  
3. 对照（T0）≤ 总预算 20%。本轮 T0 = 2（kz）。  
4. 禁止 Payment resend / 假收据。  
5. 失败号 cancel；接码余额不低于安全线（smsbower ≥ 4 USD，Grizzly ≥ 5 USD）。  
6. 余额不够跑满 50：跑能负担的最大 N，报告写「需充值 $X」。

脚本：`backend/scripts/run_country_passrate_50.py`。
