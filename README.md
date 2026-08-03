# Steam × BUFF 饰品雷达

一个本地运行的 CS2 饰品价格分析工具：完整遍历 BUFF 关键词搜索结果，持续维护全市场饰品索引，查询 Steam 最低在售价，并按可配置手续费模型估算 Steam 到手余额与折价。

前端采用紧凑的深色游戏主题操作台，支持最新/缓存数据标记、关键词、枪械、类别、磨损、三种价格口径、来源、盈利条件筛选和多维排序。点击商品行会直接打开对应的 BUFF 商品页。

> 本项目只采集和估算价格，不执行购买、交易、提货或上架操作。报价、流动性、手续费、交易限制和账号风控随时可能变化，请在实际操作前自行核实。

## 核心功能

- BUFF App 扫码登录，不需要手工复制 Cookie。
- 登录会话只保存在本机，应用重启后自动恢复。
- “立即扫描”逐关键词遍历全部搜索分页，不再只读取第一页。
- “深度扫描”先建立 BUFF 全市场索引，再逐件查询 Steam 价格。
- 深度扫描的阶段、页码和定价进度持久化到 SQLite，支持终止和重启续传。
- 深度任务运行时，立即扫描会在安全检查点优先执行，结束后自动恢复深度任务。
- 拉取 BUFF 商品时并发缓存图片 BLOB 到 SQLite；页面通过本地图片接口加载。
- SQLite 保存商品快照、图片资产、价格历史、扫描记录、深度索引和检查点。
- 列表使用服务端筛选、排序和分页，可支撑持续增长的全量索引。
- 商品保存 BUFF ID、中文名、图片、枪械、类别、磨损、品质和稀有度等真实元数据。
- WebSocket 实时推送登录、扫描、调度和深度任务进度。
- 响应式游戏主题界面，支持键盘操作、细腻动画和低动态模式。

## 两种扫描的边界

### 立即扫描：完整关键词扫描

立即扫描面向用户配置的关键词列表：

1. 对每个关键词调用 BUFF 搜索接口。
2. 从第 1 页继续请求到该关键词的最后一页。
3. 跨关键词按 `market_hash_name` 去重。
4. 逐件查询 Steam 市场价格并写入最新快照。

`page_size` 只控制每次 BUFF 请求的分页大小，不再代表“只取前 N 条”。普通扫描不会被旧版本的 `max_items_per_cycle` 截断。

### 深度扫描：全市场索引与慢速定价

深度扫描是明确的长时间任务，分为两个阶段：

1. `indexing`：按 BUFF 市场价格升序遍历所有分页，将完整商品元数据与图片资产写入 SQLite。
2. `pricing`：遍历本轮索引，慢速查询每件商品的 Steam 价格，并更新排名与历史。

BUFF 市场规模很大，加上 Steam 请求节流，完整任务可能持续数小时至数天。页面会醒目展示当前阶段、页码、已索引数、已定价数、失败数和整体进度。

每完成一个 BUFF 页面、每处理一个 Steam 商品，检查点都会落到 SQLite：

- 扫描中的按钮会变为“终止”按钮；终止深度扫描后，下次点击会从当前检查点继续。
- 正常关闭或重启应用不会清空检查点。
- 登录失效或临时错误不会要求从第 1 页重来。
- 深度扫描完成后再次启动，会创建新一轮全市场刷新。
- 深度扫描期间触发立即扫描，深度任务会在当前页或当前商品完成后让出执行权，随后自动续跑。
- 相同模式已经运行时，重复点击或重复调度会被合并，不会无限积压同类任务。

## 最新数据与缓存数据

列表中的每一行都有醒目的状态标签：

- **最新数据**：该商品由当前最近一次实际写入商品的扫描任务刷新。
- **缓存数据**：商品仍来自本机历史快照，但没有在最近一轮扫描中刷新。

