<template>
  <section class="ce-page">
    <div class="ce-page-head">
      <div>
        <h2>⚙️ 参数拓扑 & 探针审计</h2>
        <p>SMS Bower / SMSCode / Grizzly SMS / Vak-SMS / REGHelp / AntiSafety / RecaptchaMobile / 2FA 一键探针，实时返回可见。</p>
      </div>
      <button class="ce-btn" :disabled="isSavingConfig" @click="saveConfig">
        {{ isSavingConfig ? '正在保存...' : '持久化全局配置' }}
      </button>
    </div>

    <div class="ce-panel stack">
      <div class="ce-panel-head">
        <div class="row">
          <h3>📡 SMSBazaar 通知 → 半自动注册</h3>
          <span class="ce-badge" :class="config.smsall_auto_register ? 'is-danger' : 'is-info'">
            {{ config.smsall_auto_register ? '全自动开跑' : '半自动：只收通知' }}
          </span>
        </div>
        <button class="ce-btn-ghost" :disabled="smsallLoading" @click="refreshSmsallStatus">
          {{ smsallLoading ? '刷新中...' : '刷新通知' }}
        </button>
      </div>
      <p class="ce-tiny">
        前期先半自动：Webhook 只记账，你在下表确认国家后再一键测试。
        确认流程没问题，再打开「通知后自动注册」。
      </p>
      <div class="ce-alert is-info">
        把下面两项原样填到 SMSBazaar「设置 → 程序推送」。Secret 是当前实际校验用的对接凭证。
      </div>
      <div>
        <label class="ce-label">Webhook URL</label>
        <div class="ce-copy-row">
          <input :value="smsallWebhookUrl" class="ce-input mono" readonly @focus="$event.target.select()" />
          <button type="button" class="ce-btn-ghost ce-btn-sm" @click="copyText(smsallWebhookUrl, 'Webhook URL')">复制</button>
        </div>
      </div>
      <div>
        <label class="ce-label">对接凭证 Secret</label>
        <div class="ce-copy-row">
          <input
            :value="smsallLiveSecret"
            :type="secretVisible ? 'text' : 'password'"
            class="ce-input mono"
            readonly
            :placeholder="smsallLiveSecret ? '' : '尚未生成，刷新通知后会出现'"
            @focus="$event.target.select()"
          />
          <button type="button" class="ce-btn-ghost ce-btn-sm" @click="secretVisible = !secretVisible">
            {{ secretVisible ? '隐藏' : '显示' }}
          </button>
          <button type="button" class="ce-btn ce-btn-sm" :disabled="!smsallLiveSecret" @click="copyText(smsallLiveSecret, 'Secret')">
            复制
          </button>
        </div>
      </div>
      <label class="ce-check">
        <input type="checkbox" v-model="config.smsall_auto_register" />
        通知后自动注册（确认好了再开；默认关）
      </label>
      <div v-if="config.smsall_auto_register" class="ce-alert is-warn">
        全自动已开：单价不超过阈值的 Telegram 补货/新上架会直接开跑。先半自动测几轮再开更稳。
      </div>
      <div v-if="config.smsall_auto_register" class="grid-2">
        <div>
          <label class="ce-label">自动开跑最高单价 USD</label>
          <input v-model.number="config.smsall_auto_max_price_usd" type="number" min="0" step="0.01" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">冷却秒数 / 同国</label>
          <input v-model.number="config.smsall_auto_cooldown_seconds" type="number" min="0" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">自动任务数</label>
          <input v-model.number="config.smsall_auto_count" type="number" min="1" max="10" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">自动并发</label>
          <input v-model.number="config.smsall_auto_concurrency" type="number" min="1" max="10" class="ce-input mono" />
        </div>
      </div>
      <div class="ce-panel stack" style="margin-top:4px">
        <div class="ce-panel-head">
          <div class="row">
            <h3>🎯 狙击（程序推送 → 狙击）→ 自动猎号</h3>
            <span class="ce-badge" :class="config.smsall_sniper_enabled ? 'is-danger' : 'is-info'">
              {{ config.smsall_sniper_enabled ? '狙击即开跑' : '狙击已关闭' }}
            </span>
          </div>
        </div>
        <label class="ce-check">
          <input type="checkbox" v-model="config.smsall_sniper_enabled" />
          收到狙击推送后自动开猎号（独立通道，不看上面的「通知后自动注册」开关）
        </label>
        <div v-if="config.smsall_sniper_enabled" class="ce-alert is-danger">
          ⚠️ 狙击是全自动烧钱通道：上游一推 sniper，就按下面参数直接开
          {{ config.smsall_sniper_count }} 路 × 每任务最多取号
          {{ config.smsall_sniper_max_number_attempts }} 次的猎号，
          最多可租 {{ (config.smsall_sniper_count || 0) * (config.smsall_sniper_max_number_attempts || 0) }} 个号
          （仍受猎号联合上限 hunt_max_total_leases 裁剪）。
          猎号规则：注册成功即停，失败号拉黑换号继续扫。不想自动花钱就关掉这个开关。
        </div>
        <div v-if="config.smsall_sniper_enabled" class="grid-2">
          <div>
            <label class="ce-label">狙击任务数 / 线程</label>
            <input v-model.number="config.smsall_sniper_count" type="number" min="1" max="10" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">狙击并发</label>
            <input v-model.number="config.smsall_sniper_concurrency" type="number" min="1" max="10" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">每任务最多取号次数（猎号深度）</label>
            <input v-model.number="config.smsall_sniper_max_number_attempts" type="number" min="1" max="500" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">狙击冷却秒数 / 同国（0=不冷却）</label>
            <input v-model.number="config.smsall_sniper_cooldown_seconds" type="number" min="0" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">单次推送最多开几个国家</label>
            <input v-model.number="config.smsall_sniper_max_countries" type="number" min="1" max="10" class="ce-input mono" />
          </div>
          <div>
            <label class="ce-label">狙击全局单价硬顶 USD（留空=不过滤；配置了按国列表时作备用）</label>
            <input v-model.number="config.smsall_sniper_max_price_usd" type="number" min="0" step="0.01" class="ce-input mono" placeholder="留空则任何单价都抢" />
          </div>
        </div>
        <div v-if="config.smsall_sniper_enabled" class="stack" style="margin-top:8px">
          <div class="row" style="justify-content:space-between;align-items:center">
            <label class="ce-label" style="margin:0">按国狙击单价上限（USD）</label>
            <button type="button" class="ce-btn-ghost ce-btn-sm" @click="addSniperPriceCap">+ 添加国家</button>
          </div>
          <p class="ce-tiny">
            列表非空时：只有列表里的国家才会自动开狙击，且推送 <code>priceUsd</code> 须 ≤ 该国上限。
            例：IQ 1.55、IR 1.0 —— 同国不同 supplier 会各开一批并带上 <code>supplierIds</code> 精确取号。
          </p>
          <div v-if="!(config.smsall_sniper_price_caps || []).length" class="ce-tiny ce-muted">未配置时回落「狙击全局单价硬顶」；两者都空则不过滤单价。</div>
          <div v-for="(row, idx) in config.smsall_sniper_price_caps" :key="idx" class="grid-2" style="align-items:end">
            <div>
              <label class="ce-label">国家 ISO2</label>
              <input v-model="row.country" type="text" maxlength="2" class="ce-input mono" placeholder="IQ" />
            </div>
            <div style="display:flex;gap:8px;align-items:end">
              <div style="flex:1">
                <label class="ce-label">不高于 USD</label>
                <input v-model.number="row.max_price_usd" type="number" min="0" step="0.001" class="ce-input mono" />
              </div>
              <button type="button" class="ce-btn-danger ce-btn-sm" @click="removeSniperPriceCap(idx)">删</button>
            </div>
          </div>
        </div>
        <label v-if="config.smsall_sniper_enabled" class="ce-check">
          <input type="checkbox" v-model="config.smsall_sniper_use_item_price_as_max" />
          用推送里的单价（上浮 10%）作为本批出价；关掉则用全局 {{ config.sms_max_price ?? '未设置' }}
        </label>
        <div class="ce-tiny">
          触发条件（任一命中）：payload.source=sniper · 请求头 X-Smsall-Sniper: 1 / X-Smsall-Priority: sniper ·
          item 上 sniper=true / tags 含 sniper / priority=sniper。
          接码源仍用全局 {{ smsProviderLabel(config.sms_provider) }}，不跟随上游平台切换。
        </div>
      </div>
      <div class="grid-2">
        <div>
          <label class="ce-label">一键测试 · 任务数</label>
          <input v-model.number="trialCount" type="number" min="1" max="10" class="ce-input mono" />
        </div>
        <div>
          <label class="ce-label">一键测试 · 线程 / 并发</label>
          <input v-model.number="trialConcurrency" type="number" min="1" max="10" class="ce-input mono" />
        </div>
      </div>
      <div class="ce-tiny">当前接码源 {{ smsProviderLabel(config.sms_provider) }} · 改完开关请点右上角保存 · 通知 {{ smsallEventCount }} 条</div>
      <div v-if="smsallEvents.length" class="stack">
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
          <button class="ce-btn-danger ce-btn-sm" :disabled="!selectedEventIds.length || deleteBusy" @click="deleteSelected">
            删除选中（{{ selectedEventIds.length }}）
          </button>
          <button class="ce-btn-ghost ce-btn-sm" :disabled="deleteBusy" @click="deleteAll">清空全部</button>
        </div>
        <div class="ce-table-wrap">
          <table class="ce-table">
            <thead>
              <tr>
                <th class="nowrap">
                  <input type="checkbox" :checked="allEventsSelected" @change="toggleSelectAll" />
                </th>
                <th class="nowrap">时间</th>
                <th>国家</th>
                <th class="nowrap">类型</th>
                <th class="nowrap">单价</th>
                <th class="nowrap">库存</th>
                <th>平台</th>
                <th class="nowrap">状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ev in smsallEvents" :key="ev.id || ev.at">
                <td>
                  <input type="checkbox" :checked="selectedEventIds.includes(ev.id)" :disabled="!ev.id" @change="toggleEvent(ev.id)" />
                </td>
                <td class="nowrap mono ce-tiny">{{ ev.received_at || formatEventTime(ev.at) }}</td>
                <td>
                  <strong>{{ countryFlag(ev.country) }} {{ (ev.country_name || ev.country || '—').toString() }}</strong>
                  <div v-if="ev.country" class="ce-tiny mono">{{ String(ev.country).toUpperCase() }}</div>
                </td>
                <td class="nowrap">
                  <span v-if="ev.sniper" class="ce-badge is-danger">狙击</span>
                  <span v-else>{{ eventTypeLabel(ev.type) }}</span>
                  <div v-if="ev.sniper && ev.max_number_attempts" class="ce-tiny mono">
                    {{ ev.planned_count || '?' }}×{{ ev.max_number_attempts }} 猎号
                  </div>
                </td>
                <td class="nowrap mono">{{ ev.price_usd != null ? '$' + Number(ev.price_usd).toFixed(2) : '—' }}</td>
                <td class="nowrap mono">{{ formatStockChange(ev) }}</td>
                <td class="ce-tiny">{{ ev.provider || '—' }}</td>
                <td class="nowrap">
                  <span class="ce-badge" :class="eventBadgeClass(ev)">{{ eventStatusLabel(ev) }}</span>
                  <div v-if="ev.batch_id" class="ce-tiny mono">{{ ev.batch_id }}</div>
                </td>
                <td>
                  <div style="display:flex;flex-wrap:wrap;gap:6px">
                    <button
                      class="ce-btn ce-btn-sm"
                      :disabled="!ev.country || trialBusy[ev.id]"
                      @click="trialRegister(ev)"
                    >
                      {{ trialBusy[ev.id] ? '提交中...' : '一键测试注册' }}
                    </button>
                    <button class="ce-btn-ghost ce-btn-sm" :disabled="!ev.country" @click="openInConsole(ev)">
                      去控制台
                    </button>
                    <button class="ce-btn-danger ce-btn-sm" :disabled="!ev.id || deleteBusy" @click="deleteOne(ev)">
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <p v-else class="ce-tiny">还没有收到推送。在 SMSBazaar 点「发送测试」会来一条 IN / $0.12 / restock，然后你可以指定线程数点「一键测试注册」。</p>
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
          <div class="row">
            <h3>📩 5SIM 接码平台 (5sim.net)</h3>
            <span class="ce-badge is-success">推荐</span>
          </div>
          <button class="ce-btn-ghost" :disabled="probeTesting.fivesim" @click="testFiveSim">
            {{ probeTesting.fivesim ? '测试中...' : '余额/连通性探针' }}
          </button>
        </div>
        <div>
          <label class="ce-label">当前接码提供源选择</label>
          <select v-model="config.sms_provider" class="ce-select">
            <option value="fivesim">5SIM (推荐)</option>
            <option value="grizzlysms">Grizzly SMS</option>
            <option value="smsbower">SMS Bower</option>
            <option value="smscode">SMSCode.gg</option>
            <option value="vaksms">Vak-SMS</option>
          </select>
        </div>
        <div>
          <label class="ce-label">5SIM API JWT Token</label>
          <input v-model="config.fivesim_api_key" type="password" class="ce-input mono" placeholder="Authorization: Bearer &lt;JWT&gt;" />
        </div>
        <div class="ce-tiny">
          一手全球接码 · 国家参数使用英文全名小写（<code>indonesia</code> / <code>usa</code> / <code>england</code>），
          控制台按 ISO-2 自动转换。失败路径自动 <code>/user/cancel</code> 或 <code>/user/ban</code> 退款。
        </div>
        <div v-if="testResults.fivesim" class="ce-alert" :class="testResults.fivesim.success ? 'is-ok' : 'is-danger'">
          <div>{{ testResults.fivesim.message }}</div>
          <div v-if="testResults.fivesim.data" class="mono ce-tiny">
            余额: {{ testResults.fivesim.data.balance }} {{ testResults.fivesim.data.currency || 'RUB' }}
            <span v-if="testResults.fivesim.data.email"> | 账号 {{ testResults.fivesim.data.email }}</span>
            <span v-if="testResults.fivesim.data.rating != null"> | 评分 {{ testResults.fivesim.data.rating }}</span>
            | 拓扑 {{ testResults.fivesim.data.country }} ({{ testResults.fivesim.data.country_slug }})
            | 库存: {{ testResults.fivesim.data.telegram_stock }}
          </div>
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <div class="row">
            <h3>📩 Grizzly SMS 接码平台 (grizzlysms.com)</h3>
            <span class="ce-badge is-info">备选</span>
          </div>
          <button class="ce-btn-ghost" :disabled="probeTesting.grizzlysms" @click="testGrizzlySms">
            {{ probeTesting.grizzlysms ? '测试中...' : '余额/连通性探针' }}
          </button>
        </div>
        <div>
          <label class="ce-label">Grizzly SMS API Key</label>
          <input v-model="config.grizzly_sms_api_key" type="password" class="ce-input mono" placeholder="66bd4d8e5f54db073d15c2856c9a1366" />
        </div>
        <div>
          <label class="ce-label">📈 动态最高出价上限 (Max Price / Bidding)</label>
          <input
            v-model.number="config.sms_max_price"
            type="number"
            min="0"
            step="0.01"
            class="ce-input mono"
            placeholder="如 0.55 / 1.0 (根据平台结算币种如美元/卢布填入)"
          />
          <p class="ce-tiny">
            按账户结算币种原样填写小数出价，不要换算。美元账户（currency:840）伊拉克约
            <code>$0.5294</code>，建议 <code>0.55</code> / <code>0.6</code> / <code>1.0</code>。
            误填 <code>50</code> / <code>100</code> 会被 Grizzly 拒绝并返回 <code>NO_NUMBERS</code>。
            平台在 <code>[底价, maxPrice]</code> 内匹配高优先级现卡。
          </p>
        </div>
        <LiveStockCountryPicker
          v-model="config.target_country"
          :provider="config.sms_provider"
          label="默认地理拓扑区域（实时有货）"
        />
        <div class="ce-tiny">
          国家列表由接码平台 <code>getPrices/getCountNumber</code> 动态发现，不再写死数量。
          失败路径自动 <code>setStatus=8</code> 退款。
        </div>
        <div v-if="testResults.grizzlysms" class="ce-alert" :class="testResults.grizzlysms.success ? 'is-ok' : 'is-danger'">
          <div>{{ testResults.grizzlysms.message }}</div>
          <div v-if="testResults.grizzlysms.data" class="mono ce-tiny">
            余额: {{ testResults.grizzlysms.data.balance }} {{ testResults.grizzlysms.data.currency || '账户结算币种' }}
            | 拓扑 {{ testResults.grizzlysms.data.country }} (id={{ testResults.grizzlysms.data.country_id }})
            | 库存: {{ testResults.grizzlysms.data.telegram_stock }}
          </div>
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <div class="row">
            <h3>📩 SMS Bower 接码平台 (smsbower.app)</h3>
            <span class="ce-badge is-info">备选</span>
          </div>
          <button class="ce-btn-ghost" :disabled="probeTesting.smsbower" @click="testSmsBower">
            {{ probeTesting.smsbower ? '测试中...' : '余额/连通性探针' }}
          </button>
        </div>
        <div>
          <label class="ce-label">SMS Bower API Key</label>
          <input v-model="config.smsbower_api_key" type="password" class="ce-input mono" placeholder="SMS Bower API Key" />
        </div>
        <div class="ce-tiny">
          协议与 SMS-Activate / Grizzly 兼容，Telegram 服务码 <code>tg</code>。
          将上方「当前接码提供源」切到 SMS Bower 后生效。失败路径自动 <code>setStatus=8</code> 退款。
        </div>
        <div v-if="testResults.smsbower" class="ce-alert" :class="testResults.smsbower.success ? 'is-ok' : 'is-danger'">
          <div>{{ testResults.smsbower.message }}</div>
          <div v-if="testResults.smsbower.data" class="mono ce-tiny">
            余额: {{ testResults.smsbower.data.balance }} {{ testResults.smsbower.data.currency || '账户结算币种' }}
            | 拓扑 {{ testResults.smsbower.data.country }} (id={{ testResults.smsbower.data.country_id }})
            | 库存: {{ testResults.smsbower.data.telegram_stock }}
          </div>
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <div class="row">
            <h3>📩 SMSCode 接码平台 (smscode.gg)</h3>
            <span class="ce-badge is-info">备选</span>
          </div>
          <button class="ce-btn-ghost" :disabled="probeTesting.smscode" @click="testSmsCode">
            {{ probeTesting.smscode ? '测试中...' : '余额/连通性探针' }}
          </button>
        </div>
        <div>
          <label class="ce-label">SMSCode API Token</label>
          <input v-model="config.smscode_api_key" type="password" class="ce-input mono" placeholder="在 Settings 填写，勿提交 Git" />
        </div>
        <div class="ce-tiny">
          官方 REST <code>/v2</code>（USD）。将上方「当前接码提供源」切到 SMSCode 后生效。
          失败路径自动 <code>POST /orders/cancel</code> 退款。详见 <code>docs/SMSCODE_GG.md</code>。
        </div>
        <div v-if="testResults.smscode" class="ce-alert" :class="testResults.smscode.success ? 'is-ok' : 'is-danger'">
          <div>{{ testResults.smscode.message }}</div>
          <div v-if="testResults.smscode.data" class="mono ce-tiny">
            余额: {{ testResults.smscode.data.balance }} {{ testResults.smscode.data.currency || 'USD' }}
            | 拓扑 {{ testResults.smscode.data.country }} (id={{ testResults.smscode.data.country_id }})
            | 库存: {{ testResults.smscode.data.telegram_stock }}
          </div>
        </div>
      </div>

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <div class="row">
            <h3>📩 Vak-SMS 带外挑战源</h3>
            <span class="ce-badge is-info">备选</span>
          </div>
          <button class="ce-btn-ghost" :disabled="probeTesting.vaksms" @click="testVakSms">
            {{ probeTesting.vaksms ? '测试中...' : '状态探针' }}
          </button>
        </div>
        <div>
          <label class="ce-label">OOB Telemetry API Key</label>
          <input v-model="config.vak_sms_api_key" type="password" class="ce-input mono" />
        </div>
        <div class="ce-tiny">
          含 🇨🇦 加拿大 <code>ca</code> (+1) · 智利 <code>cl</code> (+56) · 印度 <code>in</code> (+91) · 印尼 <code>id</code> (+62) 等全球拓扑。
          将上方「当前接码提供源」切到 Vak-SMS 后生效。
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
        <label class="ce-check">
          <input type="checkbox" v-model="config.push_token_save_issued" />
          REGHelp 新签发 Push Token 写入本地库存（失败未退款的可供复用）
        </label>
        <label class="ce-check">
          <input type="checkbox" v-model="config.push_token_reuse_enabled" />
          允许复用本地旧 Push Token（优先未使用，其次用过 1 次）
        </label>
        <div>
          <label class="ce-label">旧令牌最大使用次数</label>
          <input v-model.number="config.push_token_reuse_max_uses" type="number" min="1" max="5" class="ce-input mono w-sm" />
          <p class="ce-tiny ce-muted" style="margin-top:6px">达到上限后不再被选取。库存详情见「Push 令牌库」页。</p>
        </div>
        <div>
          <label class="ce-label">验证码投递通道策略</label>
          <select v-model="config.code_delivery_mode" class="ce-select">
            <option value="balanced">balanced（默认：自建 api_id 优先 SMS，泄露 ID 需 Push）</option>
            <option value="sms_first">sms_first（始终优先 SMS，FLOOD 时再 escalate Push）</option>
            <option value="push_required">push_required（legacy：始终 attach Push Token）</option>
          </select>
          <p class="ce-tiny ce-muted" style="margin-top:6px">
            控制 auth.sendCode 是否申请并 attach REGHelp Push Token（CodeSettings.token）。
            自建 api_id 下 balanced 会跳过 Push，提高 SentCodeTypeSms 概率。
          </p>
        </div>
        <label class="ce-check">
          <input type="checkbox" v-model="config.official_client_emulation" />
          官方客户端模拟（官方模板 api_id + 每轮 Push attach + Email / Play Integrity / 内购快退）
        </label>
        <p class="ce-tiny ce-muted">
          开启后运行时覆盖凭证与通道计划：强制官方模板 api_id/api_hash（
          <code>telegram_android</code>=6，<code>telegram_android_public</code>=4，
          <code>telegram_x</code>=21724）、push_required，并在
          <code>connect()</code> 前写入 InitConnection（<code>lang_pack=android</code> /
          <code>android_x</code> + 号国 tz）。处理 SetUpEmailRequired（REGHelp Email）、
          FirebaseSms（Play Integrity）、PaymentRequired（标记需官方 App 内购并快退）。
          猎号连续 App 强制 SMS 在此模式下关闭。
          Push attach 仍把 Android FCM 塞进文档标为 iOS 的 <code>CodeSettings.token</code>
          （错槽兼容，<strong>不是</strong> iOS 客户端）。
          <strong>vault 严格对齐开启时会钉死 api_id=4</strong>，不会漂到 6（Payment 路径）。
        </p>
        <label class="ce-check">
          <input
            type="checkbox"
            :checked="config.device_alignment_mode === 'strict' || config.strict_vault_device_alignment"
            @change="onStrictAlignmentToggle($event.target.checked)"
          />
          严格设备对齐（vault 成功样本 + Telegram Expert）
        </label>
        <p class="ce-tiny ce-muted">
          <code>device_alignment_mode=strict</code>：对照 vault 成功 JSON 与俄语农场手册，发码前强制齐套
          <code>api_id=4</code> + 配对 hash、<code>device_model</code>、<code>system_version</code>、
          <code>app_version</code>（钉 12.7.3）、<code>lang_code</code> / <code>system_lang_code</code>、
          <code>lang_pack=android</code>、号国 <code>tz_offset</code>，并在
          <code>connect()</code> 前写入 InitConnection。缺字段或模拟器机型 → <strong>拒绝发码</strong>。
          非 emu 必须 attach Push；<code>SentCodeTypeApp</code> 且无 <code>next_type</code> 快丢号；
          FLOOD 冷却换 Token。一号一代理（猎号 <code>hunt_proxy_max_uses=1</code>）。
          切到 loose：缺字段不再拒绝发码，api_id 可跟模板走 6；但官方 Android/X
          api_id（4/6/21724）或官方模拟仍会写入 InitConnection（补生产缺口），并非「只有显式旗标才写握手」。
        </p>
        <label class="ce-check">
          <input type="checkbox" v-model="config.app_delivery_fast_drop" />
          SentCodeTypeApp 且无 next_type 时快丢号（勿空等 2 分钟）
        </label>
        <label class="ce-check">
          <input type="checkbox" v-model="config.flood_rotate_push_token" />
          FLOOD 后冷却并换发 Push Token
        </label>
        <div>
          <label class="ce-label">FLOOD 门闩作用域</label>
          <select v-model="config.flood_window_scope" class="ce-select">
            <option value="process">process（进程内共享冷却记录）</option>
            <option value="task">task（仅记录；兄弟任务可继续）</option>
          </select>
        </div>
        <label class="ce-check">
          <input type="checkbox" v-model="config.flood_block_new_sends" />
          省钱硬停：冷却窗内跳过新租号 / sendCode（默认关；PUBLISHED_FLOOD 不拦新测试）
        </label>
        <label class="ce-check">
          <input type="checkbox" v-model="config.ignore_published_flood_window" />
          忽略 API_ID_PUBLISHED_FLOOD 硬门闩（硬停开启时用于临时放开并发探测）
        </label>
        <div>
          <label class="ce-label">PUBLISHED_FLOOD 冷却秒数</label>
          <input v-model.number="config.published_flood_hold_seconds" type="number" min="30" max="3600" class="ce-input mono w-sm" />
          <p class="ce-tiny ce-muted" style="margin-top:6px">
            默认：本号 <code>API_ID_PUBLISHED_FLOOD</code> 只结束本任务，<strong>不阻止</strong>后续新测试 / 其它并发租号。
            若要省钱硬停，勾选 <code>flood_block_new_sends</code>（可选再设冷却秒数，默认 120）。
          </p>
        </div>
        <label class="ce-check">
          <input type="checkbox" v-model="config.inject_vault_device_secret" />
          尝试把 vault device_secret 注入 FirebaseSms（默认关：nonce 不匹配，CodeSettings 无此槽位）
        </label>
        <div>
          <label class="ce-label">猎号连续 App 后强制 SMS（次数）</label>
          <input v-model.number="config.hunt_sms_first_after_app_streak" type="number" min="0" max="20" class="ce-input mono w-sm" />
          <p class="ce-tiny ce-muted" style="margin-top:6px">
            <code>hunt_sms_first_after_app_streak</code>：连续 SentCodeTypeApp 达到该值后后续轮次不 attach Push。0=关闭。
            严格设备对齐或官方模拟开启时忽略此项，始终 attach Push。
          </p>
        </div>
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
            <option value="auto">auto（先按官方 ID 申请 Push；未拿到且已泄露时回退自建凭证并重算通道）</option>
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

      <div class="ce-panel stack">
        <div class="ce-panel-head">
          <h3>🎯 猎号策略参数 (hunt_*)</h3>
          <span class="ce-badge is-warn">影响租号花费</span>
        </div>
        <div class="grid-2">
          <div>
            <label class="ce-label">默认每任务最多试号个数</label>
            <input v-model.number="config.hunt_default_max_attempts" type="number" min="1" max="500" class="ce-input mono w-sm" />
            <p class="ce-tiny ce-muted" style="margin-top:6px">
              <code>hunt_default_max_attempts</code>：控制台勾选猎号时的默认档位。
            </p>
          </div>
          <div>
            <label class="ce-label">联合上限（任务数 × 试号个数）</label>
            <input v-model.number="config.hunt_max_total_leases" type="number" min="1" max="5000" class="ce-input mono w-sm" />
            <p class="ce-tiny ce-muted" style="margin-top:6px">
              <code>hunt_max_total_leases</code>：一次启动最多向接码平台租号的次数。超过时后端按任务数裁剪试号个数并记日志。
            </p>
          </div>
          <div>
            <label class="ce-label">无库存软重试次数</label>
            <input v-model.number="config.hunt_no_number_retries" type="number" min="0" max="100" class="ce-input mono w-sm" />
            <p class="ce-tiny ce-muted" style="margin-top:6px"><code>hunt_no_number_retries</code>：NO_NUMBERS 时的重试次数。</p>
          </div>
          <div>
            <label class="ce-label">无库存软重试间隔（秒）</label>
            <input v-model.number="config.hunt_no_number_retry_delay_sec" type="number" min="0" max="60" step="0.5" class="ce-input mono w-sm" />
            <p class="ce-tiny ce-muted" style="margin-top:6px"><code>hunt_no_number_retry_delay_sec</code></p>
          </div>
          <div>
            <label class="ce-label">同一出口最多 sendCode 次数</label>
            <input v-model.number="config.hunt_proxy_max_uses" type="number" min="1" max="50" class="ce-input mono w-sm" />
            <p class="ce-tiny ce-muted" style="margin-top:6px">
              <code>hunt_proxy_max_uses</code>：达到后尝试从注册代理池换同国节点。批量槽位或显式指定出口时不会轮换。
              严格模式强制为 1（一号一代理）。
            </p>
          </div>
          <label class="ce-check">
            <input type="checkbox" v-model="config.proxy_require_country_match" />
            代理国必须等于号国（已标注异国的节点 / fallback 拒绝使用）
          </label>
          <div>
            <label class="ce-label">同一设备指纹最多 sendCode 次数</label>
            <input v-model.number="config.hunt_device_max_uses" type="number" min="1" max="50" class="ce-input mono w-sm" />
            <p class="ce-tiny ce-muted" style="margin-top:6px">
              <code>hunt_device_max_uses</code>：达到后重采样设备并换新 Push（Push 与设备绑定，不能只换机不换 Token）。
            </p>
          </div>
        </div>
        <div class="ce-alert">
          猎号只做两件事：注册成功即停；否则在试号个数内扫号并把不可用号（站内信 APP / 已注册 / 封禁）拉黑退订。
          试号次数用尽即结束（<code>HUNT_EXHAUSTED</code>），不会「扫完平台所有号码」。
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useConfig, smsProviderLabel } from '../../composables/useConfig'
import { useProbes } from '../../composables/useProbes'
import { countryFlag } from '../../composables/useShared'
import { applyIncomingBatch, fetchTasks } from '../../composables/useTasks'
import { goTab, pushToast } from '../../composables/useUi'
import LiveStockCountryPicker from '../console/LiveStockCountryPicker.vue'

