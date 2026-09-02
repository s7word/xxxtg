# 严格设备对齐与 Push / 握手补齐

> 时间：2026-09-02  
> 对照：[Telegram Expert](https://ru.telegramexpert.pro/manuals/generator-parametrov) 俄语农场手册 + vault 成功 `lod_user` +91 JSON（`api_id=4`、`lang_pack=android`、`app_version=12.7.3`、号国 tz）。  
> **不要把密钥 / `device_secret` / Push token 原文写入 Git。**

## Expert 对照（我们补上了什么）

| Expert / vault | 旧行为（大体对齐） | 本轮（严格对齐） |
|---|---|---|
| `api_id=4` + hash `014b35…5103` | `telegram_android` 模板默认 6，指纹包抽样可漂 | 严格模式钉死 4，禁止漂到 6（避免 Payment） |
| 机型 / SDK / `app_version` | 指纹包随机，常 12.9.x | 回放 vault 成功机型，钉 `12.7.3` |
| `lang_pack=android` | Telethon InitConnection 空串；事后 `getLanguages` 改不了握手 | `connect()` **前**写入 `InitConnection.lang_pack` |
| 号国 `tz_offset` / lang | 只打日志；自动适配包可能掺 `en-us` | 写入 `InitConnection.params.tz_offset`；号国 overlay；自动合成包不再保留掺入的 en-us |
| 一号一代理 | `hunt_proxy_max_uses` 默认 5 | 严格模式强制 1 |
| Push / SafetyNet | REGHelp FCM 塞进 **文档标为 iOS** 的 `CodeSettings.token` | 仅在计划需要时 attach；校验 token 形态；日志标明 `push_slot=CodeSettings.token(android_fcm_in_ios_doc_slot)`；不合格 / FLOOD 冷却换发 |
| `SentCodeTypeApp` 且无 `next_type` | 有 timeout 时可能空等再 resend | **快丢号**（`app_delivery_fast_drop`，默认开） |
| `device_secret` | JSON 有、代码不消费 | 扫描元数据进 profile；可选 sidecar；默认 **不**注入 sendCode（见下） |

## 如何开启

Settings →「严格设备对齐（vault 成功样本 + Telegram Expert）」勾选后点「持久化全局配置」。

等价配置：

```json
{
  "device_alignment_mode": "strict",
  "strict_vault_device_alignment": true,
  "pin_app_version_substr": "12.7.3",
  "app_delivery_fast_drop": true,
  "flood_rotate_push_token": true,
  "official_client_emulation": false
}
```

关闭：把开关取消（`device_alignment_mode=loose`）。loose 下若实际 `api_id=4`，握手仍会写 `lang_pack=android` 与号国 tz（补生产缺口），但**不会**因缺字段拒绝发码，也不会钉死 4。

严格模式缺字段 / 模拟器机型 → 日志 `严格设备对齐拒绝发码`，**不租号、不发码**。

## Push 槽位说明

官方文档：`CodeSettings.token` / `app_sandbox` **仅官方 iOS Firebase**。本仓库仍把 REGHelp **Android FCM** 放进该 MTProto 字段以过 published-id 闸（历史对照：无 token 裸发 4/6 必 FLOOD）。

这**不是**在跑 iOS 客户端：指纹仍是 Android（`lang_pack=android`、api_id=4）。`iOS` 只出现在「官方文档给这个字段的语义」里；旧日志里的 `iOS-semantic` 容易误导，已改为：

- `push_slot=CodeSettings.token(android_fcm_in_ios_doc_slot) token_kind=fcm_legacy ...`
- `InitConnection 指纹: lang_pack=android tz_offset=25200`（vn）/ `28800`（ph）

## FLOOD 门闩与并发探测

`API_ID_PUBLISHED_FLOOD` 会拉起**进程级**冷却窗（默认约 120s）：后续任务在租号/sendCode 前看到门闩会**跳过发码**，避免同 published `api_id` 继续填窗烧钱。

要点：

- **不会**把已开跑的其它 asyncio 任务 cancel 掉；看起来像「全部停止」，其实是还没过门闩的兄弟任务被跳过。
- 日志会写「同 published api_id 冷却中…跳过租号/发码」，不再写吓人的「停止本任务以免继续填满窗口」。
- 默认要拦新发码（省钱）。若你要 **api_id=4 × 10 并发探测**，在 Settings 打开其一：
  - `ignore_published_flood_window=true`，或
  - `flood_window_scope=task`
- 放宽后日志会警告：同窗续发通常仍 FLOOD，会烧号/烧钱。

```json
{
  "flood_window_scope": "process",
  "flood_block_new_sends": true,
  "ignore_published_flood_window": false,
  "published_flood_hold_seconds": 120
}
```

## `device_secret` 为何默认不用

vault JSON 的 `device_secret` 是历史成功样本上的 attestation 块，绑定**当时**的 Play Integrity / SafetyNet nonce。

- `auth.sendCode` 的 `CodeSettings` **没有** `device_secret` 字段。
- 官方 Android 在收到 `SentCodeTypeFirebaseSms` 后才用**当次** nonce 调 Play Integrity，再 `auth.requestFirebaseSms`。
- 把旧 secret 塞进新 nonce 几乎必然失败。

本轮打通：

1. 扫描 `lod_user`：profile 带 `has_device_secret` / `device_secret_len`（不写原文到日志）。
2. `vault_attestation_persist_secrets`：原文落到 `data/vault_attestation.json`（gitignore）。
3. `inject_vault_device_secret`：仅 FirebaseSms 路径尝试注入，**默认关**。

## 不要做什么

- 不要在严格模式下打开「官方客户端模拟」指望走 api_id=6 Payment。严格模式会把凭证钉回 4。
- 不要大规模烧号验证。握手是否写入看任务日志里的 `InitConnection 指纹` 即可。
- 不要 commit `data/config.json` 密钥、`lod_user` 的 token/secret、`data/vault_attestation.json`。
