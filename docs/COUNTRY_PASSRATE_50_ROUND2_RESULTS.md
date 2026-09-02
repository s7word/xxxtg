# 国家通过率实验第二轮（目标 45–55，实际租号 51）

> 时间：2026-09-02 **13:16–15:47 UTC**  
> 分支：`cursor/country-passrate-50-r2-88d6`  
> 调研：[RU_COUNTRY_PASSRATE_RESEARCH.md](./RU_COUNTRY_PASSRATE_RESEARCH.md) §5  
> 脚本：`backend/scripts/run_country_passrate_50_r2.py`  
> JSON：`data/ab_reports/country_passrate_50_r2_20260902_131603.json`（主跑）、`…_142050.json`（等 35 分钟后补探针）、`…_combined_20260902.json`  
> 第一轮：[COUNTRY_PASSRATE_50_RESULTS.md](./COUNTRY_PASSRATE_50_RESULTS.md)

**先读结论：** 相对 R1，**有窗口持续时间上的进步，没有 SMS / 注册成功。** 主栈仍是 `api_id=4` + hash + Push + `official_emulation=false`。探针波 ph/vn/id 再次打出 **App×12、SMS×0**；因为填满块 ≤4 且间隔约 90 秒，**越南填满波仍拿到 4 次 App**（R1 填满波是 0 App）。窗口在约 13:36Z 翻成 FLOOD 后，**等 35 分钟、再等 35 分钟补探针，0 App / 0 SMS**。kz 仍租不到；in/iq 对照 getNumber 全 noNumber。全程 **0 Payment**。失败号 cancel，两家接码余额未扣。

---

## 用户摘要

### 相对 R1 有没有进步？

| 指标 | R1（12:32–12:57Z） | R2（13:16–15:47Z） | 判断 |
|------|---------------------|---------------------|------|
| 租号 | 44 | **51** | 样本量达到 45–55 |
| SentCodeTypeApp | 10 | **12** | 略多 |
| SentCodeTypeSms | **0** | **0** | 无进步 |
| 收码 / 注册 success | 0 / 0 | **0 / 0** | 无进步 |
| 真实 FLOOD 任务 | 22 | **26** | 补探针窗口全 FLOOD，分子变大 |
| Payment | 0 | **0** | 继续没碰 api_id=6 |
| 填满波还有 App？ | 否（探针后 10 分钟全 FLOOD） | **是（vn 填满仍 App×4）** | 节奏有效，不是 SMS |
| 等 35–70 分钟窗口会不会回来？ | （R1 建议数小时） | **不会**（14:20 与 15:02 探针 0 App） | 35 分钟不够 |

解读：换国 + 拉开填满间隔，只能把 **App 窗口从 ~6 分钟拉到 ~20 分钟**，不能把 `SentCodeTypeApp` 变成 SMS。35 分钟冷却也打不开第二扇窗。

### 国别表（租到的号）

| 国 | 租号 | 供应商 | sendCode | App | SMS | FLOOD | success | 备注 |
|----|------|--------|----------|-----|-----|-------|---------|------|
| **vn** | **18** | smsbower | 7 | **7** | 0 | 9 | 0 | 信号最强；填满波仍有 App |
| **ph** | **17** | smsbower | 2 | **2** | 0 | 11 | 0 | 探针 App，填满与补探针 FLOOD |
| **id** | **16** | smsbower | 3 | **3** | 0 | 6 | 0 | 探针全是 **Indosat 0857/0858** |
| **kz** | **0** | Grizzly | 0 | 0 | 0 | 0 | 0 | 库存接口有量，getNumber 仍 noNumber |
| **in** 对照 | **0** | smsbower | 0 | 0 | 0 | 0 | 0 | 出价 0.57 全 noNumber |
| **iq** 对照 | **0** | smsbower | 0 | 0 | 0 | 0 | 0 | 出价 0.40 全 noNumber（未走 api_id=6） |

优选租号 **51/51 = 100%**（≥70%）；对照 **0%**（≤15%，不是故意不加对照，是租不到）。

### 号段前缀（能拿到的都写了）

掩码号头 + 编号计划猜运营商。App 样本：

| 国 | 前缀 | 猜运营商 | 本轮 App |
|----|------|----------|----------|
| ph | 0955 | Globe/TM | 2 |
| vn | 052 | Vietnamobile | 1 |
| vn | 090、076、077 | Mobifone | 3 |
| vn | 037、097、098 | Viettel | 3 |
| id | 0858 | Indosat | 2 |
| id | 0857 | Indosat | 1 |

