<template>
  <div class="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col">
    <!-- 顶部导航栏 -->
    <header class="border-b border-zinc-800/80 bg-zinc-900/60 backdrop-blur-md sticky top-0 z-50 px-6 py-3.5 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 to-cyan-500 flex items-center justify-center font-bold text-white shadow-lg shadow-blue-500/20 text-xs">
          ENA
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="font-bold text-base tracking-tight text-white">EdgeNode-Auditor Console</h1>
            <span class="badge badge-info text-xs">v2.2 Enterprise</span>
          </div>
          <p class="text-xs text-zinc-400">分布式边缘节点状态机仿真、动态出口路由与密码学上下文审计系统</p>
        </div>
      </div>

      <!-- 标签页导航 -->
      <div class="flex items-center bg-zinc-900 p-1 rounded-lg border border-zinc-800">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="activeTab = tab.id"
          :class="[
            'px-3.5 py-1.5 rounded-md text-xs font-medium transition-all flex items-center gap-1.5',
            activeTab === tab.id
              ? 'bg-blue-600 text-white shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          ]"
        >
          <span>{{ tab.icon }}</span>
          <span>{{ tab.name }}</span>
        </button>
      </div>

      <!-- 右侧状态指示器 -->
      <div class="flex items-center gap-3">
        <div class="flex items-center gap-2 text-xs px-3 py-1.5 rounded-md bg-zinc-900 border border-zinc-800">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          <span class="text-zinc-300">仿真审计引擎就绪: 8000</span>
        </div>
      </div>
    </header>

    <!-- 主体内容 -->
    <main class="flex-1 max-w-7xl w-full mx-auto p-6">
      
      <!-- ================= 标签页 1: 状态机编排与控制台 ================= -->
      <div v-if="activeTab === 'console'" class="space-y-6">
        
        <!-- 上方快速执行卡片与实时状态 -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          <!-- 任务发起面板 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-4">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
              <h2 class="font-semibold text-sm text-zinc-100 flex items-center gap-2">
                <span>⚡</span> 发起边缘节点引导与握手仿真
              </h2>
              <span class="text-xs text-zinc-400">Chile 拓扑基线测试</span>
            </div>

            <!-- 选择国家与拓扑 -->
            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">目标拓扑与地理区域 (GEO Topology)</label>
              <select v-model="form.country" class="input-field">
                <option value="cl">🇨🇱 智利 (Chile, +56) - 推荐基线拓扑</option>
                <option value="in">🇮🇳 印度 (India, +91) - 专属住宅池</option>
                <option value="id">🇮🇩 印尼 (Indonesia, +62)</option>
                <option value="af">🇦🇫 阿富汗 (Afghanistan, +93)</option>
                <option value="kz">🇰🇿 哈萨克斯坦 (Kazakhstan, +7)</option>
                <option value="ru">🇷🇺 俄罗斯 (Russia, +7)</option>
                <option value="br">🇧🇷 巴西 (Brazil, +55)</option>
                <option value="us">🇺🇸 美国 (USA, +1)</option>
              </select>
            </div>

            <!-- 选择客户端模板 -->
            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">端点协议模板与 Attestation 凭证</label>
              <select v-model="form.app_type" class="input-field">
                <option value="telegram_android">📱 MTProto Android (官方主版 SDK 33 / AID: 308a...)</option>
                <option value="telegram_x">⚡ MTProto TDLib (官方极速版 / AID: 47f7...)</option>
                <option value="telegram_9">🕰️ MTProto Legacy (经典稳定版 SDK 32 / AID: 59e5...)</option>
              </select>
            </div>

            <!-- 代理模式 -->
            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">多径出站中继网关 (Multipath Egress)</label>
              <div v-if="config.use_proxy_seller_auto" class="p-2.5 rounded-lg bg-blue-950/30 border border-blue-800/60 text-xs text-blue-200 space-y-2">
                <div class="flex items-center justify-between">
                  <div>
                    <div class="font-medium">多径中继网关 API 自动分配</div>
                    <div class="text-[11px] text-zinc-400">自动分配目标区域 ({{ form.country.toUpperCase() }}) SOCKS5 中继跳点</div>
                  </div>
                  <span class="badge badge-info">动态拓扑路由</span>
                </div>
                <div v-if="matchedProxy" class="p-2 rounded-md bg-emerald-950/40 border border-emerald-800/50 text-[11px] text-emerald-100">
                  当前国家自动分配:
                  <span class="font-mono">{{ (matchedProxy.proxy_type || 'socks5') }}://{{ matchedProxy.addr }}:{{ matchedProxy.port }}</span>
                  <span class="ml-1 text-emerald-300">[{{ (matchedProxy.country_code || matchedProxy.country || form.country).toString().toUpperCase() }}]</span>
                </div>
                <div class="flex items-center gap-2">
                  <button @click="refreshProxyPool(form.country, true)" :disabled="testing.proxypool" class="btn-secondary text-[11px] py-1 px-2">
                    {{ testing.proxypool ? '刷新中...' : '🔄 自动从 API 刷新区域代理池' }}
                  </button>
                  <button @click="previewAutoSelect(form.country)" :disabled="testing.autoselect" class="text-[11px] text-blue-300 hover:underline">
                    查看当前国家分配
                  </button>
                </div>
                <div v-if="proxyPool.length" class="max-h-28 overflow-y-auto space-y-1">
                  <div
                    v-for="(p, idx) in proxyPool.slice(0, 6)"
                    :key="p.id || (p.addr + ':' + p.port + idx)"
                    class="flex items-center justify-between gap-2 px-2 py-1 rounded bg-zinc-950/60 border border-zinc-800/80 text-[11px]"
                  >
                    <span class="badge badge-info">{{ (p.country_code || p.country || '?').toString().toUpperCase() }}</span>
                    <span class="font-mono text-zinc-200 truncate">{{ p.addr }}:{{ p.port }}</span>
                    <span class="text-zinc-400 uppercase">{{ p.proxy_type }}</span>
                    <span :class="p.healthy === true ? 'text-emerald-400' : (p.healthy === false ? 'text-red-400' : 'text-zinc-500')">
                      {{ p.healthy === true ? '通' : (p.healthy === false ? '断' : '待测') }}
                    </span>
                  </div>
                </div>
                <div class="p-2 rounded-md bg-zinc-950/50 border border-zinc-800/80 space-y-1.5">
                  <div class="flex items-center justify-between text-[11px]">
                    <span class="text-zinc-300">📋 自建代理池</span>
                    <span class="text-zinc-400">{{ customProxySummaryText }}</span>
                  </div>
                  <div v-if="customProxiesForCountry.length" class="space-y-1">
                    <div
                      v-for="(p, idx) in customProxiesForCountry.slice(0, 4)"
                      :key="p.id || (p.addr + ':' + p.port + idx)"
                      class="flex items-center justify-between gap-2 text-[11px]"
                    >
                      <span>{{ countryFlag(p.country_code) }} {{ (p.country_code || '?').toString().toUpperCase() }}</span>
                      <span class="font-mono text-zinc-200 truncate">{{ p.addr }}:{{ p.port }}</span>
                      <span :class="p.healthy === true ? 'text-emerald-400' : (p.healthy === false ? 'text-red-400' : 'text-zinc-500')">
                        {{ p.healthy === true ? (p.latency_ms != null ? p.latency_ms + 'ms' : '通') : (p.healthy === false ? '断' : '待测') }}
                      </span>
                    </div>
                  </div>
                  <div v-else class="text-[11px] text-zinc-500">当前国家暂无已标注的自建节点，可到「参数拓扑」批量粘贴导入</div>
                </div>
              </div>
              <div v-else class="p-2.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 space-y-2">
                <div class="flex items-center justify-between">
                  <div>
                    <div class="font-medium text-zinc-200">{{ config.fallback_proxy.addr }}:{{ config.fallback_proxy.port }}</div>
                    <div class="text-[11px] text-zinc-500">传输: {{ config.fallback_proxy.proxy_type.toUpperCase() }} (静态后备跳点)</div>
                  </div>
                  <span class="badge badge-warning">静态中继</span>
                </div>
                <div class="pt-1 border-t border-zinc-800 text-[11px] text-zinc-400">
                  📋 自建代理池 {{ customProxySummaryText }}
                  <span v-if="customProxiesForCountry.length" class="ml-1 text-zinc-300">
                    · 当前国家可匹配 {{ customProxiesForCountry.length }} 条
                  </span>
                </div>
              </div>
            </div>

            <!-- 并发批量引导模式 -->
            <div class="p-2.5 rounded-lg bg-zinc-900/80 border border-zinc-800 space-y-2.5">
              <div class="flex items-center justify-between">
                <label class="flex items-center gap-2 text-xs text-zinc-200 cursor-pointer">
                  <input type="checkbox" v-model="batchMode" class="rounded bg-zinc-900 border-zinc-700" />
                  并发批量引导模式
                </label>
                <span class="badge badge-info text-[10px]">asyncio.Semaphore</span>
              </div>
              <div v-if="batchMode" class="space-y-2">
                <div>
                  <div class="text-[11px] text-zinc-400 mb-1">并行任务数</div>
                  <div class="flex items-center gap-1.5">
                    <button
                      v-for="n in [1, 3, 5, 10]"
                      :key="n"
                      @click="batchCount = n"
                      :class="[
                        'px-2.5 py-1 rounded-md text-[11px] border',
                        batchCount === n
                          ? 'bg-blue-600 text-white border-blue-500'
                          : 'bg-zinc-950 text-zinc-300 border-zinc-800 hover:border-zinc-600'
                      ]"
                    >{{ n }} 个</button>
                  </div>
                </div>
                <div>
                  <label class="text-[11px] text-zinc-400 mb-1 block">并发度 concurrency (1~{{ batchCount }})</label>
                  <input
                    type="range"
                    min="1"
                    :max="Math.max(1, batchCount)"
                    v-model.number="batchConcurrency"
                    class="w-full accent-blue-500"
                  />
                  <div class="flex justify-between text-[10px] text-zinc-500">
                    <span>串行 1</span>
                    <span class="text-blue-300 font-mono">{{ effectiveConcurrency }} 并行槽</span>
                    <span>{{ batchCount }}</span>
                  </div>
                </div>
                <p class="text-[11px] text-zinc-500 leading-relaxed">
                  等待 OTP 期间可并行验证多个号码。若服务端返回 <code class="font-mono">SentCodeTypeApp</code> 会自动探测 <code class="font-mono">ResendCode</code> 并快速换号，避免空等 120 秒。
                </p>
              </div>
            </div>

            <!-- 启动按钮 -->
            <button
              @click="startRegistrationTask"
              :disabled="isStartingTask"
              class="w-full btn-primary py-2.5 font-semibold text-sm shadow-lg shadow-blue-600/20"
            >
              <span v-if="isStartingTask">正在调度状态机编排流水线...</span>
              <span v-else-if="batchMode">🚀 并发启动 {{ batchCount }} 个引导任务</span>
              <span v-else>🚀 启动虚拟节点引导仿真</span>
            </button>

            <div v-if="!config.custom_api_id" class="p-2.5 rounded-lg bg-amber-950/30 border border-amber-800/50 text-[11px] text-amber-100 leading-relaxed">
              尚未配置专属 <code class="font-mono">custom_api_id</code>。
              本地 lod_user 只有 JSON、没有 .session，不能直接当开发者凭证。
              请到「🔐 凭证库 / 开发者 API」：用已登录客户端收 777000 验证码申请，或放入 <code class="font-mono">*.session</code>，或到「⚙️ 参数拓扑」手填已有 api_id/hash。
              <button @click="activeTab = 'vault'" class="ml-1 underline text-amber-50">立即前往</button>
            </div>
          </div>

          <!-- 实时日志终端 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 lg:col-span-2 flex flex-col h-[340px]">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2 mb-3">
              <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
                <h3 class="font-semibold text-sm text-zinc-200">实时状态机审计日志终端 (State Machine Audit)</h3>
                <span v-if="activeTask && !mergedLogView" class="text-xs font-mono text-zinc-400">(任务: {{ activeTask.task_id }})</span>
                <span v-else-if="mergedLogView && currentBatch" class="text-xs font-mono text-zinc-400">(批次: {{ currentBatch.batch_id }})</span>
              </div>
              <div class="flex items-center gap-2">
                <button
                  v-if="currentBatch"
                  @click="mergedLogView = !mergedLogView"
                  class="text-[11px] px-2 py-1 rounded border border-zinc-800 text-zinc-400 hover:text-zinc-200"
                >
                  {{ mergedLogView ? '单任务日志' : '合并批次日志' }}
                </button>
                <span v-if="activeTask && !mergedLogView" :class="getStatusBadgeClass(activeTask.status)">
                  {{ activeTask.status.toUpperCase() }}
                </span>
                <button @click="clearActiveLogs" class="text-xs text-zinc-400 hover:text-zinc-200 px-2 py-1 bg-zinc-900 rounded border border-zinc-800">
                  清屏
                </button>
              </div>
            </div>

            <div v-if="currentBatch" class="mb-2 flex flex-wrap items-center gap-1.5">
              <span class="text-[11px] text-zinc-500">批次 {{ currentBatch.batch_id }}</span>
              <span class="badge badge-info text-[10px]">{{ currentBatch.count }} 任务 / 并发 {{ currentBatch.concurrency }}</span>
              <span class="text-[11px] text-emerald-400">成功 {{ batchStats.success }}</span>
              <span class="text-[11px] text-cyan-400">运行 {{ batchStats.running }}</span>
              <span class="text-[11px] text-red-400">失败 {{ batchStats.failed }}</span>
              <span class="text-[11px] text-zinc-500">等待 {{ batchStats.pending }}</span>
              <button
                v-for="tid in currentBatch.task_ids"
                :key="tid"
                @click="focusBatchTask(tid)"
                :class="[
                  'font-mono text-[10px] px-1.5 py-0.5 rounded border',
                  activeTask?.task_id === tid
                    ? 'border-blue-500 text-blue-300 bg-blue-950/40'
                    : 'border-zinc-800 text-zinc-400 hover:border-zinc-600'
                ]"
              >{{ tid }}</button>
            </div>

            <!-- 日志窗口 -->
            <div ref="terminalRef" class="flex-1 bg-zinc-950 p-3 rounded-lg border border-zinc-900 overflow-y-auto font-mono text-xs space-y-1">
              <div v-if="displayLogs.length === 0" class="text-zinc-600 italic py-10 text-center">
                暂无活跃的状态机运行日志，点击左侧「启动虚拟节点引导仿真」调度流水线...
              </div>
              <div
                v-for="(log, idx) in displayLogs"
                :key="idx"
                :class="[
                  'leading-relaxed break-all',
                  log.includes('🎉') || log.includes('成功') ? 'text-green-400 font-bold' : (
                    log.includes('❌') || log.includes('失败') || log.includes('异常') ? 'text-red-400 font-bold' : (
                      log.includes('[*]') || log.includes('探测') ? 'text-cyan-400' : 'text-zinc-300'
                    )
                  )
                ]"
              >
                {{ log }}
              </div>
            </div>
          </div>
        </div>

        <!-- 下方历史任务记录与 Session 资产 -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          <!-- 历史任务表格 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 lg:col-span-2 space-y-3">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <h3 class="font-semibold text-sm text-zinc-200">📜 节点引导任务队列 (Node Provisioning Queue)</h3>
              <div class="flex items-center gap-2">
                <button
                  @click="taskFilter = taskFilter === 'batch' ? 'all' : 'batch'"
                  :disabled="!currentBatch"
                  class="text-[11px] px-2 py-1 rounded border border-zinc-800 text-zinc-400 hover:text-zinc-200 disabled:opacity-40"
                >
                  {{ taskFilter === 'batch' ? '显示全部' : '仅看本批次' }}
                </button>
                <button
                  @click="toggleSelectVisibleTasks"
                  class="text-[11px] px-2 py-1 rounded border border-zinc-800 text-zinc-400 hover:text-zinc-200"
                >{{ allVisibleSelected ? '取消全选' : '全选可见' }}</button>
                <button
                  @click="viewSelectedLogs"
                  :disabled="selectedTaskIds.length === 0"
                  class="text-[11px] px-2 py-1 rounded border border-zinc-800 text-zinc-400 hover:text-zinc-200 disabled:opacity-40"
                >查看选中日志</button>
                <button @click="fetchTasks" class="text-xs text-blue-400 hover:underline">刷新列表</button>
              </div>
            </div>

            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs">
                <thead>
                  <tr class="text-zinc-500 border-b border-zinc-800/60 pb-2">
                    <th class="py-2 w-8"></th>
                    <th>任务 ID</th>
                    <th>批次</th>
                    <th>状态机阶段</th>
                    <th>通信句柄 (Handle)</th>
                    <th>节点 UID</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-zinc-800/40">
                  <tr v-if="visibleTaskList.length === 0">
                    <td colspan="8" class="py-4 text-center text-zinc-600">队列为空，暂无历史任务</td>
                  </tr>
                  <tr
                    v-for="t in visibleTaskList"
                    :key="t.task_id"
                    :class="[
                      'hover:bg-zinc-900/40 transition-colors',
                      currentBatch?.task_ids?.includes(t.task_id) ? 'bg-blue-950/10' : ''
                    ]"
                  >
                    <td class="py-2.5">
                      <input
                        type="checkbox"
                        :checked="selectedTaskIds.includes(t.task_id)"
                        @change="toggleTaskSelection(t.task_id)"
                        class="rounded bg-zinc-900 border-zinc-700"
                      />
                    </td>
                    <td class="font-mono text-blue-400 font-medium">{{ t.task_id }}</td>
                    <td class="font-mono text-zinc-500">{{ t.batch_id || '-' }}</td>
                    <td>
                      <span :class="getStatusBadgeClass(t.status)">{{ t.status }}</span>
                    </td>
                    <td class="font-mono text-zinc-300">{{ t.phone || '-' }}</td>
                    <td class="font-mono text-zinc-300">{{ t.user_id || '-' }}</td>
                    <td class="text-zinc-500">{{ formatTime(t.created_at) }}</td>
                    <td>
                      <button @click="viewTaskLogs(t)" class="text-blue-400 hover:text-blue-300 text-xs">审计日志</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- 密码学快照资产库 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-3">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <h3 class="font-semibold text-sm text-zinc-200">💾 密码学上下文快照 (Context Artifacts)</h3>
              <button @click="fetchSessions" class="text-xs text-blue-400 hover:underline">刷新</button>
            </div>

            <div class="space-y-2 max-h-[220px] overflow-y-auto">
              <div v-if="sessions.length === 0" class="text-center text-zinc-600 py-6 text-xs">
                持久化存储区暂无 .session 快照凭证
              </div>
              <div
                v-for="s in sessions"
                :key="s.filename"
                class="p-2.5 rounded-lg bg-zinc-900/70 border border-zinc-800 flex items-center justify-between text-xs"
              >
                <div>
                  <div class="font-mono text-zinc-200 font-medium">{{ s.filename }}</div>
                  <div class="text-[11px] text-zinc-500">{{ s.created_at }}</div>
                </div>
                <span class="badge badge-info text-[11px]">{{ s.size_kb }} KB</span>
              </div>
            </div>
          </div>

        </div>

      </div>

      <!-- ================= 标签页 2: 全局参数拓扑与接口审计 ================= -->
      <div v-if="activeTab === 'settings'" class="space-y-6">
        
        <div class="flex items-center justify-between border-b border-zinc-800 pb-4">
          <div>
            <h2 class="text-lg font-bold text-white">⚙️ 全局仿真参数拓扑 & 外部接口管理</h2>
            <p class="text-xs text-zinc-400">配置并一键执行 Attestation 凭证网关、带外遥测挑战源、多径中继网关等接口探针诊断</p>
          </div>
          <button @click="saveConfig" :disabled="isSavingConfig" class="btn-primary">
            <span v-if="isSavingConfig">正在保存...</span>
            <span v-else>💾 持久化全局配置</span>
          </button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
          
          <!-- REGHelp 高可用 Attestation/Push 凭证提供源卡片 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-4">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <div class="flex items-center gap-2">
                <span class="text-base">🛰️</span>
                <h3 class="font-semibold text-sm text-zinc-200">REGHelp 高可用 Push/Attestation 凭证提供源</h3>
                <span class="badge badge-success text-[10px]">主选</span>
              </div>
              <button @click="testRegHelp" :disabled="testing.reghelp" class="btn-secondary text-xs py-1">
                {{ testing.reghelp ? '测试中...' : '⚡ 余额/连通性探针' }}
              </button>
            </div>

            <div class="flex items-center gap-2">
              <input type="checkbox" id="reghelpEnabled" v-model="config.reghelp_enabled" class="rounded bg-zinc-900 border-zinc-700" />
              <label for="reghelpEnabled" class="text-xs text-zinc-300">启用 REGHelp 作为 Attestation / Push 凭证提供源</label>
            </div>

            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">REGHelp API Key (reghelp.net)</label>
              <input v-model="config.reghelp_api_key" type="password" class="input-field font-mono" placeholder="rh_live_... / w9vcrhw7..." />
            </div>

            <div>
              <label class="block text-[11px] text-zinc-400 mb-1">Key API 候选网关地址 (仅 reghelp.net，勿填入 antisafety.net)</label>
              <textarea v-model="reghelpBaseUrlsText" rows="2" class="input-field font-mono text-xs" placeholder="https://api.reghelp.net"></textarea>
              <p class="text-[11px] text-zinc-500 mt-1">与 AntiSafety 密钥/地址严格隔离：REGHelp 的 Key 只能打 api.reghelp.net。RECAPTCHA_CHECK 自动解题也只走这把 Key 与 RecaptchaMobile 接口。</p>
            </div>

            <div class="grid grid-cols-2 gap-2">
              <div>
                <label class="block text-[11px] text-zinc-400 mb-1">连接超时 (秒)</label>
                <input v-model.number="config.reghelp_connect_timeout" type="number" step="0.5" class="input-field font-mono text-xs" />
              </div>
              <div>
                <label class="block text-[11px] text-zinc-400 mb-1">总超时 (秒)</label>
                <input v-model.number="config.reghelp_total_timeout" type="number" step="1" class="input-field font-mono text-xs" />
              </div>
            </div>

            <div class="p-2.5 rounded-lg bg-zinc-900 border border-zinc-800 text-[11px] text-zinc-500 leading-relaxed">
              对接 REGHelp Key API (<a href="https://reghelp.net" target="_blank" class="underline text-zinc-300">reghelp.net</a> ·
              <a href="https://github.com/REGHELPNET/reghelp_client" target="_blank" class="underline text-zinc-300">开源客户端参考</a>)：
              GET <code class="text-zinc-300">/push/getToken</code> → 轮询 <code class="text-zinc-300">/push/getStatus</code>，
              appName/appDevice 与内置端点模板 (tg / tg_x) 天然对齐，无需额外 AID。
            </div>

            <!-- 测试结果反馈 -->
            <div v-if="testResults.reghelp" :class="['p-3 rounded-lg text-xs', testResults.reghelp.success ? 'bg-green-950/40 border border-green-800/60 text-green-300' : 'bg-red-950/40 border border-red-800/60 text-red-300']">
              <div>{{ testResults.reghelp.message }}</div>
            </div>
          </div>

          <!-- Attestation 挑战凭证生成器配置卡片 (AntiSafety) -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-4">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <div class="flex items-center gap-2">
                <span class="text-base">🛡️</span>
                <h3 class="font-semibold text-sm text-zinc-200">Attestation 挑战凭证生成器 (AntiSafety)</h3>
                <span class="badge badge-info text-[10px]">备选</span>
              </div>
              <button @click="testAntiSafety" :disabled="testing.antisafety" class="btn-secondary text-xs py-1">
                {{ testing.antisafety ? '测试中...' : '⚡ 连通性探针' }}
              </button>
            </div>

            <div class="flex items-center gap-2">
              <input type="checkbox" id="antisafetyEnabled" v-model="config.antisafety_enabled" class="rounded bg-zinc-900 border-zinc-700" />
              <label for="antisafetyEnabled" class="text-xs text-zinc-300">启用 AntiSafety 作为 Attestation / Push 凭证提供源</label>
            </div>

            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">Attestation API Key</label>
              <input v-model="config.antisafety_api_key" type="password" class="input-field font-mono" />
            </div>

            <div class="space-y-2 pt-1">
              <div class="text-xs font-medium text-zinc-400">已绑定的端点拓扑实例 AID：</div>
              <div>
                <label class="block text-[11px] text-zinc-500 mb-0.5">MTProto Android (主版)</label>
                <input v-model="config.antisafety_aids.telegram_android" type="text" class="input-field font-mono text-xs" />
              </div>
              <div>
                <label class="block text-[11px] text-zinc-500 mb-0.5">MTProto TDLib (极速版)</label>
                <input v-model="config.antisafety_aids.telegram_x" type="text" class="input-field font-mono text-xs" />
              </div>
              <div>
                <label class="block text-[11px] text-zinc-500 mb-0.5">MTProto Legacy (经典版)</label>
                <input v-model="config.antisafety_aids.telegram_9" type="text" class="input-field font-mono text-xs" />
              </div>
            </div>

            <!-- 测试结果反馈 -->
            <div v-if="testResults.antisafety" :class="['p-3 rounded-lg text-xs', testResults.antisafety.success ? 'bg-green-950/40 border border-green-800/60 text-green-300' : 'bg-red-950/40 border border-red-800/60 text-red-300']">
              {{ testResults.antisafety.message }}
            </div>
          </div>

          <!-- 带外遥测与挑战响应源卡片 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-4">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <div class="flex items-center gap-2">
                <span class="text-base">📩</span>
                <h3 class="font-semibold text-sm text-zinc-200">异步带外遥测与挑战响应源 (OOB Telemetry / Vak-SMS)</h3>
              </div>
              <button @click="testVakSms" :disabled="testing.vaksms" class="btn-secondary text-xs py-1">
                {{ testing.vaksms ? '测试中...' : '⚡ 状态探针' }}
              </button>
            </div>

            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">OOB Telemetry API Key</label>
              <input v-model="config.vak_sms_api_key" type="password" class="input-field font-mono" />
            </div>

            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">默认地理拓扑区域代码</label>
              <input v-model="config.target_country" type="text" placeholder="例如: cl, in, id, ru" class="input-field font-mono" />
            </div>

            <div class="p-3 rounded-lg bg-zinc-900 border border-zinc-800 text-xs text-zinc-400 space-y-1">
              <div>• 智利拓扑 (Chile): <code class="text-zinc-200">cl</code> (+56) · 内置静态住宅 10000-10004</div>
              <div>• 印度拓扑 (India): <code class="text-zinc-200">in</code> (+91) · 内置静态住宅 10000-10009</div>
              <div>• 印尼拓扑 (Indonesia): <code class="text-zinc-200">id</code> (+62)</div>
              <div>• 哈萨克斯坦拓扑 (Kazakhstan): <code class="text-zinc-200">kz</code> (+7)</div>
            </div>

            <!-- 测试结果反馈 -->
            <div v-if="testResults.vaksms" :class="['p-3 rounded-lg text-xs', testResults.vaksms.success ? 'bg-green-950/40 border border-green-800/60 text-green-300' : 'bg-red-950/40 border border-red-800/60 text-red-300']">
              <div>{{ testResults.vaksms.message }}</div>
              <div v-if="testResults.vaksms.data" class="font-mono mt-1 text-[11px]">
                可用配额点数: {{ testResults.vaksms.data.balance }} | 当前拓扑 ({{ testResults.vaksms.data.country }}) 信道容量: {{ testResults.vaksms.data.telegram_stock }}
              </div>
            </div>
          </div>

          <!-- 多径传输出口中继网关池卡片 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-4">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <div class="flex items-center gap-2">
                <span class="text-base">🌐</span>
                <h3 class="font-semibold text-sm text-zinc-200">多径传输出口中继网关池 (Multipath Relay / Proxy-Seller)</h3>
              </div>
              <div class="flex items-center gap-1.5">
                <button @click="refreshProxyPool(config.target_country, true)" :disabled="testing.proxypool" class="btn-secondary text-xs py-1">
                  {{ testing.proxypool ? '刷新中...' : '🔄 自动从 API 刷新区域代理池' }}
                </button>
                <button @click="testProxySeller" :disabled="testing.proxyseller" class="btn-secondary text-xs py-1">
                  {{ testing.proxyseller ? '测试中...' : '⚡ 拓扑发现' }}
                </button>
              </div>
            </div>

            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">Relay Provider API Key</label>
              <input v-model="config.proxy_seller_key" type="password" class="input-field font-mono" />
            </div>

            <div class="flex items-center gap-2">
              <input type="checkbox" id="autoProxy" v-model="config.use_proxy_seller_auto" class="rounded bg-zinc-900 border-zinc-700" />
              <label for="autoProxy" class="text-xs text-zinc-300">节点引导时自动分配与拓扑匹配的中继跳点</label>
            </div>

            <div class="flex items-center gap-2">
              <button @click="previewAutoSelect(config.target_country, false)" :disabled="testing.autoselect" class="btn-secondary text-xs py-1">
                {{ testing.autoselect ? '匹配中...' : '查看当前国家自动分配' }}
              </button>
              <button @click="previewAutoSelect(config.target_country, true)" :disabled="testing.autoselect" class="btn-secondary text-xs py-1">
                一键设为后备代理
              </button>
              <button @click="testAllProxySeller" :disabled="testing.proxyall" class="btn-secondary text-xs py-1">
                {{ testing.proxyall ? '测活中...' : '批量测活' }}
              </button>
            </div>

            <div v-if="proxyPoolMeta.message" :class="['p-2.5 rounded-lg text-[11px]', proxyPoolMeta.success === false ? 'bg-red-950/40 border border-red-800/60 text-red-300' : 'bg-zinc-900 border border-zinc-800 text-zinc-300']">
              {{ proxyPoolMeta.message }}
              <span v-if="proxyPoolMeta.available_countries?.length" class="ml-1 text-zinc-500">
                账户区域: {{ proxyPoolMeta.available_countries.join(', ') }}
              </span>
            </div>

            <div v-if="matchedProxy" class="p-2.5 rounded-lg bg-emerald-950/30 border border-emerald-800/50 text-[11px] text-emerald-100">
              当前 {{ (matchedProxy.country_code || config.target_country || '').toString().toUpperCase() }} 自动分配:
              <span class="font-mono">{{ matchedProxy.proxy_type }}://{{ matchedProxy.addr }}:{{ matchedProxy.port }}</span>
              <span v-if="matchedProxy.egress_ip" class="ml-1 text-zinc-400">出口 {{ matchedProxy.egress_ip }} {{ matchedProxy.egress_country || '' }}</span>
            </div>

            <div v-if="proxyPool.length" class="max-h-52 overflow-y-auto rounded-lg border border-zinc-800 divide-y divide-zinc-800/60">
              <div
                v-for="(p, idx) in proxyPool"
                :key="p.id || (p.addr + ':' + p.port + idx)"
                class="flex items-center justify-between gap-2 px-2.5 py-1.5 text-[11px]"
              >
                <div class="flex items-center gap-2 min-w-0">
                  <span class="badge badge-info shrink-0">{{ (p.country_code || p.country_alpha3 || p.country || '?').toString().toUpperCase() }}</span>
                  <span class="font-mono text-zinc-200 truncate">{{ p.addr }}:{{ p.port }}</span>
                  <span class="text-zinc-500 uppercase">{{ p.proxy_type }}</span>
                  <span :class="p.healthy === true ? 'text-emerald-400' : (p.healthy === false ? 'text-red-400' : 'text-zinc-500')">
                    {{ p.healthy === true ? '连通' : (p.healthy === false ? '失败' : '未知') }}
                  </span>
                </div>
                <button @click="setProxyAsFallback(p)" class="text-blue-400 hover:text-blue-300 shrink-0">一键设为后备代理</button>
              </div>
            </div>

            <div v-if="testResults.proxyseller" :class="['p-3 rounded-lg text-xs', testResults.proxyseller.success ? 'bg-green-950/40 border border-green-800/60 text-green-300' : 'bg-red-950/40 border border-red-800/60 text-red-300']">
              {{ testResults.proxyseller.message }}
            </div>
            <div v-if="testResults.proxyall" :class="['p-3 rounded-lg text-xs', testResults.proxyall.success ? 'bg-green-950/40 border border-green-800/60 text-green-300' : 'bg-red-950/40 border border-red-800/60 text-red-300']">
              {{ testResults.proxyall.message }}
            </div>
          </div>

          <!-- 静态后备中继网关 & 二级状态锁 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-4">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <div class="flex items-center gap-2">
                <span class="text-base">🔒</span>
                <h3 class="font-semibold text-sm text-zinc-200">静态后备中继网关 & 二级状态锁 (2FA)</h3>
              </div>
              <button @click="testProxyConnectivity" :disabled="testing.connectivity" class="btn-secondary text-xs py-1">
                {{ testing.connectivity ? '探测中...' : '⚡ 中继链路探测' }}
              </button>
            </div>

            <div class="grid grid-cols-3 gap-2">
              <div class="col-span-2">
                <label class="block text-[11px] text-zinc-400 mb-1">中继节点 IP / 域名</label>
                <input v-model="config.fallback_proxy.addr" type="text" class="input-field font-mono text-xs" />
              </div>
              <div>
                <label class="block text-[11px] text-zinc-400 mb-1">端口</label>
                <input v-model.number="config.fallback_proxy.port" type="number" class="input-field font-mono text-xs" />
              </div>
            </div>

            <div class="grid grid-cols-2 gap-2">
              <div>
                <label class="block text-[11px] text-zinc-400 mb-1">鉴权账号 (可选)</label>
                <input v-model="config.fallback_proxy.username" type="text" class="input-field font-mono text-xs" />
              </div>
              <div>
                <label class="block text-[11px] text-zinc-400 mb-1">鉴权密码 (可选)</label>
                <input v-model="config.fallback_proxy.password" type="password" class="input-field font-mono text-xs" />
              </div>
            </div>

            <div class="pt-2 border-t border-zinc-800">
              <label class="block text-xs font-medium text-zinc-400 mb-1">二级密码学状态保护凭证 (Secondary State Key / 2FA)</label>
              <input v-model="config.default_2fa_password" type="text" class="input-field font-mono text-xs" />
            </div>

            <div v-if="testResults.connectivity" :class="['p-3 rounded-lg text-xs', testResults.connectivity.success ? 'bg-green-950/40 border border-green-800/60 text-green-300' : 'bg-red-950/40 border border-red-800/60 text-red-300']">
              {{ testResults.connectivity.message }}
            </div>
          </div>

          <!-- 自定义代理池 / 手动批量粘贴导入 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-4 md:col-span-2">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <div class="flex items-center gap-2">
                <span class="text-base">📋</span>
                <h3 class="font-semibold text-sm text-zinc-200">自定义代理池 / 手动批量粘贴导入 (Custom Proxy Pool)</h3>
                <span class="badge badge-info text-[10px]">{{ customProxies.length }} 条</span>
              </div>
              <div class="flex items-center gap-1.5">
                <button @click="importCustomProxyText" :disabled="testing.customimport" class="btn-secondary text-xs py-1">
                  {{ testing.customimport ? '导入中...' : '📥 批量解析并导入' }}
                </button>
                <button @click="testAllCustomProxies" :disabled="testing.customall" class="btn-secondary text-xs py-1">
                  {{ testing.customall ? '测活中...' : '⚡ 一键全量测活' }}
                </button>
                <button @click="clearCustomProxyPool" :disabled="testing.customclear" class="btn-secondary text-xs py-1">
                  {{ testing.customclear ? '清空中...' : '🧹 清空自建池' }}
                </button>
              </div>
            </div>

            <p class="text-[11px] text-zinc-400 leading-relaxed">
              支持一次粘贴多行。自动去除空行与 <code class="font-mono">#</code> / <code class="font-mono">//</code> 注释，默认协议 <code class="font-mono">socks5</code>。
              调度需要 <code class="font-mono">cl / in / id</code> 等国家时，会优先匹配本池中已标注或已测活的对应节点。
            </p>

            <textarea
              v-model="customProxyText"
              rows="7"
              class="input-field font-mono text-xs"
              placeholder="host;port;user;pass&#10;host:port:user:pass&#10;host:port&#10;user:pass@host:port&#10;socks5://user:pass@host:port&#10;http://user:pass@host:port"
            ></textarea>

            <div class="flex flex-wrap items-center gap-3 text-[11px] text-zinc-400">
              <label class="flex items-center gap-1.5">
                <input type="checkbox" v-model="customProxyImportProbe" class="rounded bg-zinc-900 border-zinc-700" />
                导入后立即测活
              </label>
              <div class="flex items-center gap-1.5">
                <span>预标注国家</span>
                <input v-model="customProxyImportCountry" type="text" placeholder="可选 cl / in / id" class="input-field font-mono text-xs w-28 py-1" />
              </div>
              <span class="text-zinc-500">格式：host;port;user;pass · host:port:user:pass · socks5://...</span>
            </div>

            <div v-if="customProxyMeta.message" :class="['p-2.5 rounded-lg text-[11px]', customProxyMeta.success === false ? 'bg-red-950/40 border border-red-800/60 text-red-300' : 'bg-zinc-900 border border-zinc-800 text-zinc-300']">
              {{ customProxyMeta.message }}
              <span v-if="customProxyMeta.countries?.length" class="ml-1 text-zinc-500">
                已识别区域: {{ customProxyMeta.countries.join(', ') }}
              </span>
            </div>

            <div v-if="customProxies.length" class="max-h-72 overflow-y-auto rounded-lg border border-zinc-800 divide-y divide-zinc-800/60">
              <div
                v-for="(p, idx) in customProxies"
                :key="p.id || (p.addr + ':' + p.port + idx)"
                class="flex items-center justify-between gap-2 px-2.5 py-2 text-[11px]"
              >
                <div class="flex items-center gap-2 min-w-0">
                  <span class="shrink-0">{{ countryFlag(p.country_code) }}</span>
                  <span class="badge badge-info shrink-0">{{ (p.country_code || p.country || '?').toString().toUpperCase() }}</span>
                  <span class="font-mono text-zinc-200 truncate">{{ p.addr }}:{{ p.port }}</span>
                  <span class="text-zinc-500 uppercase">{{ p.proxy_type || 'socks5' }}</span>
                  <span :class="p.healthy === true ? 'text-emerald-400' : (p.healthy === false ? 'text-red-400' : 'text-zinc-500')">
                    {{ p.healthy === true ? '连通' : (p.healthy === false ? '失败' : '待测') }}
                  </span>
                  <span v-if="p.latency_ms != null" class="text-zinc-400">{{ p.latency_ms }}ms</span>
                  <span v-if="p.egress_ip" class="text-zinc-500 truncate">出口 {{ p.egress_ip }}{{ p.city ? ' / ' + p.city : '' }}</span>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <button @click="setCustomProxyAsFallback(p)" class="text-blue-400 hover:text-blue-300">设为当前后备</button>
                  <button @click="deleteCustomProxy(p)" class="text-red-400/80 hover:text-red-300">删除</button>
                </div>
              </div>
            </div>
            <div v-else class="p-3 rounded-lg bg-zinc-900 border border-zinc-800 text-[11px] text-zinc-500">
              自建代理池为空。把供应商提供的多行列表粘贴到上方文本框，再点「批量解析并导入」。
            </div>
          </div>

          <!-- 自建开发者 API 凭证 & Attestation 高可用网关容灾 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-4 md:col-span-2">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <div class="flex items-center gap-2">
                <span class="text-base">🧩</span>
                <h3 class="font-semibold text-sm text-zinc-200">自建开发者 API 凭证 & Attestation 高可用网关容灾 (应对 API_ID_PUBLISHED_FLOOD)</h3>
              </div>
            </div>

            <div class="p-3 rounded-lg bg-amber-950/30 border border-amber-800/50 text-xs text-amber-200 leading-relaxed">
              官方内置 api_id (如 4 / 6 / 21724) 早年已被公开泄露，Telegram 服务端对其 auth.sendCode 请求执行近乎无差别拦截：
              若本次未附带合法 Push Token，几乎必然返回 <code class="font-mono">API_ID_PUBLISHED_FLOOD</code>。
              可在「🔐 凭证库 / 开发者 API」用已有账号申请，或在下方直接填入自建
              <code class="font-mono">custom_api_id</code> / <code class="font-mono">custom_api_hash</code>
              (<a href="https://my.telegram.org/apps" target="_blank" class="underline text-amber-100">my.telegram.org/apps</a>)。
              REGHelp 与 AntiSafety 是两套独立服务，密钥和网关地址不能交叉混用。
            </div>

            <div>
              <label class="block text-xs font-medium text-zinc-400 mb-1">Attestation / Push 凭证提供源高可用调度策略</label>
              <select v-model="config.attestation_provider_mode" class="input-field font-mono text-xs">
                <option value="reghelp_primary">reghelp_primary（REGHelp 优先，AntiSafety 备选，推荐）</option>
                <option value="antisafety_primary">antisafety_primary（AntiSafety 优先，REGHelp 备选）</option>
                <option value="reghelp_only">reghelp_only（仅使用 REGHelp）</option>
                <option value="antisafety_only">antisafety_only（仅使用 AntiSafety）</option>
              </select>
              <p class="text-[11px] text-zinc-500 mt-1">节点引导时按此顺序依次尝试各提供源，任一失败/超时自动切换至下一候选，无需人工干预。</p>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div class="space-y-3">
                <div>
                  <label class="block text-xs font-medium text-zinc-400 mb-1">API 凭证选择策略</label>
                  <select v-model="config.api_credential_mode" class="input-field font-mono text-xs">
                    <option value="auto">auto（无 Push Token 且官方 ID 已泄露时自动回退自建凭证，推荐）</option>
                    <option value="custom">custom（始终强制使用自建开发者凭证）</option>
                    <option value="official">official（始终使用官方内置凭证，依赖 Push Token）</option>
                  </select>
                </div>
                <div class="grid grid-cols-2 gap-2">
                  <div>
                    <label class="block text-[11px] text-zinc-400 mb-1">自建 API ID</label>
                    <input v-model.number="config.custom_api_id" type="number" placeholder="例如: 12345678" class="input-field font-mono text-xs" />
                  </div>
                  <div>
                    <label class="block text-[11px] text-zinc-400 mb-1">自建 API Hash</label>
                    <input v-model="config.custom_api_hash" type="text" placeholder="my.telegram.org 申请获得" class="input-field font-mono text-xs" />
                  </div>
                </div>
              </div>

              <div class="space-y-3">
                <div>
                  <label class="block text-[11px] text-zinc-400 mb-1">AntiSafety Push Token 网关候选地址 (仅 antisafety.net，勿填入 reghelp.net)</label>
                  <textarea v-model="antisafetyBaseUrlsText" rows="2" class="input-field font-mono text-xs" placeholder="https://api.antisafety.net"></textarea>
                  <p class="text-[11px] text-zinc-500 mt-1">保存时会自动剔除交叉污染地址，避免出现 Invalid API key。</p>
                </div>
                <div class="grid grid-cols-2 gap-2">
                  <div>
                    <label class="block text-[11px] text-zinc-400 mb-1">连接超时 (秒)</label>
                    <input v-model.number="config.antisafety_connect_timeout" type="number" step="0.5" class="input-field font-mono text-xs" />
                  </div>
                  <div>
                    <label class="block text-[11px] text-zinc-400 mb-1">总超时 (秒)</label>
                    <input v-model.number="config.antisafety_total_timeout" type="number" step="1" class="input-field font-mono text-xs" />
                  </div>
                </div>
              </div>
            </div>
          </div>

        </div>

      </div>

      <!-- ================= 标签页: 已有账户凭证库 & 开发者凭证申请 ================= -->
      <div v-if="activeTab === 'vault'" class="space-y-6">
        <div class="flex items-center justify-between border-b border-zinc-800 pb-4">
          <div>
            <h2 class="text-lg font-bold text-white">🔐 已有账户凭证库 & 开发者 API 凭证管理</h2>
            <p class="text-xs text-zinc-400">
              扫描 <code class="text-zinc-200">lod_user/</code> 与 <code class="text-zinc-200">data/sessions/</code>。
              现存 JSON 的 <code class="text-zinc-200">app_id=4</code> 是公开泄露官方 ID，不能直接当专属凭证。
              本地有账户就可以申请：没有 .session 也能用已登录客户端收 777000 验证码；有 session 就丢进目录自动读码；已经申请过的直接填到「⚙️ 参数拓扑」。
            </p>
          </div>
          <button @click="fetchVaultAccounts" :disabled="vaultLoading" class="btn-secondary text-xs">
            {{ vaultLoading ? '扫描中...' : '🔄 重新扫描凭证库' }}
          </button>
        </div>

        <div
          class="glass-panel p-5 rounded-xl border-2 border-dashed transition-colors space-y-3"
          :class="vaultUploadDragging ? 'border-cyan-400 bg-cyan-950/20' : 'border-cyan-700/70 bg-cyan-950/10'"
          @dragenter.prevent="vaultUploadDragging = true"
          @dragover.prevent="vaultUploadDragging = true"
          @dragleave.prevent="vaultUploadDragging = false"
          @drop.prevent="onVaultFileDrop"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <h3 class="font-bold text-sm text-cyan-100 flex items-center gap-2">
                <span>📤</span> 上传账号文件 (ZIP / Session / JSON)
              </h3>
              <p class="text-[11px] text-cyan-100/80 mt-1 leading-relaxed">
                在浏览器里自己导入账号做申请测试，无需 SSH。选择或拖入
                <code class="text-cyan-50">.zip</code> /
                <code class="text-cyan-50">.session</code> /
                <code class="text-cyan-50">.json</code>，
                上传完成后自动扫描并刷新凭证库列表。
              </p>
            </div>
            <label class="btn-primary text-xs py-2 px-3 cursor-pointer shrink-0">
              <input
                ref="vaultFileInput"
                type="file"
                accept=".zip,.session,.json,application/zip,application/json"
                class="hidden"
                :disabled="vaultUploading"
                @change="onVaultFilePicked"
              />
              {{ vaultUploading ? '上传中...' : '选择文件并上传' }}
            </label>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px] text-zinc-300">
            <div class="p-2.5 rounded-lg bg-zinc-950/60 border border-zinc-800">
              <div class="font-semibold text-zinc-100">ZIP 压缩包</div>
              <div class="text-zinc-400 mt-0.5">自动安全解压到 <code class="text-zinc-200">lod_user/&lt;压缩包名&gt;/</code>，支持账号目录。</div>
            </div>
            <div class="p-2.5 rounded-lg bg-zinc-950/60 border border-zinc-800">
              <div class="font-semibold text-zinc-100">单个 Session / JSON</div>
              <div class="text-zinc-400 mt-0.5">保存到 <code class="text-zinc-200">lod_user/imports/</code>。同名配对后可自动读 777000 验证码。</div>
            </div>
            <div class="p-2.5 rounded-lg bg-zinc-950/60 border border-zinc-800">
              <div class="font-semibold text-zinc-100">限制与安全</div>
              <div class="text-zinc-400 mt-0.5">仅接受上述后缀，最大 50MB；ZIP 会拦截路径穿越（zip-slip）。</div>
            </div>
          </div>

          <div v-if="vaultUploading || vaultUploadProgress > 0" class="space-y-1">
            <div class="flex items-center justify-between text-[11px] text-cyan-100">
              <span>{{ vaultUploading ? '正在上传并导入...' : '上传完成' }}</span>
              <span class="font-mono">{{ vaultUploadProgress }}%</span>
            </div>
            <div class="h-1.5 rounded-full bg-zinc-900 overflow-hidden">
              <div class="h-full bg-cyan-500 transition-all" :style="{ width: vaultUploadProgress + '%' }"></div>
            </div>
          </div>

          <div v-if="vaultUploadResult" :class="['p-3 rounded-lg text-xs', vaultUploadResult.success ? 'bg-green-950/40 border border-green-800/60 text-green-300' : 'bg-red-950/40 border border-red-800/60 text-red-300']">
            <div>{{ vaultUploadResult.message }}</div>
            <div v-if="vaultUploadResult.dest_dir" class="mt-1 font-mono text-[11px] text-zinc-400">目录: {{ vaultUploadResult.dest_dir }}</div>
            <div v-if="vaultUploadResult.imported_accounts?.length" class="mt-1 text-[11px]">
              新导入:
              <span v-for="acc in vaultUploadResult.imported_accounts" :key="acc.account_id" class="mr-2 font-mono">
                {{ acc.phone || acc.filename }}{{ acc.has_session ? ' ✓session' : '' }}
              </span>
            </div>
          </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <!-- 当前全局生效凭证 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-3">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <h3 class="font-semibold text-sm text-zinc-200">当前全局自建凭证</h3>
              <span class="badge badge-info text-[10px]">{{ config.api_credential_mode || 'auto' }}</span>
            </div>
            <div class="text-xs space-y-1.5">
              <div class="flex justify-between text-zinc-400">
                <span>custom_api_id</span>
                <span class="font-mono text-zinc-200">{{ config.custom_api_id || '未配置' }}</span>
              </div>
              <div class="flex justify-between text-zinc-400">
                <span>custom_api_hash</span>
                <span class="font-mono text-zinc-200">{{ maskHash(config.custom_api_hash) }}</span>
              </div>
              <div class="flex justify-between text-zinc-400">
                <span>凭证库账号数</span>
                <span class="font-mono text-zinc-200">{{ vaultAccounts.length }}</span>
              </div>
            </div>
            <div class="p-2.5 rounded-lg bg-zinc-900 border border-zinc-800 text-[11px] text-zinc-500 leading-relaxed">
              公开泄露官方 ID（如 4 / 6 / 21724）缺少 Push Token 时容易触发 API_ID_PUBLISHED_FLOOD。
              优先使用 my.telegram.org 申请的专属凭证，并将策略设为 custom / auto。
            </div>
            <div v-if="isPublishedCustomApiId" class="p-2.5 rounded-lg bg-red-950/40 border border-red-800/60 text-[11px] text-red-200 leading-relaxed">
              当前 custom_api_id={{ config.custom_api_id }} 仍是公开泄露 ID，写入全局配置不会规避 FLOOD。
              请申请专属新 ID，或填入真正的自建凭证。
            </div>
          </div>

          <!-- 开发者凭证申请与管理 -->
          <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 lg:col-span-2 space-y-4">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <div class="flex items-center gap-2">
                <span class="text-base">🧾</span>
                <h3 class="font-semibold text-sm text-zinc-200">开发者凭证申请与管理 (my.telegram.org)</h3>
              </div>
              <span v-if="appsJob" :class="getStatusBadgeClass(appsJob.status)">{{ appsJob.status }}</span>
            </div>

            <div class="p-3 rounded-lg bg-blue-950/20 border border-blue-800/40 text-[11px] text-blue-100 leading-relaxed space-y-1.5">
              <div><span class="font-semibold text-blue-50">轨 A · 已登录客户端（无需 .session）</span>：下方填入该账号手机号，点申请。系统向 777000 发 Web 登录码，你在手机 Telegram 查看后填回本页。</div>
              <div><span class="font-semibold text-blue-50">轨 B · 已有 .session</span>：把 <code class="text-blue-50">*.session</code> 复制到 <code class="text-blue-50">lod_user/</code> 或 <code class="text-blue-50">data/sessions/</code>（最好与 JSON 同名），刷新后选择账号再申请，系统会静默读 777000。</div>
              <div><span class="font-semibold text-blue-50">轨 C · 已有现成凭证</span>：到「⚙️ 参数拓扑」直接填写 <code class="text-blue-50">custom_api_id</code> / <code class="text-blue-50">custom_api_hash</code>。</div>
            </div>
            <div v-if="selectedVaultAccount?.apps_apply_hint" class="p-2.5 rounded-lg bg-zinc-900 border border-zinc-800 text-[11px] text-zinc-400 leading-relaxed">
              {{ selectedVaultAccount.apps_apply_hint }}
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label class="block text-xs font-medium text-zinc-400 mb-1">选择已有账号（JSON 或 session）</label>
                <select v-model="vaultSelectedId" class="input-field font-mono text-xs">
                  <option value="">请选择账号...</option>
                  <option v-for="acc in vaultAccounts" :key="acc.account_id" :value="acc.account_id">
                    {{ acc.phone || acc.filename }} · {{ acc.source }}{{ acc.has_session ? ' · session' : '' }}
                  </option>
                </select>
              </div>
              <div>
                <label class="block text-xs font-medium text-zinc-400 mb-1">或直接填已登录客户端的手机号（轨 A）</label>
                <input v-model="appsPhone" type="text" class="input-field font-mono text-xs" placeholder="+56 / +91 手机号" />
              </div>
              <div>
                <label class="block text-xs font-medium text-zinc-400 mb-1">可选：新建应用短名</label>
                <input v-model="appsShortname" type="text" class="input-field font-mono text-xs" placeholder="edgenode2026" />
              </div>
            </div>

            <div class="flex flex-wrap items-center gap-2">
              <button
                @click="startAppsJob"
                :disabled="(!vaultSelectedId && !appsPhone.trim()) || appsStarting"
                class="btn-primary text-xs py-2"
              >
                {{ appsStarting ? '正在发起登录...' : '从 my.telegram.org 申请专属 API ID/Hash' }}
              </button>
              <button
                v-if="appsJob && appsJob.api_id && appsJob.api_hash"
                @click="applyAppsJob"
                class="btn-secondary text-xs py-2"
              >
                将本次申请结果写入 config.json
              </button>
            </div>

            <div v-if="appsJob && appsJob.needs_manual_code" class="p-3 rounded-lg bg-amber-950/30 border border-amber-800/50 space-y-2">
              <p class="text-xs text-amber-200">
                未能自动读取验证码（本地没有可用 .session 时属于正常路径）。
                请打开该手机号已登录的 Telegram 客户端，查看官方号 777000 发来的 my.telegram.org Web 登录码后提交。
              </p>
              <div class="flex items-center gap-2">
                <input v-model="appsManualCode" type="text" class="input-field font-mono text-xs" placeholder="登录验证码" />
                <button @click="submitAppsCode" :disabled="!appsManualCode" class="btn-primary text-xs py-2">提交验证码</button>
              </div>
            </div>

            <div v-if="appsJob && appsJob.api_id" class="p-3 rounded-lg bg-green-950/40 border border-green-800/60 text-xs text-green-300">
              已获得专属凭证：api_id=<span class="font-mono">{{ appsJob.api_id }}</span>
              api_hash=<span class="font-mono">{{ maskHash(appsJob.api_hash) }}</span>
              <span v-if="appsJob.applied_to_config"> · 已写入全局配置</span>
            </div>

            <div class="bg-zinc-950 p-3 rounded-lg border border-zinc-900 font-mono text-[11px] h-36 overflow-y-auto space-y-1">
              <div v-if="!appsJob || !appsJob.logs.length" class="text-zinc-600 italic">
                选择账号后点击申请，将按官方流程：发送登录码 → 读取/提交验证码 → 查询或创建 /apps。
              </div>
              <div v-for="(log, idx) in (appsJob?.logs || [])" :key="idx" class="text-zinc-300 leading-relaxed">{{ log }}</div>
            </div>
          </div>
        </div>

        <!-- 已有账户凭证库 -->
        <div class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-3">
          <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
            <div class="flex items-center gap-2">
              <span class="text-base">🗃️</span>
              <h3 class="font-semibold text-sm text-zinc-200">已有账户凭证库 (Account Vault)</h3>
              <span class="badge badge-info text-[10px]">{{ vaultAccounts.length }} accounts</span>
            </div>
            <div class="text-[11px] text-zinc-500 font-mono">
              {{ vaultMeta.lod_user_dir }} · {{ vaultMeta.sessions_dir }}
              <span v-if="vaultMeta.published_api_id_count"> · 泄露 ID {{ vaultMeta.published_api_id_count }}</span>
              <span v-if="vaultMeta.missing_session_count"> · 缺 session {{ vaultMeta.missing_session_count }}</span>
            </div>
          </div>

          <div class="overflow-x-auto">
            <table class="w-full text-left text-xs">
              <thead>
                <tr class="text-zinc-500 border-b border-zinc-800/60">
                  <th class="py-2">手机号</th>
                  <th>来源</th>
                  <th>注册时间</th>
                  <th>设备 / SDK</th>
                  <th>app_id / hash</th>
                  <th>Session</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-zinc-800/40">
                <tr v-if="vaultAccounts.length === 0">
                  <td colspan="7" class="py-6 text-center text-zinc-600">
                    未扫描到账号。请将 JSON / .session 放入 lod_user/ 或 data/sessions/ 后刷新。
                  </td>
                </tr>
                <tr
                  v-for="acc in vaultAccounts"
                  :key="acc.account_id"
                  class="hover:bg-zinc-900/40 transition-colors"
                  :class="vaultSelectedId === acc.account_id ? 'bg-blue-950/20' : ''"
                  @click="vaultSelectedId = acc.account_id"
                >
                  <td class="py-2.5 font-mono text-zinc-200">{{ acc.phone || acc.phone_raw || '-' }}</td>
                  <td><span class="badge badge-info text-[10px]">{{ acc.source }}</span></td>
                  <td class="text-zinc-400">{{ acc.register_time || '-' }}</td>
                  <td class="text-zinc-300">
                    <div>{{ acc.device_model || '-' }}</div>
                    <div class="text-[11px] text-zinc-500">{{ acc.system_version || '' }} {{ acc.app_version || '' }}</div>
                  </td>
                  <td class="font-mono text-zinc-300">
                    <div>{{ acc.app_id || '-' }} / {{ maskHash(acc.app_hash) }}</div>
                    <span v-if="acc.is_published_api_id" class="badge badge-warning text-[10px]">公开泄露 ID</span>
                    <span v-else-if="acc.has_usable_custom_credentials" class="badge badge-success text-[10px]">可用专属凭证</span>
                  </td>
                  <td>
                    <span v-if="acc.has_session" class="badge badge-success text-[10px]">.session</span>
                    <span v-else class="badge badge-warning text-[10px]">仅 JSON</span>
                  </td>
                  <td class="space-x-2 whitespace-nowrap">
                    <button
                      v-if="acc.has_usable_custom_credentials"
                      @click.stop="applyVaultCredentials(acc)"
                      :disabled="vaultApplyingId === acc.account_id"
                      class="text-blue-400 hover:text-blue-300"
                    >
                      {{ vaultApplyingId === acc.account_id ? '写入中...' : '一键应用专属凭证' }}
                    </button>
                    <span v-else class="text-zinc-600">请申请新 API</span>
                    <button @click.stop="selectAndStartApps(acc)" class="text-cyan-400 hover:text-cyan-300">
                      申请专属 API
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="vaultGuidance" class="p-3 rounded-lg bg-amber-950/20 border border-amber-800/40 text-[11px] text-amber-100 leading-relaxed">
            {{ vaultGuidance }}
          </div>
          <div v-if="vaultApplyResult" :class="['p-3 rounded-lg text-xs', vaultApplyResult.success ? 'bg-green-950/40 border border-green-800/60 text-green-300' : 'bg-red-950/40 border border-red-800/60 text-red-300']">
            <div>{{ vaultApplyResult.message }}</div>
            <div v-if="vaultApplyResult.warning" class="mt-1 text-amber-300">{{ vaultApplyResult.warning }}</div>
          </div>
        </div>
      </div>

      <!-- ================= 标签页 3: 硬件指纹与协议拓扑 ================= -->
      <div v-if="activeTab === 'devices'" class="space-y-6">
        
        <div class="border-b border-zinc-800 pb-4">
          <h2 class="text-lg font-bold text-white">📱 边缘节点硬件拓扑指纹库与端点矩阵</h2>
          <p class="text-xs text-zinc-400">展示系统内置与外部数据库的高保真硬件特征拓扑，以及 Google Play Integrity 签名对齐规范</p>
        </div>

        <!-- 3 套 Profile 卡片展示 -->
        <div class="glass-panel p-4 rounded-xl border border-zinc-800/80 bg-blue-950/20 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <span class="text-2xl">📦</span>
            <div>
              <div class="flex items-center gap-2">
                <h3 class="font-bold text-sm text-blue-300">已成功载入本地外部真机数据库: 2026-08-23_07-06-02_Base.db</h3>
                <span class="badge badge-success">{{ dbStats.total_count }} 套机型拓扑已就绪</span>
              </div>
              <p class="text-xs text-zinc-400 mt-0.5">
                包含 Realme、Motorola、Xiaomi、Huawei、Samsung 等真实硬件遥测样本，时区偏置与西语/全球拓扑对齐，引导时将自动进行伪随机采样。
              </p>
            </div>
          </div>
          <button @click="fetchDbStats" class="btn-secondary text-xs py-1">刷新状态</button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div v-for="p in deviceProfiles" :key="p.key" class="glass-panel p-5 rounded-xl border border-zinc-800/80 space-y-3">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-2">
              <h3 class="font-bold text-sm text-white">{{ p.name }}</h3>
              <span class="badge badge-info">{{ p.app_name }}</span>
            </div>

            <div v-if="p.is_published_api_id" class="badge badge-warning text-[10px]">⚠️ 官方公开泄露 ID (需 Push Token)</div>
            <div v-else class="badge badge-success text-[10px]">✓ 自建开发者凭证</div>

            <div class="space-y-1.5 text-xs">
              <div class="flex justify-between text-zinc-400">
                <span>API ID / Hash:</span>
                <span class="font-mono text-zinc-200">{{ p.api_id }} / {{ p.api_hash.substring(0, 8) }}...</span>
              </div>
              <div class="flex justify-between text-zinc-400">
                <span>设备硬件型号:</span>
                <span class="font-mono text-zinc-200">{{ p.device_model }}</span>
              </div>
              <div class="flex justify-between text-zinc-400">
                <span>操作系统版本:</span>
                <span class="font-mono text-zinc-200">{{ p.system_version }}</span>
              </div>
              <div class="flex justify-between text-zinc-400">
                <span>端点版本号:</span>
                <span class="font-mono text-zinc-200">{{ p.app_version }}</span>
              </div>
              <div class="flex justify-between text-zinc-400">
                <span>构建编号 (Build):</span>
                <span class="font-mono text-zinc-200">{{ p.app_build }}</span>
              </div>
              <div class="flex justify-between text-zinc-400">
                <span>协议语言包:</span>
                <span class="font-mono text-zinc-200">{{ p.lang_pack }}</span>
              </div>
            </div>

            <div class="p-2.5 rounded-lg bg-zinc-900 border border-zinc-800 text-[11px] font-mono break-all text-zinc-400">
              Attestation AID: {{ p.aid }}
            </div>
          </div>
        </div>

        <!-- 详细指南说明卡片 -->
        <div class="glass-panel p-6 rounded-xl border border-zinc-800/80 space-y-4">
          <h3 class="font-bold text-sm text-white flex items-center gap-2">
            <span>🔍</span> 关于边缘节点环境遥测指纹与底层属性提取规范
          </h3>
          
          <div class="text-xs text-zinc-300 leading-relaxed space-y-2">
            <p>
              <strong>1. MTProto 协议端点对齐原理：</strong> 
              在协议客户端握手与密钥交换阶段，将 <code class="text-blue-400">device_model</code>、<code class="text-blue-400">system_version</code>、<code class="text-blue-400">app_version</code> 与目标拓扑区域的 <code class="text-blue-400">lang_code</code> 与 Attestation 签名参数精确对齐，消除状态机特征异常。
            </p>
            <p>
              <strong>2. 如何从真实 Android 边缘设备提取高保真硬件遥测属性？</strong>
              将设备通过调试接口连接，执行以下命令即可完整导出系统硬件属性字典：
            </p>
            <pre class="bg-zinc-950 p-3 rounded-lg border border-zinc-900 font-mono text-[11px] text-zinc-300 overflow-x-auto">
