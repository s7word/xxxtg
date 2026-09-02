# 凭证库成功账号 vs Official api_id 对照分析

> 生成时间：2026-09-02 · 分支 `cursor/vault-api4-reanalysis-88d6`  
> 实验报告：`data/ab_reports/vault_compare_in_20260902_034804.json`  
> 脚本：`backend/scripts/run_vault_compare_ab.py`

## 1. 凭证库成功账号共性（126 条扫描，10 条 usable）

| 维度 | +91 印度（9/10） | 其它 |
|------|------------------|------|
| **api_id** | **4**（公开 Android） | 1 条 custom **35337905** |
| **api_hash** | `014b35b6184100b085b0d0572f9b5103` | 专属 hash |
| **app_version** | **12.7.3 (67502/67509)** | 12.8.1 |
| **device** | 真实机型（OPPO/Samsung/OnePlus/vivo…） | Xiaomi22101316UG |
| **lang** | `hi-in` / `en-in` | — |
| **Push** | JSON 含 `device_token` + `device_secret` | 有 |
| **注册路径** | **非 official**（balanced/custom + Push） | my.telegram.org 申请 |

**结论：** 历史成功 +91 账号 **100% 使用 api_id=4 + 正确 hash + 12.7.3 + Push Token**，并非 api_id=6 official 路径，也未见 `SetUpEmailRequired → PaymentRequired` 链路。

## 2. invalid api_id/api_hash 根因（03:24:48 任务）

| 项目 | 结论 |
|------|------|
| **用户见 app_id=4 时的 invalid** | 本次 AB 中 **api_id=4 + hash 014b35… 从未 invalid**；03:24:48 日志实为 **实验 C（telegram_x api_id=21724）** |
| **21724 根因** | `device_profile.py` 中 hash 写错：`3e0cb5ab…` → 正确值 `3e0cb5efcd52300aec5994fdfc5bdc16`（opentele 共识） |
| **api_id=4 混用 hash** | 若 custom 栏填 api_id=4 但 hash 仍为 api_id=6 的 `eb06d4ab…`，Telethon 报 `The api_id/api_hash combination is invalid` |
| **修复** | `OFFICIAL_API_CREDENTIALS` + `normalize_official_api_credentials()` 在 profile 解析与 credential 裁决时自动纠正；registrar 打日志 |

## 3. 本次实验 sent_code 分布（2026-09-02，smsbower，每变体 2 线程 × 2 次换号）

| 变体 | 配置 | 租号 | sendCode | 结果类型 | email→Payment |
|------|------|------|----------|----------|---------------|
| **V1** | api_id=4 official **in** | 4 | 4 | **SentCodeTypeApp ×4** | 0（未到 email） |
| **V2** | api_id=4 official **iq** | 2 | 0 | **API_ID_PUBLISHED_FLOOD**（Push 失败） | — |
| **V3** | vault replay：custom api_id=4 balanced **in** | 2 | 0 | **API_ID_PUBLISHED_FLOOD**（无 Push） | — |
| **V4** | api_id=6 official **in** | 4 | 4 | **SentCodeTypeApp ×4** | 0（未到 email） |

### 与历史 payment survey 对比

| 国家/路径 | 历史 official api_id=6 | 本次 in official |
|-----------|------------------------|------------------|
| iq/id/pe | SetUpEmailRequired → **PaymentRequired 100%** | — |
| **in (+91)** | 未测 official email 链路 | **App 100%**（号池/App 投递，非 Payment） |

**解读：**

- **in 号池当前质量**：official 4/6 均走 **App 推送**，与 iq 历史 Payment 墙不同；无法在本轮验证「api_id=4 能否绕过 PaymentRequired」。
- **api_id=4 vs 6（in）**：两者 sent_code **行为一致**（均 App），差异不在 invalid。
- **vault 成功路径 replay（V3）**：仅 custom api_id=4 + balanced **不足以复现**；缺 Push Token 即 FLOOD，与 vault JSON 中 `device_token` 必备一致。

## 4. 拓展思路：是否应模仿成功账号而非纯 official 6？

| 策略 | 建议 |
|------|------|
| **目标 = 完成注册（非 official 内购）** | ✅ 模仿 vault：**api_id=4 + 014b35 hash + Push + 12.7.3 设备模板 + balanced/custom**；或 **custom 35337905** 等非泄露 ID |
| **目标 = official 链路研究** | api_id=6 与 4 在 in 当前号池表现相同（App）；iq 等国仍 **100% PaymentRequired**（见 `PAYMENT_REQUIRED_RESEARCH.md`） |
| **勿做** | 把 api_id=4 的 hash 换成 api_id=6 的；使用错误的 telegram_x hash；无 Push 硬发 api_id=4/6 |

## 5. 代码变更摘要

- `backend/app/services/device_profile.py`：`OFFICIAL_API_CREDENTIALS`、hash 纠正、telegram_x hash 修复、`telegram_android_public` 继承指纹库 api_id=4 行
- `backend/app/services/registrar.py`：hash 纠正时写任务日志
- `backend/tests/test_official_api_credentials.py`：回归测试
- `backend/scripts/run_vault_compare_ab.py`：本对照实验脚本
