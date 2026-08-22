# 快速上手

[← 文档索引](README.md)

## 环境要求

- Python 3.9+（推荐；系统自带 3.8 也可跑行情/对比/离线单测）
- 通义千问（阿里云百炼 / DashScope）API Key
- 在线选股 / 完整短线日线：需安装 `akshare`（依赖 pandas）
- macOS 若无 `python` 命令，请用 `python3`

完整技术栈（语言 · Web · 前端 CDN · 存储 · 运维）见 **[architecture · 技术栈](architecture.md#技术栈)**。

## 安装

```bash
cd investment
python3 -m pip install -U pip
python3 -m pip install -r requirements.txt
# 新建 .env，例如：
# DASHSCOPE_API_KEY=your_api_key
# DASHSCOPE_MODEL=qwen-plus
```

或使用环境变量：

```bash
export DASHSCOPE_API_KEY=your_api_key
export DASHSCOPE_ENDPOINT=https://ws-7hpevbps1ivbjf03.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
export DASHSCOPE_MODEL=qwen-plus
# 联网搜索（默认开启）
# export DASHSCOPE_ENABLE_SEARCH=1
# export DASHSCOPE_SEARCH_STRATEGY=max
# export DASHSCOPE_SEARCH_FRESHNESS=7
# 可选：对话读超时秒数（默认 120）；探测默认 30
# export DASHSCOPE_TIMEOUT=180
# export DASHSCOPE_PROBE_TIMEOUT=45
```

## 运行

### CLI

```bash
cd investment
python3 main.py
```

交互命令：

- `quit` / `exit`：退出
- `reset`：清空对话历史，并清零 token 累计
- `usage` / `tokens`：查看本轮与会话累计 token

### Web 端

```bash
cd investment
python3 -m pip install -r requirements.txt
python3 run_web.py
```

浏览器打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

| 项 | 说明 |
|----|------|
| 入口 | `run_web.py` → FastAPI `web/app.py` |
| 页面 | `web/static/`（对话 UI） |
| API | `POST /api/chat`、`GET /api/paper*`、`GET/POST /api/evals/*`、`GET /api/usage`、`GET /api/health` |
| 顶栏 | 主路径：**对话 · 观察 · 策略 · 模拟 · 回溯**；进阶页仍有 `/paper`（→模拟）、`/quant` |
| 会话 | 请求头 `X-Session-Id`（前端存 localStorage） |
| 端口 | 环境变量 `WEB_HOST`（默认 127.0.0.1）、`WEB_PORT`（默认 8000） |
| 热重载 | 默认 **关**（`WEB_RELOAD=0`）。开发改 py 时设 `WEB_RELOAD=1`；热重载会中断纸面后台任务，确认调仓时勿触发。纸面/分组/网格/对话 job 落盘 `data/jobs/*.json` |
| 日线缓存后端 | `INVESTMENT_BARS_BACKEND=sqlite`（默认）或 `json`；见 [sqlite-migration](sqlite-migration.md) · [architecture-upgrade-a](architecture-upgrade-a.md) |

Web 与 CLI 共用同一套 `InvestmentAgent` / Skills / prompts；仍需配置 `DASHSCOPE_*`。

### 纸面 / 模拟是什么？

**模拟**（`/follow`）= 用假钱练手的日常账本：加减仓、清仓、净值曲线。  
**纸面**（`/paper`）= 同一套假账的**初始化 / 高级配置**入口，不进主导航；日常请用「模拟」。  
行情可以是真的，买卖只记在本地 `data/paper.json`，**不会亏真钱、不会下到真实券商账户**。本系统现行定位为 **策略验证**：**仅限模拟账户交易**。待验证成熟后再评估实盘。新仓从**观察**页建仓进来。完整说明见 [quant.md · 纸面是什么](quant.md#纸面是什么给小白) 与 [quant-ui.md](quant-ui.md)；边界见 [design-spine · 产品边界](design-spine.md#产品边界现行)。

## 持仓（对话）

对话问持仓时，**默认读模拟账户** [`data/paper.json`](data/paper.json)（观察页建仓后即有）。

## 使用示例：分析快手的长短期持仓参考

若问的是「分析快手行情，**是否可以买入**」，系统路径与**买入依据**见 [买入决策流程](skills.md#系统如何回答分析快手行情是否可以买入) / [判断是否买入的依据](skills.md#判断是否买入的依据是什么)。

下面以港股 **快手-W（01024）** 为例，说明长短期观察/持仓参考的完整链路。可走两条路径：

| 路径 | 怎么跑 | 是否完整 Investment |
|------|--------|---------------------|
| A. Agent 对话（推荐） | `python3 main.py` + 自然语言 | 是：LLM 选工具 + 解读 |
| B. 直接调 Skill | `python3 -c ...` | 否：只跑数据层，解读需人/模型另做 |

标的代码：`01024` / `hk01024`；名称「快手」已收入映射表（`skills/common/quote_api.py`）。

### 路径 A：完整 Agent 流程（推荐）

```bash
cd investment
# 先配置 DASHSCOPE_* 环境变量或 .env
python3 main.py
```

在对话中输入例如：

```text
分析一下快手 01024 的短期和长期持仓怎么看
```

**Agent 内部预期过程**：

```text
1. 用户问题进入 InvestmentAgent.chat()
2. LLM 根据 prompts + tool_config 选择工具，常见组合：
   - quote(stock_code="快手" 或 "01024")     → 现价 / 涨跌 / 高低 / 量
   - kline(stock_code="快手", limit=10)      → 近 N 日形态
   - signal(stock_codes=["01024"], horizon_days=3) → 短线评分与失效条件
   - fundamentals / peer / news（问长期或资讯时）
   （若问已持有仓位，再调 position）
3. Handler 执行，JSON 结果以 role=tool 写回对话
4. LLM 按「事实摘要 → 观察点 → 风险/失效条件」组织回复
5. 末尾附带风险声明（量化研究与模拟 / 不保证收益 / 不代客下单）。
```

**一次实盘拉取到的行情事实示例**（数字会随交易日变化，以下为某次采样）：

| 项目 | 示例值 |
|------|--------|
| 标的 | 快手-W（01024.HK） |
| 现价 | HK$43.28 |
| 当日涨跌 | -7.76%（-HK$3.64） |
| 日内区间 | 开 47.18 / 高 47.40 / 低 43.06 |
| 成交量 | 约 8363 万 |
| 短线评分（1～3 天） | 约 38.9 / 100（偏弱） |
| 短线失效参考 | 约 HK$41.98（现价约 -3%） |
| 评分数据源 | 可能为 `quote_fallback`（港股日线未拉到时因子偏粗） |

**解读结构示例（逻辑示意，非实时结论）**：

1. **事实**：当日大跌、开高走低，短线分偏低 → 近 1～3 天动能偏弱。  
2. **短期持仓参考**：偏观望/防守；已持仓关注能否站稳低点附近，跌破失效位则降低短线优先级；未持仓不宜把急跌直接当成必反弹信号。  
3. **长期持仓参考**：长期看生意（短视频/电商/广告变现、竞争与盈利质量），单日大跌多属波动；加减仓宜基于基本面与仓位纪律，而非仅凭当日阴线。  
4. **风险**：策略结论不等于下单指令，不保证收益。

### 路径 B：无 LLM 时复现数据层（调试用）

仅验证 Skill，不经过 `main.py` / 大模型：

```bash
cd investment

# 1) 行情
python3 -c "from skills.common import StockAPI; import json; print(json.dumps(StockAPI.query('快手'), ensure_ascii=False, indent=2))"
# 等价：StockAPI.query('01024')

# 2) 短线观察池 / 评分
python3 -c "
from skills.signal.handler import SignalHandler
import json
print(SignalHandler().execute({
    'parameters': {'stock_codes': ['01024'], 'horizon_days': 3, 'limit': 5}
}))
"
```

然后再按上面的「解读结构」人工或另用模型写长短期参考。  
注意：路径 B **不算**完整 Agent 使用；完整例子请走路径 A。

### 过程中的注意点

- 港股代码可用 `01024`、`1024`、`hk01024`；优先用映射名「快手」。
- `signal` 若 `data_source=quote_fallback`，说明没用上完整日线，短线分仅供参考。
- 长期结论应优先调 `fundamentals`（及可选 `peer`/`index`/`news`）；港美财务覆盖弱于 A 股时，LLM 只能做定性框架，仍不得编造数字。
- 每次运行价格与评分都会变，文档中的表格只说明「过程长什么样」，不要当作当前买卖依据。

## 量化研究台（可选）

逐步操作（观察→回溯→纸面→模拟）见 **[quant-ui.md · 操作流程](quant-ui.md#操作流程照着做)**。

```bash
cd investment
bash scripts/setup_quant.sh                              # watching + 纸面 init
python3 research/daily_run.py --preset quant --json      # 每日量化
python3 research/daily_run.py --preset quant_paper --json # 量化 + 纸面调仓
python3 research/signal_diff_export_run.py --fresh       # 导出 diff 合并包
python3 evals/run_checklist.py --mock --presets          # golden + preset 校验
python3 run_web.py                                       # Web → 顶栏工具页
```

详见 [quant.md](quant.md) · [quant-ops.md](quant-ops.md) · [quant-upgrade.md](archive/quant-upgrade.md)。

## 无 LLM 时直接测模块

Skill 可脱离 Agent 单独调用，便于调试数据层：

```bash
cd investment
# 行情
python3 -c "from skills.common import StockAPI; print(StockAPI.query('茅台'))"

# 短线观察池（无 akshare 时自动用行情退化评分）
python3 -c "from skills.signal.handler import SignalHandler; print(SignalHandler().execute({'parameters':{'stock_codes':['茅台','招商银行'],'horizon_days':3}}))"

# 持仓参考
python3 -c "from skills.position.handler import PositionHandler; print(PositionHandler().execute({'parameters':{'horizon_days':3}}))"

# K 线形态
python3 -c "from skills.kline.handler import KlineHandler; print(KlineHandler().execute({'parameters':{'stock_code':'快手','limit':10}}))"

# 基本面 / 同行 / 相对强弱 / 资讯
python3 -c "from skills.fundamentals.handler import FundamentalsHandler; print(FundamentalsHandler().execute({'parameters':{'stock_code':'茅台'}}))"
python3 -c "from skills.peer.handler import PeerHandler; print(PeerHandler().execute({'parameters':{'stock_code':'快手'}}))"
python3 -c "from skills.index.handler import IndexHandler; print(IndexHandler().execute({'parameters':{'stock_code':'宁德时代','window_days':20}}))"
python3 -c "from skills.news.handler import NewsHandler; print(NewsHandler().execute({'parameters':{'stock_code':'茅台','limit':5}}))"
```
