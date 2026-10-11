/**
 * SportMate 后端 API 客户端
 * ---------------------------------------------------------------------------
 * 直接把这个文件复制到前端项目里用（Vue 3 / React 都行，纯 JS 无框架依赖）。
 *
 * 用法：
 *   import api from './api.js'
 *
 *   await api.register({ username: 'zhangsan', password: 'secret123', nickname: '张三' })
 *   const sports = await api.getSports()
 *   const detail = await api.getActivity(1)
 *
 * 设计要点：
 *   1. token 自动附带，自动存/取 localStorage
 *   2. 401 自动清除 token（登录过期）
 *   3. 所有错误统一抛出 ApiError，message 就是后端返回的中文提示，可直接显示
 *   4. 业务错误和参数校验错误的 detail 都是字符串，前端无需分支处理
 */

/** 后端地址。本地开发用 127.0.0.1，部署后换成 Render 域名。 */
const BASE_URL =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) ||
  'http://127.0.0.1:8000'

const TOKEN_KEY = 'sportmate_token'

/** 统一错误类型。捕获后直接显示 err.message 即可。 */
export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
  /** 是否为登录态问题 */
  get isAuthError() {
    return this.status === 401
  }
}

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
  has: () => Boolean(localStorage.getItem(TOKEN_KEY)),
}

/**
 * 底层请求。不要直接调用，用下面的 api.xxx 方法。
 * @param {string} path   路径，如 '/activities/1'
 * @param {object} options { method, body, query, auth }
 */
async function request(path, { method = 'GET', body, query, auth = true } = {}) {
  // 组装查询串。URLSearchParams 会自动做百分号编码，
  // 中文参数（sport=篮球）必须经过它，否则会报错。
  let url = BASE_URL + path
  if (query && Object.keys(query).length) {
    const usable = Object.fromEntries(
      Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '')
    )
    const qs = new URLSearchParams(usable).toString()
    if (qs) url += `?${qs}`
  }

  const headers = { 'Content-Type': 'application/json' }
  const token = tokenStore.get()
  if (auth && token) headers.Authorization = `Bearer ${token}`

  let res
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (networkError) {
    // 网络层失败：后端没启动、断网、或 Render 免费实例正在唤醒
    throw new ApiError(
      '连接不上服务器。后端可能没启动，或 Render 免费实例正在唤醒（首次约需 30~60 秒），请稍后重试。',
      0,
      networkError
    )
  }

  // 204 无响应体
  if (res.status === 204) return null

  const text = await res.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!res.ok) {
    // 后端保证 detail 一定是字符串（业务错误和校验错误都是）
    const detail =
      payload && typeof payload === 'object' && typeof payload.detail === 'string'
        ? payload.detail
        : `请求失败（HTTP ${res.status}）`

    if (res.status === 401) tokenStore.clear() // 登录过期，清掉本地 token

    throw new ApiError(detail, res.status, payload)
  }

  return payload
}