没有「某前缀出 SMS」的信号——所有 12 次 sendCode 都是 App。0997/0945 按菲律宾表是 Globe，0949/0960/0961 偏 Smart，只出现在 FLOOD/无 sendCode 波，**不能**写成这些前缀更差。

### 要不要充值？

**不必。** smsbower **23.611→23.611** USD，Grizzly **29.8** 未动（kz 从未租成）。失败号 cancel。不是余额不够跑满 50。

### 唯一下一步

**停租。** 35–70 分钟冷却已被本轮证伪。下一动作只保留：**隔数小时后只用 4 个越南号探针**；若再现 App，再加到 8 号盯 `SentCodeTypeSms`；若仍 FLOOD，当天不再租。禁止 api_id=6 / Payment / 为 kz 充值 / 再砸 in。

---

## 1. 设计（相对 R1 改了什么）

固定主栈：`api_id=4` + hash `014b35…5103` + Push attach + `official_client_emulation=false` + vault 12.7.3 + `lang_pack=android` + 号国 tz。

| R1 | R2 |
|----|----|
| 探针后立刻填满 6 号块 | 只加码有 App/SMS 的国；填满块 ≤4，间隔 ~90s |
| 开局连射到 FLOOD | 翻 FLOOD 后等 35 分钟再探针（本轮做了两次） |
| 对照 in 4 号（全 FLOOD） | in 无号则 iq；两家都 noNumber |
| 无前缀 | 记录前缀 / 猜运营商 / UTC 波次 |
| pk 倒预算 | 禁止 pk |

App 样本握手：`api_id=4`、`attach_token=是`、`lang_pack=android`、ph tz=28800、`official_client_emulation=false`、机型 `vivoV2111` / App 12.7.3。失败原因与历史相同：`SentCodeTypeApp` 且 `resendCode` 不可用。

---

## 2. 波次时间线（UTC）

### 主跑（窗口开着）

| 时间 | 波次 | 租号 | sendCode | App | FLOOD |
|------|------|------|----------|-----|-------|
| 13:16 | ph 探针 | 4 | 2 | **2** | 0 |
| 13:19 | vn 探针 | 4 | 3 | **3** | 0 |
| 13:23 | id 探针 | 4 | 3 | **3** | 0 |
| 13:26 | kz 探针 | 0 | 0 | 0 | 0 |
| 13:27 | vn 填满 | 4 | 3 | **3** | 1 |
| 13:31 | vn 填满 | 4 | 1 | **1** | 2 |
| 13:35 | vn 尾 | 1 | 0 | 0 | 1 |
| 13:38 | id 填满 | 4 | 0 | 0 | **4** |
| 13:41 | ph 填满 | 4 | 0 | 0 | **3** |
| 13:44 | in / iq 对照 | 0 | 0 | 0 | 0 |

### 冷却后再探针（窗口关着）

| 时间 | 波次 | 租号 | App | FLOOD |
|------|------|------|-----|-------|
| 14:20 | +35 min，ph/vn/id | 9 | **0** | 4 |
| 15:02 | 再 +35 min，ph/vn/id | 12 | **0** | 10 |
| 15:46 | cap 尾 1×ph | 1 | **0** | 1 |

合计租号 **29+22=51**。

---

## 3. 计费与杂讯

- smsbower 51 个租号取消未扣费。  
- Grizzly kz 从未租成。第二段结束时 Grizzly 余额接口曾读空，以主跑后的 29.8 为准；无 getNumber 成功则不应扣费。  
- `no_sendcode_reason=PROXY_UNAVAILABLE` 会误伤真实 FLOOD；**FLOOD 以 `actual_flood_tasks` / 原文 `API_ID_PUBLISHED_FLOOD` 为准**。  
- 部分任务是 Recaptcha / pre-audit / Push 空凭证，未进 sendCode，不计入 App 分母。

---

## 4. 复现

```bash
python3 backend/scripts/run_country_passrate_50_r2.py --check-only
python3 backend/scripts/run_country_passrate_50_r2.py --lease-cap 50
# 本轮已经跑过主跑 + 两次 35 分钟冷却补探针，勿无故再烧
```

Docker 后端无 `--reload`；脚本结束恢复 `config.json` 快照。
