# api_id=4 细节对照实验结果（T0–T5）

> 时间：2026-09-02 11:25–11:40 UTC  
> 分支：`cursor/api4-detail-ab-88d6`  
> 脚本：`backend/scripts/run_api4_detail_ab.py`  
> 原始 JSON：`data/ab_reports/api4_detail_ab_20260902_112546.json`、`data/ab_reports/api4_detail_ab_20260902_113727.json`  
> 合并摘要：`data/ab_reports/api4_detail_ab_combined_20260902.json`  
> 调研前提：[RU_DOLLAR_WALL_RESEARCH.md](./RU_DOLLAR_WALL_RESEARCH.md)、[API_ID_4_TGX_TELEGRAM9_RESEARCH.md](./API_ID_4_TGX_TELEGRAM9_RESEARCH.md)

**先读结论：** 俄语调研后的新细节（vault 指纹对齐、`InitConnection.lang_pack=android`、号国 `tz_offset`）**已经能写进握手**，但相对旧 public 默认路径 **没有带来 SMS / 注册成功**。in 仍是 App 或 FLOOD；iq 本轮 **未再出现 API_ID_PUBLISHED_FLOOD**（变成 App），这是唯一可能的正向信号，样本小且对照不干净。

未走 api_id=6，未做假收据 / Payment resend。

---

## 用户摘要

### 新思路有没有进步？

| ID | 配置要点 | 租号 | sendCode | sent_code | FLOOD 任务 | SMS 收码 | success |
|----|----------|------|----------|-----------|------------|----------|---------|
| **T0** | 当前 public 默认：不钉 12.7.3、不回放 vault、握手 `lang_pack` 空、不写 tz | 2 in | 1 | App×1 | **1** | 0 | 0 |
| **T1** | vault 机型 + 号国 locale + `lang_pack=android` + tz=19800 | 2 in | 0 | — | **2** | 0 | 0 |
| **T2** | 相对 T1：`lang_pack` 空，仍写 tz=19800 | 2 in | 0 | — | **2** | 0 | 0 |
| **T3** | 相对 T1：写 `lang_pack=android`，**不写** tz（首波 2 次 noNumber，补测 2 号） | 2 in | 1 | App×1 | 0（另 1 次 Push 申请失败 fail-fast） | 0 | 0 |
| **T4** | 与 T1 同栈，**iq**（ar-iq / tz=10800） | 3 iq | 2 | **App×2** | **0** | 0 | 0 |
| **T5a** | T1 栈，每号新设备+代理 | 2 in | 1 | App×1 | 0（1 号 BANNED） | 0 | 0 |
| **T5b** | T1 栈，1 任务复用设备/代理 2 次 | 2 in | 2 | App×2 | 0 | 0 | 0 |

合计租号 **15 / 16**。SMS **0**。注册成功 **0**。PaymentRequired **0**（全程 api_id=4，未进官方 6）。

相对旧路径（vault-mode sprint in 10/10 App、G1 iq 2/2 FLOOD）：

- **in：没有进步。** T1 甚至比同场 T0 更差（2/2 FLOOD，0 次 sendCode）。稍后 T5 用**同一 T1 栈**又打出 App——说明 **时段 / Token / 出口窗口 > 指纹字段**。
- **iq：可能有 FLOOD→App 的改善**（本轮 0 FLOOD / 2 App），相对 G1 的 2/2 FLOOD。但 G1 当时 `official_client_emulation=true`，本轮是 `false` + lang_pack/tz，**不是单变量**。结果仍是 App，不是 SMS。

### 哪个细节最关键（若有）

**没有出现能单独拉动 SMS/成功的细节。**

工程上已否证「Telethon 写不了 `lang_pack`/`tz_offset`」：connect 前改 `_init_request` 有效，日志已核对。  
策略上，这两个字段 **不足以** 改变 in 的 App-only，也没有在对照里压过 FLOOD 窗口噪声。

若把「iq 不再 FLOOD」算信号，它更像 **国家窗口 + 非 emu 的 4 路径**，而不是单独的 lang_pack 或 tz。T2（空 lang_pack）与 T1（android）同场都 FLOOD；T3（不写 tz）补测打出 App，T1（写 tz）同场 FLOOD——方向与「对齐时区更好」相反。

### 是否需要充值

**不需要。** smsbower 余额实验前后均为 **23.611 USD**（取消未计费）。in 参考价曾在 0.45–1.51 间跳动，iq 0.36–0.79。5SIM / Grizzly 未动。

### 下一步只保留哪 1–2 条

