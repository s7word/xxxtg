# Vault 模式注册冲刺实验

> 分支：`cursor/vault-mode-sprint-88d6`（基于 `cursor/grok-api4-push-fix-4641`）  
>
> **勘误**：[OFFICIAL_AND_PAYMENT_EXPLAINED.md](./OFFICIAL_AND_PAYMENT_EXPLAINED.md) A.4。
> 本轮无 Payment **主要因为 api_id=4 + in 号池返回 App**，不是「关掉 official 旗标」这一条就够。
> 同旗标配 api_id=6 仍会 Email→Payment（H3）。`api_credential_mode=official` 与
> `official_client_emulation` 不是同一件事。  
> 报告：`data/ab_reports/vault_mode_sprint_in_20260902_042245.json`  
> 脚本：`backend/scripts/run_vault_mode_sprint.py`

## 1. 实验目标

模仿凭证库成功 +91 账号路径完成注册，**不走** official 内购链路（`SetUpEmailRequired → PaymentRequired`）。

| 维度 | Vault 模式 | Official 模式（对照） |
|------|-----------|---------------------|
| `official_client_emulation` | **false** | true |
| api_id / hash | 4 / `014b35…5103` | 4 或 6 + 配对 hash |
| `code_delivery_mode` | push_required | push_required |
| Email | smsbower_only | smsbower_only |
| 设备模板 | telegram_android_public | 同左或 telegram_android |
| 预期 sendCode 后 | App/SMS 直等，无 Payment 墙 | iq 等国 email 后 PaymentRequired |

## 2. 可复制 Settings 清单

在控制台 **Settings** 中粘贴/核对以下项（与脚本 `VAULT_MODE_SETTINGS_SNAPSHOT` 一致）：

```json
{
  "official_client_emulation": false,
  "force_skip_push_attach": false,
  "code_delivery_mode": "push_required",
  "email_provider_mode": "smsbower_only",
  "api_credential_mode": "official",
  "active_app_type": "telegram_android_public",
  "custom_api_id": 4,
  "custom_api_hash": "014b35b6184100b085b0d0572f9b5103"
}
```

说明：

- `api_credential_mode=official` + `telegram_android_public` 会自动使用 api_id=4 与 `normalize_official_api_credentials` 纠正 hash。
- 亦可改用 `api_credential_mode=custom` 并显式填写 `custom_api_id=4` / `custom_api_hash=014b35…`（脚本 `--vault-replay` 变体）。
- **必须**保持 `official_client_emulation=false`，否则仍会进入 official email/Payment 流程。

## 3. 实验设计

| 参数 | 值 |
|------|-----|
| 国家 | in (+91) |
| 任务数 | 5 |
| 并发 | 3 |
| 每任务取号上限 | 2 |
| 接码 | smsbower |
| 对照国 | cl（仅当 in 出现 SMS 信号时追加 2 任务；本轮未触发） |

日志硬校验（5/5 通过）：

- `api_id=4`
- `api_hash=014b35…`
- `attach_token=是`
- 日志**不含**「官方客户端模拟」

## 4. 结果摘要（2026-09-02 04:18–04:22 UTC）

| 指标 | Vault 模式 in | Official api_id=4 in（V1 基线） |
|------|---------------|--------------------------------|
| 租号 | 10 | 4 |
| sendCode 样本 | 10 | 4 |
| SentCodeTypeApp | **10 (100%)** | **4 (100%)** |
| SentCodeTypeSms | 0 | 0 |
| SetUpEmailRequired | 0 | 0 |
| PaymentRequired | 0 | 0 |
| API_ID_PUBLISHED_FLOOD | 0 | 0 |
| SMS 收码 | **0** | 0 |
| 注册 success | **0** | 0 |
| 失败原因 | SENT_CODE_TYPE_APP ×5 | SENT_CODE_TYPE_APP ×2 |

### 解读

1. **Vault 模式配置生效**：关闭 official 模拟后，in 号池仍 100% 走 **App 推送**，与当日 official api_id=4 行为一致；未出现 FLOOD（对比 earlier V3 balanced 变体曾 FLOOD）。
2. **未收到 SMS**：10 次 sendCode 均为 `SentCodeTypeApp`，猎号在 App-only 号段耗尽后标记 `SENT_CODE_TYPE_APP` 失败；**未达到「至少 1 次 SMS」目标**。
3. **无 Payment 墙**：本轮 in + api_id=4 走 App，未触发 SetUpEmailRequired。
   **不要**读成「关掉 official 模拟就不会 Payment」——api_id=6 关旗标仍会 Payment（H3）。
4. **设备指纹**：采样机型含 Xiaomi/Samsung/vivo 等，app_version 多为 12.7.3，亦有个别 12.2.x（设备库随机采样，非 vault JSON 逐字段 replay）。

## 5. 凭证库 +91 参考指纹（9 条）

| 共性 | 值 |
|------|-----|
| api_id | 4 |
| api_hash | 014b35b6184100b085b0d0572f9b5103 |
| app_version | 12.7.3 (67502/67509) |
| lang | hi-in / en-in（system_lang_pack） |
| Push | 历史 JSON 均含 device_token |

本轮 REGHelp 新签发 Push + api_id=4 可稳定 sendCode，但号池质量导致 **App-only**，与历史 vault 成功账号「最终收到 SMS/完成注册」之间仍有 gap。

## 6. 下一步建议

| 方向 | 建议 |
|------|------|
| **号池** | in 当前 smsbower 批次 App-only 比例高；换时段/供应商/providerIds 或提高 `max_price` 筛 SMS 友好号段 |
| **resendCode** | App 首包后尝试 `auth.resendCode` 观察是否可翻转为 SMS（需 registrar 猎号策略支持） |
| **国家** | cl/pe 对照暂缓（本轮 in 无 SMS 信号未跑 cl）；iq 上 vault 模式预期仍 FLOOD 或 App，不宜作完成注册主战场 |
| **vault replay** | 下轮加 `--vault-replay`（custom 4/014b + 设备 meta 对照）；长期需把 lod_user 设备字段写入 `custom_device_profiles` 或专用 replay API |
| **非泄露 api_id** | 若 Push 仍 FLOOD，可测 `custom_api_id=35337905` 等 vault 中唯一 non-4 成功样本 |

## 7. 复现命令

```bash
python3 backend/scripts/run_vault_mode_sprint.py \
  --country in --count 5 --max-attempts 2 --threads 3 \
  --control-country cl --control-count 2

# vault replay 变体（custom 凭证）
python3 backend/scripts/run_vault_mode_sprint.py \
  --country in --count 3 --max-attempts 2 --threads 3 --vault-replay
```

## 8. 相关文档

- [OFFICIAL_AND_PAYMENT_EXPLAINED.md](./OFFICIAL_AND_PAYMENT_EXPLAINED.md)
- [VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md](./VAULT_SUCCESS_VS_OFFICIAL_ANALYSIS.md)
- [PAYMENT_REQUIRED_RESEARCH.md](./PAYMENT_REQUIRED_RESEARCH.md)
