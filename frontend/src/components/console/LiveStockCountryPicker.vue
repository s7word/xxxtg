<template>
  <div class="ce-stock-picker stack">
    <div class="between" style="align-items:flex-start;gap:10px">
      <div>
        <label class="ce-label">{{ label }}</label>
        <div class="ce-tiny">📊 实时有货拓扑选择器 · Live Stock Countries</div>
      </div>
      <div class="row-wrap">
        <span class="ce-badge is-info">{{ smsStock.total_countries || 0 }} 国有货</span>
        <span class="ce-badge is-success">⚡ {{ formatStockCount(smsStock.total_stock) }} 货</span>
        <button class="ce-btn-ghost" :disabled="smsStock.loading" @click="onRefresh">
          {{ smsStock.loading ? '刷新中...' : '🔄 刷新实时有货国家' }}
        </button>
      </div>
    </div>

    <input
      v-model="countrySearch"
      type="search"
      class="ce-input"
      placeholder="搜索国家名 / 区号 / ISO-2，例如 智利 · +56 · lv"
    />

    <select :value="modelValue" class="ce-select ce-stock-select" @change="onPick">
      <option v-if="!filteredStockCountries.length" value="">
        {{ smsStock.loading ? '正在拉取接码平台有货国家...' : '当前无匹配的有货国家' }}
      </option>
      <option v-for="item in filteredStockCountries" :key="item.code + ':' + item.provider" :value="item.code">
        {{ formatStockOption(item) }}
      </option>
    </select>

    <p class="ce-tiny">
      默认按 Telegram 库存量从多到少排序。
      <span v-if="smsStock.updated_at">
        更新于 {{ formatUpdated(smsStock.updated_at) }}
        <span v-if="smsStock.cached"> · 缓存 {{ Math.round(smsStock.cache_age_seconds || 0) }}s</span>
      </span>
      <span v-if="smsStock.provider"> · 源 {{ smsStock.provider === 'vaksms' ? 'Vak-SMS' : 'Grizzly SMS' }}</span>
    </p>
    <p v-if="smsStock.error" class="ce-tiny" style="color:var(--danger-soft)">{{ smsStock.error }}</p>
    <p v-else-if="selectedItem" class="ce-tiny">
      已选 {{ selectedItem.flag }} {{ selectedItem.name_zh || selectedItem.name }}
      · {{ (selectedItem.code || '').toUpperCase() }}
      · +{{ String(selectedItem.dial || '').replace(/^\+/, '') }}
      · ⚡ {{ formatStockCount(selectedItem.stock) }}
      <span v-if="selectedItem.cost"> · {{ Number(selectedItem.cost).toFixed(2) }}₽</span>
    </p>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useConfig } from '../../composables/useConfig'

const props = defineProps({
  modelValue: { type: String, default: '' },
  provider: { type: String, default: '' },
  label: { type: String, default: '目标拓扑与地理区域' }
})
const emit = defineEmits(['update:modelValue'])

const {
  smsStock,
  countrySearch,
  filteredStockCountries,
  formatStockCount,
  formatStockOption,
  fetchAvailableCountries
} = useConfig()

const selectedItem = computed(
  () => (smsStock.items || []).find((item) => item.code === props.modelValue) || null
)

const formatUpdated = (ts) => {
  if (!ts) return '-'
  const date = new Date(Number(ts) * 1000)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleTimeString()
}

const onPick = (event) => {
  emit('update:modelValue', event.target.value)
}

const onRefresh = () => {
  fetchAvailableCountries({ provider: props.provider, refresh: true }).catch(() => {})
}

watch(
  () => props.provider,
  (next, prev) => {
    if (next && next !== prev) {
      fetchAvailableCountries({ provider: next, toast: false }).catch(() => {})
    }
  }
)
</script>
