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
- `data/banned_phones_cache.json` — 封禁号本地缓存

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
    fivesim.py / grizzlysms.py / vak_sms 等 SMS 网关
    device_db_manager.py     # 设备指纹库管理
    account_vault.py         # 金库上传/探针
    auth.py                  # 控制台 Session 登录
```

### 注册主流程（简化）

1. SMS 取号 → 2. **预检**（ResolvePhone，不烧 Push）→ 3. REGHelp Push Token → 4. MTProto connect → 5. `auth.sendCode` → 6. 等短信 / 处理 SentCodeTypeApp → 7. signIn/signUp → 8. 可选 auto_set_2fa

失败时：SMS `cancel` +（若 REGHelp）`setStatus` 退款（#26 已实现，需 `ref=task_id`）。

---

## 7. 关键集成与配置

| 服务 | 配置字段 | 说明 |
|------|----------|------|
| REGHelp | `reghelp_api_key` | Push Token / Integrity / Recaptcha；退款用 `setStatus` |
| Vak-SMS | `vak_sms_api_key` | 接码 |
| Grizzly SMS | `grizzlysms_api_key` | 接码，注意 `maxPrice` 美元 |
| 5SIM | `fivesim_api_key` | JWT Bearer |
| Proxy-Seller | `proxy_seller_key` | API 拉列表；失败则用内置 CL/IN 静态账密池 |
| 自建代理 | Settings → 代理池 | 格式 `host:port:user:pass #registration:za` |

内置静态住宅池（`proxyseller.py` → `STATIC_REGIONAL_POOLS`）：**仅 CL、IN**，无 ZA。南非需用户购买后导入自建池。

---

## 8. 已确认的产品/协议结论（勿重复踩坑）

### Push Token 默认不复用；可选本地库存

REGHelp Push Token 官方按一次性计费。平台侧正确优化仍是 `setStatus` 退款。
另支持**可选**本地库存：新签发可入库；失败且未退款的可按开关复用（优先未使用，其次用过 1 次）。
默认 `push_token_reuse_enabled=false`。详见「Push 令牌库」页。

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
