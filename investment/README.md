# Investment · 量化交易（融合 AI）

**定位（现行边界）**：本地 **策略验证系统**——信号 · 策略 · 回测 · **模拟账户（纸面）** · 研究台；并融合 **AI** 做自然语言编排与解释。  
**阶段**：现阶段只做「假设 → 回测 → 纸面」验证；**暂不接实盘**。待策略在回测与纸面验证成熟后，再另立项支持实盘交易（N6）。

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

**产品核心设计主轴**：**本质** 已发生事实 → 影响估计 → 验证 → 动作（[因果链](docs/design-spine.md#因果链已发生--影响估计--动作)）；**现行** 数据 → 信号 → 因子 → 倾向 → 动作（量化主轴 + AI 旁路）；**北极星** = 纸面风险调整收益 × 迭代速度 × 回测–纸面拟合度（[产品北极星](docs/design-spine.md#产品北极星)）——六大模块是 [能力地图](docs/design-spine.md#能力地图六大模块)；**现行只收口研究台 + 模拟账户做策略验证**，真·实盘 OMS 待验证成熟后另立项（N6）——见 **[docs/design-spine.md](docs/design-spine.md)**。实现节奏见 **[北极星实现规划 P0–P3](docs/roadmap.md#北极星实现规划p0p3)**。

架构一句话：**量化领域层（`core` 信号/回测/模拟）+ Skills 取数与规则 + AI Agent（意图理解 · 工具编排 · 研究话术）**。  
Web 主路径见 [docs/quant-ui.md](docs/quant-ui.md)；改 UI 契约见 [docs/quant-ui-standard.md](docs/quant-ui-standard.md)；**现行下一程**见 [docs/data-layer-strengthen.md](docs/data-layer-strengthen.md)（D0–D4 数据层）与 [docs/validation-strengthen.md](docs/validation-strengthen.md)（S0–S4）；历史节奏见 [docs/roadmap.md](docs/roadmap.md)。

> **现行仅限研究与模拟账户（策略验证）。不涉及真实账户交易。** 待策略验证成熟后再评估实盘。市场有风险，不保证收益，不代客下单。

---

## 核心架构

### 产品设计主轴（必读）

| 环节 | 含义 | 代码落点 |
|------|------|----------|
| **数据** | 行情 / 日线 / 基本面 / 舆情 / 配置 | `core/ports` · `skills/*` · `data/*.json` |
| **信号** | `score` + `hard_reject` | `score_stock` → `score_bars` |
| **因子** | 子分加权合成 score | `core/signal/factors` · `signal_config.json` |
| **倾向** | `stance_label`（非下单） | `compute_buy_stance` · `advise` |
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
│  服务层    services/* · quant/services（Mixin 门面）         │
├─────────────────────────────────────────────────────────────┤
│  编排层    agent/agent.py · routing.py · registry        │
├─────────────────────────────────────────────────────────────┤
│  认知层    llm_client.py + prompts.py                       │
├─────────────────────────────────────────────────────────────┤
│  适配层    skills/*/handler.py（薄包装，委托 engine/core）   │
├─────────────────────────────────────────────────────────────┤
│  领域层    core/（facts · advise · stance · store · t0）    │
├─────────────────────────────────────────────────────────────┤
│  数据层    skills/common/ · AkShare · data/*.json           │
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
├── agent/                    # Agent 编排层（核心）
│   ├── agent.py                # InvestmentAgent（多轮对话 + 工具调度）
│   ├── llm_client.py           # 通义千问 HTTP 客户端
│   ├── prompts.py              # 系统提示词（角色、路由、合规）
│   ├── registry.py             # Skill 单一注册表（13 个工具）
│   ├── contracts.py            # 接口契约（Protocol + BaseSkillHandler）
│   ├── routing.py              # 意图检测与参数增强
│   └── artifacts.py            # 结构化产物生成
├── core/                       # 领域层（无 LLM、无 HTTP）
│   ├── advise.py               # 规则引擎买卖结论
│   ├── stance.py               # 买入/观望/减仓等倾向
│   ├── facts.py                # 行情事实聚合
│   ├── paper.py                # 纸面账户模拟
│   ├── portfolio_optimize.py   # 组合权重（非对照仓）
│   ├── store.py                # 日线缓存
│   ├── signal/                 # 因子注册与评分
│   └── backtest/               # 历史回测引擎
├── skills/                     # 能力层（13 个 Skill）
│   ├── quote/                  # 实时行情
│   ├── compare/                # 多股对比
│   ├── screen/                 # A股条件选股
│   ├── signal/                 # 1~3天短线观察池
│   ├── kline/                  # K线形态分析
│   ├── fundamentals/           # 基本面分析
│   ├── peer/                   # 同行对比
│   ├── index/                  # 相对大盘超额
│   ├── news/                   # 资讯标题摘要
│   ├── position/               # 持仓建议
│   ├── advise/                 # 规则引擎买卖结论
│   ├── backtest/               # 回测
│   ├── quant/                  # 量化研究台
│   └── common/                 # 共享数据层（行情API、日线history）
├── web/                        # Web 层（FastAPI）
│   ├── app.py                  # FastAPI 入口
│   ├── routers/                # 各域路由（chat/paper/quant/watching）
│   └── static/                 # 前端资源（HTML/CSS/JS）
├── quant/                      # 量化服务层
│   ├── services/               # QuantService Mixin（配置/因子/组合/运维）
│   └── research/               # 研究脚本
├── research/                   # 量化研究入口脚本
├── evals/                      # 黄金用例回归校验
├── services/                   # 业务服务边界
├── data/                       # 配置与存储
├── docs/                       # 完整文档体系
├── main.py                     # CLI 入口
└── run_web.py                  # Web 入口
```

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
- **设计**：规则 score_bars + compute_buy_stance，**未默认使用**拟合模型，保持可解释性

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

```bash
cd investment
python3 -m pip install -r requirements.txt
# 新建 .env，填入 DASHSCOPE_API_KEY / DASHSCOPE_MODEL（见 docs/getting-started.md）

python3 main.py        # CLI
python3 run_web.py     # Web → http://127.0.0.1:8000
```

- Python 3.9+ 推荐；完整选股/日线需 `akshare`
- 无 LLM 时可单独调 Skill 做数据层调试：
  ```bash
  python3 -c "from skills.quote.handler import QuoteHandler; print(QuoteHandler().execute({'parameters':{'stock_code':'茅台'}}))"
  ```

---

## 常用命令

```bash
python3 -m unittest discover -s tests -v          # 单测
python3 evals/run_checklist.py --mock             # 离线黄金用例（CI 同款）
python3 evals/run_checklist.py --mock --presets   # 15 golden + preset（CI 同款）
python3 evals/run_repro.py                        # 信号可复现指纹
bash scripts/setup_quant.sh                       # 初始化 watching + 纸面
python3 research/paper_run.py --init && python3 research/paper_run.py --run
python3 research/watching_run.py --init && python3 research/watching_run.py --refresh --sync-paper
python3 research/cross_section_run.py --limit 10
python3 research/factor_experiment.py --code 茅台
python3 research/portfolio_backtest_run.py --top-k 3 --json
python3 research/paper_rebalance_run.py --top-k 3 --json
python3 research/t0_backtest_run.py --code 茅台 --json   # 底仓做T模拟
python3 research/threshold_suggest_run.py --code 茅台 --json
python3 research/signal_diff_export_run.py --fresh -o data/reports/signal_config_diff_bundle.json
python3 research/quant_export_run.py --format html -o /tmp/quant_daily.html
python3 research/daily_run.py --preset quant --json
python3 research/daily_run.py --preset quant_paper --json
bash scripts/daily_quant.sh && bash scripts/daily_check.sh
bash scripts/ci_quant.sh                          # 本地 CI 全量
bash scripts/agent_regression.sh                    # 周末 Agent 回归（需 API key）
```

Web 主路径：**对话** · **观察** · **模拟** · **回溯**。说明见 [docs/quant-ui.md](docs/quant-ui.md)。

---

## 文档

详细说明已拆到 [`docs/`](docs/) 子目录：

| 文档 | 说明 |
|------|------|
| [docs/structure.md](docs/structure.md) | 目录结构与模块索引 |
| [docs/architecture.md](docs/architecture.md) | 分层架构、Agent 生命周期、registry |
| [docs/data-layer.md](docs/data-layer.md) | 数据层：采集/清洗/存储/服务/监控 · 现状与演进 |
| [docs/strategy-layer.md](docs/strategy-layer.md) | 策略层：选股择时/仓位/风控 · 设计文档模板 |
| [docs/risk-layer.md](docs/risk-layer.md) | 风控模型：风险因子 · Alpha×Risk · 现状与演进 |
| [docs/rl-layer.md](docs/rl-layer.md) | 强化学习：Policy/Reward 映射 · 奖励函数 · 非生产默认 |
| [docs/sentiment-layer.md](docs/sentiment-layer.md) | 舆情/另类：新闻→风险分 · 与 news 对照 |
| [docs/skills.md](docs/skills.md) | 各 Skill 能力、买入决策流程 |
| [docs/getting-started.md](docs/getting-started.md) | 安装、运行、示例 walkthrough |
| [docs/development.md](docs/development.md) | 单测、黄金用例 evals、扩展约定 |
| [docs/quant-concepts.md](docs/quant-concepts.md) | 入门：信号→策略→验证；测试类型与是否要纸面 |
| [docs/quant.md](docs/quant.md) | 量化层：score_bars → stance → 回测 → 纸面 |
| [docs/quant-ui.md](docs/quant-ui.md) | Web 主路径说明书：观察 · 模拟 · 回溯 |
| [docs/quant-ui-standard.md](docs/quant-ui-standard.md) | 改 UI 契约 · `ASSET_V` · 验收清单 |
| [docs/quant-upgrade.md](docs/quant-upgrade.md) | P6～P26 量化升级规划与落地状态 |
| [docs/quant-summary.md](docs/quant-summary.md) | P6～P26 一页总览与验收命令 |
| [docs/quant-ops.md](docs/quant-ops.md) | preset、cron、报告归档与分享链接 |
| [docs/roadmap.md](docs/roadmap.md) | 能力评估、Q1–Q5、**北极星实现规划 P0–P3** |
| [docs/upgrade-refactor-plan.md](docs/upgrade-refactor-plan.md) | 已收口 R0–R5 归档 |
| [docs/strategy-validation-upgrade.md](docs/strategy-validation-upgrade.md) | **现行下一程**：策略验证 V0–V5 |

完整索引：[docs/README.md](docs/README.md)

各代码子目录均有 [`README.md`](agent/README.md) 说明职责与入口；覆盖见 `GET /api/readme-index`，在线浏览见 `GET /api/readme?dir=` 或 Web 量化面板运维区。

---

## 合规

本仓库是 **策略验证系统**（量化研究 + 模拟账户，融合 AI 编排），**非持牌投顾问诊、现行不涉及真实账户交易、不代客下单**；策略结论与模拟盈亏**不保证收益**。待策略验证成熟后再评估实盘（N6）。细则见 [docs/development.md#合规与风险说明](docs/development.md#合规与风险说明)。
