# SportMate · 校园运动组局平台（后端）

面向福大学生的运动组局平台后端。**一句话定位**：让想运动的人快速找到时间、水平、地点都合适的搭子。

核心闭环：**发起活动 → 报名 → 签到 → 考勤 → 评价 → 信用分**。

---

## 1. 技术选型

沿用你现有 `calculator-backend` 的技术栈与目录风格，方便统一维护：

| 层 | 选型 | 说明 |
|---|---|---|
| Web 框架 | FastAPI 0.115 | 自带 OpenAPI 文档、依赖注入、Pydantic 校验 |
| ORM | SQLAlchemy 2.1（`Mapped` 声明式） | 与现有项目一致 |
| 校验 | Pydantic v2 | 请求/响应双向校验 |
| 数据库 | SQLite（默认） | 零配置；换 PostgreSQL 只需改 `SPORTMATE_DATABASE_URL` |
| 密码 | 标准库 PBKDF2-HMAC-SHA256（24 万次迭代 + 随机盐） | **不引入 passlib/bcrypt 依赖** |
| 鉴权 | 数据库不透明 Token（只存 SHA-256 摘要） | **不引入 python-jose 依赖** |
| 测试 | pytest + FastAPI TestClient | 内存库，不落盘 |

> 依赖刻意压到 4 个运行期包（fastapi / uvicorn / pydantic / sqlalchemy），全部你本机已装好，**无需 pip install**。

---

## 2. 快速开始

```bash
cd sportmate-backend

# 启动（依赖已就绪，直接跑）
py -m uvicorn main:app --reload --port 8000
```

- 交互式文档：http://127.0.0.1:8000/docs
- 接口清单与信用分规则：http://127.0.0.1:8000/

首次启动会自动建表并写入 10 个运动项目种子数据（篮球 / 羽毛球 / 足球 / 乒乓球 / 网球 / 跑步 / 徒步 / 骑行 / 健身 / 排球）。

### 跑测试

```bash
py -m pytest -q
```

16 个测试覆盖完整业务闭环，包括防爽约、签到围栏、评价权限、匹配排序。

---

## 3. 文件结构

```
sportmate-backend/
├── main.py              # 应用入口、CORS、全局异常处理、健康检查
├── config.py            # 配置与业务阈值（信用分规则、签到半径等）
├── db.py                # 引擎/会话/Base、建表与种子数据、utcnow()
├── models.py            # 7 张表的 ORM 模型
├── schemas.py           # Pydantic 请求/响应模型
├── security.py          # PBKDF2 密码哈希、Token 生成与摘要
├── services.py          # 业务规则：报名闭环、签到、考勤、评价、匹配引擎
├── converters.py        # ORM → 响应模型（含字段可见性控制）
├── deps.py              # FastAPI 依赖：数据库会话、当前用户
├── routers.py           # 全部 HTTP 路由
├── tests/
│   ├── conftest.py      # 内存库 fixture、注册/建活动辅助函数
│   ├── test_flow.py     # 端到端业务流程测试
│   └── test_startup.py  # 启动路径与路由注册回归
├── push-to-github.bat   # 一键推送到 GitHub（含常见报错指引）
├── requirements.txt     # 运行期依赖（4 个）
└── requirements-dev.txt # 测试依赖
```

### 数据模型