1. **T4 复查（小样本）：** 同 T1 栈再跑 2–4 个 **iq**，并加 2 个 iq **T0**（不写 lang_pack/tz）看 FLOOD→App 是否可重复。仍禁止 api_id=6 Payment。  
2. **in 号池 SMS 窗口：** 指纹侧停烧；换时段 / 供应商 / 价位，目标仍是 `SentCodeTypeSms`。历史 vault +91 成功发生在能收 SMS 的批次，不是握手字段。

**放弃：** T2 单独测 lang_pack、T3 单独测 tz、指望 T1 vault 机型回放在 in 上打出 SMS。

---

## 1. 实验设计

全程强制：`api_id=4` + hash `014b35…` + `official_client_emulation=false` + `push_required` attach + `telegram_android_public`。禁止 api_id=6。

| 变体 | 自变量 |
|------|--------|
| T0 | 不钉 app_version、不回放 vault、握手保持 Telethon 默认（空 lang_pack、无 params） |
| T1 | `vault_fingerprint_replay` + `force_country_locale` + `pin 12.7.3` + lang_pack=android + tz=号国 |
| T2 | T1 但 `init_connection_set_lang_pack=false` |
| T3 | T1 但 `init_connection_set_tz_offset=false` |
| T4 | T1 栈，国家 iq |
| T5 | 同 T1；新鲜设备/代理 vs 复用 |

租号硬上限 16。T3 首波 in 平台 `noNumber`（0 租号），用剩余额度补测 T3+T4。

---

## 2. 工程：lang_pack / tz 不再阻塞

Telethon 1.44 构造时写死 `lang_pack=''`（注释「official apps only」），`params` 默认空。

本轮最小 diff：

- `backend/app/services/init_connection.py`：connect **之前** 写 `_init_request.lang_pack` 与 `params.tz_offset`
- 配置旗标：`init_connection_set_lang_pack` / `init_connection_set_tz_offset` / `force_country_locale` / `vault_fingerprint_replay`
- vault 回放只覆盖机型 / SDK / `app_version`，**不复制** `device_token` / `device_secret`

日志核对（节选）：

| 变体 | InitConnection 日志 |
|------|---------------------|
| T0 | `lang_pack=(empty) tz_offset=未写入` |
| T1 / T5 | `lang_pack=android tz_offset=19800`，并有 `vault 指纹回放: autoc_sessions_…json` |
| T2 | `lang_pack=(empty) tz_offset=19800` |
| T3 补测 | `lang_pack=android tz_offset=未写入` |
| T4 | `lang_pack=android tz_offset=10800`，`网络语言拓扑: ar-iq` |

单测：`backend/tests/test_init_connection_fingerprint.py`。

---

## 3. 与历史基线对照

| 来源 | 配置 | 国家 | 结果 |
|------|------|------|------|
| vault-mode sprint | api_id=4 emu=false push | in | 10/10 App，0 SMS，0 FLOOD |
| G1 grok_api4_retest | api_id=4 official emu **true** attach | iq | **2/2 FLOOD**（已 attach） |
| 本轮 T0 | public 默认 | in | 1 App + 1 FLOOD |
| 本轮 T1 栈 in | vault+lang_pack+tz | in | 先 2/2 FLOOD，后 T5 3 App |
| 本轮 T4 | T1 栈 emu=false | iq | **2 App，0 FLOOD**（+1 Recaptcha 失败、1 noNumber） |

解读：in 的 App-only 号池没有被指纹对齐打破。iq 的 FLOOD 可能随窗口下降，但 G1 对照差了 `official_client_emulation` 和握手字段两层，不能把功劳记在 lang_pack 上。

T1 与 T5 同栈不同结局，再次说明 **不要把单场 FLOOD/App 当成字段结论**。

---

## 4. 杂讯与失败形态

- T3 第一波：smsbower in `noNumber`（库存接口仍显示数十万；价位随后跳到 1.51，可能是出价窗口）。补测成功租号。
- T3 补测 1 号：Push 申请失败，registrar **拒绝裸发**（日志里出现 FLOOD 字样是警告文案，**不是** Telegram 回包）。任务级 FLOOD 计为 0。
- T4 补测 1 号：sendCode 前 `RECAPTCHA_CHECK`，REGHelp RecaptchaMobile 网关不可达。
- T5a 1 号：`PHONE_NUMBER_BANNED`。
- 余额未扣：取消/失败号未计费。

握手层无阻塞。Push / Recaptcha 网关与号池质量仍是主失败因。

---

## 5. 复现

```bash
python3 backend/scripts/run_api4_detail_ab.py --lease-cap 16 --min-smsbower 4
# 仅查余额
python3 backend/scripts/run_api4_detail_ab.py --check-only
```

Docker 后端无 `--reload`，改代码后需 `docker restart edgenode-backend`。脚本结束会把 `config.json` 恢复为启动快照（新旗标默认 false）。
