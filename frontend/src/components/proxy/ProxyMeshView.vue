<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>🌐 代理网关 & 多径中继</h2>
        <p>自建池多格式粘贴导入、一键测活、国家旗帜与延迟指示；Proxy-Seller 动态区域池监控与拓扑发现。</p>
      </div>
    </div>

    <div class="ce-panel stack">
      <div class="ce-panel-head">
        <div class="row">
          <h3>📋 自定义代理池 / 手动批量粘贴导入</h3>
          <span class="ce-badge is-info">{{ customProxies.length }} 条</span>
        </div>
        <div class="row-wrap">
          <button class="ce-btn-ghost" :disabled="testing.customimport" @click="importCustomProxyText">
            {{ testing.customimport ? '导入中...' : '批量解析并导入' }}
          </button>
          <button class="ce-btn-ghost" :disabled="testing.customall" @click="testAllCustomProxies">
            {{ testing.customall ? '测活中...' : '一键全量测活' }}
          </button>
          <button class="ce-btn-danger" :disabled="testing.customclear" @click="clearCustomProxyPool">
            {{ testing.customclear ? '清空中...' : '清空自建池' }}
          </button>
        </div>
      </div>
      <p class="ce-tiny">
        支持一次粘贴多行。自动去除空行与 <code>#</code> / <code>//</code> 注释，默认协议 <code>socks5</code>。
        调度需要 <code>cl / in / id</code> 等国家时，会优先匹配本池中已标注或已测活的对应节点。
      </p>
      <textarea
        v-model="customProxyText"
        rows="7"
        class="ce-textarea"
        placeholder="host;port;user;pass&#10;host:port:user:pass&#10;host:port&#10;user:pass@host:port&#10;socks5://user:pass@host:port&#10;http://user:pass@host:port"
      ></textarea>
      <div class="row-wrap">
        <label class="ce-check">
          <input type="checkbox" v-model="customProxyImportProbe" />
          导入后立即测活
        </label>
        <label class="ce-label" style="margin:0">预标注国家</label>
        <input v-model="customProxyImportCountry" type="text" class="ce-input w-sm mono" placeholder="可选 cl / in / id" />
        <span class="ce-muted">格式：host;port;user;pass · host:port:user:pass · socks5://...</span>
      </div>
      <div v-if="customProxyMeta.message" class="ce-alert" :class="customProxyMeta.success === false ? 'is-danger' : 'is-ok'">
        {{ customProxyMeta.message }}
        <span v-if="customProxyMeta.countries?.length" class="ce-muted"> 已识别区域: {{ customProxyMeta.countries.join(', ') }}</span>
      </div>
      <div v-if="customProxies.length" class="ce-list" style="max-height:320px">
        <div v-for="(p, idx) in customProxies" :key="p.id || (p.addr + ':' + p.port + idx)" class="ce-item">
          <div class="row grow">
            <span>{{ countryFlag(p.country_code) }}</span>
            <span class="ce-badge is-info">{{ (p.country_code || p.country || '?').toString().toUpperCase() }}</span>
            <span class="mono">{{ p.addr }}:{{ p.port }}</span>
            <span class="ce-muted">{{ (p.proxy_type || 'socks5').toUpperCase() }}</span>
            <span :class="p.healthy === true ? 'ce-badge is-success' : (p.healthy === false ? 'ce-badge is-danger' : 'ce-muted')">
              {{ p.healthy === true ? '连通' : (p.healthy === false ? '失败' : '待测') }}
            </span>
            <span v-if="p.latency_ms != null" class="ce-latency" :title="p.latency_ms + 'ms'"><i :style="{ width: latencyWidth(p.latency_ms) + '%' }"></i></span>
            <span v-if="p.egress_ip" class="ce-muted">出口 {{ p.egress_ip }}{{ p.city ? ' / ' + p.city : '' }}</span>
          </div>
          <div class="row">
            <button class="ce-link" @click="setCustomProxyAsFallback(p)">设为当前后备</button>
            <button class="ce-link is-danger" @click="deleteCustomProxy(p)">删除</button>
          </div>
        </div>
      </div>
      <div v-else class="ce-alert">自建代理池为空。把供应商提供的多行列表粘贴到上方文本框，再点「批量解析并导入」。</div>
    </div>

    <div class="grid-2">
      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>🌐 Proxy-Seller 动态区域池</h3>
          <div class="row-wrap">
            <button class="ce-btn-ghost" :disabled="testing.proxypool" @click="refreshProxyPool(config.target_country, true)">
              {{ testing.proxypool ? '刷新中...' : '从 API 刷新' }}
            </button>
            <button class="ce-btn-ghost" :disabled="testing.proxyseller" @click="testProxySeller">
              {{ testing.proxyseller ? '测试中...' : '拓扑发现' }}
            </button>
          </div>
        </div>
        <div>
          <label class="ce-label">Relay Provider API Key</label>
          <input v-model="config.proxy_seller_key" type="password" class="ce-input mono" />
        </div>
        <label class="ce-check">
          <input type="checkbox" v-model="config.use_proxy_seller_auto" />
          节点引导时自动分配与拓扑匹配的中继跳点
        </label>
        <div class="row-wrap">
          <button class="ce-btn-ghost" :disabled="testing.autoselect" @click="previewAutoSelect(config.target_country, false)">
            {{ testing.autoselect ? '匹配中...' : '查看当前国家自动分配' }}
          </button>
          <button class="ce-btn-ghost" :disabled="testing.autoselect" @click="previewAutoSelect(config.target_country, true)">
            一键设为后备代理
          </button>
          <button class="ce-btn-ghost" :disabled="testing.proxyall" @click="testAllProxySeller">
            {{ testing.proxyall ? '测活中...' : '批量测活' }}
          </button>
        </div>
        <div v-if="proxyPoolMeta.message" class="ce-alert" :class="proxyPoolMeta.success === false ? 'is-danger' : ''">
          {{ proxyPoolMeta.message }}
          <span v-if="proxyPoolMeta.available_countries?.length" class="ce-muted"> 账户区域: {{ proxyPoolMeta.available_countries.join(', ') }}</span>
        </div>
        <div v-if="matchedProxy" class="ce-alert is-ok">
          当前 {{ (matchedProxy.country_code || config.target_country || '').toString().toUpperCase() }} 自动分配:
          <span class="mono">{{ matchedProxy.proxy_type }}://{{ matchedProxy.addr }}:{{ matchedProxy.port }}</span>
          <span v-if="matchedProxy.egress_ip" class="ce-muted"> 出口 {{ matchedProxy.egress_ip }} {{ matchedProxy.egress_country || '' }}</span>
        </div>
        <div v-if="proxyPool.length" class="ce-list">
          <div v-for="(p, idx) in proxyPool" :key="p.id || (p.addr + ':' + p.port + idx)" class="ce-item">
            <div class="row grow">
              <span class="ce-badge is-info">{{ (p.country_code || p.country_alpha3 || p.country || '?').toString().toUpperCase() }}</span>
              <span class="mono">{{ p.addr }}:{{ p.port }}</span>
              <span class="ce-muted">{{ p.proxy_type }}</span>
              <span :class="p.healthy === true ? 'ce-badge is-success' : (p.healthy === false ? 'ce-badge is-danger' : 'ce-muted')">
                {{ p.healthy === true ? '连通' : (p.healthy === false ? '失败' : '未知') }}
              </span>
            </div>
            <button class="ce-link" @click="setProxyAsFallback(p)">设为后备</button>
          </div>
        </div>
        <div v-if="testResults.proxyseller" class="ce-alert" :class="testResults.proxyseller.success ? 'is-ok' : 'is-danger'">{{ testResults.proxyseller.message }}</div>
        <div v-if="testResults.proxyall" class="ce-alert" :class="testResults.proxyall.success ? 'is-ok' : 'is-danger'">{{ testResults.proxyall.message }}</div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>🔒 静态后备中继网关</h3>
          <button class="ce-btn-ghost" :disabled="testing.connectivity" @click="testProxyConnectivity">
            {{ testing.connectivity ? '探测中...' : '中继链路探测' }}
          </button>
        </div>
        <div class="grid-2">
          <div class="span-2">
            <label class="ce-label">中继节点 IP / 域名</label>
            <input v-model="config.fallback_proxy.addr" type="text" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">端口</label>
            <input v-model.number="config.fallback_proxy.port" type="number" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">协议</label>
            <input v-model="config.fallback_proxy.proxy_type" type="text" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">鉴权账号</label>
            <input v-model="config.fallback_proxy.username" type="text" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">鉴权密码</label>
            <input v-model="config.fallback_proxy.password" type="password" class="ce-input mono" />
          </div>
        </div>
        <div v-if="testResults.connectivity" class="ce-alert" :class="testResults.connectivity.success ? 'is-ok' : 'is-danger'">
          {{ testResults.connectivity.message }}
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { countryFlag, latencyWidth } from '../../composables/useShared'
import { useConfig } from '../../composables/useConfig'
import { useProxy } from '../../composables/useProxy'
import { useProbes } from '../../composables/useProbes'

const { config } = useConfig()
const {
  proxyPool, proxyPoolMeta, matchedProxy, customProxies, customProxyText, customProxyImportProbe,
  customProxyImportCountry, customProxyMeta, testing, refreshProxyPool, previewAutoSelect,
  setProxyAsFallback, importCustomProxyText, testAllCustomProxies, setCustomProxyAsFallback,
  deleteCustomProxy, clearCustomProxyPool
} = useProxy()
const { testResults, testProxySeller, testAllProxySeller, testProxyConnectivity } = useProbes()
</script>
