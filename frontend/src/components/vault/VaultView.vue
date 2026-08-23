<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>🔐 凭证库 & 开发者 API</h2>
        <p>
          扫描 <code>lod_user/</code> 与 <code>data/sessions/</code>。现存 JSON 的 <code>app_id=4</code> 是公开泄露官方 ID，不能直接当专属凭证。
          没有 .session 也能用已登录客户端收 777000 验证码；有 session 就自动读码。
        </p>
      </div>
      <button class="ce-btn-ghost" :disabled="vaultLoading" @click="fetchVaultAccounts">
        {{ vaultLoading ? '扫描中...' : '重新扫描凭证库' }}
      </button>
    </div>

    <div
      class="ce-dropzone stack"
      :class="{ 'is-over': vaultUploadDragging }"
      @dragenter.prevent="vaultUploadDragging = true"
      @dragover.prevent="vaultUploadDragging = true"
      @dragleave.prevent="vaultUploadDragging = false"
      @drop.prevent="onVaultFileDrop"
    >
      <div class="between">
        <div>
          <h3>📤 上传账号文件 (ZIP / Session / JSON)</h3>
          <p class="ce-tiny" style="margin-top:6px">
            在浏览器里导入账号做申请测试，无需 SSH。拖入 <code>.zip</code> / <code>.session</code> / <code>.json</code>，上传完成后自动刷新凭证库。
          </p>
        </div>
        <label class="ce-btn" style="cursor:pointer">
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
      <div class="grid-2">
        <div class="ce-item" style="display:block">
          <strong>ZIP 压缩包</strong>
          <div class="ce-tiny">自动安全解压到 <code>lod_user/&lt;压缩包名&gt;/</code>，支持账号目录。</div>
        </div>
        <div class="ce-item" style="display:block">
          <strong>单个 Session / JSON</strong>
          <div class="ce-tiny">保存到 <code>lod_user/imports/</code>。同名配对后可自动读 777000 验证码。</div>
        </div>
        <div class="ce-item span-2" style="display:block">
          <strong>限制与安全</strong>
          <div class="ce-tiny">仅接受上述后缀，最大 50MB；ZIP 会拦截路径穿越（zip-slip）。</div>
        </div>
      </div>
      <div v-if="vaultUploading || vaultUploadProgress > 0" class="stack">
        <div class="between ce-tiny">
          <span>{{ vaultUploading ? '正在上传并导入...' : '上传完成' }}</span>
          <span class="mono">{{ vaultUploadProgress }}%</span>
        </div>
        <div class="ce-progress"><i :style="{ width: vaultUploadProgress + '%' }"></i></div>
      </div>
      <div v-if="vaultUploadResult" class="ce-alert" :class="vaultUploadResult.success ? 'is-ok' : 'is-danger'">
        <div>{{ vaultUploadResult.message }}</div>
        <div v-if="vaultUploadResult.dest_dir" class="mono ce-muted">目录: {{ vaultUploadResult.dest_dir }}</div>
        <div v-if="vaultUploadResult.imported_accounts?.length" class="ce-tiny">
          新导入:
          <span v-for="acc in vaultUploadResult.imported_accounts" :key="acc.account_id" class="mono">
            {{ acc.phone || acc.filename }}{{ acc.has_session ? ' ✓session' : '' }}
          </span>
        </div>
      </div>
    </div>

    <div class="grid-vault">
      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>当前全局自建凭证</h3>
          <span class="ce-badge is-info">{{ config.api_credential_mode || 'auto' }}</span>
        </div>
        <div class="ce-stat"><span>custom_api_id</span><span>{{ config.custom_api_id || '未配置' }}</span></div>
        <div class="ce-stat"><span>custom_api_hash</span><span>{{ maskHash(config.custom_api_hash) }}</span></div>
        <div class="ce-stat"><span>凭证库账号数</span><span>{{ vaultAccounts.length }}</span></div>
        <div class="ce-tiny">
          公开泄露官方 ID（如 4 / 6 / 21724）缺少 Push Token 时容易触发 API_ID_PUBLISHED_FLOOD。
          优先使用 my.telegram.org 申请的专属凭证。
        </div>
        <div v-if="isPublishedCustomApiId" class="ce-alert is-danger">
          当前 custom_api_id={{ config.custom_api_id }} 仍是公开泄露 ID，写入全局配置不会规避 FLOOD。
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>🧾 开发者凭证申请 (my.telegram.org)</h3>
          <span v-if="appsJob" :class="getStatusBadgeClass(appsJob.status)">{{ appsJob.status }}</span>
        </div>
        <div class="ce-alert is-info">
          <div><strong>轨 A · 已登录客户端（无需 .session）</strong>：填入手机号后申请，在 Telegram 查看 777000 验证码并填回。</div>
          <div><strong>轨 B · 已有 .session</strong>：放入 lod_user/ 或 data/sessions/，系统静默读 777000。</div>
          <div><strong>轨 C · 已有现成凭证</strong>：到「参数拓扑」直接填写 custom_api_id / hash。</div>
        </div>
        <div v-if="selectedVaultAccount?.apps_apply_hint" class="ce-alert">{{ selectedVaultAccount.apps_apply_hint }}</div>
        <div class="grid-2">
          <div>
            <label class="ce-label">选择已有账号</label>
            <select v-model="vaultSelectedId" class="ce-select">
              <option value="">请选择账号...</option>
              <option v-for="acc in vaultAccounts" :key="acc.account_id" :value="acc.account_id">
                {{ acc.phone || acc.filename }} · {{ acc.source }}{{ acc.has_session ? ' · session' : '' }}
              </option>
            </select>
          </div>
          <div>
            <label class="ce-label">或直接填已登录客户端手机号（轨 A）</label>
            <input v-model="appsPhone" type="text" class="ce-input mono" placeholder="+56 / +91 手机号" />
          </div>
          <div class="span-2">
            <label class="ce-label">可选：新建应用短名</label>
            <input v-model="appsShortname" type="text" class="ce-input mono" placeholder="edgenode2026" />
          </div>
        </div>
        <div class="row-wrap">
          <button class="ce-btn" :disabled="(!vaultSelectedId && !appsPhone.trim()) || appsStarting" @click="startAppsJob">
            {{ appsStarting ? '正在发起登录...' : '从 my.telegram.org 申请专属 API ID/Hash' }}
          </button>
          <button v-if="appsJob && appsJob.api_id && appsJob.api_hash" class="ce-btn-ghost" @click="applyAppsJob">
            将本次申请结果写入 config.json
          </button>
        </div>
        <div v-if="appsJob && appsJob.needs_manual_code" class="ce-alert is-warn stack">
          <p>未能自动读取验证码。请打开该手机号已登录的 Telegram，查看官方号 777000 的 Web 登录码后提交。</p>
          <div class="row">
            <input v-model="appsManualCode" type="text" class="ce-input mono" placeholder="登录验证码" />
            <button class="ce-btn" :disabled="!appsManualCode" @click="submitAppsCode">提交验证码</button>
          </div>
        </div>
        <div v-if="appsJob && appsJob.api_id" class="ce-alert is-ok">
          已获得专属凭证：api_id=<span class="mono">{{ appsJob.api_id }}</span>
          api_hash=<span class="mono">{{ maskHash(appsJob.api_hash) }}</span>
          <span v-if="appsJob.applied_to_config"> · 已写入全局配置</span>
        </div>
        <div class="ce-terminal" style="min-height:140px">
          <div v-if="!appsJob || !appsJob.logs?.length" class="ce-log is-empty">选择账号后点击申请，将按官方流程推进。</div>
          <div v-for="(log, idx) in (appsJob?.logs || [])" :key="idx" class="ce-log is-plain">{{ log }}</div>
        </div>
      </div>
    </div>

    <div class="ce-panel stack">
      <div class="ce-panel-head">
        <div class="row">
          <h3>🗃️ 已导入账号网格</h3>
          <span class="ce-badge is-info">{{ vaultAccounts.length }} accounts</span>
        </div>
        <div class="ce-muted mono">
          {{ vaultMeta.lod_user_dir }} · {{ vaultMeta.sessions_dir }}
          <span v-if="vaultMeta.published_api_id_count"> · 泄露 ID {{ vaultMeta.published_api_id_count }}</span>
          <span v-if="vaultMeta.missing_session_count"> · 缺 session {{ vaultMeta.missing_session_count }}</span>
        </div>
      </div>
      <div class="ce-table-wrap">
        <table class="ce-table">
          <thead>
            <tr>
              <th>手机号</th>
              <th>来源</th>
              <th>注册时间</th>
              <th>设备 / SDK</th>
              <th>UID</th>
              <th>app_id / hash</th>
              <th>Session</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="vaultAccounts.length === 0">
              <td colspan="8" class="ce-muted" style="text-align:center;padding:24px">未扫描到账号。请上传或放入 lod_user/ 后刷新。</td>
            </tr>
            <tr
              v-for="acc in vaultAccounts"
              :key="acc.account_id"
              :class="{ 'is-on': vaultSelectedId === acc.account_id }"
              @click="vaultSelectedId = acc.account_id"
            >
              <td class="mono">{{ acc.phone || acc.phone_raw || '-' }}</td>
              <td><span class="ce-badge is-info">{{ acc.source }}</span></td>
              <td>{{ acc.register_time || '-' }}</td>
              <td>
                <div>{{ acc.device_model || '-' }}</div>
                <div class="ce-muted">{{ acc.system_version || '' }} {{ acc.app_version || '' }}</div>
              </td>
              <td class="mono">{{ acc.user_id || '-' }}</td>
              <td class="mono">
                <div>{{ acc.app_id || '-' }} / {{ maskHash(acc.app_hash) }}</div>
                <span v-if="acc.is_published_api_id" class="ce-badge is-warn">公开泄露 ID</span>
                <span v-else-if="acc.has_usable_custom_credentials" class="ce-badge is-success">可用专属凭证</span>
              </td>
              <td>
                <span v-if="acc.has_session" class="ce-badge is-success">.session</span>
                <span v-else class="ce-badge is-warn">仅 JSON</span>
              </td>
              <td class="row-wrap">
                <button
                  v-if="acc.has_usable_custom_credentials"
                  class="ce-link"
                  :disabled="vaultApplyingId === acc.account_id"
                  @click.stop="applyVaultCredentials(acc)"
                >
                  {{ vaultApplyingId === acc.account_id ? '写入中...' : '一键应用' }}
                </button>
                <span v-else class="ce-muted">请申请新 API</span>
                <button class="ce-link is-cyan" @click.stop="selectAndStartApps(acc)">申请专属 API</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="vaultGuidance" class="ce-alert is-warn">{{ vaultGuidance }}</div>
      <div v-if="vaultApplyResult" class="ce-alert" :class="vaultApplyResult.success ? 'is-ok' : 'is-danger'">
        <div>{{ vaultApplyResult.message }}</div>
        <div v-if="vaultApplyResult.warning" class="ce-tiny" style="color:#fcd34d;margin-top:4px">{{ vaultApplyResult.warning }}</div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { maskHash, getStatusBadgeClass } from '../../composables/useShared'
import { useConfig } from '../../composables/useConfig'
import { useVault } from '../../composables/useVault'

const { config, isPublishedCustomApiId } = useConfig()
const {
  vaultLoading, vaultAccounts, vaultMeta, vaultSelectedId, vaultApplyingId, vaultApplyResult,
  vaultGuidance, vaultFileInput, vaultUploading, vaultUploadDragging, vaultUploadProgress,
  vaultUploadResult, selectedVaultAccount, appsStarting, appsJob, appsShortname, appsPhone,
  appsManualCode, onVaultFilePicked, onVaultFileDrop, fetchVaultAccounts, applyVaultCredentials,
  startAppsJob, selectAndStartApps, submitAppsCode, applyAppsJob
} = useVault()
</script>