# 导出整机系统属性字典
adb shell getprop > device_build_prop.txt

# 提取核心硬件指纹
adb shell getprop ro.product.model       # 设备型号 (如 SM-S918B)
adb shell getprop ro.product.brand       # 品牌 (如 samsung)
adb shell getprop ro.build.version.sdk   # SDK 版本 (如 33)
adb shell getprop ro.build.fingerprint   # 官方完整签名指纹
            </pre>
          </div>
        </div>

      </div>

    </main>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'

const tabs = [
  { id: 'console', name: '状态机编排与控制台', icon: '⚡' },
  { id: 'vault', name: '凭证库 / 开发者 API', icon: '🔐' },
  { id: 'settings', name: '参数拓扑与接口审计', icon: '⚙️' },
  { id: 'devices', name: '硬件指纹与协议拓扑', icon: '📱' }
]

const activeTab = ref('console')
const terminalRef = ref(null)

const config = reactive({
  active_app_type: 'telegram_android',
  antisafety_api_key: '',
  antisafety_aids: {
    telegram_android: '',
    telegram_x: '',
    telegram_9: ''
  },
  vak_sms_api_key: '',
  target_country: 'cl',
  proxy_seller_key: '',
  use_proxy_seller_auto: false,
  fallback_proxy: {
    proxy_type: 'socks5',
    addr: '127.0.0.1',
    port: 10808,
    username: '',
    password: ''
  },
  custom_proxies: [],
  default_2fa_password: 'Password@2026!Sec',
  api_credential_mode: 'auto',
  custom_api_id: null,
  custom_api_hash: '',
  antisafety_base_urls: ['https://api.antisafety.net'],
  antisafety_reporting_base_urls: ['https://reporting.antisafety.net'],
  antisafety_connect_timeout: 6.0,
  antisafety_total_timeout: 20.0,
  antisafety_enabled: true,
  reghelp_api_key: '',
  reghelp_base_urls: ['https://api.reghelp.net'],
  reghelp_enabled: true,
  reghelp_connect_timeout: 6.0,
  reghelp_total_timeout: 20.0,
  attestation_provider_mode: 'reghelp_primary'
})

