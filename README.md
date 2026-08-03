# Steam × BUFF 折价对比

一个运行在本机的 CS2 饰品价格对比工具：从 BUFF 获取买入价，从 Steam 市场获取最低在售价，按可配置的手续费模型估算 Steam 到手余额，并以折价从低到高展示候选饰品。

> 本项目只提供价格采集与估算，不执行购买、交易或上架操作。行情、手续费、交易限制和账号风控都可能变化，请在实际操作前自行核实。

## 功能

- BUFF App 扫码登录，无需手工复制 Cookie。
- 登录会话保存到本机，应用重启后自动恢复。
- 按关键词定时扫描，支持手动立即扫描。
- 按 BUFF 价格区间分页执行深度扫描。
- 使用 `curl_cffi` 获取 Steam 市场价格。
- 计算 Steam 估算到手余额与 BUFF/Steam 折价。
- SQLite 保存最新快照、价格历史与扫描记录。
- WebSocket 推送登录、扫描、配置和排名更新。
- Web 页面提供筛选、历史趋势、配置修改和扫码入口。

## 计算方式

默认配置使用两项手续费：Steam 5%、游戏 10%，每项向上取整到分，最低费用为 ¥0.01：

```text
steam_net = steam_price
            - ceil_to_cent(steam_price × steam_fee_steam_pct)
            - ceil_to_cent(steam_price × steam_fee_game_pct)

discount = buff_price / steam_net
```

例如 `discount = 0.70` 会在页面显示为 `7.0折`。数值越低，代表按当前报价估算的 BUFF 成本越低；它不包含价格波动、成交等待、资金时间成本等因素。

手续费比例、最低费用和取整方式都可在 `data/config.json` 中修改，部分常用项也可在网页设置中调整。

## 环境要求

- Python 3.11+
- macOS、Linux 或 Windows
- 可访问 BUFF 与 Steam 市场
- BUFF 账号及可扫码的 BUFF App

## 安装与启动

建议使用虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python run.py
```

默认监听 [http://127.0.0.1:8080](http://127.0.0.1:8080)，并自动打开浏览器。

常用启动参数：

```bash
python run.py --no-browser      # 不自动打开浏览器
python run.py --port 9000       # 临时覆盖端口
python run.py --host 0.0.0.0    # 允许其他设备访问；请先阅读安全说明
python run.py --reload          # 开发模式自动重载
```

应用固定使用单 worker。登录会话、扫描队列和 WebSocket 状态保存在进程内，多 worker 会造成状态分裂和重复请求。

## 使用流程

1. 打开页面，点击“扫码登录”。
2. 使用 BUFF App 扫码并在手机上确认。
3. 登录完成后，如果 `auto_scan` 已启用，应用会立即执行一次关键词扫描。
4. 使用“立即扫描”触发关键词扫描，或使用“深度扫描”按价格区间扫描。
5. 表格默认只显示折价不高于 1.0 的结果，可切换来源或取消过滤。

同一时间只执行一个扫描任务。扫描中再次触发时，应用会保留最后一次请求，并在当前任务结束后补跑。

## 配置

首次启动会创建 `data/config.json`。未知的旧字段会被忽略，保存配置时只写入当前版本支持的字段。

主要字段：

| 字段 | 默认值 | 说明 |
| --- | ---: | --- |
| `host` | `127.0.0.1` | 监听地址，修改后需重启 |
| `port` | `8080` | 监听端口，修改后需重启 |
| `game` | `csgo` | BUFF 游戏标识 |
| `steam_appid` | `730` | Steam App ID |
| `currency` | `23` | Steam 价格接口币种 |
| `keywords` | 4 个默认词 | 关键词候选池 |
| `page_size` | `20` | 每个关键词或深度扫描页的条数 |
| `scan_interval_minutes` | `15` | 关键词扫描间隔 |
| `auto_scan` | `true` | 是否启动定时调度；修改后立即生效 |
| `max_items_per_cycle` | `100` | 单轮最多请求 Steam 价格的候选数 |
| `request_delay_sec` | `1.0` | BUFF 请求间隔 |
| `steam_delay_sec` | `0.5` | Steam 请求间隔 |
| `history_keep_days` | `30` | 价格历史保留天数，`0` 表示不自动清理 |
| `steam_fee_steam_pct` | `5.0` | 第一项手续费比例 |
| `steam_fee_game_pct` | `10.0` | 第二项手续费比例 |
| `fee_min` | `0.01` | 单项最低手续费 |
| `fee_round` | `cent` | `cent` 向上取整到分，`yuan` 向上取整到元 |
| `user_agent` | Chrome UA | BUFF 请求使用的 User-Agent |
| `log_level` | `INFO` | `DEBUG`、`INFO`、`WARNING` 或 `ERROR` |

深度扫描配置位于 `deep_scan`：

```json
{
  "enabled": false,
  "min_price": 20.0,
  "max_price": 300.0,
  "max_pages": 10,
  "interval_minutes": 240
}
```

`enabled` 控制定时深度扫描；手动点击“深度扫描”时仍会使用当前价格区间和页数配置。

## 运行时数据

所有运行时数据都位于 `data/`，该目录不会提交到 Git：

```text
data/
├── config.json          # 应用配置
├── buff_session.json    # BUFF Cookie 会话；内容不是普通 JSON
└── prices.db            # SQLite 数据库
```

`buff_session.json` 包含可用于访问账号的会话 Cookie，应像密码一样保护，不要上传、分享或提交到版本库。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/status` | 完整应用快照 |
| `GET` | `/api/auth/status` | BUFF 登录状态 |
| `POST` | `/api/auth/qr` | 创建/刷新登录二维码 |
| `POST` | `/api/auth/logout` | 清除本机会话 |
| `GET` | `/api/config` | 读取配置 |
| `PUT` | `/api/config` | 校验、保存并应用配置 |
| `POST` | `/api/scan` | 触发 `keyword` 或 `deepscan` 扫描 |
| `GET` | `/api/scan/status` | 扫描状态 |
| `GET` | `/api/items` | 当前排名列表 |
| `GET` | `/api/items/{name}/history` | 单个饰品的价格历史 |
| WebSocket | `/ws` | 实时状态推送 |

