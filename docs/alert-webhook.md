# 告警 Webhook（程序推送）接收说明

SMSBazaar 在检测到 **Telegram 服务**补货 / 新上架后，除了发 Telegram Bot，还可以把**简化后的 JSON** `POST` 到你的程序。

配置入口：登录后台 → **设置 → 程序推送**。

## 推荐方式：HTTP Webhook

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| Content-Type | `application/json; charset=utf-8` |
| Schema | `smsall.alert.v1`（请求头 `X-Smsall-Schema` 同步声明） |
| 超时 | 默认 8s（前端可改 1–30s） |
| 重试 | 当前版本不重试；接收方应尽快返回 2xx |

### 鉴权（可选）

若在前端填写了 **Secret**：

1. 请求头 `Authorization: Bearer <secret>`
2. 请求头 `X-Smsall-Signature: sha256=<hmac_hex>`  
   其中 `hmac_hex = HMAC_SHA256(secret, raw_body_bytes)`

接收方应校验其一或两者都校验。

### 请求体示例

```json
{
  "schema": "smsall.alert.v1",
  "sentAt": "2026-08-28T19:20:00.000Z",
  "serviceKey": "telegram",
  "serviceLabel": "Telegram 接码",
  "itemCount": 2,
  "items": [
    {
      "type": "restock",
      "country": "IN",
      "countryName": "India",
      "priceUsd": 0.12,
      "currency": "USD",
      "stockFrom": 0,
      "stockTo": 18,
      "provider": "SMSTG",
      "providerCode": "P24",
      "balance": 12.5,
      "balanceCurrency": "USD",
      "portalUrl": "https://smstg.org"
    },
    {
      "type": "new_listing",
      "country": "PH",
      "countryName": "Philippines",
      "priceUsd": 0.28,
      "currency": "USD",
      "stockFrom": 0,
      "stockTo": 40,
      "provider": "Hero SMS",
      "providerCode": "P01",
      "balance": 3.1,
      "balanceCurrency": "USD",
      "portalUrl": "https://hero-sms.com"
    }
  ]
}
```

`items` **已按单价从低到高排序**。

### 字段说明

| 字段 | 含义 |
|---|---|
| `type` | `restock` 补货 / `new_listing` 新上架 |
| `country` | ISO2 |
| `priceUsd` | 最低价（USD） |
| `stockFrom` / `stockTo` | 库存变化 |
| `provider` | 平台展示名 |
| `providerCode` | 内部编号（P01…），便于日志 |
| `balance` | 平台账户余额数字；未知则为 `null` |
| `portalUrl` | 平台入口链接 |

### 期望响应

- `2xx`：视为成功  
- 其他状态码：记入服务日志，不阻断 Telegram 推送  

响应体可为空。

## 狙击（Sniper）通道

「设置 → 程序推送 → **狙击**」推来的告警走独立通道：xxxtg 收到后**不看**「通知后自动注册」开关，
直接按猎号参数开跑（默认 10 路 × 每任务最多取号 20 次，成功即停，失败号拉黑换号继续扫）。

判定为狙击的条件（**任一命中即可**）：

| 位置 | 取值 |
|---|---|
| 请求体 | `payload.source == "sniper"`（`priority` / `channel` / `mode` / `kind` 同样识别，`tags` 含 `sniper` 亦可） |
| 请求头 | `X-Smsall-Sniper: 1`（`true` / `yes` / `on` 同样算）或 `X-Smsall-Priority: sniper` |
| 单条 item | `sniper: true`、`tags` 含 `"sniper"`、`priority: "sniper"` |

同一次 POST 里 sniper 与普通条目**分别决策**：普通条目仍走 `smsall_auto_*` 规则，
sniper 条目走 `smsall_sniper_*` 规则，两者冷却互相独立（sniper 冷却键带 `sniper:` 前缀）。

狙击条目的 `type` 不限于 `restock` / `new_listing`，认不出的取值也不会被丢弃。