const {
  config, isSavingConfig, saveConfig, reghelpBaseUrlsText,
  antisafetyBaseUrlsText, antisafetyReportingBaseUrlsText, form
} = useConfig()
const { probeTesting, testResults, testRegHelp, testAntiSafety, testFiveSim, testGrizzlySms, testSmsBower, testSmsCode, testVakSms } = useProbes()

const onStrictAlignmentToggle = (checked) => {
  config.strict_vault_device_alignment = !!checked
  config.device_alignment_mode = checked ? 'strict' : 'loose'
  if (checked) {
    config.pin_app_version_substr = config.pin_app_version_substr || '12.7.3'
    config.app_delivery_fast_drop = true
    config.flood_rotate_push_token = true
  }
}

const smsallLoading = ref(false)
const smsallEvents = ref([])
const smsallEventCount = ref(0)
const smsallLiveSecret = ref('')
const secretVisible = ref(true)
const selectedEventIds = ref([])
const deleteBusy = ref(false)
const trialCount = ref(1)
const trialConcurrency = ref(1)
const trialBusy = reactive({})
const smsallWebhookUrl = computed(() => `${window.location.origin}/hooks/smsall`)
let smsallPoll = null

const allEventsSelected = computed(() => {
  const ids = smsallEvents.value.map((ev) => ev.id).filter(Boolean)
  return ids.length > 0 && ids.every((id) => selectedEventIds.value.includes(id))
})