// 候选网关地址在界面上以逐行文本编辑，实际持久化为字符串数组
const antisafetyBaseUrlsText = ref('')
const antisafetyReportingBaseUrlsText = ref('')
const reghelpBaseUrlsText = ref('')

const syncBaseUrlsTextFromConfig = () => {
  antisafetyBaseUrlsText.value = (config.antisafety_base_urls || []).join('\n')
  antisafetyReportingBaseUrlsText.value = (config.antisafety_reporting_base_urls || []).join('\n')
  reghelpBaseUrlsText.value = (config.reghelp_base_urls || []).join('\n')
}

const isCrossProviderUrl = (url, provider) => {
  const host = String(url || '').toLowerCase()
  if (provider === 'reghelp') return host.includes('antisafety.net')
  return host.includes('reghelp.net')
}

const applyBaseUrlsTextToConfig = () => {
  const parseLines = (text) => text.split('\n').map(s => s.trim()).filter(Boolean)
  config.antisafety_base_urls = parseLines(antisafetyBaseUrlsText.value)
    .filter(url => !isCrossProviderUrl(url, 'antisafety'))
  config.antisafety_reporting_base_urls = parseLines(antisafetyReportingBaseUrlsText.value)
    .filter(url => !isCrossProviderUrl(url, 'antisafety'))
  config.reghelp_base_urls = parseLines(reghelpBaseUrlsText.value)
    .filter(url => !isCrossProviderUrl(url, 'reghelp'))
  if (!config.antisafety_base_urls.length) config.antisafety_base_urls = ['https://api.antisafety.net']
  if (!config.antisafety_reporting_base_urls.length) config.antisafety_reporting_base_urls = ['https://reporting.antisafety.net']
  if (!config.reghelp_base_urls.length) config.reghelp_base_urls = ['https://api.reghelp.net']
}

