<!--
  Vue 3 示例组件：登录 + 活动列表 + 一句话找局
  ---------------------------------------------------------------------------
  用法：
    1. 把 frontend/api.js 复制到你 Vue 项目的 src/ 下
    2. 本文件放到 src/components/ 或 src/views/ 下
    3. 在页面里引进来即可：
         <script setup>
         import ActivityList from './components/ActivityList.vue'
         </script>
         <template><ActivityList /></template>

  这是一个「能跑起来的最小可用页面」，把后端的主要接口都用了一遍，
  抄过去改样式就能当作业交。
-->
<script setup>
import { computed, onMounted, ref } from 'vue'
import api, { ApiError } from '../api.js'

/* ---------------- 状态 ---------------- */
const loading = ref(false)
const error = ref('')
const me = ref(null)
const activities = ref([])
const matches = ref([])
const sports = ref([])

const loginForm = ref({ username: '', password: '' })
const sentence = ref('今晚想在旗山校区打篮球，新手可以吗')

const isLoggedIn = computed(() => Boolean(me.value))

/* ---------------- 通用包装：统一错误处理 ---------------- */
async function run(fn) {
  loading.value = true
  error.value = ''
  try {
    await fn()
  } catch (e) {
    if (e instanceof ApiError) {
      error.value = e.message // 后端返回的中文提示，可直接显示
      if (e.isAuthError) me.value = null
    } else {
      error.value = '发生未知错误：' + e.message
    }
  } finally {
    loading.value = false
  }
}

/* ---------------- 动作 ---------------- */

const login = () =>
  run(async () => {
    await api.login(loginForm.value.username, loginForm.value.password)
    me.value = await api.getMe() // 登录后拿资料（含信用分）
    await loadActivities()
  })

const register = () =>
  run(async () => {
    await api.register({
      username: loginForm.value.username,
      password: loginForm.value.password,
      nickname: loginForm.value.username,
      skill_level: 'any',
    })
    me.value = await api.getMe()
    await loadActivities()
  })

const logout = () =>
  run(async () => {
    await api.logout()
    me.value = null
    activities.value = []
    matches.value = []
  })

const loadActivities = () =>
  run(async () => {
    activities.value = await api.getActivities({ only_available: true, limit: 20 })
  })

const loadSports = () =>
  run(async () => {
    sports.value = await api.getSports()
  })

const findMatches = () =>
  run(async () => {
    const res = await api.matchBySentence(sentence.value)
    matches.value = res.items
  })

const join = (activityId) =>
  run(async () => {
    await api.joinActivity(activityId, '来自网页报名')
    await loadActivities()
  })

const leave = (activityId) =>
  run(async () => {
    const reg = await api.leaveActivity(activityId)
    if (reg.is_late_cancel) {
      error.value = '已取消报名。因临近活动开始，你的信用分被扣了 5 分。'
    }
    await loadActivities()
  })

/* ---------------- 初始化 ---------------- */
onMounted(async () => {
  await loadSports()
  if (api.tokenStore.has()) {
    // 有本地 token：尝试恢复登录态（token 过期会自动被清掉）
    try {
      me.value = await api.getMe()
    } catch {
      me.value = null
    }
  }
  await loadActivities()
})

/* ---------------- 小工具 ---------------- */

/**
 * 后端返回的时间是【不带时区偏移的 UTC 串】，例如 "2026-10-12T06:14:07.169800"。
 *
 * ⚠️ 关键：必须补上 'Z' 再解析。直接 new Date(iso) 会被浏览器当成【本地时间】，
 *    在中国会少显示 8 小时（实测：06:14 应为 14:14）。
 */
const fmtTime = (iso) => {
  if (!iso) return ''
  // 已经有 Z 或 +/-偏移 就不重复补
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso)
  return new Date(hasZone ? iso : iso + 'Z').toLocaleString('zh-CN', { hour12: false })
}

const levelText = { beginner: '新手', intermediate: '一般', advanced: '熟练', any: '不限' }

const statusText = {
  open: '可报名',
  full: '已满员',
  ongoing: '进行中',
  finished: '已结束',
  cancelled: '已取消',
}
</script>