## 项目结构

```text
.
├── backend/
│   ├── main.py                 # FastAPI 生命周期与依赖装配
│   ├── config.py               # 配置模型和原子持久化
│   ├── db.py                   # SQLite schema 与查询
│   ├── models.py               # API 输入模型
│   ├── state.py                # 单进程共享状态与 WebSocket 广播
│   ├── routers/                # auth / config / items / scan / ws
│   └── services/
│       ├── buff.py             # BUFF HTTP 客户端和会话持久化
│       ├── buff_login.py       # 扫码登录状态机
│       ├── scanner.py          # 单 worker 扫描队列
│       ├── scheduler.py        # 关键词/深度扫描调度器
│       └── steam.py            # Steam 价格与折价计算
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   └── js/app.js
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

测试覆盖手续费与价格解析、配置持久化、扫码登录状态机、调度器、扫描排队、错误/取消状态和 SQLite 写入。

## 安全说明

- 默认仅监听 `127.0.0.1`，服务本身没有用户鉴权，不建议直接暴露到公网。
- 如果改为 `0.0.0.0`，同一网络中的其他设备可能访问页面、触发扫描或执行登出。
- 登录 Cookie 只保存在本机 `data/`，但拥有该文件访问权的程序或用户可能读取会话。
- 不要通过高并发、极短间隔或多 worker 运行来绕过平台限制。

## 常见问题

### 扫码确认后仍显示未登录

刷新二维码后重试，并查看终端中的 `buff_login` 日志。当前实现会在绑定后校验 BUFF 完整登录态，失败时不会把已确认二维码误当作超时码继续轮换。

### 重启后没有昵称

BUFF 恢复会话时可能只返回登录状态而不返回用户资料。此时页面显示“已登录”但不显示昵称，不影响扫描。

### Steam 价格大量缺失

通常是临时限流、网络错误或饰品没有有效最低价。可以提高 `steam_delay_sec`，等待下一轮扫描，不要并发启动多个实例。

### 修改 host 或 port 后没有变化

监听地址和端口在进程启动时读取，保存配置后需要重启应用；也可以用命令行参数临时覆盖。
