<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>📱 硬件指纹 & 拓扑库</h2>
        <p>Base.db 真机指纹统计、采样模型与官方端点环境参数矩阵。</p>
      </div>
    </div>

    <div class="ce-panel is-glow between">
      <div class="row" style="align-items:flex-start">
        <span style="font-size:28px">📦</span>
        <div>
          <div class="row">
            <h3>已载入本地真机数据库 Base.db</h3>
            <span class="ce-badge is-success">{{ dbStats.total_count }} 套机型拓扑已就绪</span>
          </div>
          <p class="ce-tiny" style="margin-top:4px">
            包含 Realme、Motorola、Xiaomi、Huawei、Samsung 等真实硬件遥测样本。引导时自动伪随机采样。
          </p>
          <div v-if="dbStats.sample_models?.length" class="row-wrap" style="margin-top:8px">
            <span v-for="model in dbStats.sample_models.slice(0, 10)" :key="model" class="ce-badge is-info">{{ model }}</span>
          </div>
        </div>
      </div>
      <button class="ce-btn-ghost" @click="fetchDbStats">刷新状态</button>
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

    <div class="ce-panel stack">
      <h3>🔍 边缘节点环境遥测指纹提取规范</h3>
      <p class="ce-tiny">
        <strong>1. MTProto 协议端点对齐：</strong>
        握手阶段将 <code>device_model</code>、<code>system_version</code>、<code>app_version</code> 与目标拓扑
        <code>lang_code</code>、Attestation 签名精确对齐。
      </p>
      <p class="ce-tiny">
        <strong>2. 从真实 Android 设备导出硬件属性：</strong>
      </p>
      <pre class="ce-terminal" style="min-height:auto"># 导出整机系统属性字典
adb shell getprop > device_build_prop.txt

# 提取核心硬件指纹
adb shell getprop ro.product.model       # 设备型号 (如 SM-S918B)
adb shell getprop ro.product.brand       # 品牌 (如 samsung)
adb shell getprop ro.build.version.sdk   # SDK 版本 (如 33)
adb shell getprop ro.build.fingerprint   # 官方完整签名指纹</pre>
    </div>
  </section>
</template>

<script setup>
import { useTasks } from '../../composables/useTasks'

const { deviceProfiles, dbStats, fetchDbStats } = useTasks()
</script>
