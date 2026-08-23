<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>⚙️ 参数拓扑 & 探针审计</h2>
        <p>REGHelp / AntiSafety / Vak-SMS / RecaptchaMobile / 2FA 一键探针，实时返回可见。</p>
      </div>
      <button class="ce-btn" :disabled="isSavingConfig" @click="saveConfig">
        {{ isSavingConfig ? '正在保存...' : '持久化全局配置' }}
      </button>
    </div>

    <div class="grid-2">
      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <div class="row">
            <h3>🛰️ REGHelp Key API</h3>
            <span class="ce-badge is-success">主选</span>
          </div>
          <button class="ce-btn-ghost" :disabled="probeTesting.reghelp" @click="testRegHelp">
            {{ probeTesting.reghelp ? '测试中...' : '余额/连通性探针' }}
          </button>
        </div>
        <label class="ce-check">
          <input type="checkbox" v-model="config.reghelp_enabled" />
          启用 REGHelp 作为 Attestation / Push 凭证提供源
        </label>
        <div>
          <label class="ce-label">REGHelp API Key</label>
          <input v-model="config.reghelp_api_key" type="password" class="ce-input mono" placeholder="rh_live_... / w9vcrhw7..." />
        </div>
        <div>
          <label class="ce-label">Key API 候选网关地址（仅 reghelp.net）</label>
          <textarea v-model="reghelpBaseUrlsText" rows="2" class="ce-textarea" placeholder="https://api.reghelp.net"></textarea>
          <p class="ce-tiny">与 AntiSafety 严格隔离。RECAPTCHA_CHECK 自动解题只走这把 Key 与 RecaptchaMobile 接口。</p>
        </div>
        <div class="grid-2">
          <div>
            <label class="ce-label">连接超时 (秒)</label>
            <input v-model.number="config.reghelp_connect_timeout" type="number" step="0.5" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">总超时 (秒)</label>
            <input v-model.number="config.reghelp_total_timeout" type="number" step="1" class="ce-input mono" />
          </div>
        </div>
        <div class="ce-tiny">
          对接 REGHelp Key API（<a href="https://reghelp.net" target="_blank">reghelp.net</a>）：
          GET <code>/push/getToken</code> → 轮询 <code>/push/getStatus</code>，appName/appDevice 与内置端点模板对齐。
        </div>
        <div class="ce-alert is-info">
          RecaptchaMobile 通道与 REGHelp Key 绑定：引导过程遇到 RECAPTCHA_CHECK 时自动解题，无需额外 AID。
          上方探针成功即表示解题通道可用。
        </div>
        <div v-if="testResults.reghelp" class="ce-alert" :class="testResults.reghelp.success ? 'is-ok' : 'is-danger'">
          {{ testResults.reghelp.message }}
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <div class="row">
            <h3>🛡️ AntiSafety 挑战凭证</h3>
            <span class="ce-badge is-info">备选</span>
          </div>
          <button class="ce-btn-ghost" :disabled="probeTesting.antisafety" @click="testAntiSafety">
            {{ probeTesting.antisafety ? '测试中...' : '连通性探针' }}
          </button>
        </div>
        <label class="ce-check">
          <input type="checkbox" v-model="config.antisafety_enabled" />
          启用 AntiSafety 作为 Attestation / Push 凭证提供源
        </label>
        <div>
          <label class="ce-label">Attestation API Key</label>
          <input v-model="config.antisafety_api_key" type="password" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">MTProto Android AID</label>
          <input v-model="config.antisafety_aids.telegram_android" type="text" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">MTProto TDLib AID</label>
          <input v-model="config.antisafety_aids.telegram_x" type="text" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">MTProto Legacy AID</label>
          <input v-model="config.antisafety_aids.telegram_9" type="text" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">Push Token 网关候选（仅 antisafety.net）</label>
          <textarea v-model="antisafetyBaseUrlsText" rows="2" class="ce-textarea" placeholder="https://api.antisafety.net"></textarea>
        </div>
        <div>
          <label class="ce-label">Reporting 网关候选</label>
          <textarea v-model="antisafetyReportingBaseUrlsText" rows="2" class="ce-textarea" placeholder="https://reporting.antisafety.net"></textarea>
        </div>
        <div class="grid-2">
          <div>
            <label class="ce-label">连接超时 (秒)</label>
            <input v-model.number="config.antisafety_connect_timeout" type="number" step="0.5" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">总超时 (秒)</label>
            <input v-model.number="config.antisafety_total_timeout" type="number" step="1" class="ce-input mono" />
          </div>
        </div>
        <div v-if="testResults.antisafety" class="ce-alert" :class="testResults.antisafety.success ? 'is-ok' : 'is-danger'">
          {{ testResults.antisafety.message }}
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>📩 Vak-SMS 带外挑战源</h3>
          <button class="ce-btn-ghost" :disabled="probeTesting.vaksms" @click="testVakSms">
            {{ probeTesting.vaksms ? '测试中...' : '状态探针' }}
          </button>
        </div>
        <div>
          <label class="ce-label">OOB Telemetry API Key</label>
          <input v-model="config.vak_sms_api_key" type="password" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">默认地理拓扑区域</label>
          <select v-model="config.target_country" class="ce-select">
            <optgroup v-for="group in COUNTRY_GROUPS" :key="group.id" :label="group.label">
              <option v-for="opt in group.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </optgroup>
          </select>
        </div>
        <div class="ce-tiny">
          含 🇨🇦 加拿大 <code>ca</code> (+1) · 智利 <code>cl</code> (+56) · 印度 <code>in</code> (+91) · 印尼 <code>id</code> (+62) 等全球拓扑。
        </div>
        <div v-if="testResults.vaksms" class="ce-alert" :class="testResults.vaksms.success ? 'is-ok' : 'is-danger'">
          <div>{{ testResults.vaksms.message }}</div>
          <div v-if="testResults.vaksms.data" class="mono ce-tiny">
            可用配额: {{ testResults.vaksms.data.balance }} | 拓扑 {{ testResults.vaksms.data.country }} 信道容量: {{ testResults.vaksms.data.telegram_stock }}
          </div>
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>🔒 二级状态锁 (2FA) & 凭证策略</h3>
        </div>
        <div>
          <label class="ce-label">二级密码学状态保护凭证</label>
          <input v-model="config.default_2fa_password" type="text" class="ce-input mono" />
        </div>
        <label class="ce-check">
          <input type="checkbox" v-model="config.phone_precheck_enabled" />
          启用号码注册状态预检探测（租号后、申请 Push Token 前拦截已注册二手号）
        </label>
        <div>
          <label class="ce-label">Attestation / Push 高可用调度策略</label>
          <select v-model="config.attestation_provider_mode" class="ce-select">
            <option value="reghelp_primary">reghelp_primary（REGHelp 优先，AntiSafety 备选）</option>
            <option value="antisafety_primary">antisafety_primary（AntiSafety 优先，REGHelp 备选）</option>
            <option value="reghelp_only">reghelp_only（仅使用 REGHelp）</option>
            <option value="antisafety_only">antisafety_only（仅使用 AntiSafety）</option>
          </select>
        </div>
        <div>
          <label class="ce-label">API 凭证选择策略</label>
          <select v-model="config.api_credential_mode" class="ce-select">
            <option value="auto">auto（无 Push Token 且官方 ID 已泄露时自动回退自建凭证）</option>
            <option value="custom">custom（始终强制使用自建开发者凭证）</option>
            <option value="official">official（始终使用官方内置凭证，依赖 Push Token）</option>
          </select>
        </div>
        <div class="grid-2">
          <div>
            <label class="ce-label">自建 API ID</label>
            <input v-model.number="config.custom_api_id" type="number" class="ce-input mono" placeholder="例如: 12345678" />
          </div>
          <div>
            <label class="ce-label">自建 API Hash</label>
            <input v-model="config.custom_api_hash" type="text" class="ce-input mono" placeholder="my.telegram.org 申请获得" />
          </div>
        </div>
        <div class="ce-alert is-warn">
          官方内置 api_id（如 4 / 6 / 21724）已被公开泄露。未附带合法 Push Token 时几乎必然返回
          <code>API_ID_PUBLISHED_FLOOD</code>。REGHelp 与 AntiSafety 密钥/网关不能交叉混用。
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { COUNTRY_GROUPS } from '../../composables/useShared'
import { useConfig } from '../../composables/useConfig'
import { useProbes } from '../../composables/useProbes'

const {
  config, isSavingConfig, saveConfig, reghelpBaseUrlsText,
  antisafetyBaseUrlsText, antisafetyReportingBaseUrlsText
} = useConfig()
const { probeTesting, testResults, testRegHelp, testAntiSafety, testVakSms } = useProbes()
</script>
