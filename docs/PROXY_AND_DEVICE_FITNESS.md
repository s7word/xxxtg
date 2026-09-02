# 代理类型与安卓设备生成：对照俄语农场 / vault

> 时间：2026-09-02  
> 方法：只读代码 + 既有 `data/ab_reports/*` / `docs/*` + 9 条 `lod_user` 成功 JSON + 指纹包 catalog。  
> **本轮 0 租号。** 未对 Proxy-Seller 下单；只读 `GET /proxy/list` 因会话出口不在 IP 白名单（`IP not allowed`）未能盘点账户库存。  
> 对照：[RU_REGISTRATION_EXPERIENCE_FACTORS.md](./RU_REGISTRATION_EXPERIENCE_FACTORS.md)、[VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md](./VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md)、[API4_DETAIL_AB_RESULTS.md](./API4_DETAIL_AB_RESULTS.md)、[API4_FOLLOWUP_AB_RESULTS.md](./API4_FOLLOWUP_AB_RESULTS.md)。

**先读两问：**

1. **移动代理 vs 住宅代理：没有系统测过，也没有提高过。** 流水线默认走号国**住宅**（自建池 → `{CC}_tg` 住宅列表 → 内置 CL/IN 静态住宅）；API 桶里的 `mobile` 与 `ipv4`（数据中心）混在最低优先级，**没有**按 `catalog_type` 偏好移动。俄语农场把「住宅 **或** 4G/5G 移动」当成相对数据中心的同一档，并不声称移动能把 `SentCodeTypeApp` 变成 SMS。
2. **设备字段：能生成「看起来像官方 Android」的机型串，默认握手仍不像官方客户端。** 指纹库 / 合成器在 `device_model`、`system_version`、`lang_pack=android`、号国 `tz_offset` 上对齐 vault；但生产默认 **不把** `lang_pack` / `tz_offset` 写入 InitConnection，**不消费** vault 的 `device_secret`，Push 仍是 REGHelp 签发、塞进 iOS 专用 `CodeSettings.token`。这三处缺口仍在。

---

## 1. 短答

| 问题 | 结论 |
|------|------|
| 有没有提高过 / 测过移动 vs 住宅？ | **未系统测过。** 全库 `docs/*` 与 `data/ab_reports/*` 没有任何以 `catalog_type=mobile` 为自变量的 AB。近几轮实验 `proxy_mode=auto`，出口是号国住宅（`resident_tg` / 静态住宅 / 自建池），不是 4G 移动对照。 |
| 结果如何？ | 无结果可报。已测的是 **号国对齐 vs 错国/超时**（pk 代理超时后 FLOOD），以及 **住宅池内部** 的 App / FLOOD 窗口。换代理类型从未被当成 SMS 开关。 |
| 自己的安卓设备生成是否符合要求？ | **机型字符串符合**（`samsungSM-G950F` 这类 MANUFACTURER+MODEL，与 vault 一致）。**协议身份部分符合、默认未启用。** vault / 官方 InitConnection 要的 `lang_pack=android` + `params.tz_offset` 代码能写，但 `data/config.json` 默认两个旗标都是 `false`。Push / Integrity **质量不符合**官方包签名。 |

俄语农场原文（[RU_REGISTRATION_EXPERIENCE_FACTORS.md](./RU_REGISTRATION_EXPERIENCE_FACTORS.md) §1.4）：注册用「住宅或 4G/5G 移动 IP，不要 AWS/DO 数据中心」+ sticky + 1 号 1 IP + 时区/语言/机型对齐号国。他们**没有**把移动写成比住宅更高一档的 sendCode 杠杆。本仓选国实验已经证明：换国能改 App/FLOOD 窗口，**不能**把 App 变成 SMS。

---

## 2. 已测 / 未测（代理）

