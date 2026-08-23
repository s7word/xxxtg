<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>📱 硬件指纹 & 拓扑库</h2>
        <p>
          多国家 REGISTRATOR 指纹包持久化在 <code>data/device_dbs/</code>。
          注册调度按目标国家优先抽取已激活库；也可按品牌规则库一键合成。
        </p>
      </div>
      <button class="ce-btn-ghost" :disabled="deviceLoading" @click="fetchDeviceCatalog">
        {{ deviceLoading ? '刷新中...' : '刷新目录' }}
      </button>
    </div>

    <div class="ce-panel is-glow between">
      <div class="row" style="align-items:flex-start">
        <span style="font-size:28px">📦</span>
        <div>
          <div class="row-wrap">
            <h3>调度池</h3>
            <span class="ce-badge is-success">{{ deviceCatalogMeta.enabled_packs }} 套已激活</span>
            <span class="ce-badge is-info">{{ deviceCatalogMeta.total_count }} 条样本</span>
            <span v-if="deviceCatalogMeta.disabled_packs" class="ce-badge is-warn">
              {{ deviceCatalogMeta.disabled_packs }} 套停用
            </span>
          </div>
          <p class="ce-tiny" style="margin-top:4px">
            国家匹配优先；无匹配时回退到任一已激活包，并强制覆盖目标国家的语言 / 时区，避免智利包打进印尼任务。
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
              接受 <code>2026-08-23_14-49-28_ Indonesia.db</code> 这类固定表结构 SQLite。
              系统自动解析机型 / SDK / 语言包 / 时区，并从文件名推断国家。
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
          <h3>🧬 参数化合成</h3>
          <span class="ce-badge is-info">真机 SKU 规则库</span>
        </div>
        <p class="ce-tiny">
          不是盲目随机机型名。按 Samsung / Xiaomi / Huawei / Motorola / Realme / Vivo / OPPO
          真实货号、出厂 SDK 区间、Telegram Android 版本矩阵和国家语言/时区联合分布生成。
        </p>
        <div class="grid-2">
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
          <div>
            <label class="ce-label">样本条数</label>
            <input v-model.number="generateForm.count" type="number" min="10" max="5000" class="ce-input mono" />
          </div>
          <div class="span-2">
            <label class="ce-label">别名（可选）</label>
            <input v-model="generateForm.alias" type="text" class="ce-input" placeholder="例如：印尼安装300.db" />
          </div>
        </div>
        <label class="ce-label" style="display:flex;align-items:center;gap:8px">
          <input v-model="generateForm.enabled" type="checkbox" />
          生成后立即投入调度
        </label>
        <button class="ce-btn" :disabled="generateBusy" @click="generateDevicePack">
          {{ generateBusy ? '正在按规则库合成...' : '一键合成该国家指纹库' }}
        </button>
      </div>
    </div>

    <div v-if="!devicePacks.length" class="ce-panel">
      <p class="ce-tiny">目录为空。上传现有 Base.db / Indonesia.db，或先合成一套目标国家样本。</p>
    </div>

    <div class="grid-cards">
      <div
        v-for="pack in devicePacks"
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
        </div>
        <div class="ce-stat"><span>国家</span><span>{{ (pack.country || '—').toUpperCase() }} · {{ pack.country_name || '未标注' }}</span></div>
        <div class="ce-stat"><span>样本</span><span>{{ pack.sample_count }}</span></div>
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
              placeholder="ca / cl / id"
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
        质量 {{ selectedPack.quality?.score ?? '—' }} ·
        {{ selectedPack.quality?.notes || '已完成 REGISTRATOR 解析' }}
      </p>
      <div class="grid-2">
        <div class="stack">
          <strong class="ce-tiny">品牌分布</strong>
          <div v-for="(count, name) in selectedPack.stats?.brands || {}" :key="'b'+name" class="stack" style="gap:4px">
            <div class="between ce-tiny"><span>{{ name }}</span><span class="mono">{{ count }}</span></div>
            <div class="ce-progress"><i :style="{ width: percentOf(count, selectedPack.sample_count) + '%' }"></i></div>
          </div>
        </div>
        <div class="stack">
          <strong class="ce-tiny">SDK 分布</strong>
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
          <strong class="ce-tiny">时区偏置 / 性能档</strong>
          <div class="row-wrap">
            <span v-for="(count, name) in selectedPack.stats?.tz_offsets || {}" :key="'tz'+name" class="ce-badge is-warn">
              tz {{ name }} · {{ count }}
            </span>
            <span v-for="(count, name) in selectedPack.stats?.perf_cats || {}" :key="'pf'+name" class="ce-badge">
              perf {{ name }} · {{ count }}
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
        <span v-if="p.is_published_api_id" class="ce-badge is-warn">官方公开泄露 ID（需 Push Token）</span>
        <span v-else class="ce-badge is-success">自建开发者凭证</span>
        <div class="ce-stat"><span>API ID / Hash</span><span>{{ p.api_id }} / {{ (p.api_hash || '').substring(0, 8) }}...</span></div>
        <div class="ce-stat"><span>设备硬件型号</span><span>{{ p.device_model }}</span></div>
        <div class="ce-stat"><span>操作系统版本</span><span>{{ p.system_version }}</span></div>
        <div class="ce-stat"><span>端点版本号</span><span>{{ p.app_version }}</span></div>
        <div class="ce-stat"><span>构建编号</span><span>{{ p.app_build }}</span></div>
        <div class="ce-stat"><span>协议语言包</span><span>{{ p.lang_pack }}</span></div>
        <div class="ce-aid">Attestation AID: {{ p.aid }}</div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { COUNTRY_CATALOG, COUNTRY_GROUP_META, formatCountryLabel } from '../../composables/useShared'
import { useDevices } from '../../composables/useDevices'

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
  renameDrafts,
  countryDrafts,
  busyPackId,
  fetchDeviceCatalog,
  onDeviceFilePicked,
  onDeviceFileDrop,
  updateDevicePack,
  toggleDevicePack,
  deleteDevicePack,
  generateDevicePack,
  percentOf,
  countryFlag
} = useDevices()

const countryGroups = computed(() => {
  const listed = deviceCatalogMeta.value.supported_countries || []
  const catalogByCode = Object.fromEntries(COUNTRY_CATALOG.map((item) => [item.value, item]))
  const items = (listed.length ? listed : COUNTRY_CATALOG).map((item) => {
    const code = item.code || item.value
    const extra = catalogByCode[code] || {}
    const merged = {
      ...extra,
      ...item,
      value: code,
      code,
      name_zh: item.name_zh || extra.name_zh,
      name_en: extra.name_en || item.name,
      dial: item.dial ? (String(item.dial).startsWith('+') ? item.dial : `+${item.dial}`) : extra.dial
    }
    return { ...merged, label: formatCountryLabel(merged) }
  })
  const groups = COUNTRY_GROUP_META.map((group) => ({
    ...group,
    options: items.filter((item) => (item.group || catalogByCode[item.code]?.group || 'other') === group.id)
  })).filter((group) => group.options.length)
  const groupedCodes = new Set(groups.flatMap((group) => group.options.map((item) => item.code)))
  const leftover = items.filter((item) => !groupedCodes.has(item.code))
  if (leftover.length) {
    groups.push({ id: 'other', label: '其它 · Other', options: leftover })
  }
  return groups
})

const sourceLabel = (source) => ({
  upload: '上传解析',
  generated: '参数化合成',
  imported: '遗留导入'
}[source] || source || '未知')
</script>