const form = reactive({
  country: 'cl',
  app_type: 'telegram_android'
})

const batchMode = ref(false)
const batchCount = ref(3)
const batchConcurrency = ref(3)
const currentBatch = ref(null)
const taskFilter = ref('all')
const selectedTaskIds = ref([])
const mergedLogView = ref(false)

const testing = reactive({
  vaksms: false,
  antisafety: false,
  reghelp: false,
  proxyseller: false,
  proxypool: false,
  autoselect: false,
  proxyall: false,
  connectivity: false,
  customimport: false,
  customall: false,
  customclear: false
})

const testResults = reactive({
  vaksms: null,
  antisafety: null,
  reghelp: null,
  proxyseller: null,
  proxyall: null,
  connectivity: null
})

const proxyPool = ref([])
const proxyPoolMeta = reactive({
  success: null,
  message: '',
  available_countries: [],
  cached: false
})
const matchedProxy = ref(null)
const customProxies = ref([])
const customProxyText = ref('')
const customProxyImportProbe = ref(false)
const customProxyImportCountry = ref('')
const customProxyMeta = reactive({
  success: null,
  message: '',
  countries: []
})

const countryFlag = (code) => {
  const iso = String(code || '').trim().toUpperCase()
  if (iso.length !== 2 || !/^[A-Z]{2}$/.test(iso)) return '🏳️'
  return String.fromCodePoint(...[...iso].map((ch) => 127397 + ch.charCodeAt(0)))
}

