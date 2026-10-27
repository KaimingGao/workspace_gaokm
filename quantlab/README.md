# QuantLab · 量化交易（融合 AI）

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![Docs](https://img.shields.io/badge/docs-docs%2FREADME-4D9BFF)](docs/README.md)

**定位**：本地 **策略验证系统**——信号 · 策略 · 回测 · **模拟账户（纸面）** · 研究台；并融合 **AI** 做自然语言编排与解释。

| 做 | 不做（现行） |
|----|------|
| 观察名单、回测、纸面账本买卖与调仓 | **真实券商账户交易**、代客下单、OMS 实盘（推迟到策略验证成熟后，N6） |
| 用真实行情做研究与模拟盯市 | 保证收益、持牌投顾 |

对外称「模拟」；内部账本名 `paper`。观察名单（`watching`）和已有仓位的账本是两件事。

**侧栏**（`/` → `/dashboard`；AI 在顶栏抽屉 ⌘K，不占侧栏）：

| 页面 | 路径 | 做什么 |
|------|------|--------|
| 仪表盘 | `/dashboard` | 市场、纸面 KPI、净值；不下单、不改名单 |
| 策略中心 | `/strategy` | M prior · 因子字典 |
| 数据中心 | `/watching` | 观察名单、评分/倾向、建仓入口 |
| 交易执行 | `/follow` | 纸面调仓 · 做 T（`/paper` 重定向到此） |
| 历史回测 | `/replay` | 调仓回测 · 做 T 回测 |
| 研究枢纽 | `/quant` | 拉数 · 物化特征 · 拟合 ŷ · 日报 |
| 平台 | `/platform` | 调度 · 纸面日更 |

谁决定「买什么、买多少」，就归谁。人决定的走数据中心 / 交易执行；规则决定的标在持仓出处上。闭环与易漏能力见 [docs/quant-ui.md · 用户心智与验证闭环](docs/quant-ui.md#用户心智与验证闭环)。

**产品核心设计主轴**：**本质** 已发生事实 → 影响估计 → 验证 → 动作；**现行** 数据 → 信号 → 因子 → 模型(ŷ) → 动作（量化主轴 + AI 旁路）；**北极星** = 纸面风险调整收益 × 迭代速度 × 回测–纸面拟合度。见 **[docs/design-spine.md](docs/design-spine.md)**。

架构一句话：**领域层 `core`（DS / SS / BS + 信号 / 回测 / 纸面）+ `adapters` 出站取数 + Agent 编排 Skills**。分层与技术栈见 [docs/architecture.md](docs/architecture.md)；Web 契约见 [docs/quant-ui.md](docs/quant-ui.md)。

> **现行仅限研究与模拟账户（策略验证）。不涉及真实账户交易。** 市场有风险，不保证收益，不代客下单。

---

## 核心架构

### 产品设计主轴

| 环节 | 含义 | 代码落点 |
|------|------|----------|
| **数据** | 行情 / 日线 / 5m / 基本面 / 舆情；**L1 Feature Lake · L2 DayCube** | `core/data`（DS）· `core/ports` · `adapters.bind` · `core/research/materialize` |
| **信号** | `hard_reject` + 因子子分；生产打分经 SS | `core/signal_service` → `core/signal` |
| **因子** | 动量 / 量价 / … 子分；截面 z（PIT，`date\|τ`） | `core/signal/factors` · `data/signal_config.json` |
| **模型** | ŷ：日线 ŷ_oo / ŷ_co · 分钟 ŷ_τc · 窗头 ŷ_τ30…90→ŷ_τw | `core/yhat` · `core/research/horizon_*` · 双层 fuse（调仓） |
| **动作** | 建仓 · 规则调仓 · 做 T · 回测 | `core/watching` · `core/paper` · `core/t0` · `core/backtest` |

量化主轴算数字；AI 旁路只编排与解释（不得改写 `score` / `stance_label`）。

### 工程分层

```
┌─────────────────────────────────────────────────────────────┐
│  接入层    main.py / run_web.py / web/app.py + routers/     │
├─────────────────────────────────────────────────────────────┤
│  应用服务  services/* · quant/services（QuantService）       │
├─────────────────────────────────────────────────────────────┤
│  编排层    agent/（agent · routing · registry · prompts）   │
├─────────────────────────────────────────────────────────────┤
│  工具层    skills/*/handler.py · quant/skill（薄包装）       │
├─────────────────────────────────────────────────────────────┤
│  领域层    core/  DS · SS · BS → signal / paper / backtest  │
│            research · t0 · yhat · watching · risk           │
├─────────────────────────────────────────────────────────────┤
│  端口层    core/ports + adapters.bind（出站，无领域计算）    │
├─────────────────────────────────────────────────────────────┤
│  数据层    adapters/market · AkShare · data/store（bars.db）│
└─────────────────────────────────────────────────────────────┘
```

读行情走 **DS**（`core/data/facade`），打分走 **SS**（`core/signal_service`），回测信封走 **BS**（`core/backtest_service`）。产品回测主循环在 `core/backtest/paper_replay.py`。控制论闭环与技术栈见 [docs/architecture.md](docs/architecture.md)。

---

## 功能

| 能力 | 落点 |
|------|------|
| 信号 / 倾向 | `core/signal` 打分；`core/advise` + `core/stance` 给出 `stance_label`（只吃 ŷ） |
| 研究台 ŷ | ŷ_oo / ŷ_τc / ŷ_co；窗头 ŷ_τ30…90 **同窗双训 LightGBM + Ridge**（Holdout AUC 择优）；做 T 用 ŷ_τw |
| 物化特征（L1/L2） | 日线/分钟 **Feature Lake + DayCube**；拟合与回测只读，缺覆盖返回 `materialize_required` |
| 回测 / 模拟 | `/replay` 调仓 · 做 T；`/follow` 假钱账本、出处标记、成本模型 |
| 观察 | `/watching` 名单 + 现价 / 涨跌 + 舆情；建仓进模拟账户 |
| AI 辅助 | 顶栏抽屉：问价 / 对比 / 分析；Agent 编排 13 个 Skill |

**ŷ 与截面口径**

- **调仓**：ŷ_oo 主排序 + fuse（ŷ_τc / ŷ_co），基准是 τ→open[T+1]。
- **做 T**：纯 ŷ_τw（五窗 ŷ_τ30…90 相对中位点投票）定方向与金额；训练网格 **09:35…11:00**（分钟 L2），live 可含 09:30。
- **CS-z**：拟合与 live 均为 **`date|τ` PIT**（同日多钟不混组）。细节见 [docs/t0-logic.md](docs/t0-logic.md)、[docs/quant.md · ŷ 全链路](docs/quant.md#predicted_scoreŷ全链路)、[docs/internal/feature-lake-daycube.md](docs/internal/feature-lake-daycube.md)。

---

## 目录结构

```
quantlab/
├── agent/                    # 意图 · 多轮工具调度 · prompts · LLM 客户端
├── core/                     # 领域层（无 LLM、无 HTTP）
│   ├── data/ · signal_service.py · backtest_service.py   # DS · SS · BS
│   ├── ports/ · yhat/        # 出站契约 · ŷ 加载与指纹
│   ├── signal/ · paper/ · backtest/   # 打分 · 账本 · replay
│   ├── research/ · t0/ · watching/ · risk/ · market/
│   └── persistence/ · platform/ · store.py   # Bars/JSON 门面；store 为缓存入口
├── adapters/                 # 出站 I/O（经 adapters.bind 注入 ports）
├── skills/                   # Agent 工具（handler；注册 13 个）
├── quant/                    # 研究台：services · ops · research · skill
├── services/                 # 应用服务（纸面 / 观察 / 对话 / 日更 / 平台）
├── web/                      # FastAPI + static（侧栏七页 + AI 抽屉）
├── research/ · scripts/      # 薄 CLI · 日更 / CI
├── evals/ · tests/
├── data/                     # watching / paper / signal_config · store/bars.db
├── docs/
├── main.py                   # CLI
└── run_web.py                # Web
```

包职责与依赖纪律见 [core/README.md](core/README.md)。完整树见 [docs/architecture.md · 代码目录结构](docs/architecture.md#代码目录结构)。

---

## 关键组件

### Agent

[agent/agent.py](agent/agent.py) 多轮对话，最多 5 轮工具调用：用户输入 → LLM 选工具 → Skill 执行 → 再生成回复，并强制追加免责声明。

[agent/registry.py](agent/registry.py) 是工具名、Handler、`tool_config.json` 的单一事实源（13 个）：

| 工具名 | 功能 |
|--------|------|
| `quote` | 实时行情 |
| `compare` | 多股对比 |
| `screen` | A 股条件选股 |
| `signal` | 短线观察池（因子评分） |
| `backtest` | signal 规则历史回测 |
| `quant` | 研究台（横截面、组合回测、因子、日报、做 T 回测等 task） |
| `kline` | 日 K 形态摘要 |
| `fundamentals` | 基本面 |
| `peer` | 同行对比 |
| `index` | 相对大盘超额 |
| `news` | 资讯标题摘要 |
| `position` | 持仓建议（默认读 `paper.json`） |
| `advise` | 规则引擎买卖结论 |

[agent/prompts.py](agent/prompts.py) 约束路由与合规：问「能不能买」必须逐字引用 `advise.stance_label`；只有用户明确要求「深度分析 / 详细拆解 / 长文」才展开长文。禁止保证收益、代客下单。

### 规则引擎（advise）

[core/advise.py](core/advise.py) 组装 facts，[core/stance.py](core/stance.py) 给出买入 / 观望等 `stance_label`。主分必须是 ŷ（`predicted_score`，多为 ŷ_oo）；没有 ŷ 则「信息不足」。启发式 `score` 只作研究对照，不驱动倾向。LLM 只引用 `stance_label`，不改写 ŷ。

---

## 设计原则

1. **已发生 → 影响估计**：只用决策时点可见事实；禁止未来函数与不可审计改分（[因果链](docs/design-spine.md#因果链已发生--影响估计--动作)）
2. **事实与结论分离**：价格、评分、财务只来自 Skill / `core` JSON；LLM 只编排和解释
3. **工具即边界**：每个能力一个 `skills/<name>/` + `tool_config.json`（`quant` 的实现在 `quant/skill`）
4. **可组合**：复杂问题靠多轮工具串联（如 `quote` + `kline` + `signal`）
5. **可降级**：日线失败时标明「日线不完整」
6. **风险披露**：代码兜底追加免责声明；模拟账户不是实盘

---

## 快速开始

逐步操作（虚拟环境、先放 3 只股票、在研究枢纽拉日线和分钟线、物化特征、拟合并启用研究套、再跑调仓回测）见 **[docs/getting-started.md](docs/getting-started.md)**。下面是环境已经就绪时的命令。需要 Python 3.10+。

```bash
cd quantlab
python3 -m pip install -r requirements.txt
cp .env.example .env          # 仅 AI 抽屉 / Agent 需要；填 DASHSCOPE_API_KEY、DASHSCOPE_MODEL
bash scripts/setup_quant.sh   # watching.json / paper.json 不存在时初始化（观察名单约 500 只）
python3 run_web.py            # → http://127.0.0.1:8000  （首页 /dashboard）
```

第一次回测按 [getting-started.md](docs/getting-started.md) 先写成 3 只，在研究枢纽 **拉日线 / 5m → 物化特征（L1/L2）→ 拟合并启用研究套**，再到历史回测开跑。整池模板会把拉数、物化和拟合拖得很久；窗头双训内存压力大，宜单任务跑，不要和整池分钟预热并行。行情、观察、回测、模拟账户不依赖 API key。

离线校验（不连外网、不配 key）：

```bash
python3 evals/run_checklist.py --mock
```

日线回测（需外网）：

```bash
python3 research/t0_backtest_run.py --code 茅台 --json
```

无 LLM 时可以直接调 Skill：

```bash
python3 -c "from skills.quote.handler import QuoteHandler; print(QuoteHandler().execute({'parameters':{'stock_code':'茅台'}}))"
```

---

## 数据准备

量化研究与回测依赖行情缓存和两份本地 JSON。`data/watching.json`、`data/paper.json` 在 `.gitignore` 里，由 `setup_quant.sh` 生成。文件已存在时脚本会跳过，不会覆盖。

| 数据 | 来源 | 初始化 | 说明 |
|------|------|--------|------|
| 观察名单 | `data/watching.example.json` | `setup_quant.sh` → `watching_run.py --init` | 运行时读写 `data/watching.json` |
| 纸面账户 | 脚本内置 | 同上，`paper_run.py --init` | `data/paper.json` |
| 日线 | 新浪 `stock_zh_a_daily` | 研究枢纽增量补齐 / preset | 默认 SQLite `data/store/bars.db`（`QUANTLAB_BARS_BACKEND=sqlite`） |
| 5 分钟 K | 新浪 `getKLineData`（不拉日线） | 研究枢纽增量补齐 | Ready 后才物化分钟 L2 |
| L1 Feature Lake | 物化 Job | 研究枢纽「物化特征」/ `POST /api/quant/materialize` | `data/cache/feature_lake/`；按股×日 raw |
| L2 DayCube | 同上 | 日线 open + 分钟 τ | `data/cache/day_cubes/`；截面 μ/σ |

只初始化观察名单：

```bash
python3 research/watching_run.py --init    # 已有 watching.json 会报错，避免覆盖
python3 research/watching_run.py --show
```

仓库提交的是模板 `data/watching.example.json`（约 500 只，`sources` 为空）。改自己的池子时改 `data/watching.json`，或在数据中心 / 研究枢纽里改。

`sources` 为空时，`python3 research/watching_run.py --refresh` 只补全名称，不改 `watchlist`。要按规则重算名单，在 `watching.json` 里写 `sources`（上限 `max_size`，最大 500），保存后再 `--refresh`：

| `type` | 含义 | 示例 |
|--------|------|------|
| `static` | 固定代码或名称 | `{"type": "static", "codes": ["600519", "茅台"]}` |
| `screen` | 按条件筛选后并入（需外网） | `{"type": "screen", "sector": "银行", "limit": 5}` |

字段与数据源见 [docs/development.md · 数据源一览](docs/development.md#数据源一览)。K 线缓存见 [data/README.md](data/README.md)。

---

## 常用命令

```bash
python3 evals/run_checklist.py --mock             # 离线黄金用例（CI 同款）
bash scripts/ci_quant.sh                          # 本地 CI 全量
python3 run_web.py                                # Web
python3 -m unittest discover -s tests -v          # 单测
```

研究脚本（T0 回测、因子实验、纸面调仓、信号导出）见 [docs/development.md](docs/development.md)。

---

## 文档

活跃文档索引见 [docs/README.md](docs/README.md)。入口：

| 文档 | 说明 |
|------|------|
| [docs/getting-started.md](docs/getting-started.md) | 从 clone 到第一次调仓回测 |
| [docs/architecture.md](docs/architecture.md) | 分层、端口、目录、技术栈 |
| [docs/design-spine.md](docs/design-spine.md) | 因果链、北极星、能力地图、产品边界 |
| [docs/quant.md](docs/quant.md) | ŷ 全链路、回测 / 纸面原理、运维 |
| [docs/quant-ui.md](docs/quant-ui.md) | Web 说明书与 UI 契约 |
| [docs/rebalance-logic.md](docs/rebalance-logic.md) | 调仓 |
| [docs/t0-logic.md](docs/t0-logic.md) | 做 T（纯 ŷ_τw） |
| [docs/development.md](docs/development.md) | 环境、测试、Skill |
| [docs/internal/feature-lake-daycube.md](docs/internal/feature-lake-daycube.md) | L1/L2 物化与消费 |

各代码子目录有 `README.md`。在线索引：`GET /api/readme-index`；单篇：`GET /api/readme?dir=`。

---

## 合规

本仓库是 **策略验证系统**（量化研究 + 模拟账户，融合 AI 编排），**非持牌投顾问诊，现行不涉及真实账户交易、不代客下单**。策略结论与模拟盈亏**不保证收益**。细则见 [docs/development.md#合规与风险说明](docs/development.md#合规与风险说明)。