扫描刚开始时，旧结果会继续保留，不会因为当前请求尚未完成而消失。状态标签用于区分本轮新鲜报价与仍可参考的历史缓存；它不代表第三方市场价格在此刻绝对有效。

## 筛选、排序与商品跳转

商品列表提供：

- 中英文商品名关键词搜索。
- 枪械筛选，例如 AK-47、M4A1-S、USP-S。
- 类别筛选，例如步枪、手枪、匕首、印花、容器。
- 磨损筛选，例如崭新出厂、略有磨损、久经沙场。
- 价格区间可切换为 BUFF 最低价、Steam 最低价或 Steam 到手价。
- 最新数据 / 缓存数据。
- 关键词 / 深度索引来源。
- 只看 `0 < 折价 ≤ 1.0`。
- 折价、BUFF 价、Steam 价、成交量、在售量、更新时间与名称排序。

筛选和排序在 SQLite 中执行，前端默认每页读取 100 条，不会把整个全量索引一次性装入浏览器。

点击商品名称或整行空白区域会在新标签页打开对应 BUFF 商品页。新扫描到的商品使用 BUFF `goods_id` 精确跳转；旧缓存数据缺少商品 ID 时回退到 BUFF 市场名称搜索页，下一次刷新后会自动补齐元数据。

## 计算方式

默认配置按 Steam 5%、游戏 10% 估算两项手续费，每项向上取整到分，最低费用为 ¥0.01：

```text
steam_net = steam_price
            - ceil_to_cent(steam_price × steam_fee_steam_pct)
            - ceil_to_cent(steam_price × steam_fee_game_pct)

discount = buff_price / steam_net
```

例如 `discount = 0.70` 会在页面显示为 `7.0折`。数值越低，表示按当前报价估算的 BUFF 买入成本相对 Steam 到手余额越低。该估算不包含价格波动、成交等待、提货冷却、资金时间成本等因素。

## 环境要求

- Python 3.11+
- macOS、Linux 或 Windows
- 可访问 BUFF 与 Steam 市场
- BUFF 账号及可扫码的 BUFF App

## 安装与启动

