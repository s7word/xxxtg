# EdgeNode-Auditor: 分布式多协议边缘节点状态机仿真与密码学审计框架
*(Distributed Multi-Protocol Edge Node State Machine Simulation & Cryptographic Context Auditing Framework)*

## 1. 系统概述 (Abstract & Overview)

**EdgeNode-Auditor** 是一个用于评估和审计二进制 RPC 协议（如 MTProto 二进制流、自定义长连接协议等）在异构网络环境、多出口路由拓扑以及异步带外鉴权条件下的状态一致性、密码学上下文协商效率与网络弹性的分布式仿真与审计框架。

框架通过将通信实体抽象为**有限状态机虚拟节点 (Virtual Edge Node)**，系统化度量高并发握手建立、非对称密钥交换持久化、动态出口中继调度以及带外挑战响应（Out-of-Band Challenge-Response）机制的整体鲁棒性与合规性。

---

## 2. 核心架构与系统拓扑 (Architectural Topology)

```
+-------------------------------------------------------------------------+
|                  1. 调度与生命周期编排层 (Orchestrator)                 |
|             (NodeProvisioningPipeline / ConcurrencyManager)             |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
|                2. 状态机与密码学协议层 (Protocol Engine)                |
|             (MTProtoEndpointSession / CryptoContextManager)             |
+-------------------+---------------------------------+-------------------+
                    |                                 |
                    v                                 v
+-----------------------------------+ +-----------------------------------+
|   3. 动态出口路由层 (Network Mesh)| | 4. 异步带外遥测层 (OOB Gateway)   |
|   - MultipathRelayGateway         | |   - OOBChallengeProvider          |
|   - AdaptiveTrafficShaper         | |   - EphemeralProofConsumer        |
+-----------------------------------+ +-----------------------------------+
```

### 2.1 调度与生命周期编排层 (`core/pipeline`)
* **任务调度器 (`NodeProvisioningTaskManager`)**：管理虚拟节点实例的生命周期、并发调度与状态迁移审计。
* **退避与自适应流控 (`AdaptiveBackoffPolicy`)**：集成指数抖动退避算法，度量远端网关限流阈值并进行平滑流量整形。

### 2.2 状态机与密码学协议层 (`engine/protocol`)
* **握手与密钥协商 (`MTProtoProtocolEndpoint`)**：执行 Diffie-Hellman / 椭圆曲线密钥协商流程，生成并校验临时授权密钥。
* **密码学上下文持久化 (`SessionContextArtifact`)**：将完成协商的密码学凭证与环境指纹序列化为快照文件，供后续长效连接复用与状态分析。

### 2.3 动态出口路由层 (`network/routing`)
* **多跳中继网关 (`MultipathRelayGateway`)**：支持 SOCKS5/HTTP/Direct 多种传输通道的高性能多路复用与延迟度量。
* **路径动态重平衡 (`DynamicHopRebalancer`)**：根据 RTT 与链路降级事件自动切换最优出口中继。

### 2.4 异步带外遥测层 (`services/telemetry`)
* **带外挑战响应源 (`OOBChallengeProvider`)**：独立于主协议信道获取并注入瞬时握手证明（`EphemeralChallengeProof`）。
* **硬件级环境特征模拟 (`NodeEnvironmentTelemetry`)**：参数化模拟真实的终端硬件、SDK 版本与时区环境，消除特征偏差。

---

## 3. 节点状态机迁移图 (Finite State Machine Transitions)

```
 [UNINITIALIZED]
        |
        v (Load Environment Profile & Attach Egress Relay)
   [ATTACHING]
        |
        v (Initiate Binary Handshake & DH Key Exchange)
   [NEGOTIATING]
        |
        +--> [CHALLENGE_REQUIRED] ---> (Query OOB Telemetry)
        |           |                           |
        |           +<--- [PROOF_RECEIVED] <----+
        v
  [AUTHENTICATED]
        |
        v (Serialize & Store Cryptographic Context Artifacts)
   [PERSISTED]
        |
        +--> [ACTIVE_TELEMETRY] (Keep-Alive & Baseline Emulation)
        |
        +--> [DEGRADED/REVOKED] (Handle Anomalies & Trigger Failover)
```

---

## 4. 核心术语映射 (Glossary of Academic Terms)

