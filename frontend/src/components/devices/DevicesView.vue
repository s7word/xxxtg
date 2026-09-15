<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>📱 硬件指纹 & 拓扑库</h2>
        <p>
          iOS 与 Android 两套包分开调度。注册按目标国家抽对应平台；缺包时按该国语言/时区即时合成。
          当前基线是官方 iOS（<code>api_id=8</code> / <code>lang_pack=ios</code>），按指定国家生成。
        </p>
      </div>
      <div class="row-wrap">
        <button class="ce-btn-ghost" :disabled="deviceLoading" @click="fetchDeviceCatalog">
          {{ deviceLoading ? '刷新中...' : '刷新目录' }}
        </button>
        <button
          class="ce-btn-ghost"
          :disabled="purgeBusy || !deviceCatalogMeta.android_pack_count"
          @click="purgeAndroidPacks"
        >
          {{ purgeBusy ? '清理中...' : `清空 Android（${deviceCatalogMeta.android_pack_count || 0}）` }}
        </button>
      </div>
    </div>

    <div class="ce-panel is-glow between">
      <div class="row" style="align-items:flex-start">
        <span style="font-size:28px">📦</span>
        <div>
          <div class="row-wrap">
            <h3>调度池</h3>
            <span class="ce-badge is-success">{{ deviceCatalogMeta.enabled_packs }} 套已激活</span>
            <span class="ce-badge is-info">iOS {{ deviceCatalogMeta.ios_pack_count || 0 }}</span>
            <span class="ce-badge is-warn">Android {{ deviceCatalogMeta.android_pack_count || 0 }}</span>
            <span class="ce-badge">{{ deviceCatalogMeta.total_count }} 条样本</span>
          </div>
          <p class="ce-tiny" style="margin-top:4px">
            指定国家生成 iOS 会写入官方 api_id=8。Android 按下方 AntiSafety 模板选 App ID（4 / 6 / 21724），custom 模式不再盖这些值。
          </p>
          <div v-if="deviceCatalogMeta.active_countries.length" class="row-wrap" style="margin-top:8px">
            <span v-for="code in deviceCatalogMeta.active_countries" :key="code" class="ce-badge is-info">
              {{ countryFlag(code) }} {{ (code || '').toUpperCase() }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="grid-vault">
      <div
        class="ce-dropzone stack"
        :class="{ 'is-over': deviceUploadDragging }"
        @dragenter.prevent="deviceUploadDragging = true"
        @dragover.prevent="deviceUploadDragging = true"
        @dragleave.prevent="deviceUploadDragging = false"
        @drop.prevent="onDeviceFileDrop"
      >
        <div class="between">
          <div>
            <h3>📤 上传国家指纹库</h3>
            <p class="ce-tiny" style="margin-top:6px">
              接受 REGISTRATOR 结构 SQLite。系统解析机型 / SDK / 语言包 / 时区，并从文件名推断国家。
              上传包若带别人的 APP_ID，调度时仍按平台合同纠偏（iOS 钉 8）。
            </p>
          </div>
          <label class="ce-btn" style="cursor:pointer">
            <input
              ref="deviceFileInput"
              type="file"
              accept=".db,.sqlite,.sqlite3"
              class="hidden"
              :disabled="deviceUploading"
              @change="onDeviceFilePicked"
            />
            {{ deviceUploading ? '解析中...' : '选择 .db' }}
          </label>
        </div>
        <div v-if="deviceUploading || deviceUploadProgress > 0" class="stack">
          <div class="between ce-tiny">
            <span>{{ deviceUploading ? '正在上传并解析 REGISTRATOR...' : '上传完成' }}</span>
            <span class="mono">{{ deviceUploadProgress }}%</span>
          </div>
          <div class="ce-progress"><i :style="{ width: deviceUploadProgress + '%' }"></i></div>
        </div>
        <div v-if="deviceUploadResult" class="ce-alert" :class="deviceUploadResult.success === false ? 'is-danger' : 'is-ok'">
          {{ deviceUploadResult.message }}
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>🧬 指定国家合成</h3>
          <span class="ce-badge is-info">{{ generateBadge }}</span>
        </div>
        <p class="ce-tiny">
          iOS：16/17 系机型、iOS 18.x、App 12.9.3、<code>lang_pack=ios</code>，语言/时区跟出口国 overlay。
          Android：真机 SKU + 国别 locale，App ID 必须与 AntiSafety AID 模板对齐；custom 自建栏不再改设备参数。
        </p>
        <div class="grid-2">
          <div>
            <label class="ce-label">平台</label>
            <select
              class="ce-select"
              :value="generateForm.platform"
              @change="setGeneratePlatform($event.target.value)"
            >
              <option value="ios">iOS 官方（api_id=8）</option>
              <option value="android">Android（AntiSafety 模板）</option>
            </select>
          </div>
          <div>
            <label class="ce-label">目标国家</label>
            <select v-model="generateForm.country" class="ce-select">
              <optgroup v-for="group in countryGroups" :key="group.id" :label="group.label">
                <option v-for="item in group.options" :key="item.code" :value="item.code">
                  {{ item.label }}
                </option>
              </optgroup>
            </select>
          </div>
          <div v-if="generateForm.platform === 'android'" style="grid-column:1 / -1">
            <label class="ce-label">Android App ID / AntiSafety 模板</label>
            <select v-model="generateForm.app_type" class="ce-select">
              <option v-for="opt in ANDROID_GENERATE_OPTIONS" :key="opt.value" :value="opt.value">
                {{ opt.label }} · {{ opt.version }}
              </option>
            </select>
            <p class="ce-tiny" style="margin-top:6px">
              写入官方 <code>api_id={{ selectedAndroidGenerate.apiId }}</code>，
              版本 {{ selectedAndroidGenerate.version }}。
              AntiSafety AID（{{ selectedAndroidGenerate.aidKey }}）：
              <span class="mono">{{ selectedAndroidAid || '未配置' }}</span>
            </p>
          </div>
          <div>
            <label class="ce-label">样本条数</label>
            <input v-model.number="generateForm.count" type="number" min="8" max="5000" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">别名（可选）</label>
            <input v-model="generateForm.alias" type="text" class="ce-input" placeholder="例如：iOS 备用 葡萄牙 PT" />
          </div>
        </div>
        <label class="ce-label" style="display:flex;align-items:center;gap:8px">
          <input v-model="generateForm.enabled" type="checkbox" />
          生成后立即投入调度
        </label>
        <button class="ce-btn" :disabled="generateBusy" @click="generateDevicePack">
          {{ generateBusy ? '正在合成...' : `合成 ${generateForm.platform === 'ios' ? 'iOS' : 'Android'} · ${(generateForm.country || '').toUpperCase()}` }}
        </button>
      </div>
    </div>

    <div class="row-wrap" style="margin:8px 0">
      <button class="ce-btn-ghost" :class="{ 'is-glow': packFilter === 'ios' }" @click="packFilter = 'ios'">iOS 包</button>
      <button class="ce-btn-ghost" :class="{ 'is-glow': packFilter === 'android' }" @click="packFilter = 'android'">Android 包</button>
      <button class="ce-btn-ghost" :class="{ 'is-glow': packFilter === 'all' }" @click="packFilter = 'all'">全部</button>
    </div>

    <div v-if="!visiblePacks.length" class="ce-panel">
      <p class="ce-tiny">这一栏是空的。选国家后点「合成」，或清空 Android 后只看 iOS。</p>
    </div>

    <div class="grid-cards">
      <div
        v-for="pack in visiblePacks"
        :key="pack.id"
        class="ce-panel stack"
        :class="{ 'is-glow': selectedPackId === pack.id }"
        @click="selectedPackId = pack.id"
        style="cursor:pointer"
      >
        <div class="ce-panel-head">
          <h3>{{ countryFlag(pack.country) }} {{ pack.alias }}</h3>
          <span :class="pack.enabled ? 'ce-badge is-success' : 'ce-badge is-warn'">
            {{ pack.enabled ? '调度中' : '已停用' }}
          </span>
          <span class="ce-badge is-info">{{ pack.platform === 'ios' ? 'iOS' : (pack.app_type || 'Android') }}</span>
        </div>
        <div class="ce-stat"><span>国家</span><span>{{ (pack.country || '—').toUpperCase() }} · {{ pack.country_name || '未标注' }}</span></div>
        <div class="ce-stat"><span>样本</span><span>{{ pack.sample_count }}</span></div>
        <div class="ce-stat"><span>包内 App ID</span><span class="mono">{{ formatPackApiIds(pack) }}</span></div>
        <div class="ce-stat"><span>来源</span><span>{{ sourceLabel(pack.source) }}</span></div>
        <div class="ce-stat"><span>质量</span><span>{{ pack.quality?.score ?? '—' }} / 100</span></div>
        <div class="row-wrap">
          <span v-for="model in (pack.stats?.sample_models || []).slice(0, 4)" :key="model" class="ce-badge is-info">
            {{ model }}
          </span>
        </div>
        <div class="stack" @click.stop>
          <input
            v-model="renameDrafts[pack.id]"
            type="text"
            class="ce-input"
            placeholder="别名 / 标签"
          />
          <div class="row-wrap">
            <input
              v-model="countryDrafts[pack.id]"
              type="text"
              class="ce-input w-sm mono"
              placeholder="ca / cl / pt"
              style="max-width:88px"
            />
            <button
              class="ce-btn-ghost"
              :disabled="busyPackId === pack.id"
              @click="updateDevicePack(pack.id, { alias: renameDrafts[pack.id], country: countryDrafts[pack.id] })"
            >
              保存
            </button>
            <button class="ce-btn-ghost" :disabled="busyPackId === pack.id" @click="toggleDevicePack(pack, !pack.enabled)">
              {{ pack.enabled ? '停用' : '启用' }}
            </button>
            <button class="ce-btn-ghost" :disabled="busyPackId === pack.id" @click="deleteDevicePack(pack)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="selectedPack" class="ce-panel stack">
      <div class="ce-panel-head">
        <h3>📊 {{ selectedPack.alias }} · 解析画像</h3>
        <span class="ce-badge is-info">{{ selectedPack.sample_count }} 条</span>
      </div>
      <p class="ce-tiny">
        原始文件 {{ selectedPack.origin_name }} ·
        平台 {{ selectedPack.platform === 'ios' ? 'iOS' : 'Android' }} ·
        质量 {{ selectedPack.quality?.score ?? '—' }} ·
        {{ selectedPack.quality?.notes || '已完成 REGISTRATOR 解析' }}
      </p>
      <div class="grid-2">
        <div class="stack">
          <strong class="ce-tiny">{{ selectedPack.platform === 'ios' ? '机型分布' : '品牌分布' }}</strong>
          <div v-for="(count, name) in (selectedPack.platform === 'ios' ? selectedPack.stats?.models : selectedPack.stats?.brands) || {}" :key="'b'+name" class="stack" style="gap:4px">
            <div class="between ce-tiny"><span>{{ name }}</span><span class="mono">{{ count }}</span></div>
            <div class="ce-progress"><i :style="{ width: percentOf(count, selectedPack.sample_count) + '%' }"></i></div>
          </div>
        </div>
        <div class="stack">
          <strong class="ce-tiny">{{ selectedPack.platform === 'ios' ? '系统版本' : 'SDK 分布' }}</strong>
          <div v-for="(count, name) in selectedPack.stats?.sdks || {}" :key="'s'+name" class="stack" style="gap:4px">
            <div class="between ce-tiny"><span>{{ name }}</span><span class="mono">{{ count }}</span></div>
            <div class="ce-progress"><i :style="{ width: percentOf(count, selectedPack.sample_count) + '%' }"></i></div>
          </div>
        </div>
        <div class="stack">
          <strong class="ce-tiny">语言包 / 系统语言</strong>
          <div class="row-wrap">
            <span v-for="(count, name) in selectedPack.stats?.lang_packs || {}" :key="'lp'+name" class="ce-badge is-info">
              {{ name }} · {{ count }}
            </span>
            <span v-for="(count, name) in selectedPack.stats?.system_lang_codes || {}" :key="'sl'+name" class="ce-badge">
              {{ name }} · {{ count }}
            </span>
          </div>
        </div>
        <div class="stack">
          <strong class="ce-tiny">时区 / App ID</strong>
          <div class="row-wrap">
            <span v-for="(count, name) in selectedPack.stats?.tz_offsets || {}" :key="'tz'+name" class="ce-badge is-warn">
              tz {{ name }} · {{ count }}
            </span>
            <span v-for="(count, name) in selectedPack.stats?.api_ids || {}" :key="'api'+name" class="ce-badge">
              api_id {{ name }} · {{ count }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="grid-cards">
      <div v-for="p in deviceProfiles" :key="p.key" class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>{{ p.name }}</h3>
          <span class="ce-badge is-info">{{ p.app_name }}</span>
        </div>
        <span v-if="p.is_ios" class="ce-badge is-success">官方 iOS 模板（不被 custom 覆盖）</span>
        <span v-else-if="p.custom_overlay" class="ce-badge is-warn">Android 被全局 custom 覆盖</span>
        <span v-else-if="p.is_published_api_id" class="ce-badge is-warn">官方公开泄露 ID（需 Push Token）</span>
        <span v-else class="ce-badge is-success">模板官方凭证</span>
        <div class="ce-stat">
          <span>模板 App ID</span>
          <span class="mono">{{ p.template_api_id || p.api_id }}</span>
        </div>
        <div v-if="p.custom_overlay" class="ce-stat">
          <span>全局 custom（仅 Android）</span>
          <span class="mono">{{ p.api_id }}</span>
        </div>
        <div v-else class="ce-stat">
          <span>生效 App ID</span>
          <span class="mono">{{ p.api_id }}</span>
        </div>
        <div class="ce-stat"><span>设备硬件型号</span><span>{{ p.device_model }}</span></div>
        <div class="ce-stat"><span>操作系统版本</span><span>{{ p.system_version }}</span></div>
        <div class="ce-stat"><span>端点版本号</span><span>{{ p.app_version }}</span></div>
        <div class="ce-stat"><span>协议语言包</span><span>{{ p.lang_pack }}</span></div>
        <div v-if="!p.is_ios" class="ce-aid">Attestation AID: {{ p.aid || '—' }}</div>
        <div v-else class="ce-tiny">iOS 不使用 AntiSafety AID</div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { ANDROID_GENERATE_OPTIONS, COUNTRY_CATALOG, COUNTRY_GROUP_META, formatCountryLabel } from '../../composables/useShared'
import { useConfig } from '../../composables/useConfig'
import { useDevices } from '../../composables/useDevices'

const { config } = useConfig()

const {
  deviceProfiles,
  devicePacks,
  deviceCatalogMeta,
  selectedPackId,
  selectedPack,
  deviceLoading,
  deviceUploading,
  deviceUploadDragging,
  deviceUploadProgress,
  deviceUploadResult,
  deviceFileInput,
  generateForm,
  generateBusy,
  packFilter,
  purgeBusy,
  renameDrafts,
  countryDrafts,
  busyPackId,
  fetchDeviceCatalog,
  onDeviceFilePicked,
  onDeviceFileDrop,
  updateDevicePack,
  toggleDevicePack,
  deleteDevicePack,
  setGeneratePlatform,
  generateDevicePack,
  purgeAndroidPacks,
  percentOf,
  countryFlag
} = useDevices()

const countryGroups = computed(() => {
  const catalogByCode = Object.fromEntries(COUNTRY_CATALOG.map((item) => [item.value, item]))
  const items = COUNTRY_CATALOG.map((item) => {
    const code = item.value
    return { ...item, code, label: formatCountryLabel(item) }
  })
  const groups = COUNTRY_GROUP_META.map((group) => ({
    ...group,
    options: items.filter((item) => (item.group || catalogByCode[item.code]?.group) === group.id)
  })).filter((group) => group.options.length)
  return groups
})

const visiblePacks = computed(() => {
  const rows = devicePacks.value || []
  if (packFilter.value === 'ios') return rows.filter((item) => item.platform === 'ios')
  if (packFilter.value === 'android') return rows.filter((item) => item.platform !== 'ios')
  return rows
})

const sourceLabel = (source) => ({
  upload: '上传解析',
  generated: '参数化合成',
  imported: '遗留导入'
}[source] || source || '未知')

const selectedAndroidGenerate = computed(() => (
  ANDROID_GENERATE_OPTIONS.find((item) => item.value === generateForm.value.app_type) || ANDROID_GENERATE_OPTIONS[0]
))
const selectedAndroidAid = computed(() => (
  config.antisafety_aids?.[selectedAndroidGenerate.value.aidKey] || ''
))
const generateBadge = computed(() => {
  if (generateForm.value.platform === 'ios') return '官方 iOS api_id=8'
  return `Android api_id=${selectedAndroidGenerate.value.apiId}`
})

const formatPackApiIds = (pack) => {
  const ids = pack?.stats?.api_ids || {}
  const keys = Object.keys(ids)
  if (!keys.length) return pack?.platform === 'ios' ? '8（合成合同）' : '—'
  return keys.map((id) => `${id}×${ids[id]}`).join(' · ')
}
</script>