建议使用独立虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python run.py
```

默认监听 [http://127.0.0.1:8080](http://127.0.0.1:8080)，并自动打开浏览器。

常用参数：

```bash
python run.py --no-browser      # 不自动打开浏览器
python run.py --port 9000       # 临时覆盖端口
python run.py --host 0.0.0.0    # 允许局域网访问；请先阅读安全说明
python run.py --reload          # 开发模式自动重载
```

应用固定使用单 worker。登录会话、扫描优先级和 WebSocket 状态位于单进程内，多 worker 会造成重复扫描和状态分裂。

## 使用流程

1. 打开页面，点击“扫码登录”。
2. 使用 BUFF App 扫码并在手机上确认。
3. 点击“立即扫描”，完整遍历配置关键词的所有结果。
4. 使用枪械、类别、磨损、价格和排序条件查找机会。
5. 点击商品行打开 BUFF 对应商品页面。
6. 需要扩大覆盖范围时，点击“深度扫描”，阅读长任务提示后确认启动。
7. 可以随时终止深度扫描；再次点击会从检查点继续。

如果 `auto_scan` 已启用，扫码登录成功后会自动执行一次关键词扫描，之后按配置间隔运行。`deep_scan.enabled` 控制定时器是否按深度扫描间隔启动或续跑全量任务。

## 配置

首次启动会创建 `data/config.json`。旧版本配置中的 `max_items_per_cycle`、深度扫描价格区间和页数上限已不再使用，加载时会被忽略；通过网页保存设置后，配置文件会按当前字段重写。

主要字段：

| 字段 | 默认值 | 说明 |
| --- | ---: | --- |
| `host` | `127.0.0.1` | 监听地址，修改后需重启 |
| `port` | `8080` | 监听端口，修改后需重启 |
| `game` | `csgo` | BUFF 游戏标识 |
| `steam_appid` | `730` | Steam App ID |
| `currency` | `23` | Steam 价格接口币种，23 为 CNY |
| `keywords` | 4 个默认词 | 立即扫描使用的关键词 |
| `page_size` | `80` | BUFF 每页请求量；不会截断总结果 |
| `scan_interval_minutes` | `15` | 关键词扫描间隔 |
| `auto_scan` | `true` | 是否启动定时调度；网页修改后立即生效 |
| `request_delay_sec` | `1.0` | BUFF 分页请求间隔 |
| `steam_delay_sec` | `0.5` | Steam 商品请求间隔 |
| `history_keep_days` | `30` | 价格历史保留天数，`0` 表示不修剪 |
| `steam_fee_steam_pct` | `5.0` | 第一项手续费比例 |
| `steam_fee_game_pct` | `10.0` | 第二项手续费比例 |
| `fee_min` | `0.01` | 单项最低手续费 |
| `fee_round` | `cent` | `cent` 向上到分，`yuan` 向上到元 |
| `user_agent` | Chrome UA | BUFF 请求使用的 User-Agent |
| `log_level` | `INFO` | `DEBUG`、`INFO`、`WARNING` 或 `ERROR` |

深度扫描配置：

```json
{
  "enabled": false,
  "interval_minutes": 240
}
```

`enabled` 只控制定时调度。手动启动、终止和续传不受该开关限制。

## 运行时数据与数据库迁移

所有运行时数据位于 `data/`，不会提交到 Git：

```text
data/
├── config.json          # 应用配置
├── buff_session.json    # BUFF Cookie 会话；内容不是普通 JSON
└── prices.db            # 商品、历史、扫描记录和深度索引
```

启动时会自动检查并迁移旧版 `items` 表，并创建图片资产表。迁移不会伪造旧记录缺失的 BUFF 分类元数据；新一轮关键词或深度扫描会用真实接口数据补齐。重要数据仍建议自行备份 `data/prices.db`。

SQLite 主要表：

| 表 | 用途 |
| --- | --- |
| `items` | 当前可展示的价格快照与商品元数据 |
| `item_assets` | 商品图片 MIME 类型与二进制数据 |
| `price_history` | 折价与价格历史点 |
| `scans` | 每次扫描的模式、状态和耗时 |
| `deep_scan_index` | 按代维护的 BUFF 全市场索引 |
| `deep_scan_progress` | 深度扫描阶段、下一页和完成计数 |

`buff_session.json` 包含可用于访问账号的会话 Cookie，应像密码一样保护，不要上传、分享或提交到仓库。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/status` | 登录、扫描、调度与配置状态快照 |
| `GET` | `/api/auth/status` | BUFF 登录状态 |
| `POST` | `/api/auth/qr` | 创建或刷新登录二维码 |
| `POST` | `/api/auth/logout` | 清除本机会话 |
| `GET` | `/api/config` | 读取配置 |
| `PUT` | `/api/config` | 校验、保存并应用配置 |
| `POST` | `/api/scan` | 触发 `keyword` 或 `deepscan` |
| `GET` | `/api/scan/status` | 扫描与深度检查点状态 |
| `POST` | `/api/scan/stop` | 按模式终止当前或排队任务 |
| `POST` | `/api/scan/deep/pause` | 兼容旧客户端的深度暂停接口 |
| `GET` | `/api/items` | 筛选、排序、分页商品列表 |
| `GET` | `/api/items/image/{name}` | 读取 SQLite 中的商品图片 |
| `GET` | `/api/items/{name}/history` | 单个商品价格历史 |
| WebSocket | `/ws` | 实时状态与扫描进度 |

`GET /api/items` 支持：

```text
q                 名称关键词
weapon            枪械
item_type         类别
exterior          磨损
price_basis       buff_price | steam_price | steam_net
min_price         所选价格口径的下限
max_price         所选价格口径的上限
only_profitable   只看 0 < 折价 <= 1
source            keyword | deepscan
data_state        latest | cached
sort_by           name | buff_price | steam_price | steam_net | discount |
                  steam_volume | buff_sell_num | updated_at
sort_order        asc | desc
page              页码
page_size         20—200
```