const customProxiesForCountry = computed(() => {
  const wanted = String(form.country || config.target_country || '').trim().toLowerCase()
  if (!wanted) return customProxies.value
  return customProxies.value.filter((item) => {
    const code = String(item.country_code || '').toLowerCase()
    const name = String(item.country || '').toLowerCase()
    return code === wanted || name.includes(wanted)
  })
})

const customProxySummaryText = computed(() => {
  const total = customProxies.value.length
  const healthy = customProxies.value.filter((item) => item.healthy === true).length
  const pending = customProxies.value.filter((item) => item.healthy == null).length
  if (!total) return '空'
  return `${total} 条 / ${healthy} 通 / ${pending} 待测`
})

const isStartingTask = ref(false)
const isSavingConfig = ref(false)
const activeTask = ref(null)
const taskList = ref([])
const effectiveConcurrency = computed(() => Math.max(1, Math.min(Number(batchConcurrency.value) || 1, Number(batchCount.value) || 1)))
const visibleTaskList = computed(() => {
  if (taskFilter.value === 'batch' && currentBatch.value?.batch_id) {
    const ids = new Set(currentBatch.value.task_ids || [])
    return taskList.value.filter((t) => ids.has(t.task_id) || t.batch_id === currentBatch.value.batch_id)
  }
  return taskList.value
})
const allVisibleSelected = computed(() => {
  const ids = visibleTaskList.value.map((t) => t.task_id)
  return ids.length > 0 && ids.every((id) => selectedTaskIds.value.includes(id))
})
const batchStats = computed(() => {
  const ids = new Set(currentBatch.value?.task_ids || [])
  const items = taskList.value.filter((t) => ids.has(t.task_id) || (currentBatch.value && t.batch_id === currentBatch.value.batch_id))
  return {
    success: items.filter((t) => t.status === 'success').length,
    failed: items.filter((t) => t.status === 'failed').length,
    running: items.filter((t) => t.status === 'running').length,
    pending: items.filter((t) => t.status === 'pending' || !t.status).length
  }
})
const displayLogs = computed(() => {
  if (mergedLogView.value) {
    const ids = selectedTaskIds.value.length
      ? selectedTaskIds.value
      : (currentBatch.value?.task_ids || [])
    const rows = []
    for (const tid of ids) {
      const task = taskList.value.find((t) => t.task_id === tid)
      for (const line of (task?.logs || [])) {
        rows.push(`[${tid}] ${line}`)
      }
    }
    return rows
  }
  return activeTask.value?.logs || []
})
const sessions = ref([])
const deviceProfiles = ref([])
const dbStats = ref({ total_count: 0, is_loaded: false, sample_models: [] })

