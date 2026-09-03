# SMSCode.gg 接码对接

官方文档：<https://smscode.gg/docs>  
本仓库走 **USD 投影的 `/v2` API**（`https://api.smscode.gg/v2`），与控制台 `sms_max_price` 美元出价一致。

## 如何启用

1. 在 **Settings → SMSCode 接码平台** 填入 API Token（控制台 Account Settings 生成）。
2. 将「当前接码提供源」选为 **SMSCode.gg**，点右上角保存。
3. 控制台发起任务时也可临时把「本次接码平台源」切到 SMSCode。
4. 可选：用「余额/连通性探针」确认鉴权（会查余额 + 国家目录，**不租号**）。

密钥只写 `data/config.json` 字段 `smscode_api_key`，或环境变量：

```bash
export SMSCODE_API_KEY="你的 token"
# 或
export SMSCODE_TOKEN="你的 token"
```

**不要把完整 Token 提交进 Git。**

配置字段：

| 字段 | 说明 |
|------|------|
| `smscode_api_key` | Bearer Token |
| `sms_provider` | 设为 `smscode`（别名：`smscode.gg` / `sms-code` / `smscodegg`） |
| `sms_max_price` | 美元出价上限，原样作为 `max_price` 传给 `/orders/create` |

## 对接的 API

| 能力 | 方法 | 路径 |
|------|------|------|
| 余额 | GET | `/v2/balance` |
| 国家目录 | GET | `/v2/catalog/countries` |
| 服务目录 | GET | `/v2/catalog/services` |
| 价格/库存 | GET | `/v2/catalog/products` |
| 租号 | POST | `/v2/orders/create`（`catalog_product_id` + 可选 `max_price`） |
| 查码 | GET | `/v2/orders/{id}`（轮询 `otp_code`） |
| 取消退款 | POST | `/v2/orders/cancel` |
| 完结 | POST | `/v2/orders/finish` |

错误映射：`UNAUTHORIZED` → Key 无效；`INSUFFICIENT_BALANCE` → 余额不足；`NO_OFFER_AVAILABLE` / 上游 `no_numbers` → 无号；`CANCEL_TOO_EARLY` → 取消过早（租号后约 120s 内不能退订）。客户端会解析剩余秒数；编排层默认**后台延迟重试 cancel**，不阻塞换号。日志里 Token 只打前后缀。