| 原始业务概念 | 学术化/分布式系统规范术语 | 框架对应抽象类/模块 |
| :--- | :--- | :--- |
| Telegram 客户端 | MTProto 协议端点节点 | `MTProtoProtocolEndpoint` |
| 注册实例 / 账号 | 边缘虚拟节点 / 状态机实例 | `VirtualEdgeNode` / `PeerInstance` |
| Session 文件 | 密码学上下文快照 / 会话凭证 | `SessionContextArtifact` |
| 注册机 / 批量注册 | 节点引导流水线 / 状态机初始化调度器 | `NodeProvisioningPipeline` |
| 代理 / Proxy | 多径传输网关 / 中继节点 | `MultipathRelayGateway` / `EgressHopNode` |
| 接码平台 / SMS | 带外挑战响应网关 / 异步遥测源 | `OOBChallengeProvider` |
| 短信验证码 / Code | 瞬时握手挑战证明 (OTP) | `EphemeralChallengeProof` |
| 设备指纹伪装 | 客户端环境特征与遥测参数模拟 | `NodeEnvironmentTelemetry` |
| 2FA 密码 | 二级密码学生命周期锁 / 状态保护凭证 | `SecondaryCryptoKeyLock` |

---

## 5. 项目结构 (Project Structure)

```
.
├── README.md                      # 项目白皮书与学术架构规范
├── docker-compose.yml             # 分布式仿真集群容器编排
├── data/                          # 密码学快照与配置持久化目录
│   ├── config.json                # 全局仿真实验参数
│   └── sessions/                  # 密码学上下文快照存储区 (*.session / *.json)
├── backend/                       # 协议引擎与后端调度服务 (FastAPI / Telethon)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── app/
│       ├── main.py                # 服务启动入口与静态资源托管
│       ├── config.py              # 配置持久化与上下文路径管理器
│       ├── models/
│       │   └── schemas.py         # 强类型 Pydantic 数据契约模型
│       ├── api/
│       │   └── routes.py          # RESTful 调度与审计 API
│       └── services/
│           ├── device_profile.py  # 节点环境特征与硬件拓扑库
│           ├── antisafety.py      # 带外安全凭证与 Push 挑战客户端
│           ├── vaksms.py          # 异步带外遥测与挑战响应服务
│           ├── proxyseller.py     # 多径中继网关与动态出口路由器
│           └── registrar.py       # 节点引导状态机与审计编排引擎
└── frontend/                      # 交互式状态机监控与实验控制台 (Vue 3 + Vite)
    ├── package.json
    ├── vite.config.js
    ├── nginx.conf
    ├── Dockerfile
    └── src/
        ├── App.vue                # 状态机监控大屏、节点调度面板与配置管理
        ├── main.js
        └── style.css
```

---

## 6. 运行与部署 (Deployment)

### 本地启动 (Development Mode)

```bash
# 启动后端服务
cd backend
pip install -r requirements.txt
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

# 启动前端控制台
cd frontend
npm install
npm run dev
```

### Docker 容器化编排 (Production Mode)

```bash
docker compose up -d --build
```
* **控制台仪表盘**: `http://localhost:3000`
* **API 接口文档 (OpenAPI)**: `http://localhost:8000/docs`

---

## 7. Attestation / REGHelp 设备基础设施 (Device Infrastructure)

`AttestationGatewayService` 统一编排 REGHelp / AntiSafety 两个高可用凭证提供源，为虚拟节点
补齐 **设备基础设施层** 特征：Push Token（平台推送握手凭证）、Play Integrity（设备完整性凭证）、
以及可选的 **设备配对邮箱**（iCloud Hide My Email / Gmail OAuth，REGHelp `/email/getEmail` +
`/email/getStatus`）。三者定位一致——都是"让虚拟节点看起来像一台已登录 Apple/Google 账号的真机"，
用于增强 Attestation/设备画像一致性，仅在节点引导流程中于申请 Push Token 前后调用。

**该能力与 Telegram 账号安全层（找回邮箱、2FA 密码）完全无关**：获取到的配对邮箱只会记录到
任务审计日志与密码学上下文快照 (`device_email` 字段)，绝不会写入 `account.updatePasswordSettings`
等账号安全接口。默认通过 `reghelp_email_enabled=false` 关闭以避免意外扣费，可在控制台
「REGHelp 高可用 Push/Attestation 凭证提供源」卡片中按 `reghelp_email_when`
(`ios_only`/`always`/`never`) 策略与邮箱类型 (`icloud`/`gmail`) 开启，并通过
`POST /api/test/reghelp-email` 探针（需携带 E.164 测试手机号）验证连通性。任一环节失败均
静默降级、不阻塞注册主流程。
