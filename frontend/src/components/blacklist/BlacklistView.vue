<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>🚫 号码黑名单</h2>
        <p>
          本地记录已确认<strong>拉黑</strong>或<strong>已注册</strong>（含站内 App 推送）的号码。
          接码平台再次下发时，在预检 / Push Token / sendCode 之前直接退订，避免重复走注册路程。
        </p>
      </div>
      <div class="ce-actions">
        <button class="ce-btn-ghost" :disabled="blacklistLoading" @click="fetchBlacklist">
          {{ blacklistLoading ? '刷新中...' : '刷新' }}
        </button>
        <button class="ce-btn-ghost" :disabled="!!blacklistBusy" @click="onPurgeCategory">
          {{ blacklistBusy === 'purge' ? '清理中...' : '按筛选清理' }}
        </button>
        <button class="ce-btn-ghost is-danger" :disabled="!!blacklistBusy" @click="onPurgeAll">
          清空全部
        </button>
      </div>
    </div>

    <div class="ce-grid stats">
      <div class="ce-stat"><span>总计</span><span>{{ blacklistSummary.total }}</span></div>
      <div class="ce-stat"><span>已拉黑</span><span>{{ blacklistSummary.banned }}</span></div>
      <div class="ce-stat"><span>已注册</span><span>{{ blacklistSummary.already_registered }}</span></div>
      <div class="ce-stat"><span>手动</span><span>{{ blacklistSummary.manual }}</span></div>
    </div>

    <div class="ce-panel stack" style="margin-top:14px">
      <div class="ce-panel-head">
        <h3>查询与录入</h3>
        <span class="ce-muted">{{ blacklistMessage || '—' }}</span>
      </div>
      <div class="ce-actions" style="flex-wrap:wrap; gap:8px">
        <input
          v-model="blacklistQuery"
          class="ce-input"
          placeholder="按号码搜索（数字）"
          style="min-width:160px"
          @keyup.enter="fetchBlacklist"
        />
        <select v-model="blacklistCategory" class="ce-input" style="min-width:140px" @change="fetchBlacklist">
          <option value="">全部分类</option>
          <option value="banned">已拉黑</option>
          <option value="already_registered">已注册</option>
          <option value="manual">手动</option>
        </select>
        <input
          v-model="blacklistCountry"
          class="ce-input"
          placeholder="国家码 za/co/id…"
          style="width:120px"
          @keyup.enter="fetchBlacklist"
        />
        <button class="ce-btn-ghost" :disabled="blacklistLoading" @click="fetchBlacklist">筛选</button>
      </div>
      <div class="ce-actions" style="flex-wrap:wrap; gap:8px; margin-top:8px">
        <input
          v-model="addForm.phone"
          class="ce-input"
          placeholder="手动录入 +2782…"
          style="min-width:180px"
          @keyup.enter="addBlacklistPhone"
        />
        <select v-model="addForm.category" class="ce-input" style="min-width:120px">
          <option value="manual">手动</option>
          <option value="banned">已拉黑</option>
          <option value="already_registered">已注册</option>
        </select>
        <input
          v-model="addForm.note"
          class="ce-input"
          placeholder="备注（可选）"
          style="min-width:160px"
        />
        <button class="ce-btn" :disabled="blacklistBusy === 'add'" @click="addBlacklistPhone">
          {{ blacklistBusy === 'add' ? '录入中...' : '加入黑名单' }}
        </button>
      </div>
    </div>

    <div class="ce-panel" style="margin-top:14px">
      <div class="ce-panel-head">
        <h3>明细</h3>
        <span class="ce-muted">显示 {{ blacklistItems.length }} / 共 {{ blacklistTotal }}</span>
      </div>
      <div v-if="!blacklistItems.length" class="ce-empty">暂无黑名单记录</div>
      <div v-else class="stack" style="gap:8px">
        <div v-for="item in blacklistItems" :key="item.digits" class="ce-item ce-item-wrap">
          <div class="between" style="width:100%; gap:12px; flex-wrap:wrap">
            <div class="stack" style="gap:4px; min-width:200px">
              <div class="mono">{{ item.phone || ('+' + item.digits) }}</div>
              <div class="ce-tiny ce-muted">
                {{ item.country || '?' }} · {{ item.prefix || '-' }} · {{ item.source || '-' }}
                <span v-if="item.note"> · {{ item.note }}</span>
              </div>
            </div>
            <div class="ce-chips">
              <span class="ce-badge" :class="categoryClass(item.category)">{{ categoryLabel(item.category) }}</span>
              <span class="ce-badge">{{ item.reason || '-' }}</span>
              <span class="ce-badge">命中 {{ item.hits }}</span>
            </div>
            <div class="ce-tiny ce-muted mono">
              首见 {{ shortTime(item.first_seen) }}
              <span v-if="item.last_seen"> · 最近 {{ shortTime(item.last_seen) }}</span>
            </div>
            <button
              class="ce-link is-danger"
              :disabled="!!blacklistBusy"
              @click="deleteBlacklistPhone(item.digits || item.phone)"
            >移除</button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { onMounted } from 'vue'
import { useBlacklist } from '../../composables/useBlacklist'

const {
  blacklistLoading,
  blacklistBusy,
  blacklistQuery,
  blacklistCategory,
  blacklistCountry,
  blacklistSummary,
  blacklistItems,
  blacklistTotal,
  blacklistMessage,
  addForm,
  fetchBlacklist,
  addBlacklistPhone,
  deleteBlacklistPhone,
  purgeBlacklist,
} = useBlacklist()

const categoryLabel = (c) => {
  if (c === 'banned') return '已拉黑'
  if (c === 'already_registered') return '已注册'
  if (c === 'manual') return '手动'
  return c || '未知'
}

const categoryClass = (c) => {
  if (c === 'banned') return 'is-danger'
  if (c === 'already_registered') return 'is-warn'
  if (c === 'manual') return 'is-info'
  return ''
}

const shortTime = (iso) => {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

const onPurgeCategory = () => {
  const cat = blacklistCategory.value
  const label = cat ? categoryLabel(cat) : '全部（当前筛选分类为空=全部）'
  if (!confirm(`确认清理黑名单中「${label}」记录？`)) return
  purgeBlacklist(cat || null)
}

const onPurgeAll = () => {
  if (!confirm('确认清空全部号码黑名单？此操作不可恢复。')) return
  purgeBlacklist(null)
}

onMounted(fetchBlacklist)
</script>
