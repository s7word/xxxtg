# xxxtg AI 接手文档

> 供新开 Cursor / Cloud Agent 窗口时快速恢复上下文。最后更新：2026-08-26。

## 1. 项目是什么

**xxxtg（EdgeNode-Auditor）** 是一个 Telegram 批量注册编排系统：

- 后端：FastAPI + Telethon（MTProto 注册状态机）
- 前端：Vue 3 + Vite（Cyber Emerald Dark 模块化 UI）
- 部署：Docker Compose（backend:8000 + frontend nginx）

核心能力：多 SMS 网关取号、REGHelp/AntiSafety Push Token、代理区域匹配、号码预检、设备指纹库、账号金库、手动单号调试控制台。

**仓库**：https://github.com/s7word/xxxtg  
**所有者**：s7word（dark s7word / s7word@gmail.com）

---

## 2. 用户的工作方式（必读）

**2026-08-26 起：远程开发，当前模型自己完成，禁止委派。**

1. 工作区就是这台服务器：`/opt/xxxtg`（Cursor Remote-SSH）
2. 自己读代码、改代码、跑测试、重建 Docker、用 curl / 浏览器验证
3. **禁止**用 `Task` 委派其他模型或子代理（项目规则 `.cursor/rules/remote-dev.mdc`）
4. 与用户用 **简体中文** 沟通
5. 分支命名仍可用：`cursor/<descriptive-name>-9abd`（全小写，必须带 `-9abd` 后缀）
6. 未要求时不要 commit / push；密钥只写 `data/config.json`，不进 Git

### Remote Control（手机 / Web 遥控这台机器）

服务器已常驻 worker：`xxxtg@srv1884560`（systemd `cursor-agent-worker-xxxtg`，开机自启）。

- 机器入口：https://cursor.com/agents#workerId=23e7e6dc-4fbd-432f-b0e7-87054a3de4cf
- 本机健康检查：`curl http://127.0.0.1:18789/readyz`
- 当前对话交给手机：Agents Window → Settings → Agents 打开 **Remote Control**，输入框发 `/remote-control`
- 新任务：cursor.com/agents 或 iOS App 选这台机器 `xxxtg@srv1884560`

---

## 3. 当前推荐开发分支