const vaultLoading = ref(false)
const vaultAccounts = ref([])
const vaultMeta = reactive({ lod_user_dir: '', sessions_dir: '', published_api_id_count: 0, missing_session_count: 0 })
const vaultSelectedId = ref('')
const vaultApplyingId = ref('')
const vaultApplyResult = ref(null)
const vaultGuidance = ref('')
const vaultFileInput = ref(null)
const vaultUploading = ref(false)
const vaultUploadDragging = ref(false)
const vaultUploadProgress = ref(0)
const vaultUploadResult = ref(null)
const PUBLISHED_API_IDS = new Set([4, 6, 8, 10, 2040, 2100, 17349, 21724])
const isPublishedCustomApiId = computed(() => PUBLISHED_API_IDS.has(Number(config.custom_api_id)))
const selectedVaultAccount = computed(() => vaultAccounts.value.find(acc => acc.account_id === vaultSelectedId.value) || null)
const appsStarting = ref(false)
const appsJob = ref(null)
const appsShortname = ref('')
const appsPhone = ref('')
const appsManualCode = ref('')
let appsPollTimer = null

let pollTimer = null

const maskHash = (hash) => {
  if (!hash) return '未配置'
  const text = String(hash)
  if (text.length <= 10) return text
  return `${text.substring(0, 8)}...${text.substring(text.length - 4)}`
}

