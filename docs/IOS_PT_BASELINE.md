# 官方 iOS 黄金基线（葡萄牙 2026-09-15）

> 给后续 AI / 人类：这是本仓**第一份 10/10 注册成功**的官方 iOS 对照。  
> **先读本文再改 iOS / locale / CodeSettings / InitConnection / REGHelp / 代理。**  
> 改坏了用 Git 标签 `baseline/pt-ios-10-20260915` 回退，不要凭印象重写。

冻结日志：[`docs/baselines/pt-ios-20260915/`](./baselines/pt-ios-20260915/README.md)

---

## 1. 为什么说这次测是完整对照（不是「看起来成功」）

批次 `1e213443`（2026-09-15 10:44:40Z）：

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 注册 | **10/10 success** | 约 255s，不是连上就停 |
| 语言 / 时区 / 出口 | 10/10 `aligned=是` | `lang=pt` `system_lang=pt-PT` `tz=0` |
| 客户端 | api_id=**8** `lang_pack=ios` | REGHelp `appName=tgiOS` `appDevice=iOS`，无 AID |
| InitConnection.params | `[tz_offset, bundleId]` | `bundleId=ph.telegra.Telegraph` |
| Push | 10/10 attach APNS | `app_sandbox=False`（生产证书，不是进程沙盒） |
| 发码 | 10 路首次 **SMS** | 1 路短信窗空后 `resendCode` → Call，仍成功 |
| 墙 | 无邮箱、无付款 | 和 PH 漏斗不同 |
| DC | 10/10 建议 **DC4** | 从默认 DC2 切到 nearest 再 sendCode |
| 代理 | `PT_tg` 10000–10009 | 1:1，出口复测 10/10 在 PT |

对照（同一套 iOS 协议，不要混读）：

| 国家 | 批次 | 结果 | 卡点 |
|------|------|------|------|
| PH | `f2594ada` 等 | 邮箱能过，**0 注册成功** | 邮箱后 Call / 付款墙；Call 不是 flashcall 造成的 |
| TR | `c4b3fef9` | 10/10 失败 | 当时 `TR_tg` 连不上 Telegram（出口） |
| **PT** | **`1e213443`** | **10/10 成功** | 协议 + 出口 + 短信同时成立 |

PH 闪信 A/B（off vs grammers 各 10 路）已经证明：**Call ≠ CodeSettings flashcall/missed**。不要为了「去 Call」再改这两个开关。

---

## 2. 合同：改这些就等于拆基线

### 必须保持

- iOS 官方：`api_id=8` / hash 官方配对 / `lang_pack=ios`
- REGHelp Push：**只** `tgiOS` + `iOS`；**禁止** AntiSafety / AID / Play Integrity
- InitConnection.params：**只** `tz_offset` + `bundleId=ph.telegra.Telegraph`
- **禁止** iOS 提交 `perf_cat` / `cert_fingerprint` / `safety_net` / `device_token` 进 params
- `CodeSettings.app_sandbox`：有 APNS 时为生产 `false`；这是证书环境，**不是**进程沙盒
- iOS `unknown_number` **固定 false**
- 闪信 / 漏接默认 `ios_code_settings_call_flags=grammers`（与已验证 grammers PH 成功 payload 对齐）。**不要**因为 PH 出 Call 就改掉
- 发码前：`GetNearestDc` → 切到建议 DC。**不要写死 DC5**（PH 常是 5，PT 实测是 4）
- locale 跟**出口国**：PH=`en`/`en-PH`/`28800`；PT=`pt`/`pt-PT`/`0`；TR=`tr`/`tr-TR`/`10800`
- 本仓 locale **不做夏令时**。葡萄牙 9 月墙上钟是 WEST+1，目录仍是 `tz=0`（和 GB 一样）。**不要自作主张改 3600**
- `tz_offset=0` 是合法值。合成行里禁止 `tz_offset or 28800`（会把 PT 错写成 PH +8）
- 非官方 api（如 94575）本轮**不启用**
- 批量 `count/concurrency` 上限 10；未经用户点名不要开 30 路

### 葡萄牙一等公民字段

| 层 | 值 |
|----|----|
| `COUNTRY_LANG_MAP["pt"]` | lang=`pt` system=`pt-pt` tz=`0` dial=`351` |
| iOS overlay 后 | `pt` / `pt-PT` / `0` |
| iOS 备用包 | `IOS_SEED_COUNTRIES` 含 `pt` |
| 姓名 | `SYNTHETIC_IDENTITY_POOLS["pt"]`（大陆葡语，不是巴西） |
| 代理 | `{CC}_tg` → `PT_tg`；区号 `351` |

### 主要代码

- `backend/app/services/ios_protocol.py`
- `backend/app/services/ios_device_catalog.py`
- `backend/app/services/device_profile.py`
- `backend/app/services/init_connection.py`
- `backend/app/services/code_delivery.py`
- `backend/app/services/registrar.py`（姓名池 + 切 DC）
- `backend/app/services/proxyseller.py`
- `backend/scripts/run_ph_smscode_warp.py`（`--country pt --app-type telegram_ios`）

---

## 3. 复现（出口必须先绿）

宿主机直连 SOCKS 会鉴权失败。**必须在 `edgenode-backend` 容器内测活**（`res.proxy-seller.com` extra_hosts 钉 WARP hop）。

```bash
# 1) 容器内 / API test-all：PT_tg 要健康
# 2) 再跑（脚本会临时改 config，结束时 restore）
python3 backend/scripts/run_ph_smscode_warp.py \
  --country pt --app-type telegram_ios \
  --count 10 --concurrency 10 --max-price 1.0
```

SMSCode PT 当时参考价约 $0.75，`max_price` 至少 0.80。

禁止：`docker compose up` 覆盖正在跑的容器。热更新用 `docker cp` + `docker restart edgenode-backend`。

---

## 4. 回退

```bash
git fetch origin tag baseline/pt-ios-10-20260915
git checkout baseline/pt-ios-10-20260915
# 或只看合同文件
git show baseline/pt-ios-10-20260915:docs/IOS_PT_BASELINE.md
```

校验冻结日志没被改：

```bash
cd docs/baselines/pt-ios-20260915 && sha256sum -c MANIFEST.sha256
```

`data/ab_reports/` 里的同名原始文件只在服务器磁盘上，**不作为**回退源。以本目录 + Git 标签为准。

---

## 5. 后来者不要做的「假修复」

| 冲动 | 为什么错 |
|------|----------|
| 把 PH 改成 `en-US` | 成功对照不是美国机 |
| 给 iOS 加 Android  attestation | iOS 没有 AID / Play Integrity |
| 写死 DC5 | PT 建议 DC4 |
| 关 flashcall 来消 Call | PH A/B 已否证 |
| 用巴西姓名 / `pt-BR` 冒充葡萄牙 | 出口是 PT |
| 把 `tz=0` 改成 3600 | 目录合同，不是墙上钟 |
| 看见 runner `unique_ips=0` 就去改代理分配 | 那是日志正则没吃到 IP；口 10000–10009 已 1:1 |
