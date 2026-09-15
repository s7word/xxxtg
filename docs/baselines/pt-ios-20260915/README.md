# 冻结基线：葡萄牙官方 iOS 10/10

> **不要改这个目录里的文件。** 它们是 2026-09-15 成功批次的只读存档。  
> 以后协议被改坏时，用 Git 标签 `baseline/pt-ios-10-20260915` 对照，不要凭记忆重写。

| 项 | 值 |
|----|----|
| 批次 | `1e213443` |
| 时间 | 2026-09-15 10:44:40Z，约 255s |
| 国家 / 客户端 | `pt` / `telegram_ios` |
| 结果 | **10/10 success**，首次通道 10 SMS，1 路短信窗空后 resend 成 Call 仍成功 |
| 墙 | 无邮箱、无付款 |
| 代理 | `PT_tg` 口 10000–10009 各一条 |
| DC | GetNearestDc → **DC4**（从 Telethon 默认 DC2 切过去） |

## 文件

| 文件 | 内容 |
|------|------|
| `runner.log` | 跑批控制台输出（无密钥） |
| `batch_report.json` | `run_ph_smscode_warp.py` 汇总，号码已掩码 |
| `task_audit.sanitized.json` | 10 路关键日志摘录（号码 / token / hash / UID 已打码） |
| `MANIFEST.sha256` | 上面三个文件的校验和 |

完整协议合同见仓库根下 [`docs/IOS_PT_BASELINE.md`](../../IOS_PT_BASELINE.md)。