WebSocket 快照不再携带整个商品列表，避免深度索引扩大后产生超大消息；商品始终通过分页 HTTP API 获取。

## 项目结构

```text
.
├── backend/
│   ├── main.py                 # FastAPI 生命周期与装配
│   ├── config.py               # 配置模型和原子持久化
│   ├── db.py                   # SQLite schema、迁移、筛选与检查点
│   ├── models.py               # API 输入模型
│   ├── state.py                # 单进程状态与 WebSocket 广播
│   ├── routers/                # auth / config / items / scan / ws
│   └── services/
│       ├── buff.py             # BUFF HTTP 客户端与商品标准化
│       ├── buff_login.py       # 扫码登录状态机
│       ├── scanner.py          # 扫描优先级、全分页和深度续传
│       ├── scheduler.py        # 关键词/深度扫描调度器
│       └── steam.py            # Steam 价格与折价计算
├── frontend/
│   ├── index.html              # 语义化控制台结构
│   ├── css/styles.css          # 游戏主题、动画与响应式布局
│   └── js/app.js               # 筛选、分页、实时状态与交互
├── tests/
├── requirements.txt
├── requirements-dev.txt
└── run.py
```

## 开发与验证

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
python -m compileall -q backend tests run.py
node --check frontend/js/app.js      # 可选，需要 Node.js
```

测试覆盖手续费与价格解析、BUFF 元数据标准化、配置持久化、扫码登录、图片资产缓存、三种价格口径、全分页扫描、深度索引检查点、关键词优先级、筛选排序、新鲜度标签、数据库迁移、错误和取消状态。

## 安全与风控

- 默认只监听 `127.0.0.1`，服务本身没有用户鉴权，不应直接暴露到公网。
- 改为 `0.0.0.0` 后，同一网络中的其他设备可能访问页面、触发扫描或执行登出。
- 登录 Cookie 只保存在本机 `data/`，但拥有文件权限的程序或用户仍可能读取会话。
- 不要通过多 worker、高并发或过短间隔绕过 BUFF / Steam 平台限制。
- 全市场深度扫描会产生大量长期请求，建议保留默认节流并在网络稳定时运行。

## 常见问题

### 扫码确认后仍显示未登录

刷新二维码后重试，并查看终端中的 `buff_login` 日志。完整登录状态必须为 BUFF `state = 2`；中间绑定状态不会被误判为成功。

### 为什么立即扫描很久

立即扫描现在会遍历每个关键词的所有分页，并逐件请求 Steam 价格。关键词越宽泛、搜索结果越多，耗时越长。可以缩小关键词范围，但不会再静默截断结果。

### 深度扫描终止或重启后会重来吗

不会。索引页码、阶段和已处理商品保存在 `deep_scan_progress` 与 `deep_scan_index`。再次点击“继续深度扫描”会从检查点恢复。

### 为什么有些行显示缓存数据

这些商品存在于本机历史快照，但最近一轮扫描没有刷新它们。可以继续参考历史价格，但应留意更新时间并在交易前打开 BUFF 页面核实。

### 枪械、类别或磨损下拉框为空

旧版本数据没有保存 BUFF 分类元数据，数据库迁移无法从空字段推导真实值。完成一次新的关键词扫描或开始重建深度索引后，真实的枪械、类别和磨损会写入数据库，筛选项会自动出现；没有磨损属性的容器、印花等商品会保持空值。

### Steam 价格大量缺失

通常是临时限流、网络错误或商品没有有效最低价。提高 `steam_delay_sec` 并等待下一轮扫描，不要并发启动多个实例。

### 修改 host 或 port 后没有变化

监听地址和端口在进程启动时读取，保存后需要重启应用；也可以用命令行参数临时覆盖。