/* ------------------------------------------------------------------ */
/* 接口封装                                                            */
/* ------------------------------------------------------------------ */
const api = {
  ApiError,
  tokenStore,
  BASE_URL,

  /* ---------------- 认证 ---------------- */

  /** 注册。成功后 token 自动保存。 */
  async register({ username, password, nickname, campus, skill_level, favorite_sports, phone }) {
    const data = await request('/auth/register', {
      method: 'POST',
      auth: false,
      body: { username, password, nickname, campus, skill_level, favorite_sports, phone },
    })
    tokenStore.set(data.access_token)
    return data
  },

  /** 登录。成功后 token 自动保存。 */
  async login(username, password) {
    const data = await request('/auth/login', {
      method: 'POST',
      auth: false,
      body: { username, password },
    })
    tokenStore.set(data.access_token)
    return data
  },

  /** 退出登录（同时让服务端吊销 token）。 */
  async logout() {
    try {
      await request('/auth/logout', { method: 'POST' })
    } finally {
      tokenStore.clear()
    }
  },

  /** 我的资料。含 reliability_score（信用分）与评分汇总。 */
  getMe: () => request('/me'),

  /** 修改资料。只传要改的字段。 */
  updateMe: (patch) => request('/me', { method: 'PATCH', body: patch }),

  /* ---------------- 基础数据 ---------------- */

  /** 运动项目字典，用于下拉框。返回 [{id, name, icon, default_capacity}] */
  getSports: () => request('/sports', { auth: false }),

  getHealth: () => request('/health', { auth: false }),

  /* ---------------- 用户 ---------------- */

  getUser: (userId) => request(`/users/${userId}`, { auth: false }),

  getUserReviews: (userId) => request(`/users/${userId}/reviews`, { auth: false }),

  /* ---------------- 活动 ---------------- */

  /**
   * 发起活动。
   * ⚠️ 返回的是精简模型，**不含 checkin_code**。
   *    需要签到码请再调 getActivity(id)（发起人身份才可见）。
   */
  createActivity: (payload) => request('/activities', { method: 'POST', body: payload }),

  /**
   * 活动列表。
   * @param {object} filters { sport, campus, skill_level, status,
   *                           date_from, date_to, only_available, limit, offset }
   */
  getActivities: (filters = {}) => request('/activities', { query: filters, auth: false }),

  /** 我发起的 / 我报名的。返回 { organized: [], joined: [] } */
  getMyActivities: () => request('/activities/mine'),

  /**
   * 活动详情。
   * @param {boolean} withAuth 传 true 且当前用户是发起人时，返回里才有 checkin_code
   */
  getActivity: (id, withAuth = true) => request(`/activities/${id}`, { auth: withAuth }),

  updateActivity: (id, patch) => request(`/activities/${id}`, { method: 'PATCH', body: patch }),

  /** 取消活动（仅发起人）。所有报名者会被置为 cancelled。 */
  cancelActivity: (id) => request(`/activities/${id}/cancel`, { method: 'POST' }),

  /* ---------------- 报名 ---------------- */

  /** 报名。note 可选。 */
  joinActivity: (id, note = '') =>
    request(`/activities/${id}/join`, { method: 'POST', body: { note } }),

  /** 取消报名。临近开始 2 小时内取消会扣信用分，返回里 is_late_cancel 会为 true。 */
  leaveActivity: (id) => request(`/activities/${id}/join`, { method: 'DELETE' }),

  /* ---------------- 签到 ---------------- */

  /** 用签到码签到。 */
  checkinWithCode: (id, code) =>
    request(`/activities/${id}/checkin`, { method: 'POST', body: { code } }),

  /**
   * 用定位签到（需在活动地点 300 米内）。
   * 用浏览器定位拿坐标：const {coords} = await new Promise(r => navigator.geolocation.getCurrentPosition(r))
   * 注意：浏览器定位必须 HTTPS 或 localhost，普通 http 域名会被浏览器拦。
   */
  checkinWithLocation: (id, latitude, longitude) =>
    request(`/activities/${id}/checkin`, { method: 'POST', body: { latitude, longitude } }),

  /* ---------------- 考勤（发起人） ---------------- */

  /**
   * 记录考勤：传【到场者】的 user_id 数组，其余报名者自动记为爽约并扣分。
   * @param {number[]} attendedUserIds
   */
  recordAttendance: (id, attendedUserIds) =>
    request(`/activities/${id}/attendance`, { method: 'POST', body: attendedUserIds }),

  /* ---------------- 评价 ---------------- */

  /** 活动结束后评价同场伙伴（仅到场者可评）。 */
  createReview: (activityId, { reviewee_id, score, tags = [], comment = '' }) =>
    request(`/activities/${activityId}/reviews`, {
      method: 'POST',
      body: { reviewee_id, score, tags, comment },
    }),

  /* ---------------- 匹配 ---------------- */

  /**
   * 结构化匹配。
   * @param {object} query { sport, campus, skill_level, available_from, available_to,
   *                         latitude, longitude, max_distance_km, limit }
   * 返回 { query, count, items: [{ activity, score, reason }] }
   */
  match: (query = {}) => request('/match', { query }),

  /** 一句话找局。q 例如「今晚想在旗山校区打篮球，新手可以吗」 */
  matchBySentence: (q, limit = 10) => request('/match/parse', { query: { q, limit } }),
}

export default api