xxxtg 侧配置（设置页「程序推送 → 狙击」子面板）：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `smsall_sniper_enabled` | `true` | 关掉即完全不自动开跑 |
| `smsall_sniper_count` | `10` | 任务数 / 线程 |
| `smsall_sniper_concurrency` | `10` | 并发，实际取 `min(并发, 任务数)` |
| `smsall_sniper_max_number_attempts` | `20` | 每任务最多取号次数（猎号深度） |
| `smsall_sniper_cooldown_seconds` | `60` | 同国冷却；`0` = 不冷却 |
| `smsall_sniper_max_countries` | `3` | 单次推送最多开几个国家（按单价从低到高） |
| `smsall_sniper_max_price_usd` | `null` | 全局单价硬顶；按国列表为空时生效 |
| `smsall_sniper_price_caps` | `[]` | 按国单价上限列表，如 `[{"country":"IQ","max_price_usd":1.55}]`；**非空时仅列表内国家可开跑** |
| `smsall_sniper_use_item_price_as_max` | `true` | 用 `priceUsd` 上浮 10% 作为本批出价，否则用全局 `sms_max_price` |

狙击条目新增字段（2026-08 上游格式）：

| 字段 | 含义 |
|---|---|
| `providerRef` | 上游供应商标识，与 `supplierIds` 互补 |
| `supplierIds` | 供应商 ID 列表；开跑后会作为 SMS Bower `providerIds` 精确取号 |
| `priceUsd` | 单价；须 ≤ 该国在 `smsall_sniper_price_caps` 中的上限（或全局硬顶） |

请求体可以是标准 `{ schema, items: [...] }`，也可以直接 POST **items 数组**（纯狙击推送常见）。

`10 × 20 = 200` 恰好等于默认猎号联合上限 `hunt_max_total_leases`。调大任一参数会触发裁剪，
裁剪结果会原样写进后端日志（`SmsallHooks: XX 批次租号预算：…`）并回在 Webhook 响应的
`launches[].max_number_attempts` / `planned_leases` 里。

接码源：狙击批次会按上游平台 / `supplierIds` **自动选择**——
有 `supplierIds` 或上游是 SMS Bower → 本批用 `smsbower`；上游 Grizzly → `grizzlysms`；
否则才回落全局 `sms_provider`。普通自动开跑仍始终用全局接码源。

## 前端可配过滤（简化推送）

在「程序推送」页可设置：

1. **最高单价（USD）**：只推 `priceUsd ≤ 阈值` 的条目（适合「只要低价」）
2. **仅有余额平台**：余额未知或 `≤ 0` 的丢弃
3. **最低余额**：例如只推余额 ≥ 1 的平台
4. **事件类型**：新上架 / 补货 可分别开关
5. **平台白名单**：不选=全部；勾选后只推这些平台
6. **单次最多条数**：默认 50

过滤只影响 Webhook，**不影响** Telegram 原文推送。

## 最小接收示例（Node）

```js
const express = require('express');
const crypto = require('crypto');
const app = express();

app.post('/hooks/smsall', express.raw({ type: 'application/json' }), (req, res) => {
  const secret = process.env.SMSALL_HOOK_SECRET || '';
  const raw = req.body; // Buffer
  if (secret) {
    const expect = `sha256=${crypto.createHmac('sha256', secret).update(raw).digest('hex')}`;
    if (req.get('x-smsall-signature') !== expect) {
      res.status(401).send('bad signature');
      return;
    }
  }
  const payload = JSON.parse(raw.toString('utf8'));
  for (const item of payload.items || []) {
    console.log(item.country, item.priceUsd, item.provider, item.type);
  }
  res.status(204).end();
});

app.listen(9090);
```

前端 Webhook URL 填：`http://<你的程序主机>:9090/hooks/smsall`

xxxtg 已接在本机：

- URL：`http://187.127.218.157:3100/hooks/smsall`（或直连后端 `:8000/hooks/smsall`）
- Secret：设置页「SMSBazaar 通知 → 半自动注册」；也可环境变量 `SMSALL_HOOK_SECRET`
- 默认**半自动**：校验通过后立刻 2xx 并记入通知列表，**不自动开跑**
- 设置页可对某国指定任务数 / 线程数后「一键测试注册」
- 「通知后自动注册」确认流程后再开；开了才会按单价阈值自动 `create_batch`

## 测试

设置页点 **发送测试**，会推一条样例 `IN / $0.12 / restock`。也可用：

```bash
curl -s -X POST http://127.0.0.1:8787/api/settings/webhook/test \
  -H "Authorization: Bearer <登录token>" \
  -H "Content-Type: application/json" \
  -d '{}'
```