| 对照 | 状态 | 证据 | 对注册效果的含义 |
|------|------|------|------------------|
| **移动 vs 住宅** | **未测** | 无 AB 脚本、无报告、无 `catalog_type` 过滤 | 未知；俄语圈当同一「好 IP」档 |
| 号国住宅 vs 错国 / 无节点 | **部分测过** | 选国 pk 先代理超时再 FLOOD；geo 1:1 禁止跨区 silent fallback（`select_best_proxy` / `proxy_slot_pool.py`） | 出口质量会先于协议把任务打死；不是 SMS 开关 |
| 住宅 `_tg` vs 内置静态 CL/IN | **工程有，无 AB** | `merge_proxy_pools(custom, resident_tg, api, static)`；静态仅 cl/in | 选源优先级，不是效果实验 |
| 自建池 vs Proxy-Seller | **工程有，无 AB** | 自建池优先；MA 等国靠导入 | 未做效果对照 |
| 数据中心 `ipv4` vs 住宅 | **未测** | API 全量列表含 `ipv4/ipv6/mobile/isp/mix/resident`，排序**不看** bucket | 俄语圈标红区；本仓未单独打出 Region Gateway 错误码 |
| sticky / 1:1 槽位 | **工程已做，未单独 AB** | `ProxyLeaseRegistry` + `BatchProxySlotPool`；`hunt_proxy_max_uses` | 俄语圈养号逻辑强；对 sendCode 出 SMS 证据弱（经验文档 §3） |
| InitConnection `lang_pack` / `tz` | **已测，无 SMS** | [API4_DETAIL_AB_RESULTS.md](./API4_DETAIL_AB_RESULTS.md) T0–T5；follow-up T4 vs T0 同场 FLOOD | 窗口/Token >> 握手字段 |
| api_id=4 + Push vs 无 Token | **已测** | G3 无 Token 必 FLOOD；G1 有 Token 仍可能 FLOOD | Push 是 published 闸，不是 SMS 通道 |
| 号池 App vs 新鲜号 | **已测（阴性）** | 选国 R1/R2：App 主形态，0 SMS | 当前失败主因 |

选型代码（默认，不是实验）：

1. 用户自建池（`custom`，可绑 `assigned_country`）  
2. Proxy-Seller `{CC}_tg` **住宅**列表（`ensure_tg_resident_list`，可 POST 创建列表，**不买 IP**）  
3. `/proxy/list` 全桶（**含 mobile 与 ipv4 数据中心**，`_source_rank=3` 最低）  
4. 内置静态住宅：仅 **CL / IN**（`STATIC_REGIONAL_POOLS`）  
5. 指定区域全军覆没 → **禁止跨区**，回落 `fallback_proxy`

`_sort_candidates` 只按健康 / 来源档 / ACTIVE，**不**按 `mobile > resident > ipv4`。因此只要该国有 `_tg` 住宅节点，自动路径**几乎永远抽不到移动代理**。前端代理页也没有「强制 mobile」开关。

本轮只读验证：从本会话出口打 `GET /proxy/list`、`GET /resident/lists` 返回 `IP not allowed`（白名单是生产机，不是 Cloud Agent）。**没有**因此去租号或改白名单。

---

## 3. 设备字段：达标 / 缺口

对照源：9 条 `lod_user/**/91*.json`（全部 `app_id=4`、hash `014b35…`、`lang_pack=android`、`tz_offset=19800`、有 `device_token` + `device_secret`）；官方 InitConnection（device / system / app / lang / `lang_pack` / 可选 `params.tz_offset`）；俄语参数生成器（时区秒偏移、真机型、App 版本、语言）。

生成链路：`device_generator.py` 写 REGISTRATOR SQLite → `DeviceDbManager` 按号国采样 → `DeviceProfileManager.get_resolved_profile` → `TelegramClient(...)` → 可选 `apply_init_connection_overrides`。