const clampTrial = (value, fallback = 1) => {
  const n = Number(value)
  if (!Number.isFinite(n)) return fallback
  return Math.max(1, Math.min(10, Math.trunc(n)))
}

const ensureSniperPriceCaps = () => {
  if (!Array.isArray(config.smsall_sniper_price_caps)) {
    config.smsall_sniper_price_caps = []
  }
}

const addSniperPriceCap = () => {
  ensureSniperPriceCaps()
  config.smsall_sniper_price_caps.push({ country: '', max_price_usd: 1 })
}

const removeSniperPriceCap = (idx) => {
  ensureSniperPriceCaps()
  config.smsall_sniper_price_caps.splice(idx, 1)
}

const formatEventTime = (at) => {
  const ts = Number(at)
  if (!Number.isFinite(ts) || ts <= 0) return '—'
  return new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
}

const eventTypeLabel = (type) => {
  if (type === 'restock') return '补货'
  if (type === 'new_listing') return '新上架'
  return type || '—'
}

const formatStockChange = (ev) => {
  if (ev.stock_from == null && ev.stock_to == null) return '—'
  return `${ev.stock_from ?? 0} → ${ev.stock_to ?? 0}`
}

const eventStatusLabel = (ev) => {
  if (ev.action === 'trial') return '已手动测试'
  if (ev.action === 'launch') return ev.sniper ? '狙击已开猎号' : '已自动开跑'
  if (ev.reason === 'sniper_disabled') return '狙击已关'
  if (ev.action === 'received') return '待确认'
  if (ev.reason === 'price_above_cap') return '超阈值'
  if (ev.reason === 'country_not_in_caps') return '未配该国上限'
  if (ev.reason === 'upstream_no_balance') return '上游无余额'
  if (ev.reason === 'awaiting_confirm' || ev.reason === 'auto_disabled') return '待确认'
  if (ev.action === 'ignored') return '已忽略'
  return ev.reason || ev.action || '已收'
}