| 分支 | 说明 | PR |
|------|------|-----|
| **`cursor/reghelp-push-refund-9abd`** | **当前最全栈**：Cyber Emerald UI + 手动控制台 + REGHelp Push 退款闭环 | [#26](https://github.com/s7word/xxxtg/pull/26) DRAFT |
| `cursor/manual-registration-console-9abd` | 手动单号控制台（#26 的上游基线） | [#23](https://github.com/s7word/xxxtg/pull/23) OPEN |
| `cursor/proxyseller-za-pool-fix-9abd` | 南非代理零候选误导提示 + fallback 区域告警 | [#25](https://github.com/s7word/xxxtg/pull/25) DRAFT |
| `cursor/reghelp-email-infra-9abd` | REGHelp 设备邮箱基础设施（**未合并**进上分支） | [#24](https://github.com/s7word/xxxtg/pull/24) DRAFT |

`main` 分支较旧，**不要**在 main 上直接开发新功能。

### 合并建议（待用户确认）

若要做「一条线」长期维护，建议按依赖顺序合并到 main：

1. #23 手动控制台 → 2. #25 南非代理 → 3. #26 REGHelp 退款 → 4. #24 REGHelp 邮箱（可选）

---

## 4. 自有服务器部署（生产/继续开发）

| 项 | 值 |
|----|-----|
| IP | `187.127.218.157` |
| SSH | `root@187.127.218.157:22`（公钥登录；**勿把私钥/密码写入 Git**） |
| 项目路径 | `/opt/xxxtg` |
| 当前分支 | `cursor/reghelp-push-refund-9abd` @ `fad2357` |
| 后端 | http://187.127.218.157:8000 |
| 前端 | http://187.127.218.157:3100 |

### 端口冲突

服务器 `3000` 已被 **`autoc-frontend`** 占用。xxxtg 前端映射为 **`3100:80`**（仅服务器本地 `docker-compose.yml` 修改，未推 Git）。

### 常用命令（服务器）

```bash
ssh root@187.127.218.157
cd /opt/xxxtg
git pull origin cursor/reghelp-push-refund-9abd
sudo docker compose build
sudo docker compose up -d
curl http://127.0.0.1:8000/api/health
```

### 数据目录（不在 Git 中）

- `data/config.json` — API Key、代理、2FA 等敏感配置
- `data/sessions/` — 注册产出 session
- `lod_user/` — 探针账号 `.session` / `.json`
- `data/device_dbs/` — 设备指纹库
- `data/banned_phones_cache.json` — 号码黑名单（拉黑 / 已注册 / 手动）

云机已打包同步过；后续改 config 需在服务器与 Git 之外单独备份。

### 控制台登录

前端 `:3100` 需登录后才能调 `/api/*`（`/api/health` 仍公开，docker healthcheck 不受影响）。账号密码读环境变量 `EDGENODE_AUTH_USER` / `EDGENODE_AUTH_PASSWORD`，未设置时使用代码 fallback。Session cookie 名 `edgenode_session`；secret 优先 `EDGENODE_AUTH_SECRET`，否则写入 `data/edgenode_auth_secret`（已 gitignore）。仅测试可设 `EDGENODE_AUTH_DISABLED=1`。

---

## 5. 云机环境（Cursor Cloud Agent）

- 工作区：`/workspace`
- Docker 可用（部分环境需 `sudo`）
- 云机 **出口 IP 不固定**，Proxy-Seller API 白名单最多 3 个 IP → **不适合**在云机调 Proxy-Seller API
- 自有服务器 `187.127.218.157` 为 **固定 IP**，适合加入 Proxy-Seller 白名单（1 个即可）

---

## 6. 架构速查

```
frontend/src/
  App.vue                    # 壳：ce-app 模块化路由
  components/console/        # 控制台 + 手动单号调试
  components/settings/       # 全局配置、REGHelp、5SIM 等
  components/proxy/          # 代理网格
  components/tokens/          # Push Token 库存
  components/blacklist/       # 号码黑名单查询管理
  components/vault/          # 账号金库
  components/devices/        # 设备指纹库
  composables/               # useManualRegister, useConfig, useTasks...

backend/app/
  main.py                    # FastAPI 入口
  api/routes.py              # REST API
  models/schemas.py          # Pydantic 配置与请求体
  services/
    registrar.py             # 核心注册状态机（最重要）
    manual_registrar.py      # 手动单号两阶段状态机
    reghelp.py               # REGHelp Push/Integrity/Recaptcha
    attestation_gateway.py   # REGHelp ↔ AntiSafety 调度
    proxyseller.py           # Proxy-Seller + 内置 CL/IN 静态池
    proxy_manager.py         # 自建代理池导入
    phone_precheck.py        # ResolvePhone 白号预检
    banned_phones.py         # 本地号码黑名单（封禁/已注册）
    fivesim.py / grizzlysms.py / smsbower.py / smscode.py / vak_sms 等 SMS 网关
    device_db_manager.py     # 设备指纹库管理
    account_vault.py         # 金库上传/探针
    auth.py                  # 控制台 Session 登录
```

### 注册主流程（简化）

1. SMS 取号 → 1.4 **本地黑名单**（已拉黑/已注册号直接退订）→ 2. **预检**（ResolvePhone，不烧 Push）→ 3. REGHelp Push Token → 4. MTProto connect → 5. `auth.sendCode` → 6. 等短信 / 处理 SentCodeTypeApp → 7. signIn/signUp → 8. 可选 auto_set_2fa

失败时：SMS `cancel` +（若 REGHelp）`setStatus` 退款（#26 已实现，需 `ref=task_id`）。预检已注册 / `SENT_CODE_TYPE_APP` / `PHONE_NUMBER_BANNED` 会写入本地黑名单，防止平台二次下发。

**验证码通道策略**（`code_delivery_mode`，默认 `balanced`）：
- `balanced`：非泄露 effective api_id（如已配置 custom）→ 不申请/不 attach Push Token，提高 SMS 概率
- `sms_first`：同上但更激进；遇 `API_ID_PUBLISHED_FLOOD` 可一次性 escalate 到 Push
- `push_required`：legacy，始终 attach Push Token
- `hunt_sms_first_after_app_streak`：猎号连续 App 达到该值后强制 SMS 优先

`CodeSettings` 里唯一影响 App/SMS 通道选择的是 `token`/`app_sandbox`（iOS APNS 推送凭证，
带上就等于给服务端一条推送通道）。`allow_app_hash` 是 Android SMS Retriever 的**短信正文**
协商位，官方 Android 客户端恒设，因此按设备平台决定，不参与通道策略。

规律：复用号池导致的 App（`next_type=None`，号码在 Telegram 侧仍有已授权会话）是主因，
代码无法消除，只能换号源；attach Push Token 带来的推送投递可通过上述策略消除。

---

## 7. 关键集成与配置

| 服务 | 配置字段 | 说明 |
|------|----------|------|
| REGHelp | `reghelp_api_key` | Push Token / Integrity / Recaptcha；退款用 `setStatus` |
| Vak-SMS | `vak_sms_api_key` | 接码 |
| Grizzly SMS | `grizzlysms_api_key` | 接码，注意 `maxPrice` 美元 |
| 5SIM | `fivesim_api_key` | JWT Bearer |
| SMSCode.gg | `smscode_api_key` | Bearer Token；`sms_provider=smscode`。详见 [SMSCODE_GG.md](./SMSCODE_GG.md) |
| Proxy-Seller | `proxy_seller_key` | API 拉列表；失败则用内置 CL/IN 静态账密池 |
| 自建代理 | Settings → 代理池 | 格式 `host:port:user:pass #registration:za` |

内置静态住宅池（`proxyseller.py` → `STATIC_REGIONAL_POOLS`）：**仅 CL、IN**，无 ZA。南非需用户购买后导入自建池。

---

## 8. 已确认的产品/协议结论（勿重复踩坑）

### Push Token 默认不复用；可选本地库存

REGHelp Push Token 官方按一次性计费。平台侧正确优化仍是 `setStatus` 退款。
另支持**可选**本地库存：新签发可入库；失败且未退款的可按开关复用（优先未使用，其次用过 1 次）。
默认 `push_token_reuse_enabled=false`。详见「Push 令牌库」页。

### app_id / app_hash 轮换策略

**结论先说：不需要定期更换。** Telegram 的 `api_id` / `api_hash` 是**开发者应用身份**，
不是会过期的会话密钥：没有 TTL、没有轮换周期，Telegram 也不提供「换一批」的接口。
按日历轮换只有坏处 —— 新 ID 没有任何历史积累，而 my.telegram.org 每个账号能创建的
应用数量很有限，换掉就拿不回来。

更关键的是：**换 api_id 不会提高 SMS 命中率。** 见下一节 A/B 实测：自建非泄露
`api_id=35337905` 在 IQ 与 CO 两国 35 个 sendCode 样本里仍然 100% `SentCodeTypeApp`。
把 SentCodeTypeApp 归因到「凭证该换了」是错的归因。

**必须更换的情况**（只有这几种）：

| 触发条件 | 判据 | 为什么必须换 |
|----------|------|--------------|
| ID 已公开泄露 | 落在 `PUBLISHED_API_ID_BLOCKLIST`（`4/6/8/10/2040/2100/17349/21724`），或带合法 Push Token 仍反复 `API_ID_PUBLISHED_FLOOD` | 服务端对这些 ID 无差别风控，无 Push 时 `auth.sendCode` 几乎必失败，与账号/IP/地区无关 |
| `api_hash` 外泄 | 进过公开仓库、截图、日志、工单 | `api_hash` 是凭证不是标识，等价于密码；泄露后别人可以冒用你的应用身份 |
| 该 ID 被平台限制 | my.telegram.org 显示受限，或该 ID 上所有号、所有出口都恒定 FLOOD | 已经是 ID 维度的处置，换号换代理都无效 |
| 多项目/多客户隔离 | 不同业务线共用一个 ID | 一条业务被风控会连带其它业务，需要故障隔离就必须分 ID |

**不要因为这些换**（都是号码/出口维度的问题，换 ID 纯属浪费）：
`SentCodeTypeApp`、`PHONE_NUMBER_BANNED`、单号 `FLOOD_WAIT`、预检判定已注册。

**与 Push Token / `code_delivery_mode` 的关系**（这才是 api_id 真正的价值）：

- 泄露 ID 需要 Push Token 给请求「背书」才能发出 `sendCode`；**自建非泄露 ID 不需要**。
- `resolve_code_delivery_plan()` 就是按这一条分轨：`balanced` 下预测到的 effective
  `api_id` 非泄露 → 走 `sms_first`，**完全不申请 Push Token**；泄露 / official 路径 →
  才升到 `push_required` 去买 Token。
- 所以「有一个自建 api_id」= **可以不买 Push Token**，省的是 REGHelp 的钱，
  不是换来更高的 SMS 率。
- `sms_first` 万一真撞上 `API_ID_PUBLISHED_FLOOD`，`escalation_plan_after_published_flood()`
  会一次性 escalate 到 `push_required` 重试，不需要人工介入。
- 换 ID 必须 **同时** 换 `api_hash`（严格配对），并且已入库账号的 `app_id` 字段记录的是
  它当初注册时用的 ID —— 凭证库按账号存 `app_id`/`app_hash` 就是这个原因，别用一个
  全局 ID 去覆盖历史账号的归属。

**更换步骤**：my.telegram.org 申请 → 控制台「全局参数拓扑」填 `custom_api_id` /
`custom_api_hash` → `api_credential_mode` 设 `custom`（想保留官方优先则设 `auto`）→
保存后观察任务日志里 `API 凭证策略:` 那一行确认真的生效。

### code_delivery_mode A/B 实测：SMS 率的瓶颈是号池，不是投递模式

2026-09-01 在自有服务器实测（`backend/scripts/run_code_delivery_ab.py`，报告在
`data/ab_reports/`），接码源 Grizzly SMS，出口用 proxy-seller 同国住宅（IQ_tg / CO_tg，1:1 绑定）：

| 国家 | 模式 | 租号 | 发码样本 | SMS | App | next_type=None | SMS 率 |
|------|------|-----:|---------:|----:|----:|---------------:|-------:|
| IQ | `balanced`（实际走 `sms_first`，不带 Push） | 14 | 13 | 0 | 13 | 13 | 0% |
| IQ | `push_required`（申请并 attach Push Token） | 20 | 6 | 0 | 6 | 6 | 0% |
| CO | `balanced` | 20 | 16 | 0 | 16 | 16 | 0% |
| CO | `push_required` | 20 | 17 | 0 | 17 | 17 | 0% |

IQ 的 `push_required` 只拿到 6 个样本，是因为前一轮刚把 13 个号写进本地黑名单，
第二轮 20 次取号里 14 次直接命中黑名单跳过。**这只压缩了样本量、不改变入选口径**
（两轮都只对「不在黑名单里的号」发码）。CO 那一对没有这个干扰：两轮各 20 次取号、
黑名单跳过均为 0，是干净的对照。

**结论**：4 轮共 52 个 sendCode 样本，SMS 命中 **0 个**，`SentCodeTypeApp` 52 个，
且 `next_type` 全为 `None` —— Telegram 连 SMS 降级窗口都不给，说明这些号在服务端仍挂着
已授权会话，OTP 被投进旧客户端。两种模式统计上完全不可区分。买 Push Token、换 api_id、
换设备指纹都救不了，**只能换号源 / 换国 / 换供应商**。IQ 与 CO 同时 0% 说明这是接码平台
号池的性质，不是某一国的特例。

因此 `code_delivery_mode` 保持默认 **`balanced`**：SMS 率与 `push_required` 相同（都是 0），
但非泄露 api_id 下完全不申请 Push Token，省掉 REGHelp 每号的开销，也少一个变量。
`push_required` 只在 effective api_id 确实是泄露 ID 时才有存在意义，而那种情况 `balanced`
自己就会自动升级过去，不需要手工切成 legacy 模式。

### 「直接登录探测」不能替代预检

Telegram 登录/注册共用 `auth.sendCode`。预检用 `ResolvePhone`（静默）；`SentCodeTypeApp` 仅作漏网快退。

### 绑临时邮箱不能防号码找回

防别人拿号登录靠 **2FA 云密码**（`auto_set_2fa` / `default_2fa_password`）。REGHelp Email 是设备基础设施，不是 Telegram 找回邮箱。

### Proxy-Seller API 白名单

最多 3 IP；云机 IP 轮换 → 用服务器固定 IP 或放弃 API、改自建池账密。

### 前端版本

**Cyber Emerald 模块化 UI** 在 `manual-registration-console` 及之后分支。`reghelp-email-infra` 分支前端是旧版单体 `App.vue`，不要误部署。

---

## 9. 已知待办 / 未完成

- [ ] 合并 PR #23/#25/#26 到 main（用户未明确要求）
- [ ] REGHelp Email 设备基础设施（#24）合并进主开发线
- [ ] 服务器 `docker-compose.yml` 端口 3100 变更未推 Git（可做成 `docker-compose.override.yml` 示例）
- [ ] Proxy-Seller 白名单：用户需在后台添加 `187.127.218.157`
- [ ] 南非 ZA 代理：需购买后导入自建池或扩展 `STATIC_REGIONAL_POOLS`
- [ ] 手动控制台：#23 已修同号多任务 + waiting_code 取消

---

## 10. 测试与验证

```bash
# 后端全量
cd /workspace  # 或 /opt/xxxtg
python3 -m pytest backend/tests/ -q

# 专项
python3 -m pytest backend/tests/test_reghelp_push_refund.py -v
python3 -m pytest backend/tests/test_manual_registration.py -v

# Docker
sudo docker compose build && sudo docker compose up -d
curl http://localhost:8000/api/health
```

前端构建：`cd frontend && npm run build`

---

## 11. Git / PR 规范（Cloud Agent）

```bash
git checkout -b cursor/my-feature-9abd
# ... 改动 ...
git add -A && git commit -m "feat: ..."
git push -u origin cursor/my-feature-9abd
# ManagePullRequest create_pr, base_branch=main
```

- 用 `ManagePullRequest`，不要用 `gh pr create`
- 每轮有代码改动结束前更新 PR

---

## 12. 新开窗口时 AI 应做的第一件事

1. 读本文档与 `.cursor/rules/remote-dev.mdc`
2. `git branch` 确认分支；优先 `cursor/reghelp-push-refund-9abd` 或用户指定
3. 确认工作区是服务器 `/opt/xxxtg`（远程开发），不是云机 `/workspace`
4. 问用户本轮目标后 **自己执行**，不要派 Task
5. 本机已在服务器上时直接操作；不要再 SSH 一遍

本地 Cursor 连接本机（已装 `~/.cursor-server`）：

```
Host xxxtg
  HostName 187.127.218.157
  User root
  Port 22
  IdentityFile ~/.ssh/id_ed25519
  ForwardAgent no
```

命令面板：`Remote-SSH: Connect to Host` → `xxxtg` → 打开文件夹 `/opt/xxxtg`。

---

## 13. 术语对照（代码/UI 里的「学术化」命名）

| UI/日志用语 | 实际含义 |
|-------------|----------|
| 端点模板 / MTProto Endpoint | Telegram 客户端 api_id/hash + 设备画像 |
| 二级密码学状态保护 | 2FA 云密码 |
| 多径中继网关 | 代理（Proxy-Seller / 自建池） |
| Attestation Push Token | REGHelp FCM/APNs 推送凭证 |
| 通信句柄 | SMS 租用号码（activation id） |

---

## 14. 联系与链接

- GitHub：https://github.com/s7word/xxxtg
- REGHelp 文档：https://reghelp.net/en/api-docs/
- Proxy-Seller API：https://docs.proxy-seller.com/

---

*随项目演进请增量更新 `docs/AI_HANDOVER.md`。未要求不要擅自 push。*
