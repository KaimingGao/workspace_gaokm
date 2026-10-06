# QuantLab · 量化交易（融合 AI）

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![Docs](https://img.shields.io/badge/docs-docs%2FREADME-4D9BFF)](docs/README.md)

**定位（现行边界）**：本地 **策略验证系统**——信号 · 策略 · 回测 · **模拟账户（纸面）** · 研究台；并融合 **AI** 做自然语言编排与解释。

| 做 | 不做（现行） |
|----|------|
| 观察名单、回测、纸面/模拟账本买卖与调仓 | **真实券商账户交易**、代客下单、OMS 实盘（推迟到策略验证成熟后） |
| 用真实行情做研究与模拟盯市 | 保证收益、持牌投顾 |

产品主路径三件事（**观察 ≠ 模拟**）：

1. **观察** — 维护观察名单，并从这里建仓进**模拟账户**  
2. **模拟** — 已有仓位的假钱账本（加减仓 · 清仓 · 策略调仓；**非实盘**）  
3. **回溯** — 组合级历史验证  

用户常感知的两件事（纸面策略调仓 · 做 T 回测）**方向对、覆盖不全**；完整闭环与易漏能力见 **[docs/quant-ui.md · 用户心智与验证闭环](docs/quant-ui.md#用户心智与验证闭环)** / [design-spine 同节](docs/design-spine.md#用户心智与验证闭环现行)。

贯穿原则：谁决定「买什么、买多少」，就归谁。人决定的走主路径；规则/策略决定的收进「进阶」，并在持仓上标出处。

**产品核心设计主轴**：**本质** 已发生事实 → 影响估计 → 验证 → 动作（[因果链](docs/design-spine.md#因果链已发生--影响估计--动作)）；**现行** 数据 → 信号 → 因子 → 模型(ŷ) → 动作（量化主轴 + AI 旁路）；**北极星** = 纸面风险调整收益 × 迭代速度 × 回测–纸面拟合度（[产品北极星](docs/design-spine.md#产品北极星)）——六大模块是 [能力地图](docs/design-spine.md#能力地图六大模块)；**现行只收口研究台 + 模拟账户做策略验证**，真·实盘 OMS 待验证成熟后另立项（N6）——见 **[docs/design-spine.md](docs/design-spine.md)**。实现节奏见 **[北极星实现规划 P0–P3](docs/roadmap.md#北极星实现规划p0p3)**。

架构一句话：**量化领域层（`core` 信号/回测/模拟）+ Skills 取数与规则 + AI Agent（意图理解 · 工具编排 · 研究话术）**。  
Web 主路径见 [docs/quant-ui.md](docs/quant-ui.md)（含 Web 契约与升级方案）；**已收口加强验收**见 [docs/archive/pro-core-strengthen.md](docs/archive/pro-core-strengthen.md)（DC/FM/RK）；已收口历史轨见 [docs/archive/](docs/archive/)；历史节奏见 [docs/design-spine.md · 路线图](docs/design-spine.md#能力评估与升级规划路线图视角)。

> **现行仅限研究与模拟账户（策略验证）。不涉及真实账户交易。** 待策略验证成熟后再评估实盘。市场有风险，不保证收益，不代客下单。

---

## 核心架构

### 产品设计主轴（必读）

| 环节 | 含义 | 代码落点 |
|------|------|----------|
| **数据** | 行情 / 日线 / 基本面 / 舆情 / 配置 | `core/ports` · `skills/*` · `data/*.json` |
| **信号** | `hard_reject` + 因子子分（启发式 `score` 仅对照） | `score_stock` → `score_bars` |
| **因子** | 动量 / 量价 / … 子分特征 | `core/signal/factors` · `signal_config.json` |
| **模型** | ŷ（`predicted_score`；组 β / Ridge） | `ReturnScoreModel` · 双层 ŷ_oo+ŷ_τ |
| **动作** | 观察展示 · 人建仓 · 规则调仓 · 回溯 | watching / paper / backtest |

**两条轨**：量化主轴算数字；AI 旁路只编排与解释（不得改写 `score` / `stance_label`）。  
**北极星**（纸面夏普/卡玛 × TTM × 回测–纸面拟合）与 **能力地图**（收集→清洗→因子→组合风控→回测→纸面迭代）见 **[docs/design-spine.md](docs/design-spine.md)**。

### 控制论闭环

剥离业务细节后，系统抽象为 **感知-决策-执行-反馈** 闭环（量化管线 + 可选 AI 编排）：

```
Input (State) → Policy (Model) → Action (Tool) → Reward (Feedback) → Update (Learning)
```

| 逻辑层 | 职责 | 代码落点 |
|--------|------|---------|
| **感知与交互** | 自然语言→意图；行情/财报→可计算结构 | Web/CLI + `skills/quote|fundamentals|news|…` |
| **认知与推理** | 上下文、拆解子任务、解释信号与策略结果 | `agent/agent.py` + `prompts.py` + `llm_client.py` |
| **规划与工具** | 高层目标→Skill 调用链 | `agent/registry` + `skills/*/tool_config.json` |
| **评估与反馈** | 回测指标、模拟账本、回归校验 | `core/backtest` + 模拟账户（paper）+ `evals/` |

### 工程分层

```
┌─────────────────────────────────────────────────────────────┐
│  接入层    main.py / run_web.py / web/app.py + routers/     │
├─────────────────────────────────────────────────────────────┤
│  应用服务  services/* · quant/services（Application Service） │
├─────────────────────────────────────────────────────────────┤
│  编排层    agent/agent.py · routing.py · registry        │
├─────────────────────────────────────────────────────────────┤
│  认知层    llm_client.py + prompts.py                       │
├─────────────────────────────────────────────────────────────┤
│  适配层    skills/*/handler.py（薄包装，委托 engine/core）   │
├─────────────────────────────────────────────────────────────┤
│  领域层    core/（facts · advise · stance · store · t0）    │
├─────────────────────────────────────────────────────────────┤
│  数据层    adapters/market/ · AkShare · data/*.json           │
└─────────────────────────────────────────────────────────────┘
```

技术栈明细（语言 · FastAPI · 前端 CDN · 存储 · 运维）见 **[docs/architecture.md · 技术栈](docs/architecture.md#技术栈)**。

---

## 功能

| 能力 | 示例 |
|------|------|
| 信号 / 策略 | 评分、买卖倾向、规则调仓（`core/signal` · `advise` · 模拟调仓） |
| 回测 / 模拟 | Web **回溯**；Web **模拟**（假钱账本 · 出处标记 · 成本模型） |
| 观察 | Web **观察**（名单 + 现价 / 涨跌 + 舆情；建仓入口） |
| AI 辅助 | 自然语言问价/对比/分析；Agent 编排 Skills |
| 其它 Skills | 选股、K 线、基本面、同行、强弱、资讯、持仓规则 |

---

## 目录结构

```
investment/
├── agent/                    # Agent 编排层
│   ├── agent.py                # InvestmentAgent（多轮对话 + 工具调度）
│   ├── llm_client.py           # 通义千问 HTTP 客户端
│   ├── prompts.py              # 系统提示词（角色、路由、合规）
│   ├── registry.py             # Skill 单一注册表（13 个工具）
│   ├── contracts.py            # 接口契约（Protocol + BaseSkillHandler）
│   ├── routing.py              # 意图检测与参数增强
│   └── artifacts.py            # 结构化产物生成
├── core/                       # 领域层（无 LLM、无 HTTP）
│   ├── data/ · ports/          # 读口 facade · 端口注入
│   ├── signal/                 # 因子注册与评分
│   ├── backtest/               # 历史回测引擎
│   ├── paper/                  # 模拟账本 · exec · cycle · rebalance
│   ├── watching/               # 观察池
│   ├── t0/ · risk/ · research/ # T+0 · 风控 · 研究模型
│   ├── advise.py · stance.py   # 规则引擎买卖结论 / 倾向
│   └── store.py                # 日线缓存
├── adapters/                   # 出站 I/O（经 adapters.bind 注入 ports）
├── skills/                     # Agent 工具层（handler + shim；注册 13 个）
│   ├── quote/ compare/ screen/ signal/
│   ├── kline/ fundamentals/ peer/ index/ news/
│   ├── position/ advise/ backtest/
│   └── quant/                  # tool_config；Handler 在 quant/skill/
├── web/                        # Web 层（FastAPI）
│   ├── app.py                  # FastAPI 入口
│   ├── routers/                # chat / paper / quant* / watching / daily / …
│   └── static/                 # 前端资源（HTML/CSS/JS）
├── quant/                      # 量化研究台
│   ├── services/               # QuantService
│   ├── research/               # 研究脚本
│   ├── ops/                    # 日更 preset · 健康检查
│   └── skill/                  # Agent quant Handler
├── services/                   # Application Service（纸面 / 观察 / 对话 / 日更）
├── research/                   # 薄 CLI 入口
├── scripts/                    # 日更 / 回归 / 对照脚本
├── evals/ · tests/             # 黄金用例 · 单元测试
├── data/                       # 配置与本地存储
├── docs/                       # 文档（细目录见 architecture.md）
├── main.py                     # CLI 入口
└── run_web.py                  # Web 入口
```

分层与完整树见 [docs/architecture.md · 代码目录结构](docs/architecture.md#代码目录结构)。

---

## 关键组件

### InvestmentAgent（编排核心）

[agent/agent.py](agent/agent.py) — 多轮对话 + 多工具调度：

```python
class InvestmentAgent:
    MAX_TOOL_ROUNDS = 5  # 最多5轮工具调用
    
    def chat(self, user_input: str) -> str:
        # 1. 用户输入 → LLM（带全部工具定义）
        # 2. LLM 返回 tool_calls → 执行对应 Skill
        # 3. 工具结果 → 再次 LLM → 生成自然语言回复
        # 4. 强制追加免责声明 + token 消耗统计
```

### Skill 注册表（单一事实源）

[agent/registry.py](agent/registry.py) — 13 个工具：

| 工具名 | 功能 |
|--------|------|
| `quote` | 实时行情 |
| `compare` | 多股对比 |
| `screen` | A股条件选股 |
| `signal` | 短线观察池（因子评分） |
| `backtest` | signal 规则历史回测 |
| `quant` | 量化研究台（15+子任务） |
| `kline` | 日K形态摘要 |
| `fundamentals` | 基本面分析 |
| `peer` | 同行对比 |
| `index` | 相对大盘超额 |
| `news` | 资讯标题摘要 |
| `position` | 持仓建议 |
| `advise` | **规则引擎买卖结论**（核心） |

### Prompts（认知层行为说明书）

[agent/prompts.py](agent/prompts.py) — 定义 AI 如何服务量化工作流（编排工具、解释结果、守合规红线）：

**核心约束**：
- **路由规则**：软引导（如持仓→`position`；「能否买入」→`advise`）
- **深度分析模式（可选）**：仅当用户明确要求「深度分析 / 详细拆解 / 长文」时展开 700~1100 字结构；普通「能不能买」短答优先（引用 `advise.stance_label`）
- **合规红线**：禁止保证收益、代客下单、内幕交易
- **「是否买入」必答节**：必须逐字引用 `advise.stance_label`（规则引擎结论，非模型臆造）
- **DISCLAIMER**：代码兜底强制追加（双保险）

### 规则引擎（advise）

[core/advise.py](core/advise.py) — 买入/观望/减仓的规则引擎：

- **输入**：quote + signal + kline + peer + index
- **输出**：`stance_label`（买入/观望/减仓/止损）+ `facts` + `invalidation`（失效条件）
- **设计**：`compute_buy_stance` 只吃 **ŷ**（`predicted_score`）；无 ŷ 则「信息不足」。启发式加权 `score` 仅研究对照，不驱动倾向。LLM 只引用 `stance_label`，不改写 ŷ

---

## 设计原则

0. **已发生 → 影响估计**：只用决策时点可见事实估计对股票/组合的影响；禁止未来函数与不可审计改分（见 [因果链](docs/design-spine.md#因果链已发生--影响估计--动作)）  
1. **事实与结论分离**：价格、评分、财务等数字只来自 Skill/`core` JSON；LLM 只组织解释与编排，不得编造数字  
2. **工具即边界**：每个能力对应一个 `skills/<name>/` + `tool_config.json`  
3. **可组合**：复杂问题靠多轮/多工具串联（如 `quote + kline + signal`）  
4. **可降级**：日线失败时 `quote_fallback`，且须在回复中标明「日线不完整」  
5. **深度分析模式（可选）**：仅明确要求「深度分析」等时展开多层长文；默认短答 + 引用规则结论  
6. **风险披露**：代码兜底强制追加免责声明；模拟非实盘

---

## 快速开始

逐步操作（clone、虚拟环境、先放 3 只股票、在研究枢纽拉日线和分钟线、拟合并启用研究套、再跑调仓回测）见 **[docs/getting-started.md](docs/getting-started.md)**。下面是已经准备好环境时的命令摘要。需要 Python 3.10+。

```bash
cd investment
python3 -m pip install -r requirements.txt
cp .env.example .env          # 对话 / Agent 才需要；填 DASHSCOPE_API_KEY、DASHSCOPE_MODEL
bash scripts/setup_quant.sh   # watching.json 不存在时，从 watching.example.json 复制（约 500 只）
python3 run_web.py            # → http://127.0.0.1:8000
```

第一次回测建议先按 [getting-started.md](docs/getting-started.md) 写成 3 只，在研究枢纽拉数并拟合后再跑。直接用模板整池，拉数和拟合都会很久。行情、观察、回测、模拟账户不依赖 API key。Web 侧栏：**研究枢纽 · 历史回测**。

不想连外网、也不配 key 时，先跑离线校验：

```bash
python3 evals/run_checklist.py --mock
```

要验证日线回测（需外网）：

```bash
python3 research/t0_backtest_run.py --code 茅台 --json
```

完整选股 / 日线依赖 `akshare`。无 LLM 时可单独调 Skill：

```bash
python3 -c "from skills.quote.handler import QuoteHandler; print(QuoteHandler().execute({'parameters':{'stock_code':'茅台'}}))"
```

---

## 数据准备

量化研究与回测依赖行情缓存和两份本地 JSON。`data/watching.json`、`data/paper.json` 在 `.gitignore` 里，clone 下来没有，由 `setup_quant.sh` 生成。文件已存在时脚本会跳过，不会覆盖。

| 数据 | 来源 | 初始化 | 说明 |
|------|------|--------|------|
| 观察名单 | `data/watching.example.json` | `setup_quant.sh` 调用 `watching_run.py --init` | 复制为 `data/watching.json` |
| 纸面账户 | 脚本内置 | 同上，`paper_run.py --init` | `data/paper.json` |
| 日线行情 | `akshare` / `baostock` | 评分 / 回测时写入缓存 | `core/store.py`；`python3 research/daily_run.py --preset quant` 可刷新 |
| 5 分钟 K 线 | `baostock` | 做 T 回测时按需拉取 | 缺失则当日跳过 |

只初始化观察名单：

```bash
python3 research/watching_run.py --init    # 已有 watching.json 会报错，避免覆盖
python3 research/watching_run.py --show
```

### 观察名单（`watching.example.json`）

仓库里提交的是模板 `data/watching.example.json`（约 500 只，`sources` 为空）。运行时只读写 `data/watching.json`。改自己的池子时改后者，或在 Web 观察页 / 量化面板里改；不要改 example。

当前模板 `sources` 为空，表示**手动名单**：`python3 research/watching_run.py --refresh` 只补全名称，不改 `watchlist`。

要让刷新按规则重算名单，在 `watching.json` 里写 `sources`（保存后需再 `--refresh` 或在页面点「刷新」）。上限是 `max_size`（最大 500）：

| `type` | 含义 | 示例 |
|--------|------|------|
| `static` | 固定代码或名称 | `{"type": "static", "codes": ["600519", "茅台"]}` |
| `screen` | 按条件筛选后并入（需外网行情） | `{"type": "screen", "sector": "银行", "limit": 5}` |

- 无外网时可走 `evals/run_checklist.py --mock` 做离线能力校验。
- 数据源一览与字段说明见 [docs/development.md · 数据源一览](docs/development.md#数据源一览)。

---

## 常用命令

```bash
python3 evals/run_checklist.py --mock             # 离线黄金用例（CI 同款）
bash scripts/ci_quant.sh                          # 本地 CI 全量（回测 + 校验）
python3 run_web.py                                # 启动 Web 面板
```

- 单测：`python3 -m unittest discover -s tests -v`
- 更多研究脚本（T0 回测、因子实验、纸面调仓、信号导出等）见 [docs/development.md](docs/development.md)。

Web 主路径：**对话** · **观察** · **模拟** · **回溯**。说明见 [docs/quant-ui.md](docs/quant-ui.md)。

---

## 文档

详细说明已拆到 [`docs/`](docs/) 子目录（活跃文档如下，归档见 [docs/README.md](docs/README.md)）：

| 文档 | 说明 |
|------|------|
| [docs/architecture.md](docs/architecture.md) | **架构总览**：架构图、分层模块、目录结构、技术栈、数据/策略/风控各层 |
| [docs/design-spine.md](docs/design-spine.md) | 产品核心设计主轴：因果链、北极星、能力地图、N1–N6 路径、两条轨、决策链路 |
| [docs/quant.md](docs/quant.md) | 量化层：入门概念 + score_bars/stance/回测/纸面原理 + 运维 preset & cron；含 [ŷ 全链路](docs/quant.md#predicted_scoreŷ全链路) |
| [docs/quant-ui.md](docs/quant-ui.md) | Web：说明书 + UI 契约与验收 + W0–W5 升级方案 |
| [docs/development.md](docs/development.md) | 开发手册：环境安装 + 单测/evals/扩展约定 + 各 Skill 详解 |
| [docs/product-intro.md](docs/product-intro.md) | 产品概览与核心能力 |
| [docs/rebalance-logic.md](docs/rebalance-logic.md) | 调仓逻辑说明 |
| [docs/t0-logic.md](docs/t0-logic.md) | 做T（T0）逻辑说明 |

完整索引与历史归档：[docs/README.md](docs/README.md)

各代码子目录均有 [`README.md`](agent/README.md) 说明职责与入口；覆盖见 `GET /api/readme-index`，在线浏览见 `GET /api/readme?dir=` 或 Web 量化面板运维区。

---

## 合规

本仓库是 **策略验证系统**（量化研究 + 模拟账户，融合 AI 编排），**非持牌投顾问诊、现行不涉及真实账户交易、不代客下单**；策略结论与模拟盈亏**不保证收益**。待策略验证成熟后再评估实盘（N6）。细则见 [docs/development.md#合规与风险说明](docs/development.md#合规与风险说明)。
