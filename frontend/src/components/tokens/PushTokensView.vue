<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>♻️ Push 令牌库</h2>
        <p>
          本地保存 REGHelp 签发的 Push Token。开启复用后，按<strong>未使用 → 用过 1 次</strong>排序取用；
          已成功注册或已 setStatus 退款的不会再复用。完整令牌不在此展示。
        </p>
      </div>
      <div class="ce-actions">
        <button class="ce-btn-ghost" :disabled="pushTokenLoading" @click="fetchPushTokens">
          {{ pushTokenLoading ? '刷新中...' : '刷新库存' }}
        </button>
        <button class="ce-btn-ghost" :disabled="!!pushTokenBusy" @click="onPurge">
          {{ pushTokenBusy === 'purge' ? '清理中...' : '清理已退款/已消耗' }}
        </button>
      </div>
    </div>

    <div class="ce-grid stats">
      <div class="ce-stat"><span>总计</span><span>{{ pushTokenSummary.total }}</span></div>
      <div class="ce-stat"><span>可复用</span><span>{{ pushTokenSummary.reusable }}</span></div>
      <div class="ce-stat"><span>未使用</span><span>{{ pushTokenSummary.unused }}</span></div>
      <div class="ce-stat"><span>用过 1 次</span><span>{{ pushTokenSummary.used_once }}</span></div>
      <div class="ce-stat"><span>已成功消耗</span><span>{{ pushTokenSummary.consumed }}</span></div>
      <div class="ce-stat"><span>已退款</span><span>{{ pushTokenSummary.refunded }}</span></div>
    </div>

    <div class="ce-panel stack" style="margin-top:14px">
      <div class="ce-chips">
        <span class="ce-chip" :class="pushTokenMeta.reuse_enabled ? 'is-ok' : ''">
          复用开关：{{ pushTokenMeta.reuse_enabled ? '已开启' : '关闭（默认）' }}
        </span>
        <span class="ce-chip">最大使用次数 {{ pushTokenMeta.reuse_max_uses }}</span>
        <span class="ce-chip">签发入库：{{ pushTokenMeta.save_issued ? '开' : '关' }}</span>
      </div>
      <p class="ce-tiny ce-muted">
        开关在「参数拓扑 & 探针审计」中修改并保存配置后生效。复用有风控不确定性，建议小流量验证。
      </p>
    </div>

    <div class="ce-panel" style="margin-top:14px">
      <div class="ce-panel-head">
        <h3>库存明细</h3>
        <span class="ce-muted">{{ pushTokenItems.length }} 条</span>
      </div>
      <div v-if="!pushTokenItems.length" class="ce-empty">暂无本地 Push Token 记录</div>
      <div v-else class="stack" style="gap:8px">
        <div v-for="item in pushTokenItems" :key="item.id" class="ce-item ce-item-wrap">
          <div class="between" style="width:100%; gap:12px; flex-wrap:wrap">
            <div class="stack" style="gap:4px; min-width:220px">
              <div class="mono">{{ item.token_preview || item.id }}</div>
              <div class="ce-tiny ce-muted">
                id={{ item.id }} · task={{ item.reghelp_task_id || '-' }} · {{ item.app_type || item.app_name || '-' }}
              </div>
            </div>
            <div class="ce-chips">
              <span class="ce-badge" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span>
              <span class="ce-badge">使用 {{ item.use_count }} 次</span>
              <span v-if="item.last_outcome" class="ce-badge">{{ item.last_outcome }}</span>
            </div>
            <div class="ce-tiny ce-muted mono">
              创建 {{ shortTime(item.created_at) }}
              <span v-if="item.last_used_at"> · 最近 {{ shortTime(item.last_used_at) }}</span>
            </div>
            <button
              class="ce-link is-danger"
              :disabled="!!pushTokenBusy"
              @click="deletePushToken(item.id)"
            >删除</button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { onMounted } from 'vue'
import { usePushTokens } from '../../composables/usePushTokens'

const {
  pushTokenLoading,
  pushTokenBusy,
  pushTokenMeta,
  pushTokenSummary,
  pushTokenItems,
  fetchPushTokens,
  deletePushToken,
  purgePushTokens,
} = usePushTokens()

const statusLabel = (status) => {
  if (status === 'available') return '可复用'
  if (status === 'consumed') return '已成功'
  if (status === 'refunded') return '已退款'
  if (status === 'retired') return '已退役'
  return status || '未知'
}

const statusClass = (status) => {
  if (status === 'available') return 'is-success'
  if (status === 'consumed') return 'is-info'
  if (status === 'refunded') return 'is-warn'
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

const onPurge = () => {
  if (!confirm('确认清理全部「已退款」和「已成功消耗」的 Push Token 记录？')) return
  purgePushTokens({ refunded: true, consumed: true, exhausted: false })
}

onMounted(() => {
  fetchPushTokens()
})
</script>