<template>
  <div class="wrap">
    <h1>SportMate · 校园运动组局</h1>

    <!-- 全局错误提示条 -->
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="loading" class="loading">加载中…</p>

    <!-- ============ 未登录：登录 / 注册 ============ -->
    <section v-if="!isLoggedIn" class="card">
      <h2>登录 / 注册</h2>
      <div class="row">
        <input v-model="loginForm.username" placeholder="用户名（字母数字下划线，3 位以上）" />
        <input v-model="loginForm.password" type="password" placeholder="密码（6 位以上）" />
      </div>
      <div class="row">
        <button :disabled="loading" @click="login">登录</button>
        <button :disabled="loading" @click="register">注册新账号</button>
      </div>
      <p class="hint">用户名只能用字母、数字、下划线，至少 3 位</p>
    </section>

    <!-- ============ 已登录：个人卡片 ============ -->
    <section v-else class="card">
      <h2>你好，{{ me.nickname }}</h2>
      <ul class="info">
        <li>校区：{{ me.campus }}</li>
        <li>
          信用分：<b :class="{ low: me.reliability_score < 60 }">{{ me.reliability_score }}</b>
          <span class="hint">（低于 60 无法报名新活动）</span>
        </li>
        <li>已完成：{{ me.completed_count }} 次　爽约：{{ me.no_show_count }} 次</li>
        <li v-if="me.review_average">评价：{{ me.review_average }} 分（{{ me.review_count }} 条）</li>
      </ul>
      <button :disabled="loading" @click="logout">退出登录</button>
    </section>

    <!-- ============ 一句话找局 ============ -->
    <section class="card">
      <h2>一句话找局</h2>
      <div class="row">
        <input v-model="sentence" placeholder="例如：今晚想在旗山校区打篮球，新手可以吗" />
        <button :disabled="loading || !isLoggedIn" @click="findMatches">找局</button>
      </div>
      <p v-if="!isLoggedIn" class="hint">需要先登录（匹配接口需要知道你的水平）</p>

      <ul v-if="matches.length" class="list">
        <li v-for="m in matches" :key="m.activity.id">
          <b>{{ m.activity.title }}</b>
          <span class="tag">{{ m.activity.sport }}</span>
          <span class="tag">{{ levelText[m.activity.skill_level] }}</span>
          <span class="tag">匹配度 {{ m.score }}</span>
          <br />
          <small>
            {{ fmtTime(m.activity.start_time) }} · {{ m.activity.location }} ·
            还剩 {{ m.activity.slots_left }} 个位置
            <template v-if="m.reason.distance_km != null">
              · 距离 {{ m.reason.distance_km }} km
            </template>
          </small>
          <button :disabled="loading" @click="join(m.activity.id)">报名</button>
        </li>
      </ul>
      <p v-else-if="isLoggedIn" class="hint">还没有结果，点「找局」试试</p>
    </section>

    <!-- ============ 活动列表 ============ -->
    <section class="card">
      <h2>正在招人的活动（{{ activities.length }}）</h2>
      <button :disabled="loading" @click="loadActivities">刷新</button>

      <ul v-if="activities.length" class="list">
        <li v-for="a in activities" :key="a.id">
          <b>{{ a.title }}</b>
          <span class="tag">{{ a.sport }}</span>
          <span class="tag">{{ statusText[a.status] }}</span>
          <span class="tag">{{ levelText[a.skill_level] }}</span>
          <br />
          <small>
            {{ fmtTime(a.start_time) }} · {{ a.campus }} {{ a.location }} ·
            {{ a.joined_count }}/{{ a.capacity }} 人
            <template v-if="a.fee > 0"> · 人均 {{ (a.fee / 100).toFixed(2) }} 元</template>
          </small>
          <br />
          <small class="hint">发起人：{{ a.organizer.nickname }}（信用分 {{ a.organizer.reliability_score }}）</small>
          <div class="row">
            <button :disabled="loading || !isLoggedIn || a.slots_left === 0" @click="join(a.id)">
              {{ a.slots_left === 0 ? '已满员' : '报名' }}
            </button>
            <button :disabled="loading || !isLoggedIn" @click="leave(a.id)">取消报名</button>
          </div>
        </li>
      </ul>
      <p v-else class="hint">暂无活动</p>
    </section>

    <!-- ============ 运动项目字典（下拉框数据源） ============ -->
    <section class="card">
      <h2>运动项目（{{ sports.length }}）</h2>
      <span v-for="s in sports" :key="s.id" class="tag">{{ s.name }}</span>
    </section>
  </div>
</template>

<style scoped>
.wrap { max-width: 760px; margin: 0 auto; padding: 16px; font-family: system-ui, sans-serif; }
.card { border: 1px solid #e2e2e2; border-radius: 8px; padding: 14px; margin-bottom: 16px; }
h1 { font-size: 20px; }
h2 { font-size: 16px; margin: 0 0 10px; }
.row { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; }
input { flex: 1; min-width: 200px; padding: 6px 8px; border: 1px solid #ccc; border-radius: 4px; }
button { padding: 6px 12px; border: 1px solid #ccc; border-radius: 4px; background: #f7f7f7; cursor: pointer; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
.error { color: #b00020; background: #fdecef; border: 1px solid #f5c2c7; padding: 8px 10px; border-radius: 4px; }
.loading { color: #666; }
.hint { color: #888; font-size: 12px; }
.info { list-style: none; padding: 0; margin: 0 0 10px; }
.info li { padding: 2px 0; }
.low { color: #b00020; }
.list { list-style: none; padding: 0; }
.list li { border-top: 1px solid #eee; padding: 10px 0; }
.tag { display: inline-block; background: #eef2ff; color: #334; border-radius: 3px; padding: 1px 6px; font-size: 12px; margin-right: 4px; }
small { color: #666; }
</style>