const fetchDbStats = async () => {
  try {
    const res = await fetch('/api/device-db-stats')
    dbStats.value = await res.json()
  } catch (e) {
    console.error('Fetch db stats error:', e)
  }
}

const fetchConfig = async () => {
  try {
    const res = await fetch('/api/config')
    const data = await res.json()
    Object.assign(config, data)
    form.country = data.target_country || 'cl'
    form.app_type = data.active_app_type || 'telegram_android'
    syncBaseUrlsTextFromConfig()
  } catch (e) {
    console.error('Fetch config error:', e)
  }
}

const saveConfig = async () => {
  isSavingConfig.value = true
  applyBaseUrlsTextToConfig()
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })
    const updated = await res.json()
    Object.assign(config, updated)
    syncBaseUrlsTextFromConfig()
    alert('全局仿真参数已成功保存并持久化！')
  } catch (e) {
    alert('保存失败: ' + e.message)
  } finally {
    isSavingConfig.value = false
  }
}

const fetchProfiles = async () => {
  try {
    const res = await fetch('/api/device-profiles')
    deviceProfiles.value = await res.json()
  } catch (e) {
    console.error('Fetch profiles error:', e)
  }
}

const fetchTasks = async () => {
  try {
    const qs = (taskFilter.value === 'batch' && currentBatch.value?.batch_id)
      ? `?batch_id=${encodeURIComponent(currentBatch.value.batch_id)}`
      : ''
    const res = await fetch(`/api/register/tasks${qs}`)
    taskList.value = await res.json()
    if (currentBatch.value?.batch_id) {
      try {
        const bres = await fetch(`/api/register/batches/${currentBatch.value.batch_id}`)
        if (bres.ok) {
          currentBatch.value = await bres.json()
        }
      } catch (e) {
        console.error('Fetch batch error:', e)
      }
    }
    if (activeTask.value) {
      const found = taskList.value.find(t => t.task_id === activeTask.value.task_id)
      if (found) {
        activeTask.value = found
      }
    }
    nextTick(() => {
      if (terminalRef.value) {
        terminalRef.value.scrollTop = terminalRef.value.scrollHeight
      }
    })
  } catch (e) {
    console.error('Fetch tasks error:', e)
  }
}

const fetchSessions = async () => {
  try {
    const res = await fetch('/api/sessions')
    sessions.value = await res.json()
  } catch (e) {
    console.error('Fetch sessions error:', e)
  }
}

const startRegistrationTask = async () => {
  isStartingTask.value = true
  const bootLogs = []
  try {
    if (config.use_proxy_seller_auto) {
      const preview = await previewAutoSelect(form.country, false)
      if (preview?.proxy) {
        matchedProxy.value = preview.proxy
        bootLogs.push(
          `[${new Date().toLocaleTimeString()}] [多径中继网关] 启动前已匹配 ${form.country.toUpperCase()} 区域代理: ${preview.proxy.proxy_type || 'socks5'}://${preview.proxy.addr}:${preview.proxy.port}`
        )
      } else if (preview?.message) {
        bootLogs.push(`[${new Date().toLocaleTimeString()}] [多径中继网关] ${preview.message}`)
      }
    }
    const useBatch = batchMode.value && Number(batchCount.value) > 1
    const endpoint = useBatch ? '/api/register/batch' : '/api/register/start'
    const payload = {
      country: form.country,
      app_type: form.app_type
    }
    if (useBatch) {
      payload.count = Number(batchCount.value)
      payload.concurrency = effectiveConcurrency.value
    }
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.detail || data.message || '任务提交失败')
    }
    if (useBatch) {
      currentBatch.value = data
      taskFilter.value = 'batch'
      selectedTaskIds.value = [...(data.task_ids || [])]
      mergedLogView.value = true
      const firstId = (data.task_ids || [])[0]
      activeTask.value = {
        task_id: firstId,
        status: 'pending',
        batch_id: data.batch_id,
        logs: [
          ...bootLogs,
          `[${new Date().toLocaleTimeString()}] 并发批次 ${data.batch_id} 已提交：${(data.task_ids || []).join(', ')} (concurrency=${data.concurrency})`
        ]
      }
    } else {
      currentBatch.value = null
      mergedLogView.value = false
      activeTask.value = {
        task_id: data.task_id,
        status: 'pending',
        logs: [
          ...bootLogs,
          `[${new Date().toLocaleTimeString()}] 虚拟节点任务 ${data.task_id} 已提交至状态机编排引擎...`
        ]
      }
    }
    await fetchTasks()
  } catch (e) {
    alert('任务提交失败: ' + e.message)
  } finally {
    isStartingTask.value = false
  }
}

const viewTaskLogs = (t) => {
  mergedLogView.value = false
  activeTask.value = t
  nextTick(() => {
    if (terminalRef.value) {
      terminalRef.value.scrollTop = terminalRef.value.scrollHeight
    }
  })
}

const toggleTaskSelection = (taskId) => {
  const set = new Set(selectedTaskIds.value)
  if (set.has(taskId)) set.delete(taskId)
  else set.add(taskId)
  selectedTaskIds.value = [...set]
}

const toggleSelectVisibleTasks = () => {
  const ids = visibleTaskList.value.map((t) => t.task_id)
  if (allVisibleSelected.value) {
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !ids.includes(id))
    return
  }
  selectedTaskIds.value = [...new Set([...selectedTaskIds.value, ...ids])]
}

const viewSelectedLogs = () => {
  if (!selectedTaskIds.value.length) return
  mergedLogView.value = true
  const first = taskList.value.find((t) => t.task_id === selectedTaskIds.value[0])
  if (first) activeTask.value = first
}

const focusBatchTask = (taskId) => {
  const found = taskList.value.find((t) => t.task_id === taskId)
  mergedLogView.value = false
  if (found) {
    activeTask.value = found
    return
  }
  activeTask.value = { task_id: taskId, status: 'pending', logs: [] }
}

const clearActiveLogs = () => {
  if (activeTask.value) {
    activeTask.value.logs = []
  }
}

// 诊断探针 API Tests
const testVakSms = async () => {
  testing.vaksms = true
  testResults.vaksms = null
  try {
    const res = await fetch('/api/test/vaksms', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: config.vak_sms_api_key, country: config.target_country })
    })
    testResults.vaksms = await res.json()
  } catch (e) {
    testResults.vaksms = { success: false, message: e.message }
  } finally {
    testing.vaksms = false
  }
}

const testAntiSafety = async () => {
  testing.antisafety = true
  testResults.antisafety = null
  applyBaseUrlsTextToConfig()
  try {
    const res = await fetch('/api/test/antisafety', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: config.antisafety_api_key,
        aid: config.antisafety_aids[config.active_app_type],
        base_urls: config.antisafety_base_urls,
        reporting_base_urls: config.antisafety_reporting_base_urls
      })
    })
    testResults.antisafety = await res.json()
  } catch (e) {
    testResults.antisafety = { success: false, message: e.message }
  } finally {
    testing.antisafety = false
  }
}

const testRegHelp = async () => {
  testing.reghelp = true
  testResults.reghelp = null
  applyBaseUrlsTextToConfig()
  try {
    const res = await fetch('/api/test/reghelp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: config.reghelp_api_key,
        base_urls: config.reghelp_base_urls
      })
    })
    testResults.reghelp = await res.json()
  } catch (e) {
    testResults.reghelp = { success: false, message: e.message }
  } finally {
    testing.reghelp = false
  }
}

const testProxySeller = async () => {
  testing.proxyseller = true
  testResults.proxyseller = null
  try {
    const res = await fetch('/api/test/proxyseller', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: config.proxy_seller_key, country: config.target_country })
    })
    testResults.proxyseller = await res.json()
    if (testResults.proxyseller?.data?.proxies) {
      proxyPool.value = testResults.proxyseller.data.proxies
    }
  } catch (e) {
    testResults.proxyseller = { success: false, message: e.message }
  } finally {
    testing.proxyseller = false
  }
}

const refreshProxyPool = async (country, refresh = true) => {
  testing.proxypool = true
  try {
    const params = new URLSearchParams()
    if (country) params.set('country', country)
    if (refresh) params.set('refresh', 'true')
    const res = await fetch(`/api/proxy-seller/proxies?${params.toString()}`)
    const data = await res.json()
    proxyPool.value = data.proxies || []
    proxyPoolMeta.success = data.success
    proxyPoolMeta.message = data.message || ''
    proxyPoolMeta.available_countries = data.available_countries || []
    proxyPoolMeta.cached = !!data.cached
    const regional = (data.proxies || []).find(p => {
      const code = String(p.country_code || p.country || '').toLowerCase()
      return !country || code.includes(String(country).toLowerCase())
    })
    if (regional) matchedProxy.value = regional
    return data
  } catch (e) {
    proxyPoolMeta.success = false
    proxyPoolMeta.message = e.message
    return null
  } finally {
    testing.proxypool = false
  }
}

const previewAutoSelect = async (country, applyFallback = false) => {
  testing.autoselect = true
  try {
    const res = await fetch('/api/proxy-seller/auto-select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_country: country,
        apply_fallback: applyFallback,
        probe: false,
        allow_fallback: true,
        api_key: config.proxy_seller_key
      })
    })
    const data = await res.json()
    proxyPoolMeta.success = data.success
    proxyPoolMeta.message = data.message || ''
    if (data.proxy) matchedProxy.value = data.proxy
    if (data.fallback_proxy) Object.assign(config.fallback_proxy, data.fallback_proxy)
    return data
  } catch (e) {
    proxyPoolMeta.success = false
    proxyPoolMeta.message = e.message
    return { success: false, message: e.message }
  } finally {
    testing.autoselect = false
  }
}

const setProxyAsFallback = async (proxy) => {
  config.fallback_proxy.proxy_type = proxy.proxy_type || 'socks5'
  config.fallback_proxy.addr = proxy.addr
  config.fallback_proxy.port = Number(proxy.port)
  config.fallback_proxy.username = proxy.username || ''
  config.fallback_proxy.password = proxy.password || ''
  matchedProxy.value = proxy
  await saveConfig()
}

