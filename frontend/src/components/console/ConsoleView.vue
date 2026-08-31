<template>
  <section class="ce-page">
    <div class="grid-launch">
      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>⚡ 状态机编排与控制台</h3>
          <span class="ce-muted">{{ launchMode === 'manual' ? '手动单号核验' : '全球国家拓扑矩阵' }}</span>
        </div>

        <div class="ce-seg ce-mode-tabs">
          <button :class="{ 'is-on': launchMode === 'auto' }" @click="launchMode = 'auto'">
            ⚡ 接码平台全自动引导
          </button>
          <button :class="{ 'is-on': launchMode === 'manual' }" @click="launchMode = 'manual'">
            🛠️ 手动单号调试控制台
          </button>
        </div>

        <LiveStockCountryPicker v-if="launchMode === 'auto'" v-model="form.country" :provider="form.sms_provider" />

        <div v-if="launchMode === 'manual'" class="stack">
          <div>
            <label class="ce-label">手动填写手机号（跳过接码平台租号）</label>
            <input
              v-model="manualPhone"
              class="ce-input mono"
              placeholder="+9647706110434  /  +628123456789"
              autocomplete="off"
            />
            <p class="ce-tiny">支持 <code>+</code> 前缀或纯数字。设备指纹、代理、Attestation 与 MTProto 握手与全自动模式一致。</p>
          </div>
          <div>
            <label class="ce-label">目标国家（可留空，由号码自动推断）</label>
            <select v-model="manualCountry" class="ce-select">
              <option value="">🌐 自动从手机号推断国家与真机指纹</option>
              <optgroup v-for="group in COUNTRY_GROUPS" :key="group.id" :label="group.label">
                <option v-for="opt in group.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </optgroup>
            </select>
          </div>
        </div>

        <div>
          <label class="ce-label">端点协议模板与 Attestation 凭证</label>
          <select v-model="form.app_type" class="ce-select">
            <option v-for="opt in APP_TYPE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </div>

        <div v-if="launchMode === 'auto'">
          <label class="ce-label">本次接码平台源（可临时覆盖）</label>
          <select v-model="form.sms_provider" class="ce-select">
            <option value="fivesim">5SIM (推荐) · 5sim.net</option>
            <option value="grizzlysms">Grizzly SMS · grizzlysms.com</option>
            <option value="smsbower">SMS Bower · smsbower.app</option>
            <option value="vaksms">Vak-SMS · vak-sms.com</option>
          </select>
          <p class="ce-tiny">
            全局默认：{{ smsProviderLabel(config.sms_provider || 'fivesim') }}。
            本次任务将使用上方选择，失败自动退款。
          </p>
        </div>

        <div v-if="launchMode === 'auto'" class="ce-alert" :class="effectiveMaxPrice ? 'is-ok' : 'is-warn'">
          <div class="between">
            <strong>📈 动态最高出价上限 (Max Price / Bidding)</strong>
            <span :class="effectiveMaxPrice ? 'ce-badge is-success' : 'ce-badge is-warn'">
              {{ effectiveMaxPrice ? `${effectiveMaxPrice}（账户结算币种）` : '未设置 · 平台底价' }}
            </span>
          </div>
          <p class="ce-tiny" style="margin-top:6px">
            按接码平台账户结算币种原样填写。美元账户（如伊拉克 IQ 网页价 $0.5294）填
            <code>0.55</code> / <code>0.6</code> / <code>1.0</code>；卢布账户按网页卢布价填写。
            勿把美元账户误填 <code>50</code> / <code>100</code>，平台会直接返回 <code>NO_NUMBERS</code>。
          </p>
          <div style="margin-top:8px">
            <label class="ce-label">本次任务出价覆盖（可选）</label>
            <input
              v-model.number="form.max_price"
              type="number"
              min="0"
              step="0.01"
              class="ce-input mono"
              placeholder="如 0.55 / 1.0 (根据平台结算币种如美元/卢布填入)"
            />
          </div>
        </div>

        <div
          v-if="launchMode === 'auto' && (form.sms_provider === 'smsbower' || form.sms_provider === 'grizzlysms')"
          class="stack"
        >
          <label class="ce-label">供应商 ID（providerIds，可选）</label>
          <input
            v-model="form.provider_ids"
            class="ce-input mono"
            placeholder="如 3330 或 2579,3451"
          />
          <p class="ce-tiny">
            SMS Bower / Grizzly 专用：指定上游 <code>providerRef</code> / <code>supplierIds</code>，
            不填则从平台任意供应商池取号（热门国可能 NO_NUMBERS）。
          </p>
        </div>

        <div>
          <label class="ce-label">代理配对策略（使用者决定）</label>
          <select v-model="form.proxy_mode" class="ce-select">
            <option value="explicit">特定代理节点 (Explicit)</option>
            <option value="custom_pool">自建池智能轮换 (Custom Pool)</option>
            <option value="auto">API 动态分配 (Auto)</option>
            <option value="fallback">全局后备代理 (Fallback)</option>
          </select>
        </div>
        <div v-if="form.proxy_mode === 'explicit'">
          <label class="ce-label">指定自建/静态代理节点</label>
          <select v-model="form.proxy_id" class="ce-select">
            <option value="">请选择节点...</option>
            <option v-for="p in registrationProxies" :key="p.id" :value="p.id">
              {{ roleLabel(p.role) }} · {{ p.addr }}:{{ p.port }} · {{ (p.assigned_country || p.country_code || '全球').toString().toUpperCase() }}
            </option>
          </select>
          <p class="ce-tiny">显式指定后 100% 遵从该节点，不施加隐式国家约束。</p>
        </div>

        <div v-if="config.use_proxy_seller_auto" class="ce-alert is-info stack">
          <div class="between">
            <div>
              <strong>多径中继网关 API 自动分配</strong>
              <div class="ce-tiny">自动分配目标区域 ({{ form.country.toUpperCase() }}) SOCKS5 中继跳点</div>
            </div>
            <span class="ce-badge is-info">动态拓扑</span>
          </div>
          <div v-if="matchedProxy" class="ce-alert is-ok">
            当前国家自动分配:
            <span class="mono">{{ (matchedProxy.proxy_type || 'socks5') }}://{{ matchedProxy.addr }}:{{ matchedProxy.port }}</span>
            <span> [{{ (matchedProxy.country_code || matchedProxy.country || form.country).toString().toUpperCase() }}]</span>
          </div>
          <div class="row-wrap">
            <button class="ce-btn-ghost" :disabled="proxyTesting.proxypool" @click="refreshProxyPool(form.country, true)">
              {{ proxyTesting.proxypool ? '刷新中...' : '自动从 API 刷新区域代理池' }}
            </button>
            <button class="ce-link" :disabled="proxyTesting.autoselect" @click="previewAutoSelect(form.country)">查看当前国家分配</button>
          </div>
          <div v-if="proxyPool.length" class="ce-list">
            <div v-for="(p, idx) in proxyPool.slice(0, 6)" :key="p.id || (p.addr + ':' + p.port + idx)" class="ce-item">
              <span class="ce-badge is-info">{{ (p.country_code || p.country || '?').toString().toUpperCase() }}</span>
              <span class="mono grow">{{ p.addr }}:{{ p.port }}</span>
              <span class="ce-muted">{{ p.proxy_type }}</span>
              <span :class="healthClass(p.healthy)">{{ healthText(p) }}</span>
            </div>
          </div>
          <div class="ce-alert">
            <div class="between">
              <span>📋 自建代理池</span>
              <span class="ce-muted">{{ customProxySummaryText }}</span>
            </div>
            <div v-if="customProxiesForCountry.length" class="stack" style="margin-top:8px">
              <div v-for="(p, idx) in customProxiesForCountry.slice(0, 4)" :key="p.id || (p.addr + ':' + p.port + idx)" class="between">
                <span>{{ countryFlag(p.country_code) }} {{ (p.country_code || '?').toString().toUpperCase() }}</span>
                <span class="mono">{{ p.addr }}:{{ p.port }}</span>
                <span :class="healthClass(p.healthy)">{{ healthText(p) }}</span>
              </div>
            </div>
            <div v-else class="ce-tiny" style="margin-top:6px">当前国家暂无已标注的自建节点，可到「代理网关」批量粘贴导入</div>
          </div>
        </div>
        <div v-else class="ce-alert stack">
          <div class="between">
            <div>
              <div class="mono">{{ config.fallback_proxy.addr }}:{{ config.fallback_proxy.port }}</div>
              <div class="ce-tiny">传输: {{ (config.fallback_proxy.proxy_type || 'socks5').toUpperCase() }} (静态后备跳点)</div>
            </div>
            <span class="ce-badge is-warn">静态中继</span>
          </div>
          <div class="ce-tiny">
            📋 自建代理池 {{ customProxySummaryText }}
            <span v-if="customProxiesForCountry.length"> · 当前国家可匹配 {{ customProxiesForCountry.length }} 条</span>
          </div>
        </div>

        <div v-if="launchMode === 'auto'" class="ce-panel" style="padding:12px">
          <div class="between">
            <label class="ce-check">
              <input type="checkbox" v-model="batchMode" />
              并发批量引导模式
            </label>
            <span class="ce-badge is-info">asyncio.Semaphore</span>
          </div>
          <div v-if="batchMode" class="stack" style="margin-top:10px">
            <div>
              <div class="ce-label">并行任务数</div>
              <div class="ce-seg">
                <button v-for="n in [1, 3, 5, 10]" :key="n" :class="{ 'is-on': batchCount === n }" @click="batchCount = n">{{ n }} 个</button>
              </div>
            </div>
            <div>
              <label class="ce-label">并发度 concurrency (1~{{ batchCount }})</label>
              <input type="range" class="ce-range" min="1" :max="Math.max(1, batchCount)" v-model.number="batchConcurrency" />
              <div class="between ce-muted">
                <span>串行 1</span>
                <span class="mono" style="color:var(--mint-light)">{{ effectiveConcurrency }} 并行槽</span>
                <span>{{ batchCount }}</span>
              </div>
            </div>
            <p class="ce-tiny">
              等待 OTP 期间可并行验证多个号码。租号后先做白号预检；已注册号直接退订换号，不消耗 Push Token。
              若服务端仍返回 <code>SentCodeTypeApp</code> 会自动探测 <code>ResendCode</code> 并快速换号。
              RECAPTCHA_CHECK 由 REGHelp RecaptchaMobile 自动解题。
              可与下方「猎号」叠加：每路任务各自试号并复用自己的 Push；每路会被钉死一个代理槽位，猎号期间不轮换出口。
            </p>
          </div>
        </div>

        <div v-if="launchMode === 'auto'" class="ce-panel" style="padding:12px">
          <div class="between">
            <label class="ce-check">
              <input type="checkbox" v-model="huntMode" />
              猎号（最多试 N 个号 · 成功即停 · 不可用号拉黑）
            </label>
            <span class="ce-badge is-warn">hunt</span>
          </div>
          <div v-if="huntMode" class="stack" style="margin-top:10px">
            <div>
              <div class="between">
                <div class="ce-label">每任务最多试号个数</div>
                <button class="ce-link" @click="resetHuntAttemptsToConfig">跟随全局默认</button>
              </div>
              <div class="ce-seg">
                <button
                  v-for="n in [50, 100, 200, 500]"
                  :key="n"
                  :class="{ 'is-on': effectiveHuntAttempts === n }"
                  @click="setHuntAttempts(n)"
                >{{ n }}</button>
              </div>
              <div class="ce-tiny" style="margin-top:6px">
                当前 <span class="mono" style="color:var(--mint-light)">{{ effectiveHuntAttempts }}</span>
                个号 · 默认值取自设置页
                <code>hunt_default_max_attempts</code>（{{ config.hunt_default_max_attempts || 100 }}）
              </div>
            </div>
            <div class="ce-alert" :class="huntPlan.clamped ? 'is-warn' : ''">
              <div class="ce-stat">
                <span>预计最多租号次数</span>
                <span class="mono">
                  {{ huntPlan.count }} 路 × {{ huntPlan.attempts }} 个号 = {{ huntPlan.plannedLeases }} 次
                </span>
              </div>
              <div class="ce-tiny" style="margin-top:4px">
                联合上限 <code>hunt_max_total_leases</code> = {{ huntPlan.limit }}。
                <template v-if="huntPlan.clamped">
                  当前 {{ huntPlan.count }} × {{ huntPlan.requested }} 超过上限，后端会把每任务试号数裁剪到 {{ huntPlan.attempts }}。
                </template>
                <template v-else>
                  这是上限而非预期消费：成功即停、未收到码的号退订后多数平台不计费。
                </template>
              </div>
              <div class="row-wrap" style="margin-top:6px">
                <button class="ce-btn-ghost" :disabled="isLoadingHuntBudget" @click="refreshHuntBudget">
                  {{ isLoadingHuntBudget ? '查询中...' : '查接码余额与费用上限' }}
                </button>
                <span v-if="huntBudget && huntBudget.balance != null" class="ce-badge is-info mono">
                  余额 {{ huntBudget.balance }}
                </span>
                <span v-if="huntBudget && huntBudget.estimated_max_cost != null" class="mono ce-muted">
                  最多花费 ≈ {{ huntBudget.estimated_max_cost }}
                </span>
                <span
                  v-if="huntBudget && huntBudget.balance_sufficient === false"
                  class="ce-badge is-danger"
                >余额可能不足</span>
                <span v-if="huntBudget && huntBudget.balance_error" class="ce-tiny" style="color:var(--danger-soft)">
                  余额查询失败
                </span>
              </div>
            </div>
            <p class="ce-tiny">
              目标只有两个：① 注册成功立刻停止；② 在试号次数内尽量扫号，不可用号（站内信 APP / 已注册 / 封禁）
              拉黑后退订换号。这不是「扫平台所有号码」——最多只试 {{ effectiveHuntAttempts }} 个号，用完即结束（HUNT_EXHAUSTED）。
              无库存软重试 {{ config.hunt_no_number_retries ?? 20 }} 次（全局配置）。
              设备每 {{ config.hunt_device_max_uses || 8 }} 次 sendCode 换指纹并换新 Push（Push 与设备绑定）。
            </p>
            <p class="ce-tiny">
              <span :class="huntProxyPinned ? 'ce-badge is-warn' : 'ce-badge is-info'">
                {{ huntProxyPinned ? '出口不轮换' : '出口按次轮换' }}
              </span>
              {{ huntProxyNote }}。
            </p>
            <p class="ce-tiny">
              运行中可在下方任务队列点「停止」：取消请求在下一轮取号前生效，当前一轮的 sendCode / OTP 轮询会跑完并正常退订退款。
            </p>
          </div>
        </div>

        <div v-if="launchMode === 'auto'" class="ce-alert" :class="phonePrecheckStatus.active ? 'is-ok' : (phonePrecheckStatus.degraded ? 'is-warn' : '')">
          <div class="between">
            <strong>🛰️ 号码注册状态预检探测</strong>
            <span :class="phonePrecheckStatus.active ? 'ce-badge is-success' : 'ce-badge is-warn'">
              {{ phonePrecheckStatus.active ? '已激活' : (phonePrecheckStatus.enabled === false ? '已关闭' : '降级') }}
            </span>
          </div>
          <p class="ce-tiny" style="margin-top:6px">{{ phonePrecheckStatus.message || '正在探测本地授权 session…' }}</p>
          <div v-if="phonePrecheckStatus.probe_count" class="mono ce-muted">
            探测源 {{ phonePrecheckStatus.probe_count }} 个
            <span v-if="phonePrecheckStatus.probe_phones?.length"> · {{ phonePrecheckStatus.probe_phones.join(' / ') }}</span>
          </div>
          <div v-if="phonePrecheckStatus.precheck_proxy?.addr" class="ce-tiny" style="margin-top:6px">
            预检出口
            <span class="mono">{{ phonePrecheckStatus.precheck_proxy.proxy_type }}://{{ phonePrecheckStatus.precheck_proxy.addr }}:{{ phonePrecheckStatus.precheck_proxy.port }}</span>
            <span class="ce-badge is-warn">{{ phonePrecheckStatus.precheck_proxy.role || 'all' }}</span>
          </div>
        </div>

        <button
          v-if="launchMode === 'auto'"
          class="ce-btn"
          style="width:100%;padding:11px"
          :disabled="isStartingTask"
          @click="startRegistrationTask"
        >
          <span v-if="isStartingTask">正在调度状态机编排流水线...</span>
          <span v-else-if="huntMode && batchMode">并发 {{ batchCount }} 路猎号 × 每路最多 {{ huntPlan.attempts }} 号（最多租号 {{ huntPlan.plannedLeases }} 次）</span>
          <span v-else-if="huntMode">开始猎号（最多试 {{ huntPlan.attempts }} 个号 · 成功即停）</span>
          <span v-else-if="batchMode">并发启动 {{ batchCount }} 个引导任务</span>
          <span v-else>启动虚拟节点引导仿真</span>
        </button>
        <div v-if="launchMode === 'auto' && startError" class="ce-alert is-danger">{{ startError }}</div>

        <div v-if="launchMode === 'manual'" class="stack">
          <button
            class="ce-btn"
            style="width:100%;padding:11px"
            :disabled="isSendingCode || !String(manualPhone || '').trim() || isManualWaiting"
            @click="startManualRegistration"
          >
            <span v-if="isSendingCode">正在握手并发码...</span>
            <span v-else>🚀 发送登录/注册验证码</span>
          </button>

          <div v-if="isManualWaiting && manualSession" class="ce-alert ce-code-card stack">
            <div class="between">
              <strong>验证码输入与核验</strong>
              <span :class="deliveryBadgeClass">{{ manualSession.delivery_type || '等待分发' }}</span>
            </div>
            <div class="ce-tiny">
              当前号码 <span class="mono">{{ manualSession.phone }}</span>
              <span v-if="manualSession.expires_at"> · 有效至 {{ formatTime(manualSession.expires_at) }}</span>
            </div>
            <div>
              <label class="ce-label">短信 / 客户端验证码</label>
              <input
                v-model="manualCode"
                class="ce-input mono"
                placeholder="输入验证码后回车提交"
                autocomplete="one-time-code"
                @keydown="onManualCodeKeydown"
              />
            </div>
            <div>
              <label class="ce-label">已有账号 2FA 口令（可选）</label>
              <input
                v-model="manualPassword"
                class="ce-input mono"
                type="password"
                placeholder="旧号已开 2FA 时填写"
              />
            </div>
            <div class="row-wrap">
              <button
                class="ce-btn"
                :disabled="isSubmittingCode || isCancelingManual || !String(manualCode || '').trim()"
                @click="submitManualCode"
              >
                {{ isSubmittingCode ? '正在提交...' : '🟢 提交验证码完成注册' }}
              </button>
              <button
                class="ce-btn-danger"
                :disabled="isCancelingManual || isSubmittingCode"
                @click="cancelManualTask"
              >
                {{ isCancelingManual ? '取消中...' : '❌ 取消任务' }}
              </button>
            </div>
          </div>

          <div v-if="manualSuccess" class="ce-alert is-ok ce-success-card stack">
            <strong>手动注册成功</strong>
            <div class="ce-stat"><span>UID</span><span class="mono">{{ manualSuccess.user_id || '-' }}</span></div>
            <div class="ce-stat"><span>号码</span><span class="mono">{{ manualSuccess.phone || '-' }}</span></div>
            <div class="ce-stat"><span>Session</span><span class="mono">{{ manualSuccess.session_file || '-' }}</span></div>
            <div class="ce-stat"><span>账号类型</span><span>{{ manualSuccess.account_kind || '-' }}</span></div>
            <button class="ce-link" @click="goVaultFromManual">跳转凭证库查看</button>
          </div>
        </div>
        <div v-if="launchMode === 'manual' && manualError" class="ce-alert is-danger">{{ manualError }}</div>

        <div v-if="!config.custom_api_id" class="ce-alert is-warn">
          尚未配置专属 <code>custom_api_id</code>。本地 lod_user 只有 JSON、没有 .session，不能直接当开发者凭证。
          请到「凭证库 & 开发者 API」申请，或到「参数拓扑」手填已有 api_id/hash。
          <button class="ce-link" @click="goTab('vault')">立即前往</button>
        </div>
      </div>

      <div class="ce-panel ce-terminal-shell" :class="{ 'is-expanded': terminalExpanded }">
        <div class="ce-panel-head">
          <div class="row">
            <span class="ce-dot is-ok"></span>
            <h3>实时状态机审计日志终端</h3>
            <span v-if="activeTask && !mergedLogView" class="ce-muted mono">任务 {{ activeTask.task_id }}</span>
            <span v-else-if="mergedLogView && currentBatch" class="ce-muted mono">批次 {{ currentBatch.batch_id }}</span>
          </div>
          <div class="row-wrap">
            <button v-if="currentBatch" class="ce-btn-ghost" @click="mergedLogView = !mergedLogView">
              {{ mergedLogView ? '单任务日志' : '合并批次日志' }}
            </button>
            <span v-if="activeTask && !mergedLogView" :class="getStatusBadgeClass(activeTask.status)">{{ (activeTask.status || '').toUpperCase() }}</span>
            <button class="ce-btn-ghost" @click="clearActiveLogs">清屏</button>
            <button class="ce-btn-ghost" @click="terminalExpanded = !terminalExpanded">{{ terminalExpanded ? '还原' : '全屏' }}</button>
          </div>
        </div>

        <div v-if="currentBatch" class="row-wrap ce-tiny" style="margin-bottom:8px">
          <span class="ce-muted">批次 {{ currentBatch.batch_id }}</span>
          <span class="ce-badge is-info">{{ currentBatch.count }} 任务 / 并发 {{ currentBatch.concurrency }}</span>
          <span style="color:var(--mint)">成功 {{ batchStats.success }}</span>
          <span style="color:var(--cyan)">运行 {{ batchStats.running }}</span>
          <span style="color:var(--danger-soft)">失败 {{ batchStats.failed }}</span>
          <span class="ce-muted">等待 {{ batchStats.pending }}</span>
          <span v-if="batchStats.precheck" style="color:#fcd34d">预检拦截 {{ batchStats.precheck }}</span>
          <span v-if="batchStats.noNumber" style="color:#fdba74">无库存 {{ batchStats.noNumber }}</span>
          <button
            v-if="batchStats.running || batchStats.pending"
            class="ce-link is-danger"
            :disabled="isCancelingBatch"
            @click="cancelBatch(currentBatch.batch_id)"
          >
            {{ isCancelingBatch ? '停止中...' : '停止整个批次' }}
          </button>
        </div>
        <div v-if="currentBatch" class="ce-task-pills">
          <button
            v-for="tid in currentBatch.task_ids"
            :key="tid"
            class="ce-pill"
            :class="{ 'is-on': activeTask?.task_id === tid }"
            @click="focusBatchTask(tid)"
          >{{ tid }}</button>
        </div>

        <div ref="terminalRef" class="ce-terminal">
          <div v-if="displayLogs.length === 0" class="ce-log is-empty">
            暂无活跃的状态机运行日志，点击左侧启动调度流水线...
          </div>
          <div v-for="(log, idx) in displayLogs" :key="idx" :class="classifyLogLine(log)">{{ log }}</div>
        </div>
      </div>
    </div>

    <div class="grid-queue">
      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>📜 节点引导任务队列</h3>
          <div class="row-wrap">
            <button class="ce-btn-ghost" :disabled="!currentBatch" @click="taskFilter = taskFilter === 'batch' ? 'all' : 'batch'">
              {{ taskFilter === 'batch' ? '显示全部' : '仅看本批次' }}
            </button>
            <button class="ce-btn-ghost" @click="toggleSelectVisibleTasks">{{ allVisibleSelected ? '取消全选' : '全选可见' }}</button>
            <button class="ce-btn-ghost" :disabled="selectedTaskIds.length === 0" @click="viewSelectedLogs">查看选中日志</button>
            <button class="ce-link" @click="fetchTasks">刷新列表</button>
          </div>
        </div>
        <div class="ce-table-wrap">
          <table class="ce-table">
            <thead>
              <tr>
                <th></th>
                <th>任务 ID</th>
                <th>批次</th>
                <th>阶段</th>
                <th>句柄</th>
                <th>UID</th>
                <th>耗时</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="visibleTaskList.length === 0">
                <td colspan="8" class="ce-muted" style="text-align:center;padding:20px">队列为空，暂无历史任务</td>
              </tr>
              <tr
                v-for="t in visibleTaskList"
                :key="t.task_id"
                :class="{ 'is-on': currentBatch?.task_ids?.includes(t.task_id) }"
              >
                <td>
                  <input type="checkbox" :checked="selectedTaskIds.includes(t.task_id)" @change="toggleTaskSelection(t.task_id)" />
                </td>
                <td class="mono" style="color:var(--mint)">{{ t.task_id }}</td>
                <td class="mono ce-muted">{{ t.batch_id || '-' }}</td>
                <td>
                  <span :class="getStatusBadgeClass(t.status)">{{ t.status }}</span>
                  <span v-if="t.mode === 'manual'" class="ce-badge is-info">手动</span>
                  <span v-if="t.hunt_max > 1" class="ce-badge is-warn mono">
                    hunt {{ t.hunt_attempt || 0 }}/{{ t.hunt_max }}
                  </span>
                  <span v-if="t.cancel_requested && !isTerminalStatus(t.status)" class="ce-badge is-warn">停止中</span>
                  <span v-if="t.precheck_intercepted" class="ce-badge is-warn">预检拦截</span>
                </td>
                <td class="mono">{{ t.phone || '-' }}</td>
                <td class="mono">{{ t.user_id || '-' }}</td>
                <td class="mono">{{ formatDuration(t.created_at, t.updated_at) }}</td>
                <td class="row-wrap">
                  <button class="ce-link" @click="viewTaskLogs(t)">日志</button>
                  <button class="ce-link is-cyan" @click="openTaskDetail(t)">详情</button>
                  <button v-if="t.status === 'failed' || t.status === 'filtered'" class="ce-link" @click="retryTask(t)">重试</button>
                  <button
                    v-if="t.mode === 'manual' && (t.status === 'waiting_code' || t.status === 'logging_in')"
                    class="ce-link is-danger"
                    :disabled="isCancelingTaskId(t.task_id)"
                    @click="cancelManualTaskById(t.task_id)"
                  >
                    {{ isCancelingTaskId(t.task_id) ? '取消中...' : '取消' }}
                  </button>
                  <button
                    v-else-if="isCancelableTask(t)"
                    class="ce-link is-danger"
                    :disabled="isCancelingAutoTask(t.task_id) || t.cancel_requested"
                    @click="cancelTask(t.task_id)"
                  >
                    {{ isCancelingAutoTask(t.task_id) ? '停止中...' : (t.cancel_requested ? '已请求停止' : '停止') }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>💾 Session 上下文资产库</h3>
          <button class="ce-link" @click="fetchSessions">刷新</button>
        </div>
        <div class="ce-list">
          <div v-if="sessions.length === 0" class="ce-muted" style="text-align:center;padding:24px">持久化存储区暂无 .session 快照凭证</div>
          <div v-for="s in sessions" :key="s.filename" class="ce-item">
            <div>
              <div class="mono">{{ s.filename }}</div>
              <div class="ce-muted">{{ s.created_at }}</div>
            </div>
            <span class="ce-badge is-info">{{ s.size_kb }} KB</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="detailTask" class="ce-modal-mask" @click.self="detailTask = null">
      <div class="ce-modal stack">
        <div class="between">
          <h3>任务详情 {{ detailTask.task_id }}</h3>
          <button class="ce-btn-ghost" @click="detailTask = null">关闭</button>
        </div>
        <div class="row-wrap">
          <span :class="getStatusBadgeClass(detailTask.status)">{{ detailTask.status }}</span>
          <span class="ce-badge is-info">{{ detailTask.batch_id || (detailTask.mode === 'manual' ? '手动单号' : '单次任务') }}</span>
          <span v-if="detailTask.delivery_type" class="ce-badge is-info">{{ detailTask.delivery_type }}</span>
          <span v-if="detailTask.precheck_intercepted" class="ce-badge is-warn">预检拦截</span>
        </div>
        <div class="ce-stat"><span>通信句柄</span><span>{{ detailTask.phone || '-' }}</span></div>
        <div class="ce-stat"><span>节点 UID</span><span>{{ detailTask.user_id || '-' }}</span></div>
        <div v-if="detailTask.session_file" class="ce-stat"><span>Session</span><span>{{ detailTask.session_file }}</span></div>
        <div class="ce-stat"><span>预检 UID</span><span>{{ detailTask.precheck_user_id || '-' }}</span></div>
        <template v-if="detailTask.hunt_max > 1">
          <div class="ce-stat">
            <span>猎号进度</span>
            <span class="mono">第 {{ detailTask.hunt_attempt || 0 }} / {{ detailTask.hunt_max }} 个号</span>
          </div>
          <div class="ce-stat">
            <span>已扫 / 已拉黑</span>
            <span class="mono">{{ detailTask.hunt_scanned ?? 0 }} / {{ detailTask.hunt_blacklisted ?? 0 }}</span>
          </div>
          <div v-if="detailTask.hunt_last_reason" class="ce-stat">
            <span>最后失败原因</span><span class="mono">{{ detailTask.hunt_last_reason }}</span>
          </div>
        </template>
        <div v-if="detailTask.cancel_requested" class="ce-alert is-warn">
          已收到停止请求：不再开新一轮取号，当前一轮结束后收尾退款。
        </div>
        <div class="ce-stat"><span>创建 / 更新</span><span>{{ formatTime(detailTask.created_at) }} · {{ formatDuration(detailTask.created_at, detailTask.updated_at) }}</span></div>
        <div v-if="detailTask.error" class="ce-alert is-danger">{{ detailTask.error }}</div>
        <div class="ce-terminal" style="min-height:180px">
          <div v-for="(log, idx) in (detailTask.logs || [])" :key="idx" :class="classifyLogLine(log)">{{ log }}</div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { APP_TYPE_OPTIONS, COUNTRY_GROUPS, countryFlag, classifyLogLine, getStatusBadgeClass, formatDuration, formatTime } from '../../composables/useShared'
import LiveStockCountryPicker from './LiveStockCountryPicker.vue'
import { useConfig } from '../../composables/useConfig'
import { useProxy } from '../../composables/useProxy'
import { useTasks } from '../../composables/useTasks'
import { useUi } from '../../composables/useUi'
import { useManualRegister } from '../../composables/useManualRegister'

const { config, form, smsProviderLabel } = useConfig()
const {
  matchedProxy, proxyPool, customProxiesForCountry, customProxySummaryText,
  registrationProxies, roleLabel, testing: proxyTesting, refreshProxyPool, previewAutoSelect
} = useProxy()
const {
  batchMode, batchCount, batchConcurrency, huntMode, currentBatch, taskFilter, selectedTaskIds, mergedLogView,
  effectiveHuntAttempts, setHuntAttempts, resetHuntAttemptsToConfig, huntPlan, huntProxyPinned, huntProxyNote,
  huntBudget, isLoadingHuntBudget, refreshHuntBudget,
  isStartingTask, startError, activeTask, sessions, terminalRef, phonePrecheckStatus,
  effectiveConcurrency, visibleTaskList, allVisibleSelected, batchStats, displayLogs,
  isCancelingBatch, isCancelableTask, isCancelingAutoTask, cancelTask, cancelBatch,
  fetchTasks, fetchSessions, startRegistrationTask, viewTaskLogs, toggleTaskSelection,
  toggleSelectVisibleTasks, viewSelectedLogs, focusBatchTask, clearActiveLogs, retryTask, openTaskDetail
} = useTasks()

const TERMINAL_TASK_STATUSES = ['success', 'failed', 'filtered', 'canceled']
const isTerminalStatus = (status) => TERMINAL_TASK_STATUSES.includes(String(status || ''))
const { terminalExpanded, detailTask, goTab } = useUi()
const {
  launchMode, manualPhone, manualCountry, manualCode, manualPassword,
  isSendingCode, isSubmittingCode, isCancelingManual, manualError,
  manualSession, manualSuccess, isManualWaiting, deliveryBadgeClass,
  startManualRegistration, submitManualCode, cancelManualTask,
  cancelManualTaskById, isCancelingTaskId,
  goVaultFromManual, onManualCodeKeydown
} = useManualRegister()

const effectiveMaxPrice = computed(() => {
  const taskBid = Number(form.max_price)
  if (Number.isFinite(taskBid) && taskBid > 0) return taskBid
  const cfgBid = Number(config.sms_max_price)
  if (Number.isFinite(cfgBid) && cfgBid > 0) return cfgBid
  return null
})

const healthClass = (healthy) => {
  if (healthy === true) return 'ce-badge is-success'
  if (healthy === false) return 'ce-badge is-danger'
  return 'ce-muted'
}
const healthText = (p) => {
  if (p.healthy === true) return p.latency_ms != null ? `${p.latency_ms}ms` : '通'
  if (p.healthy === false) return '断'
  return '待测'
}
</script>