| 字段 | vault 成功样本 | 官方 / 农场要求 | 本仓生成 / 发出 | 判定 |
|------|----------------|-----------------|-----------------|------|
| **api_id** | 9/9 = **4** | 建号用 4，不要 6 | 合成库行是 **6**（`OFFICIAL_API_ID`）；`telegram_android_public` 模板与 `apply_official_api_id` 可纠正为 4。**生产 `config.json` 默认 `telegram_android` + `official_client_emulation=true` → 6** | **能力达标 / 默认配置不达标** |
| **api_hash** | `014b35…5103` | 与 4 配对 | `OFFICIAL_API_CREDENTIALS` 已纠正错配 | **达标**（配 4 时） |
| **device_model** | `OPPOCPH2035`、`samsungSM-G950F`、`vivoV2111`… | 真机 MANUFACTURER+MODEL | 合成 SKU 同格式；空库才回退「Samsung Galaxy S23 Ultra」这种自然语言 | **达标**（有指纹包时） |
| **system_version** | SDK 29–33 | 出厂 SDK 区间 | `SDK {29–35}`，与 SKU 绑定 | **达标** |
| **app_version** | **12.7.3 (67502/67509)** | 像真机、别乱编 build | 合成矩阵含 10.x–12.9.1；IN 自动包 12.7.3 仅约 10/80。需 `pin_app_version_substr=12.7.3` 或 `vault_fingerprint_replay` | **能对齐，默认不钉** |
| **lang_code** | JSON **无此键**；由 `system_lang_pack` 可推 | App 语言 | 合成 `en`/`hi` 等；`TelegramClient(lang_code=…)` 会发出 | **达标** |
| **system_lang_code** | `system_lang_pack`：hi-in×4、en-gb×3、en-in×2（**不是**清一色 hi-in） | 系统语言像号国 | IN 包有 en-in/hi-in/**en-gb**（与 vault 同分布）。`force_country_locale` 会钉死 `en-in` | **达标**（不必强行全 hi-in） |
| **lang_pack（profile）** | `android` 9/9 | 官方 Android 语言包名 | 模板 / 合成 / vault 回放均为 `android`；`telegram_x` 才是 `android_x` | **profile 达标** |
| **InitConnection.lang_pack** | 应 = `android` | Telethon 默认 `''`（「official apps only」） | 仅当 `init_connection_set_lang_pack=true`。**默认 false**。事后 `langpack.getLanguages(android)` **改不了已发握手** | **缺口仍在（默认）**；AB 已能写入，**未带来 SMS** |
| **tz_offset（profile）** | 19800 9/9 | 号国秒偏移，填错会 масс-бан（农场） | `COUNTRY_LANG_MAP` / 合成 / vault 回放都会填；日志「时区偏置」 | **profile 达标** |
| **InitConnection.params.tz_offset** | 应写入 | 官方可选 JSON | 仅当 `init_connection_set_tz_offset=true`。**默认 false** | **缺口仍在（默认）**；AB 已写入，follow-up 与空 tz **同场 FLOOD** |
| **package** | JSON **无此字段** | Play / Firebase 用 `org.telegram.messenger`；**InitConnection 无 package 位** | 只出现在假 Play 收据探测；MTProto 握手不发包名 | **不适用 InitConnection**；Play 身份仍假 |
| **Push token** | 9/9 有 `device_token`（FCM 形态） | 俄语圈 AntiSafety；官方 Android **sendCode 不设** `CodeSettings.token`（iOS 专用） | REGHelp/AntiSafety 签发后塞进 `CodeSettings.token` | **形态杂交；质量缺口仍在**（有 token 仍 FLOOD） |
| **device_secret** | 9/9 有 attestation 块 | 农场 SafetyNet / Play Integrity | **代码不消费**（仅实验脚本统计有无） | **缺口仍在** |
| **perf_cat** | 有 | 农场「真机档」 | 合成写入 | **次要，达标** |

### 默认配置 vs 实验主栈

| 开关 | `data/config.json` 默认 | api_id=4 细节 AB / 选国主栈 |
|------|-------------------------|-----------------------------|
| `active_app_type` | `telegram_android`（6） | `telegram_android_public`（4） |
| `official_client_emulation` | **true** | **false** |
| `init_connection_set_lang_pack` | **false** | true（处理组） |
| `init_connection_set_tz_offset` | **false** | true（T1）或 false（T0/T3） |
| `vault_fingerprint_replay` | **false** | true |
| `force_country_locale` | **false** | true |
| `pin_app_version_substr` | 空 | `12.7.3` |

因此：「我们会不会生成合格安卓设备？」→ **会。**  
「注册时 Telegram 是否看见合格官方握手？」→ **默认看不见 lang_pack/tz；实验里看见了，也没有 SMS。**

指纹包：catalog 45/51 启用，含 in/iq/id/ph/vn/kz 等自动适配 80 行，`lang_pack` 全是 `android`。IN 启用包 `APP_ID` 全是 **6**（合成器写死），靠运行时模板切 4，不要指望库行自己是 4。

---

## 4. 已知缺口是否仍在

| 缺口 | 2026-09-02 状态 |
|------|-----------------|
| InitConnection `lang_pack` 空 | **默认仍空。** 旗标能写成 `android`；T1 vs T2、follow-up T4 vs T0 **没有**把 FLOOD/App 变成 SMS。 |
| `tz_offset` 只打日志不进握手 | **默认仍不进。** 旗标能写；T1 写 tz、T3 不写，方向甚至与「对齐更好」相反。 |
| Push token 质量 | **仍在。** G1/选国：已 attach 仍 `API_ID_PUBLISHED_FLOOD`；G3：无 token 必 FLOOD。REGHelp ≠ Play 为 `org.telegram.messenger` 签的 FCM/Integrity。 |
| `device_secret` 未用 | **仍在。** |
| CodeSettings.token 是 iOS 位 | **仍在。** 注释已写明；去掉则公开 ID 必 FLOOD。 |
| 合成库 api_id=6 | **仍在。** 用 public/4 模板可覆盖；默认 emu+android 会走进 6 税区。 |
| 号池 App 投递 | **仍是主失败形态。** 与代理类型、握手字段无关。 |

经验文档的优先级仍然成立：号码新鲜度 > Push/Integrity 签发质量 >> 握手字段 >> 移动 vs 住宅。

---

## 5. 若值得做：移动 vs 住宅最小实验（本轮不要跑）

**值不值得：** 只作为**将来**的低优先级出口卫生对照，**不值得**为它租号。俄语圈不当它是 SMS 开关；本仓失败形态是 App（回收号）和 FLOOD（token/窗口）。握手字段 AB 已经证明「更像官方」拉不动 SMS。再测 IP 产品线，先验更弱。

**本轮：0 租号。** 先做只读（需在已白名单的生产机上，不在 Cloud Agent）：

1. `GET /proxy/list/mobile` 与 `GET /proxy/list` 的 `resident` / `{CC}_tg`，看目标国是否**同时**有移动与住宅节点。  
2. 各抽 1 条测活，记录 ASN / `org`（4G 运营商 vs 家宽 vs 云）。  
3. 若某国没有 mobile 库存：**停止**，不要为对照去 `order/make`。

**将来若仍要租号，N 要小、且必须已有同国两类节点：**

| 项 | 规定 |
|----|------|
| 国家 | 当时能 `sendCode` 的窗口国（近期 in/ph 常 App；不要 iq+6） |
| 身份栈 | 钉死：api_id=4 + hash `014b35…` + Push attach + `official_client_emulation=false` + vault 机型 + `lang_pack=android` + 号国 tz |
| 自变量 | **仅** `catalog_type=mobile` vs `resident_tg`（或自建住宅）；禁止混进 ipv4 数据中心 |
| N | **每臂 2**，合计 **4 号**；任一侧 2/2 同形态（全 App 或全 FLOOD）即停 |
| 绑定 | geo 1:1、sticky、1 号 1 IP（现有槽位池即可） |
| 成功判据 | `SentCodeTypeSms` 或真实 FLOOD 率差异；**App 不算赢** |
| 禁止 | api_id=6、Payment、假收据、为凑 N 跨区、本轮 Cloud Agent 上强行测 |

**不要做的大规模版：** 每臂 10+、多国交叉、或「没有 mobile 就先下单买一批」。那是在烧号验证一个俄语 SEO 都只当养号卫生的变量。

若只读发现账户 **根本没有** 号国 mobile：结论写成「库存不足，AB 不做」即可，与「测过无效」不同。

---

## 6. 代码锚点（只读）

| 主题 | 位置 |
|------|------|
| API 桶（含 mobile） | `backend/app/services/proxyseller.py` `PROXY_TYPE_BUCKETS` |
| 选源优先级 | 同文件 `refresh_pool` / `_source_rank` / `select_best_proxy` |
| geo 1:1 槽位 | `backend/app/services/proxy_slot_pool.py` |
| 设备合成 | `backend/app/services/device_generator.py`（`OFFICIAL_API_ID = 6`） |
| 采样与 vault 回放 | `backend/app/services/device_profile.py` `DeviceProfileManager` |
| Base.db 列 | `device_db_manager.py` `REGISTRATOR_COLUMNS` |
| InitConnection 补丁 | `backend/app/services/init_connection.py` |
| 默认旗标 | `backend/app/models/schemas.py`（lang_pack/tz 默认 False） |
| CodeSettings.token | `backend/app/services/registrar.py` `_build_code_settings` |

---

## 7. 一句话

> 代理：我们一直在用号国住宅，**没做过**移动 vs 住宅 AB，俄语农场也不把这一刀当 SMS 开关。  
> 设备：机型/SDK/语言包名/时区**能**对齐 vault；默认握手仍空 `lang_pack`、不写 tz；Push 质量与 `device_secret` **仍缺**。不要为代理产品线大规模租号。