const eventBadgeClass = (ev) => {
  if (ev.action === 'trial' || ev.action === 'launch') return 'is-success'
  if (ev.action === 'received') return 'is-info'
  if (ev.reason === 'price_above_cap') return 'is-warn'
  if (ev.action === 'ignored') return 'is-warn'
  return 'is-info'
}

const refreshSmsallStatus = async () => {
  smsallLoading.value = true
  try {
    const res = await fetch('/api/smsall/status?limit=200')
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || '读取失败')
    smsallEvents.value = data.events || []
    smsallEventCount.value = data.event_count ?? smsallEvents.value.length
    if (data.webhook_secret) {
      smsallLiveSecret.value = data.webhook_secret
    }
    const alive = new Set(smsallEvents.value.map((ev) => ev.id).filter(Boolean))
    selectedEventIds.value = selectedEventIds.value.filter((id) => alive.has(id))
  } catch (e) {
    smsallEvents.value = []
  } finally {
    smsallLoading.value = false
  }
}

const copyText = async (text, label) => {
  const value = String(text || '')
  if (!value) {
    pushToast('warn', `${label} 还是空的`)
    return
  }
  try {
    await navigator.clipboard.writeText(value)
    pushToast('ok', `已复制${label}`)
  } catch (e) {
    const box = document.createElement('textarea')
    box.value = value
    document.body.appendChild(box)
    box.select()
    document.execCommand('copy')
    box.remove()
    pushToast('ok', `已复制${label}`)
  }
}

