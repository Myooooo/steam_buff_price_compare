# Steam × BUFF 饰品雷达

本地运行的 CS2 饰品价格分析工具。通过 BUFF 扫码登录，扫描饰品报价并对比 Steam 市场价格，按折价、流动性和买卖价差计算机会评分。

> 本项目只提供价格采集与估算，不执行购买或交易。市场价格、手续费和平台限制可能随时变化，操作前请自行核实。

## 功能

- BUFF App 扫码登录，会话仅保存在本机。
- 立即扫描：遍历所有关键词及其全部搜索分页。
- 深度扫描：建立全量饰品索引并逐件定价，支持终止、重启和断点续传。
- 实时展示扫描阶段、进度、耗时及已完成结果。
- 保存商品图片、价格快照和扫描进度到本地 SQLite。
- 支持关键词、枪械、类别、磨损、数据状态、价格、折价和评分筛选。
- 支持按评分、折价、买卖价差、BUFF/Steam 价格及在售数量排序。
- 点击商品直接打开对应的 BUFF 商品页。
- Steam 直查失败或限流时，可使用 BUFF 提供的 Steam 参考价兜底。

## 评分

评分范围为 0–100，默认按评分从高到低排序：

| 指标 | 权重 | 规则 |
| --- | ---: | --- |
| 折价质量 | 60% | 5 折为 100；过低折价与接近 10 折均非线性扣分；10 折及以上为 0 |
| 流动性质量 | 40% | 挂牌深度占 65%，买卖价差占 35% |

```text
Steam 到手价 = Steam 售价 - Steam 手续费 - 游戏手续费
折价 = BUFF 最低售价 / Steam 到手价
买卖价差 = (BUFF 最低售价 - BUFF 最高求购价) / BUFF 最低售价
流动性质量 = 65% × 挂牌深度质量 + 35% × 买卖价差质量
折价质量（0 < 折价 < 0.5）= sqrt(折价 / 0.5)
折价质量（0.5 ≤ 折价 < 1）= 1 - ((折价 - 0.5) / 0.5)²
折价质量（折价 ≥ 1）= 0
评分 = 100 × 折价质量^0.60 × 流动性质量^0.40
```

挂牌数量按对数归一化，1000 件封顶。同时取得两侧数量时，深度按 BUFF 75% + Steam 25% 合成；Steam 数量缺失时只使用 BUFF，不会按零分处理。几何平均会限制低流动性商品的总分，避免高折价完全抵消成交困难。鼠标悬浮或键盘聚焦评分可查看计算明细。

折价是主要机会信号；流动性合并买卖价差代表的交易成本与挂牌数量代表的市场深度。评分只用于快速筛选候选商品，不代表确定收益。

## 安装与启动

环境要求：Python 3.11+，并确保可以访问 BUFF 和 Steam 市场。

```bash
git clone https://github.com/Myooooo/steam_buff_price_compare.git
cd steam_buff_price_compare
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python run.py
```

浏览器默认打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)。

常用启动参数：

```bash
python run.py --no-browser
python run.py --port 9000
python run.py --reload
```

## 使用

1. 打开页面，点击“扫码登录”，使用 BUFF App 扫码确认。
2. 点击“立即扫描”，完整扫描已配置关键词的全部结果。
3. 使用评分、折价、价格、枪械、类别和磨损等条件筛选商品。
4. 需要全市场数据时启动“深度扫描”。该任务可能持续数小时或数天，可随时终止并在下次启动时续传。
5. 点击结果行进入 BUFF 商品页面核实报价。

扫描中的模式按钮会变为终止按钮。列表中的“最新数据”和“缓存数据”标签用于区分最近一轮扫描结果与历史快照。

## 配置与数据

首次启动会在 `data/` 中创建：

```text
data/config.json        应用配置
data/buff_session.json  BUFF 登录会话
data/prices.db          商品、图片、价格和扫描进度
```

主要设置可直接在页面中修改：扫描关键词、扫描间隔、BUFF/Steam 请求间隔、手续费和 Steam 价格兜底。

Steam Community 市场搜索用于获取最低在售价和当前在售数量。遇到 429 默认暂停至少 60 秒、显示倒计时并重试当前商品；设置 `steam_rate_limit_mode` 为 `buff_fallback` 后，冷却期会直接使用 BUFF 参考价。参考价不包含 Steam 实时在售数量。

`data/` 不会提交到 Git。`buff_session.json` 包含登录 Cookie，请勿上传或分享。应用默认仅监听 `127.0.0.1`，不要直接暴露到公网。

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```