const testAllProxySeller = async () => {
  testing.proxyall = true
  testResults.proxyall = null
  try {
    const res = await fetch('/api/proxy-seller/test-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        country: config.target_country,
        api_key: config.proxy_seller_key,
        refresh: false,
        limit: 20
      })
    })
    const data = await res.json()
    testResults.proxyall = data
    if (data.results) proxyPool.value = data.results
    proxyPoolMeta.message = data.message || ''
    proxyPoolMeta.success = data.success
  } catch (e) {
    testResults.proxyall = { success: false, message: e.message }
  } finally {
    testing.proxyall = false
  }
}

const applyCustomProxyPayload = (data) => {
  if (!data) return
  if (Array.isArray(data.proxies)) customProxies.value = data.proxies
  if (Array.isArray(data.results) && !data.proxies) customProxies.value = data.results
  config.custom_proxies = customProxies.value
  customProxyMeta.success = data.success
  customProxyMeta.message = data.message || ''
  customProxyMeta.countries = data.countries || []
  if (data.fallback_proxy) Object.assign(config.fallback_proxy, data.fallback_proxy)
}

const fetchCustomProxyList = async (country) => {
  try {
    const params = new URLSearchParams()
    if (country) params.set('country', country)
    const res = await fetch(`/api/proxy/custom-list${params.toString() ? '?' + params.toString() : ''}`)
    const data = await res.json()
    applyCustomProxyPayload(data)
    return data
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
    return null
  }
}

const importCustomProxyText = async () => {
  if (!customProxyText.value.trim()) {
    customProxyMeta.success = false
    customProxyMeta.message = '请先粘贴代理列表'
    return
  }
  testing.customimport = true
  try {
    const res = await fetch('/api/proxy/import-text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: customProxyText.value,
        probe: customProxyImportProbe.value,
        replace: false,
        default_protocol: 'socks5',
        default_country: customProxyImportCountry.value || undefined
      })
    })
    const data = await res.json()
    applyCustomProxyPayload(data)
    if (data.success) customProxyText.value = ''
    await fetchCustomProxyList()
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
  } finally {
    testing.customimport = false
  }
}

const testAllCustomProxies = async () => {
  testing.customall = true
  try {
    const res = await fetch('/api/proxy/test-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ concurrency: 4 })
    })
    const data = await res.json()
    applyCustomProxyPayload(data)
    await fetchCustomProxyList()
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
  } finally {
    testing.customall = false
  }
}

const setCustomProxyAsFallback = async (proxy) => {
  try {
    const res = await fetch('/api/proxy/set-fallback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        proxy_id: proxy.id,
        addr: proxy.addr,
        port: proxy.port,
        username: proxy.username
      })
    })
    const data = await res.json()
    applyCustomProxyPayload(data)
    if (data.success && data.fallback_proxy) {
      Object.assign(config.fallback_proxy, data.fallback_proxy)
      matchedProxy.value = data.proxy || proxy
    }
    if (!data.success) alert(data.message || '设为后备失败')
  } catch (e) {
    alert('设为后备失败: ' + e.message)
  }
}

const deleteCustomProxy = async (proxy) => {
  if (!confirm(`删除自建代理 ${proxy.addr}:${proxy.port} ?`)) return
  try {
    const res = await fetch('/api/proxy/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy_id: proxy.id, addr: proxy.addr, port: proxy.port, username: proxy.username })
    })
    const data = await res.json()
    customProxyMeta.success = data.success
    customProxyMeta.message = data.message || ''
    await fetchCustomProxyList()
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
  }
}

const clearCustomProxyPool = async () => {
  if (!customProxies.value.length) return
  if (!confirm('确定清空全部自建代理？此操作会从配置中删除已导入列表。')) return
  testing.customclear = true
  try {
    const res = await fetch('/api/proxy/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clear_all: true })
    })
    const data = await res.json()
    customProxyMeta.success = data.success
    customProxyMeta.message = data.message || ''
    await fetchCustomProxyList()
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
  } finally {
    testing.customclear = false
  }
}

const testProxyConnectivity = async () => {
  testing.connectivity = true
  testResults.connectivity = null
  try {
    const res = await fetch('/api/test/proxy-connectivity', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config.fallback_proxy)
    })
    testResults.connectivity = await res.json()
  } catch (e) {
    testResults.connectivity = { success: false, message: e.message }
  } finally {
    testing.connectivity = false
  }
}

const getStatusBadgeClass = (status) => {
  if (status === 'success') return 'badge badge-success'
  if (status === 'running') return 'badge badge-info animate-pulse'
  if (status === 'failed') return 'badge badge-danger'
  return 'badge badge-warning'
}

const formatTime = (iso) => {
  if (!iso) return '-'
  return iso.split('T')[1]?.substring(0, 8) || iso
}

const isAllowedVaultUpload = (file) => {
  if (!file || !file.name) return false
  return /\.(zip|session|json)$/i.test(file.name)
}

const uploadVaultFile = (file) => {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/vault/upload')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        vaultUploadProgress.value = Math.max(1, Math.round((event.loaded / event.total) * 90))
      }
    }
    xhr.onload = () => {
      let data = {}
      try {
        data = JSON.parse(xhr.responseText || '{}')
      } catch (e) {
        reject(new Error('服务器返回了无法解析的响应'))
        return
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        vaultUploadProgress.value = 100
        resolve(data)
        return
      }
      reject(new Error(data.detail || data.message || `上传失败 HTTP ${xhr.status}`))
    }
    xhr.onerror = () => reject(new Error('网络错误，上传未完成'))
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

const handleVaultUpload = async (file) => {
  if (!file) return
  if (!isAllowedVaultUpload(file)) {
    vaultUploadResult.value = { success: false, message: '仅支持 .zip / .session / .json' }
    return
  }
  vaultUploading.value = true
  vaultUploadProgress.value = 1
  vaultUploadResult.value = null
  try {
    const data = await uploadVaultFile(file)
    vaultUploadResult.value = data
    await fetchVaultAccounts()
    const first = (data.imported_accounts || [])[0]
    if (first?.account_id) vaultSelectedId.value = first.account_id
  } catch (e) {
    vaultUploadResult.value = { success: false, message: e.message }
  } finally {
    vaultUploading.value = false
    if (vaultFileInput.value) vaultFileInput.value.value = ''
  }
}

const onVaultFilePicked = async (event) => {
  const file = event.target.files?.[0]
  await handleVaultUpload(file)
}

const onVaultFileDrop = async (event) => {
  vaultUploadDragging.value = false
  const file = event.dataTransfer?.files?.[0]
  await handleVaultUpload(file)
}

const fetchVaultAccounts = async () => {
  vaultLoading.value = true
  try {
    const res = await fetch('/api/vault/accounts')
    const data = await res.json()
    vaultAccounts.value = data.accounts || []
    vaultMeta.lod_user_dir = data.lod_user_dir || ''
    vaultMeta.sessions_dir = data.sessions_dir || ''
    vaultMeta.published_api_id_count = data.published_api_id_count || 0
    vaultMeta.missing_session_count = data.missing_session_count || 0
    vaultGuidance.value = data.guidance || ''
    if (data.applied_api_id) config.custom_api_id = data.applied_api_id
    if (data.applied_api_hash) config.custom_api_hash = data.applied_api_hash
    if (data.api_credential_mode) config.api_credential_mode = data.api_credential_mode
    if (!vaultSelectedId.value && vaultAccounts.value.length) {
      vaultSelectedId.value = vaultAccounts.value[0].account_id
    }
  } catch (e) {
    console.error('Fetch vault accounts error:', e)
  } finally {
    vaultLoading.value = false
  }
}

const applyVaultCredentials = async (acc) => {
  vaultApplyingId.value = acc.account_id
  vaultApplyResult.value = null
  try {
    const res = await fetch('/api/vault/accounts/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: acc.account_id, set_mode_custom: true })
    })
    const data = await res.json()
    if (!res.ok) {
      vaultApplyResult.value = { success: false, message: data.detail || '应用失败' }
      return
    }
    vaultApplyResult.value = data
    if (data.custom_api_id) config.custom_api_id = data.custom_api_id
    if (data.custom_api_hash) config.custom_api_hash = data.custom_api_hash
    if (data.api_credential_mode) config.api_credential_mode = data.api_credential_mode
  } catch (e) {
    vaultApplyResult.value = { success: false, message: e.message }
  } finally {
    vaultApplyingId.value = ''
  }
}

const pollAppsJob = async (jobId) => {
  if (appsPollTimer) {
    clearInterval(appsPollTimer)
    appsPollTimer = null
  }
  const tick = async () => {
    try {
      const res = await fetch(`/api/vault/apps/jobs/${jobId}`)
      if (!res.ok) return
      const data = await res.json()
      appsJob.value = data
      if (['success', 'failed'].includes(data.status) || (data.needs_manual_code && data.status === 'waiting_code')) {
        if (appsPollTimer) {
          clearInterval(appsPollTimer)
          appsPollTimer = null
        }
      }
      if (data.applied_to_config && data.api_id) {
        config.custom_api_id = data.api_id
        config.custom_api_hash = data.api_hash
        config.api_credential_mode = 'custom'
      }
    } catch (e) {
      console.error('Poll apps job error:', e)
    }
  }
  await tick()
  appsPollTimer = setInterval(tick, 1500)
}

const startAppsJob = async () => {
  const phone = (appsPhone.value || '').trim()
  if (!vaultSelectedId.value && !phone) return
  appsStarting.value = true
  appsManualCode.value = ''
  try {
    const res = await fetch('/api/vault/apps/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account_id: vaultSelectedId.value || undefined,
        phone: phone || undefined,
        auto_read_code: true,
        app_shortname: appsShortname.value || undefined,
        apply_to_config: false
      })
    })
    const data = await res.json()
    if (!res.ok) {
      appsJob.value = {
        job_id: '-',
        status: 'failed',
        logs: [data.detail || '发起申请失败'],
        error: data.detail
      }
      return
    }
    appsJob.value = data
    await pollAppsJob(data.job_id)
  } catch (e) {
    appsJob.value = { job_id: '-', status: 'failed', logs: [e.message], error: e.message }
  } finally {
    appsStarting.value = false
  }
}

const selectAndStartApps = async (acc) => {
  vaultSelectedId.value = acc.account_id
  await startAppsJob()
}

const submitAppsCode = async () => {
  if (!appsJob.value?.job_id || !appsManualCode.value) return
  try {
    const res = await fetch('/api/vault/apps/submit-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: appsJob.value.job_id,
        code: appsManualCode.value,
        apply_to_config: false
      })
    })
    const data = await res.json()
    if (!res.ok) {
      alert(data.detail || '提交验证码失败')
      return
    }
    appsJob.value = data
    await pollAppsJob(data.job_id)
  } catch (e) {
    alert(e.message)
  }
}

const applyAppsJob = async () => {
  if (!appsJob.value?.job_id) return
  try {
    const res = await fetch('/api/vault/apps/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: appsJob.value.job_id, set_mode_custom: true })
    })
    const data = await res.json()
    if (!res.ok) {
      vaultApplyResult.value = { success: false, message: data.detail || '写入失败' }
      return
    }
    vaultApplyResult.value = data
    if (data.custom_api_id) config.custom_api_id = data.custom_api_id
    if (data.custom_api_hash) config.custom_api_hash = data.custom_api_hash
    if (data.api_credential_mode) config.api_credential_mode = data.api_credential_mode
    if (appsJob.value) appsJob.value.applied_to_config = true
  } catch (e) {
    vaultApplyResult.value = { success: false, message: e.message }
  }
}

onMounted(() => {
  fetchConfig()
  fetchProfiles()
  fetchDbStats()
  fetchTasks()
  fetchSessions()
  fetchVaultAccounts()
  refreshProxyPool('', false)
  fetchCustomProxyList()
  pollTimer = setInterval(() => {
    fetchTasks()
  }, 2000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (appsPollTimer) clearInterval(appsPollTimer)
})
</script>