const toggleEvent = (id) => {
  if (!id) return
  const set = new Set(selectedEventIds.value)
  if (set.has(id)) set.delete(id)
  else set.add(id)
  selectedEventIds.value = [...set]
}

const toggleSelectAll = () => {
  if (allEventsSelected.value) {
    selectedEventIds.value = []
    return
  }
  selectedEventIds.value = smsallEvents.value.map((ev) => ev.id).filter(Boolean)
}

const deleteEvents = async (payload) => {
  deleteBusy.value = true
  try {
    const res = await fetch('/api/smsall/events/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '删除失败')
    pushToast('ok', data.message || '已删除')
    selectedEventIds.value = []
    await refreshSmsallStatus()
  } catch (e) {
    pushToast('danger', `删除失败: ${e.message}`)
  } finally {
    deleteBusy.value = false
  }
}

const deleteOne = async (ev) => {
  if (!ev?.id) return
  await deleteEvents({ event_ids: [ev.id] })
}

const deleteSelected = async () => {
  if (!selectedEventIds.value.length) return
  await deleteEvents({ event_ids: [...selectedEventIds.value] })
}

const deleteAll = async () => {
  if (!smsallEvents.value.length) return
  if (!window.confirm(`清空全部 ${smsallEventCount.value || smsallEvents.value.length} 条通知？`)) return
  await deleteEvents({ clear_all: true })
}

const trialRegister = async (ev) => {
  const country = String(ev?.country || '').toLowerCase()
  if (!country) return
  const count = clampTrial(trialCount.value, 1)
  const concurrency = Math.min(clampTrial(trialConcurrency.value, 1), count)
  trialBusy[ev.id] = true
  try {
    const res = await fetch('/api/smsall/trial', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_id: ev.id || null,
        country,
        count,
        concurrency
      })
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '提交失败')
    form.country = country
    applyIncomingBatch(data)
    pushToast('ok', data.message || `${country.toUpperCase()} 测试已提交`)
    await Promise.all([refreshSmsallStatus(), fetchTasks()])
    goTab('console')
  } catch (e) {
    pushToast('danger', `测试注册失败: ${e.message}`)
  } finally {
    trialBusy[ev.id] = false
  }
}

const openInConsole = (ev) => {
  const country = String(ev?.country || '').toLowerCase()
  if (country) form.country = country
  goTab('console')
}

onMounted(() => {
  refreshSmsallStatus()
  smsallPoll = setInterval(refreshSmsallStatus, 8000)
})

onUnmounted(() => {
  if (smsallPoll) clearInterval(smsallPoll)
})
</script>