| 表 | 职责 |
|---|---|
| `users` | 账号、校区、自评水平、**信用分**、完成数、爽约数 |
| `user_sessions` | 登录态（不透明 Token 的 SHA-256 摘要 + 过期时间） |
| `sports` | 运动项目字典（启动时种子化） |
| `activities` | 活动：项目/时间/水平/校区/坐标/容量/状态/**签到码** |
| `registrations` | 报名记录：状态机 + 签到信息 + 是否迟到取消 |
| `reviews` | 活动结束后的互评（1–5 分 + 标签 + 评论） |

---

## 4. 防爽约机制（本项目的差异点）

信用分从 **100** 起算，规则集中在 `config.py`，可随时调整：

| 行为 | 分值变化 |
|---|---|
| 完成一次活动（到场） | **+2** |
| 临近开始（2 小时内）才取消 | **−5** |
| 爽约（考勤未到场） | **−15** |
| 低于 **60** 分 | **禁止报名新活动** |

两个关键设计：

1. **分数是可重算的，不是累加的。** `recalculate_reliability()` 每次都用全量历史重新推导：
   ```
   score = 100 + 2 × 到场次数 − 5 × 迟到取消次数 − 15 × 爽约次数
   ```
   因此不存在"并发扣分导致分数漂移"的问题，任何时刻都能重新校准。

2. **考勤由发起人闭环确认。** 发起人提交到场名单，**其余报名者自动记为爽约并扣分**——不需要额外操作，避免"忘了标记"导致机制失效。

---

## 5. 签到设计

支持两种互斥方式（`schemas.CheckinRequest` 校验必须提供其一）：

- **签到码**：发起活动时自动生成 4 位数字码，**只有发起人能在活动详情里看到**（同一接口对其他用户返回 `null`）。
- **定位围栏**：Haversine 公式计算直线距离，超出 `CHECKIN_RADIUS_METERS`（默认 300 米）拒绝签到，并在响应里返回实际距离。

---

## 6. 匹配引擎

`GET /match` 按加权模型排序，权重在 `services.py` 顶部常量里：

| 维度 | 权重 | 行为 |
|---|---|---|
| 运动项目 | 40 | **硬过滤**：项目不符直接排除 |
| 时间窗 | 25 | **硬过滤**：与你的空闲时段无交集则排除 |
| 水平 | 15 | 软加成；活动标注"不限水平"时对所有人成立 |
| 校区 | 10 | 软加成（不排除，只排序） |
| 距离 | 20 | 按 `1/(1+km)` 衰减，支持 `max_distance_km` 硬限制 |

另外：已满员扣 5 分、还有空位加 2 分；**自动排除自己发起的和自己已报名的活动**。

### 一句话找局

`GET /match/parse?q=今晚想在旗山校区打篮球，新手可以吗`

`parse_natural_language()` 做的是可解释的关键词抽取（项目 / 校区 / 水平），输出结构就是 `MatchQuery`——**后续换成 LLM 解析不需要改动匹配引擎**。若首轮无结果，会退化为按用户画像（`favorite_sports`、`campus`、`skill_level`）再匹配一次，保证接口不会返回空死胡同。

---

## 7. 接口一览

`Authorization: Bearer <token>`，令牌由注册/登录返回。

### 认证
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | 注册，直接返回令牌（201） |
| POST | `/auth/login` | 登录 |
| POST | `/auth/logout` | 吊销当前令牌（204） |
| GET | `/me` | 我的资料 + 评价汇总 |
| PATCH | `/me` | 修改昵称/校区/水平/常玩项目 |

### 基础数据
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sports` | 运动项目列表 |
| GET | `/health` | 健康检查 |

### 用户
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/users/{id}` | 公开资料（含评分） |
| GET | `/users/{id}/reviews` | 收到的评价 |

### 活动
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/activities` | 发起活动（自动生成签到码） |
| GET | `/activities` | 列表，支持 `sport/campus/status/date_from/...` 筛选 |
| GET | `/activities/mine` | 我发起的 / 我报名的 |
| GET | `/activities/{id}` | 详情 + 报名名单（发起人可见签到码） |
| PATCH | `/activities/{id}` | 修改（仅发起人） |
| POST | `/activities/{id}/cancel` | 取消，级联释放所有报名 |

### 报名 / 签到 / 考勤
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/activities/{id}/join` | 报名（校验容量、水平、信用分） |
| DELETE | `/activities/{id}/join` | 取消报名（临近开始扣分） |
| POST | `/activities/{id}/checkin` | 签到（签到码 或 经纬度） |
| POST | `/activities/{id}/attendance` | 记录考勤（仅发起人） |

### 评价 / 匹配
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/activities/{id}/reviews` | 活动结束后互评（仅到场者） |
| GET | `/match` | 结构化匹配 |
| GET | `/match/parse` | 一句话自然语言匹配 |

---

## 8. curl 走一遍完整闭环

```bash
BASE=http://127.0.0.1:8000

# 1. 注册两个人
ORG=$(curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"org1","password":"secret123","nickname":"组织者","campus":"旗山校区"}')
ORG_TOK=$(echo $ORG | py -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

USR=$(curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"usr1","password":"secret123","nickname":"搭子","skill_level":"beginner"}')
USR_TOK=$(echo $USR | py -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
USR_ID=$(curl -s $BASE/me -H "Authorization: Bearer $USR_TOK" | py -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2. 发起活动（时间需要显式带时区偏移）
START=$(py -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=24)).isoformat())")
END=$(py -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=26)).isoformat())")
ACT=$(curl -s -X POST $BASE/activities -H "Authorization: Bearer $ORG_TOK" \
  -H 'Content-Type: application/json' \
  -d "{\"title\":\"周三晚篮球\",\"sport\":\"篮球\",\"campus\":\"旗山校区\",\"location\":\"风雨操场3号场\",\"latitude\":26.0565,\"longitude\":119.1966,\"start_time\":\"$START\",\"end_time\":\"$END\",\"capacity\":6}")
ACT_ID=$(echo $ACT | py -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 3. 报名 → 签到 → 考勤 → 评价
curl -s -X POST $BASE/activities/$ACT_ID/join -H "Authorization: Bearer $USR_TOK"
CODE=$(curl -s $BASE/activities/$ACT_ID -H "Authorization: Bearer $ORG_TOK" | py -c "import sys,json;print(json.load(sys.stdin)['checkin_code'])")
curl -s -X POST $BASE/activities/$ACT_ID/checkin -H "Authorization: Bearer $USR_TOK" \
  -H 'Content-Type: application/json' -d "{\"code\":\"$CODE\"}"
curl -s -X POST $BASE/activities/$ACT_ID/attendance -H "Authorization: Bearer $ORG_TOK" \
  -H 'Content-Type: application/json' -d "[$USR_ID]"
```

---

## 9. 推送到 GitHub

本地仓库已经初始化好（分支 `main`，远程 `origin` 已指向
`https://github.com/3054664804/sportmate-backend.git`），**只差一次推送**。

推送需要 GitHub 登录授权，必须在**你自己的终端**里完成：

**方式一（推荐）**：双击项目根目录的 `push-to-github.bat`
→ 按提示在 `https://github.com/new` 建一个名为 `sportmate-backend` 的空仓库
（**不要**勾选 "Add a README file"）→ 回到窗口按任意键，浏览器会弹出授权，点 Authorize。

**方式二（手动）**：

```powershell
cd C:\Users\30546\Documents\deepseek-harness\default-workspace\sportmate-backend
git push -u origin main
```

常见报错与解法：

| 报错 | 原因与处理 |
|---|---|
| `Repository not found` | 远程仓库还没建，先去 `https://github.com/new` 创建 |
| `rejected ... (fetch first)` | 建仓库时勾选了 README，先 `git pull --rebase origin main` 再 push |
| `could not read Username` | 授权窗口被关掉了，重跑一次并完成浏览器登录 |
| `schannel: AcquireCredentialsHandle failed` | 证书/凭据后端问题，执行 `git config --global http.sslBackend openssl` 后重试 |

> 若要用别的账号或仓库名，改远程地址即可：
> `git remote set-url origin https://github.com/<用户名>/<仓库名>.git`

---

## 10. 关键约定（踩过的坑）

1. **时间一律用带偏移的 ISO-8601**，例如 `2026-10-12T19:00:00+08:00`。库里统一存 **朴素 UTC**，服务层在入库前用 `ensure_aware_utc()` 归一化。
   > 不带偏移的时间串会被当作 UTC，在东八区会产生 8 小时偏差——这是本项目开发中真实出现过的 bug，已在 `services.create_activity` 与 `match_activities` 两处修正并加了测试。

2. **`/activities/mine` 必须声明在 `/activities/{id}` 之前**，否则 `mine` 会被当成路径参数。

3. 返回 204 的接口使用 `response_class=Response` 且**不返回响应体**（FastAPI 会断言 204 不能带 body）。

4. **签到码是机密字段**，可见性在 `converters.activity_detail()` 统一控制，路由层不做判断。

5. `SQLite` 下所有时间都是朴素值，因此**服务层内部比较必须都是朴素 UTC**，不要再混入 aware 值。

---

## 11. 后续可做

- **并发抢位**：当前"检查容量 → 写入报名"之间存在竞态窗口，高并发下可能超员。生产环境应加唯一约束 + 事务重试，或直接改成 `SELECT ... FOR UPDATE`。
- **推送通知**：活动变更/临期提醒（WebSocket 或微信服务号模板消息）。
- **真实用户体系**：接入学校统一认证，替代用户名密码。
- **活动搜索**：`activities.title/description` 全文检索（SQLite FTS5 → PostgreSQL `tsvector`）。
- **数据看板**：社团/班级维度的组局统计。
- **自然语言升级**：把 `parse_natural_language()` 换成 LLM 调用，输出契约不变。
