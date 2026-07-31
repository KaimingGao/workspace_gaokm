# 能力评估与升级规划

[← 文档索引](README.md)

## 能力评估

### 当前能力画像（强 MVP）

| 层级 | 现状 | 成熟度 |
|------|------|--------|
| 架构 | LLM Function Calling + 12 Skills，可多轮串联 | 高 |
| 行情 `quote` | A/H/美股腾讯接口，映射表 + 60s 缓存 | 中高 |
| 对比 `compare` | 2～5 只并排涨跌量 | 中 |
| 选股 `screen` | A 股现货 + PE/PB/涨跌/行业关键词 | 中（依赖 AkShare） |
| 短线 `signal` | 动量/量价/波动；跨市场日线偶发 `quote_fallback` | 中 |
| K 线 `kline` | 近 N 日 OHLCV + 形态摘要 | 中 |
| 基本面 `fundamentals` | A 股 PE/PB/ROE/增速等 | 中 |
| 同行 `peer` | 预设同业组横向对比 | 中低 |
| 相对强弱 `index` | 近 N 日相对沪深300/恒生超额 | 中 |
| 资讯 `news` | 标题摘要（非全文研报） | 中低；数值化舆情见 [sentiment-layer.md](sentiment-layer.md) |
| 持仓 `position` | 文件/临时 holdings + 可配置规则 | 中 |
| 解读/咨询 | prompt + 多工具串联 | 中 |
| 产品形态 | CLI + Web | MVP |

**已做得对的地方**：数据与话术分离；Skill 合约清晰；合规措辞；离线单测；P1～P3 核心能力已齐；**P4/P5 量化链路已落地**（因子 → 回测 → 纸面 → stance）。

**主要缺口（相对产品北极星 / 策略验证阶段）**：能力地图上财务 PIT / 统一数仓仍弱；组合缺完整 QP；撮合非交易所级。**实盘/OMS 未接是有意推迟**（N6：策略验证成熟后再立项，不计入当前北极星分子）。路径内 P2++ / R0–R5 已补日线 PIT、行业预算、撮合近似、归因、出站告警、北极星仪表。能力地图粗估约 **~70%～78%**；详见 [design-spine · 产品北极星](design-spine.md#产品北极星) · [能力地图](design-spine.md#能力地图六大模块) · [达成度评估](design-spine.md#达成度评估2026-07)。

---

## 量化升级与开发规划

### 当前定位 vs 量化系统

| 维度 | 当前 Investment | 典型量化系统（能力地图对照） |
|------|-----------------|--------------------------------|
| **阶段** | **策略验证**（回测 + 纸面） | 研究 → 仿真 → **实盘** |
| 输出 | 自然语言策略解读（旁路）+ 确定性 score/stance | 确定性信号 → 回测 → 执行 → 监控 |
| 决策 | 规则事实为主；LLM 合成解释 | 策略代码为主，LLM 可选做解读 |
| 数据 | DataService + 观察池增量/快照缓存（见 [data-layer.md](data-layer.md)） | 统一 DataService、完整 PIT、本地历史库 / 多源对齐 |
| 验证 | 回测 · OOS/WF · IC · 简化归因 · evals | 回测、样本外、IC/IR、完整归因、冲击模型 |
| 交易 | **现行不代客下单**（纸面验证） | OMS / 券商 API |

**结论**：**产品定位 = 策略验证**；北极星 = 纸面风险调整收益 × 迭代速度 × 回测–纸面拟合度（见 [design-spine · 产品北极星](design-spine.md#产品北极星)）。真·实盘 Realization 单列 **N6**：待策略在回测与纸面验证成熟后再立项。**不宜**在验证未成熟时接实盘 OMS。

### 三条升级路径

| 路径 | 内容 | 与现有代码关系 | 优先级 |
|------|------|----------------|--------|
| **A 量化研究台** | 历史库、回测、因子报告；LLM 只解释 JSON | 复用 `signal/scorer`、`history` | **P4 已落地** |
| **B 半自动量化** | 定时信号、纸面账户、净值曲线 Web | 在 A 之上加调度与持久化 | **P5 已落地** |
| **C 生产量化** | 实盘/仿真、风控、OMS |  largely 新建，仅复用因子；风控演进见 [risk-layer.md](risk-layer.md) | 远期 |

**继续不做**：自动下单、保证收益、黑盒荐股。

### 分阶段路线图

| 阶段 | 交付物 | 状态 |
|------|--------|------|
| **P4.1** | `core/backtest/engine.py` + `backtest` Skill（signal_v1 walk-forward） | **已落地** |
| **P4.2** | 日线本地缓存 `data/store/`、质量标记 | **已落地** |
| **P4.3** | 回测报告扩展：基准对比、分层收益、参数扫描 CLI | **已落地** |
| **P4.4** | evals 增加「信号可复现性」（同输入同输出，不依赖 LLM） | **已落地** |
| **P5.1** | 定时任务生成观察池 + 纸面持仓 JSON | **已落地** |
| **P5.2** | Web 展示净值/回撤曲线 | **已落地** |
| **P5.3** | 「是否买入」改为规则输出 stance + LLM 只解释 | **已落地** |

### P4.1 已落地：`backtest` Skill

**目录**：`core/backtest/engine.py`（引擎） + `skills/backtest/`（Agent 工具）

**策略 `signal_v1`**：Walk-forward 调用 `score_bars`（详见 [量化原理 · 第三层](quant.md#第三层历史回测walk-forward)）；分数 ≥ `min_score` 且非 `hard_reject` 时，模拟持有 `horizon_days` 日；输出胜率、均收益、累计收益、最大回撤、夏普近似。

**局限（须在回复中说明）**：未含手续费/滑点/涨跌停/T+1；与 live `signal` 的 `quote_fallback` 数据源可能不一致；**研究结果 ≠ 实盘建议**。

**无 LLM 直调**：

```bash
cd investment
python3 -c "
from skills.backtest.handler import BacktestHandler
import json
print(BacktestHandler().execute({
  'parameters': {
    'stock_codes': ['茅台', '招商银行'],
    'lookback_days': 120,
    'horizon_days': 3,
    'min_score': 55
  }
}))
"
```

**Agent 典型问法**：「回测茅台 signal 规则过去 120 天表现」「这个短线评分历史胜率如何」→ 路由 `backtest`。

### P4.2 已落地：日线本地缓存

**目录**：`data/store/daily/{CN|HK|US}/{code}.json`（缓存文件不入 git）

**模块**：`core/store.py`；`fetch_daily_bars()` 自动读/写缓存。

| 字段 | 含义 |
|------|------|
| `data_source` | 原始来源；命中缓存时为 `cache:akshare_cn_daily` 等 |
| `quality.level` | `good` / `thin` / `empty`（样本量、末根日期、数据源） |
| `fetched_at` | 写入时间；默认 **24h** 内有效 |

**环境变量**：

- `INVESTMENT_STORE_DIR` — 自定义缓存根目录  
- `INVESTMENT_DISABLE_CACHE=1` — 关闭缓存，始终走 AkShare  

**管理 CLI**：

```bash
cd investment
python3 research/cache_cli.py --list
python3 research/cache_cli.py --list --market CN
python3 research/cache_cli.py --clear
```

首次拉取某标的日线后会落盘；同一标的 24h 内重复调用 `signal` / `kline` / `backtest` / `index` 将优先读缓存，减轻 AkShare 压力并提高 evals 可复现性。

### P4.3 已落地：回测报告扩展

**增强字段**（`backtest` Skill / `core/backtest/engine.py`）：

| 块 | 字段 | 含义 |
|----|------|------|
| `benchmark` | `buy_hold_period_pct` | 同期买入持有收益 |
| | `excess_vs_buy_hold_pct` | 策略累计 − 买入持有 |
| | `index_period_pct` / `excess_avg_vs_index_pct` | 指数基准（`benchmark=auto/hs300/hsi`） |
| `score_buckets` | `55-64` / `65-74` / `75+` | 按入场 score 分层均收益/胜率 |

**参数扫描 CLI**：

```bash
cd investment
python3 research/backtest_scan.py --code 茅台 --lookback 120
python3 research/backtest_scan.py --code 茅台 --min-scores 50,55,60,65 --horizons 2,3,5 --json
```

按 `total_return_pct` 降序输出 top N 组 `(horizon_days, min_score)`，用于快速筛参数（仍须样本外验证，勿过拟合）。

**Skill 新参数**：`benchmark` — `auto`（默认，A 股→沪深300，港股→恒生）/ `hs300` / `hsi` / `none`。

### P4.4 已落地：信号可复现 evals

**脚本**：`evals/run_repro.py` + `evals/repro_fixtures.json`

对 `score_bars` / `backtest_signal` / mock 下的 `signal` Handler **双跑**，SHA256 指纹须一致。改 scorer 或回测逻辑后应先跑：

```bash
python3 evals/run_repro.py
```

### P5.1 已落地：纸面账户

**文件**：`data/paper.example.json` → 复制为 `data/paper.json`（不入 git）

**模块**：`core/paper.py` + CLI `research/paper_run.py`

| 步骤 | 命令 |
|------|------|
| 初始化 | `python3 research/paper_run.py --init` |
| 跑观察池 + 快照 | `python3 research/paper_run.py --run` |
| 纸面模拟买入 | `python3 research/paper_run.py --run --simulate-buy` |
| 查看净值 | `python3 research/paper_run.py --status` |

规则在 `paper.json` 的 `rules`：`min_score`、`max_positions`、`position_pct` 等。

**每日 cron（推荐）**：

```bash
cd investment
# 工作日收盘后：纸面观察池 + 离线 mock checklist
python3 research/daily_run.py --paper-run --eval-mock --json

# 可选：周末跑全量 Agent 回归（需 DOUBAO_API_KEY）
python3 research/daily_run.py --eval-agent --json
```

**非实盘、不代客下单**。

### P5.2 已落地：Web 纸面净值曲线

**API**（`web/app.py`）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/paper` | 账户摘要 + `snapshots` 快照序列 |
| POST | `/api/paper/init` | 从 `data/paper.example.json` 初始化 |
| POST | `/api/paper/run` | 跑观察池；`{"simulate_buy": true}` 可纸面买入 |
| POST | `/api/daily/run` | 每日任务：`paper_run` + `eval_mock` 等组合 |

**Web UI**：顶栏 **「模拟」** → 净值/盈亏/持仓 + 曲线；观察页建仓。

| GET | `/api/evals/cases` | 列出 golden case 摘要 |
| GET | `/api/evals/last` | 读取上次保存的校验报告 |
| GET | `/api/evals/job` | 后台任务状态（全量 Agent 回归） |
| POST | `/api/evals/run` | 跑 checklist；body: `{case_id?, use_mock, with_agent, background?}` |

顶栏 **「校验」** → 黄金用例 Skills checklist（**每日 eval** 一键 mock；全量 Agent 后台跑并轮询；上次结果 / 下载 JSON）。  
顶栏 **「模拟」** → 假钱账本；对话 position 读 `paper.json`。

环境变量 `INVESTMENT_PAPER_PATH` 可自定义 `paper.json` 路径。

### P5.3 已落地：规则 stance + LLM 只解释

**模块**：`core/stance.py`（`compute_buy_stance`）  
**Skill**：`advise` — 委托 `core.advise.evaluate_buy_advice` → `core.facts` + stance。决策流程见 [量化原理 · 第二层](quant.md#第二层买卖-stancecompute_buy_stance--advise)。

| `stance_code` | `stance_label`（LLM 须逐字引用） |
|---------------|----------------------------------|
| `insufficient` | 信息不足暂不建议操作 |
| `avoid` / `wait` | 建议观望（暂不买入） |
| `probe` | 建议逢低分批关注但暂不追入 |
| `buy_light` | 可考虑轻仓试探（非追涨） |

**约束**（`prompts.py` / `agent.py`）：问「能不能买 / 是否买入」时 Agent 必须调 `advise`；「策略结论：是否买入」节只能引用 `advise.stance_label`，LLM 负责解读 `facts` 与 `invalidation`，不得升级/降级结论。

**无 LLM 直调**：

```bash
cd investment
python3 -c "
from skills.advise.handler import AdviseHandler
print(AdviseHandler().execute({'parameters': {'stock_code': '茅台'}}))
"
```

### 架构演进示意

```text
AI 编排层（旁路）         量化主轴（现行）              模拟账本
─────────────────        ─────────────────────        ─────────────
Agent 选工具/解释结果  ←→  signal → strategy → backtest   paper.json
Skills 取数                 store / ports / evals repro     观察建仓 · 调仓
                          quant 研究台 · watching/follow
```

---

### 升级路线（状态）

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P1** | 日线（A/港）、`kline`、`.env`/gitignore、错误可读、名称映射 | 已落地 |
| **P2** | `fundamentals` / `peer` / `index` | 已落地 |
| **P3** | 持仓规则配置、临时 holdings、美股日线、`news` | 已落地 |
| **P4** | Web、`backtest`、日线缓存、repro evals | **已落地**（P4.1～P4.4） |
| **P5** | 纸面/模拟交易、定时信号、绩效看板 | **已落地**（P5.1～P5.3） |
| **D1–D6** | 观测/记忆/决策契约/反馈/调度/非交易预填 | **骨架已落地**（见下节） |
| **Q1–Q5** | 量化重心重构（见下节「下一阶段规划」） | **已落地**（本轮） |

---

## 下一阶段规划（量化交易 + AI 定位）

> 前提：系统定位已改为 **量化交易系统（融合 AI）**，不是持牌投顾。  
> P4/P5/D 骨架已具备「骨头」；本阶段把**重心从对话荐股拨到策略生命周期与可复现交易研究台**。  
> AI 保持旁路：编排与解释，不改写分数、不代客实盘下单。

### 目标架构（一句话）

```text
Data(PIT/缓存) → Factors/Signal → Strategy(版本) → Backtest/OOS → Paper(真实成本) → Risk/Ops
                                      ↑
                               AI Agent（可选入口）
```

### 原则与不做

| 做 | 不做（本阶段） |
|----|----------------|
| 策略可版本、可晋级、可复现 | 券商实盘 OMS / 自动下单 |
| 风控进调仓前门禁 | 在线 RL 自动调权 |
| 成交假设默认可审计 | 黑盒荐股、保证收益 |
| 文档/产品语言去「投顾脊柱」 | 为聊天重写整套量化内核 |

### 阶段总览

| 阶段 | 主题 | 周期（建议） | 依赖 | 验收标准 | 状态 |
|------|------|--------------|------|----------|------|
| **Q1** | 重心与契约 | 1～2 周 | — | 文档/叙事一致；architecture 主轴=量化管线 | **已落地** |
| **Q2** | Strategy 生命周期 | 2～3 周 | Q1 | `StrategySpec` 收编 signal_v1；research→paper 有显式 promote | **已落地** |
| **Q3** | 数据端口 + 运行清单 | 2～3 周 | Q1 | `core` 经 ports；每次回测/调仓写出 manifest | **已落地** |
| **Q4** | 风控 + 成交默认 | 2～3 周 | Q2 | DD/限额进调仓前；默认 `simple_cn` | **已落地** |
| **Q5**（可选） | 产品重力 | 1～2 周 | Q2 | 调仓/回溯优先；深度买入长文可选 | **已落地** |

**合计约 2～3 个月可打完 Q1–Q4**（单人兼职按上限估）；Q5 可与 Q4 并行。

### Q1 · 重心与契约（低风险，先做）

1. 统一对外叙事：README / architecture 产品金字塔 / skills 总述 / structure「投顾编排」→「Agent 编排」。  
2. 架构图改画：量化管线为主轴，Agent 画在旁路。  
3. 约定命名：产品「模拟」= 内部 `paper`；`agent/` 文档称 Agent（物理改名可放 Q5）。  
4. 明确账本：`paper` = 唯一交易真相源（对照仓已下线）。

**产出**：文档 PR；不改运行时行为（或仅文案）。

### Q2 · Strategy 对象与生命周期（核心）

1. 定义 `StrategySpec`（宇宙、参数、风控钩子、成交模型、版本号）。  
2. 将现有 `signal_v1` + `paper.rules` 相关阈值收进第一版 Strategy。  
3. **晋级闸门**：research 配置 diff → 人工确认 → promote 到 paper 使用的策略版本（禁止静默覆盖 `signal_config`，延续现有纪律）。  
4. 回测 CLI / `paper/run` / 调仓预演均声明 `strategy_id@version`。

**产出**：`core/backtest/strategies.py`（或 `core/strategy/`）扩展；单测；`strategy-layer.md` 更新。

### Q3 · 端口封死 + 可复现契约

1. 行情/日线/基本面经 `core/ports`（或 DataService）；清掉 `core` 内剩余 `skills.*` 直连。  
2. 每次 `backtest` / `paper run` / 调仓 dry-run 写 **Run Manifest**（git/config fingerprint、数据源、成本模型、策略版本）。  
3. 强化 `evals/run_repro`：manifest 字段进 checklist。

**产出**：ports 覆盖率；manifest JSON schema；CI 一步验 repro。

### Q4 · 风控模块 + 成交诚实默认

1. `core/risk/`（或等价）：账户最大回撤熔断、单票/组合上限、调仓前检查。  
2. 研究/策略对照默认 `cost_model=simple_cn`；`zero` 仅标「教学/调试」。  
3. 文档对齐：回测成交价假设（如 next_open）与模拟账本一致处写清，不一致处显式警告。

**产出**：调仓预演展示「风控拦截」；成本默认切换 + 回归单测。

### Q5 · 产品重力（可选加固）

1. Web：模拟调仓报告 / 回溯结果优先于单票长文。  
2. `advise` 保留为可解释 stance，prompts「深度分析」改为可选模式。  
3. ~~评估是否将 `advisor/` 目录重命名为 `agent/`~~：**已落地**（`agent/` 为正本；`advisor/` 兼容转发包已删除）。

### 与 D1–D6 / 路径 C 的关系

- **D1–D6**：继续作平台骨架，**不**抢 Q2–Q4 主轴；调度（D5）优先挂「日更信号 + 模拟调仓」，而非荐股推送。  
- **路径 C（实盘/OMS）**：待策略验证成熟（回测 + 纸面可复现、风控审计齐全、合规确认）后另立项 N6；**现行不排期**。

### 建议执行顺序（甘特逻辑）

```text
Q1 ─────┐
        ├──► Q2 ─────┬──► Q4
        └──► Q3 ─────┘      │
                            └──► Q5（可选）
```

Q2 与 Q3 可部分并行（Strategy 接口先定，ports 清债同步）；Q4 依赖策略版本能挂风控/成本参数。

### 成功画像（Q4 结束时）

- 新人读 README/architecture 不会误认成投顾问诊产品。  
- 任意一次模拟调仓能回答：用的哪版策略、什么成本模型、数据是否可复现、是否被风控拦住。  
- AI 仍可通过对话触发工具，但**决策数字只来自 Strategy + core**。

---

## 北极星实现路径（N1–N6）

> 承接 [产品北极星](design-spine.md#产品北极星)（三项乘积）；能力地图对照见 [design-spine · 能力地图](design-spine.md#能力地图六大模块)（旧锚点 [专业系统满分画像](design-spine.md#专业系统北极星满分画像) 仍可用）。  
> **产品定位**：现行 = **策略验证**（研究台 + 纸面）；暂不接实盘。  
> **子项目拆分**：能力地图 **6** 模块 ↔ 工程 **N1–N6**；默认可交付 **N1–N5**；**N6 验证成熟后另立项**（见 [design-spine · 子项目拆分](design-spine.md#北极星子项目拆分)）。  
> **底座**：Q1–Q5 已完成；R0–R5 已收口。继续加深验证可信度与样本。  
> **锁定**：生产 Alpha = 线性 `score_bars`；NN 仅离线 artifact；**现行不接券商 OMS**。

### 子项目对照（与六大模块）

| # | 产品子项目 | 工程轨 | 路径内目标（压缩） | 粗估 | 本路径 |
|---|------------|--------|--------------------|------|--------|
| 1 | 多维数据收集 | N1 | DataService / 可复现拉取 | ~62% | 主路径加深 |
| 2 | 数据清洗 / PIT | N1 | 日线 PIT、质量门禁 | ~58% | 主路径加深 |
| 3 | 因子 / 模型 | N2 | 线性 score + IC；NN 研究轨 | ~60% | 主路径加深 |
| 4 | 组合 / 风控 | N3 | 分数预算 + 波动缩放 + 限额 | ~60% | 主路径加深 |
| 5 | 历史回测 | N4 | OOS / WF / 成本 / 板别涨跌停·跌停延后 | ~80% | 主路径加深 |
| 6 | 部署与迭代 | N5 / N6 | 纸面日更+人审（验证）；OMS 成熟后另立 | ~52% / — | N5 主路径；N6 **有意推迟** |

**结论**：**6** 个能力子项目；工程 **6** 轨；仓库现行排期 **N1–N5**（策略验证）；**N6 待验证成熟后另立**。模拟账户交易为验证阶段必做主能力。  
**下一程详细排期**：[upgrade-refactor-plan.md](upgrade-refactor-plan.md)（R0–R5；与本文 P0–P2++ 已交付区分）。

### 阶段总览

| 阶段 | 主题 | 周期（建议） | 依赖 | 验收标准 | 状态 |
|------|------|--------------|------|----------|------|
| **N1** | 数据质量 / DataService | 2～3 周 | Q3 | 回测/调仓能回答数据源、复权、质量、是否 fallback | **骨架落地** |
| **N2** | 因子实验室 | 3～4 周 | N1 | IC/权重建议可导出；`research/ml` 不直连 score | **骨架落地** |
| **N3** | 组合 + 行业风控 | 2～3 周 | Q4 | StrategySpec 限额；分数预算权重 + 高波缩放 | **骨架 + 动态轻量** |
| **N4** | OOS / regime 回测 | 2～3 周 | N3 | 报告含成本、OOS、regime 切片 | **骨架落地** |
| **N5** | 纸面运营闭环 | 2～3 周 | N4 | `paper_daily` + 衰减告警 + 人审晋升 | **P2 可验收** |
| **N6** | 真·实盘闸门 | 验证成熟后 | N4+N5 + 合规 | OMS 另立项；默认人工确认下单 | **有意推迟（现行不排期）** |

### 达成度评估（2026-07）

与 [design-spine · 达成度评估](design-spine.md#达成度评估2026-07) 同源，此处给路线图读者的摘要：

| 判断 | 状态 |
|------|------|
| 产品北极星（三项乘积） | **已仪表化**（见 [产品北极星](design-spine.md#产品北极星) · R0） |
| 能力地图（六大模块对照） | **部分～较强**（见 [能力地图](design-spine.md#能力地图六大模块)） |
| 本仓库路径内采纳目标 | **部分～较强**（P2++ 后粗估 ~68%～74%） |
| N1–N5 | **P0–P2 可验收**（五问 / 回测默认 / 限额硬拦 / 质量 / 日更 / 告警晋升） |
| 升级重构 R0–R5 | **R0–R4 出门；R5 主干落地**（[upgrade-refactor-plan](upgrade-refactor-plan.md)） |
| 现行「研究台 + 模拟账本 + AI 旁路」 | **基本达成** |
| N6 真·实盘 | **未启动**（策略验证成熟后再立项；不计入当前北极星分子） |

### 北极星实现规划（P0–P3）

> 策略：**北极星 = 纸面夏普/卡玛 × TTM × 回测–纸面拟合**（见 [design-spine · 产品北极星](design-spine.md#产品北极星)）；六大模块为能力地图。实现上把 N1–N5 做成**策略验证**闭环并仪表化二级指标；**N6 待验证成熟后另立项**。  
> 路径原则：先验证可信度，再加深模块。扩功能面（Tick / 数仓 / NN 上线 / OMS）一律排在策略验证成熟之后。  
> 本路径现行终点：**P0→P2++** + **R0–R5** + 样本加深；真·实盘 **N6 推迟**。

#### 四阶段总览

| 阶段 | 名称 | 周期 | 目标 | 达标信号 | 状态 |
|------|------|------|------|----------|------|
| **P0** | 做实骨架 | 2～3 周 | N1–N5 从「有代码」到「默认路径可答五问」 | ~55% 六大模块粗估 | **已完成** |
| **P1** | 加深模块 | 4～6 周 | 清洗门禁 · 组合限额真约束 · 回测稳健性 | ~65%～70% | **已完成** |
| **P2** | 准实盘稳态 | 3～4 周 | 纸面日更运维化 · 衰减→人审→晋升可演示 | N5 成功画像达标 | **已完成** |
| **P3** | N6 闸门 | 另立项 | 策略验证成熟 + 合规后再评估 OMS | 有意推迟 | 规划中 |

#### P0 · 做实骨架（已完成）

对应「下一刀」四条；每条须有 Web 可见或 API 可查的验收物。

| 轨道 | 工程动作 | 验收 | 状态 |
|------|----------|------|------|
| N5 日更五问 | `paper_daily` / `run_daily_cycle` 固定输出 `data_quality` · `strategy_id` · `cost_model` · `risk_blocks` · `monitor_alerts`；模拟页「调仓五问」 | 跑通一次日更并能答五问 | **已落地** |
| N4 回测默认 | 组合回测 API/Web 默认附带 `cost_model` + `oos_summary` + `regime_summary` | 任意一次组合回溯报告头三项齐全 | **已落地** |
| N3 限额真验收 | 调仓路径强制 `check_account_risk`（单票+行业）；超限拦加仓并写 `risk_block` 日志；集成测 | 故意超行业上限被拦，且日志可查 | **已落地** |
| N1 质量可见 | 调仓/回测/日更与 DecisionRecord 可查 `data_quality`；`fallback_count` 进 UI | 一次调仓能指出源、复权、是否 fallback | **已落地** |

落点摘要另见 [design-spine · P0 落地状态](design-spine.md#p0-落地状态2026-07)。

#### P1 · 加深模块（已完成）

| 模块 | 内容 | 状态 |
|------|------|------|
| **清洗** | 复权策略写入 manifest；bars 质量 level 门禁（差数据不进生产 score）；PIT 最小约定文档化 | **门禁+manifest+PIT 文档已落地** |
| **组合** | `optimize_weights` 默认进策略调仓建议；行业 map 覆盖观察池；限额进 StrategySpec UI | **目标权重+限额只读 UI 已落地** |
| **回测** | Walk-forward 最小切片；成本敏感对照（zero vs simple_cn）；OOS 失败时报告标红 | **已落地**（OOS 标红 · 成本对照 · `wf_slices`） |
| **因子** | IC 实验室一键导出；`weight_suggest` 只读 diff 进策略页；情绪因子默认仍 0 | **已落地**（策略页分析/导出；情绪默认 0） |

#### P2 · 准实盘稳态（已完成）

| 项 | 工程动作 | 验收 | 状态 |
|----|----------|------|------|
| 日更可定时 | `scripts/daily_paper.sh` · launchd · `POST /api/schedule/run` `paper_daily` · `GET /api/schedule/last` | cron/平台一键可跑 | **已落地** |
| 告警进 UI | 横截面 `assess_strategy_health` → ops_report；平台/模拟展示 | 日更/调仓可见 monitor_alerts | **已落地** |
| 演示闭环 | 告警 → `feedback/suggest(monitor_alerts)` → 策略页 promote → 再回测/纸面 | 人审路径可复现、不静默写盘 | **已落地** |

- **成功画像**：一次纸面日更稳定回答数据质量、策略版本、成本、风控拦截、监控告警；生产分仍可引用。

#### P2+ · 稳态加深（已完成）

| 项 | 工程动作 | 验收 | 状态 |
|----|----------|------|------|
| 滚动 IC | 日更/调仓估合成分 IC，写入 `monitor_metrics`；IC&lt;0.02 → `ic_decay` | 调仓五问可见滚动 IC | **已落地** |
| 行业覆盖 | `sector_map` 显式覆盖率告警；UI 展示 mapped/total | 覆盖不足时 info 告警 | **已落地** |

#### P2++ · 专业差距补强（已完成）

| 项 | 工程动作 | 验收 | 状态 |
|----|----------|------|------|
| 日线 PIT | `core/data_pit` · 回测窗口 as_of · `get_bars(as_of=)` · `pit_report` | 打分窗无未来 bar；报告含 PIT 标记 | **已落地** |
| 行业预算 | 扩 `sector_map.json`；`optimize_weights` 返回 coverage / budget_alerts | 热门观察池显式覆盖↑；近上限告警 | **已落地** |
| 撮合近似 | `matching.py`：next_open / 涨跌停过滤 / slippage_tier | 单票+组合回测可开关 | **已落地** |
| 收益归因 | `attribution.py`：个股/行业/选股超额 | 组合回测返回 `attribution`；回溯页可见 | **已落地** |
| 告警出站 | `alert_outbound`：本地 `data/alerts/` + 可选 webhook | `paper_daily` 写出 `alert_outbound` | **已落地** |

#### P2+++ · 模拟账户风控加深（已完成）

| 项 | 工程动作 | 验收 | 状态 |
|----|----------|------|------|
| 分数风险预算 | `optimize_weights` 默认 `score_budget`；可选 `greedy_cap` | 高分标的目标权重大于低分；仍受单票/行业上限 | **已落地** |
| 市场波动缩放 | `core/risk/budget.market_vol_scale`；高波有效上限 ×0.8 | 五问「仓位预算」可见；`budget_alerts` 含 `market_vol_dampen` | **已落地** |
| 撮合加深 | 板别涨跌停阈值；`resolve_exit_index` 跌停延后卖；单票+TopK | `skipped_limit_exit` / `exit_deferred` 可查 | **已落地** |

#### P3 · N6 闸门（策略验证成熟后另立项）

须 N4 OOS 可复现、N5 纸面运行达标、风控审计齐全、合规确认后，再评估 OMS；**现行不排期**（产品定位：先验证策略）。

#### 依赖顺序

```text
P0-N1 质量可见 ──┬──► P0-N4 回测默认 ──► P1 回测加深 ──┐
P0-N3 限额拦截 ──┤                                      ├──► P2 日更稳态 ──► (闸门) N6
P0-N5 日更五问 ──┘                                      │
                         P1 清洗门禁 / 因子导出 ─────────┘
```

#### 锁定取舍（全程）

- 生产 Alpha = `score_bars` 线性加权；NN 不得直连 `score`
- LLM 不得改写 `score` / `stance_label`
- **现行不接**券商 OMS（N6：策略验证成熟后另立项）
- 不扩 Tick/宏观/数仓大面，先把现有源质量做实
- 不自动改权：监控只告警，晋升须人审

**开工顺序**：P0→P1→P2 已验收；维持稳态，不要并行冲 NN / OMS / 数仓。

```text
N1 ──► N2 ──► N3 ──► N4 ──► N5 ──►（闸门）N6
         │                      ▲
         └── research/ml ───────┘ promote only
```

### N1 · DataService + 质量契约

1. `core/data_service.py`：`get_quote` / `get_bars` / `get_fundamentals` 收口 ports。  
2. Run Manifest 增加 `data_quality`（level、data_source、adjust、fallback 标记）。  
3. 调度 kinds：`bars_warmup` · `spot_refresh`。  
4. AkShare 继续 `ak_lock` 串行。

### N2 · 因子实验室 + ML 隔离

1. 巩固 IC / `weight_suggest`（只读 diff）。  
2. 情绪因子可选（`signal_config` 权重默认 0，人审后启用）。  
3. `research/ml/`：artifact 约定；禁止写入生产 `score`。

### N3 · 组合限额 + 贪心权重

1. StrategySpec.risk：`max_position_pct` · `max_sector_pct` · `max_positions` · `target_drawdown_pct`。  
2. `core/portfolio_optimize.py`：`optimize_weights`（按 score 贪心填仓）。  
3. `check_account_risk` 扩展行业集中度（`data/sector_map.json`）。

### N4 · OOS / regime 报告

1. 组合回测报告头强制 `cost_model`。  
2. `oos_summary` / `regime_summary` 字段进 portfolio backtest 结果。  
3. evals repro checklist 含 manifest 数据质量键。

### N5 · 准实盘（纸面）

1. 调度 `paper_daily` → `run_daily_cycle` + DecisionRecord。  
2. `core/strategy_monitor.py`：滚动衰减/回撤告警（只告警，不自动改权）。  
3. 演示路径：监控告警 → feedback suggest → 人审 promote → 再回测/纸面。

### N6 · 真·实盘（策略验证成熟后另立项）

须 N4 OOS 可复现、N5 纸面运行达标、风控审计齐全、合规确认后另立项。**现行产品定位为策略验证，暂不启动。**

### 成功画像（N5 结束）

- 六大模块各自落点与成熟度可指着仓库说清（见 [达成度评估](design-spine.md#达成度评估2026-07)）。  
- 一次纸面日更可回答：数据质量、策略版本、成本、风控拦截、监控告警。  
- 生产分仍可引用；NN 若存在只在研究轨。  

**当前相对成功画像**：P0–P2 / R0–R5 主干已接线；日常复跑与 OOS 纪律仍须持续。N6 OMS **有意推迟**（验证成熟后再立）。

---

### 架构升级 D1–D6（骨架）

对应 [architecture.md · 控制论闭环](architecture.md#控制论视角感知决策执行反馈闭环) 的工程补强；**不**开通实盘 OMS / 在线 RL（RL 概念映射见 [rl-layer.md](rl-layer.md)）。

| 编号 | 方向 | 落点 | API |
|------|------|------|-----|
| **D1** | 统一 Observation + JobRegistry | `core/observation.py` · `core/job_progress.py`（多槽） | `GET /api/jobs` · `GET /api/jobs/{name}` |
| **D2** | 长期偏好 Memory | `core/memory_store.py` → `data/memory.json` | `GET/PUT /api/memory` |
| **D3** | DecisionRecord | `core/decision_record.py`；`advise` 默认可落盘 | `GET /api/decisions` · `POST /api/decisions/record` |
| **D4** | 配置反馈建议（不写盘） | `core/feedback_suggest.py` | `POST /api/feedback/suggest` |
| **D5** | 调度骨架（异动/盘后复盘） | `core/schedule_jobs.py` | `POST /api/schedule/run` · `GET /api/schedule/job` |
| **D6** | 非交易订单预填 | `core/order_prefill.py` | `GET /api/orders/prefill` |

门面：`services/platform_service.py` · 路由：`web/routers/platform.py`。  
Web：平台面板（`partials/platform_panel.html` · `js/platform.js`，`/?tab=platform`）；纸面进度轮询统一为 `GET /api/jobs/paper`。  
单测：`tests/test_d1_d6_platform.py`。

**环境变量**：`INVESTMENT_MEMORY_PATH` · `INVESTMENT_DECISIONS_PATH` · `INVESTMENT_RECORD_DECISIONS=0`（关闭 advise 自动落盘）。

**继续不做**：自动下单、保证收益、黑盒荐股、密码/资金划拨进本系统。

### 持仓规则配置

编辑 [`data/position_rules.json`](data/position_rules.json) 可调阈值，例如：

- `concentration_high`：降集中度触发权重%  
- `take_profit_pnl` / `take_profit_day_change`：减仓锁定  
- `stop_loss_pnl` / `stop_loss_day_change`：止损参考  

对话也可直接传临时持仓（不必改文件）：「我有 100 股茅台成本 1400，现金 5 万，怎么看」。

无 LLM 时测工具：

```bash
cd investment
python3 -c "from skills.news.handler import NewsHandler; print(NewsHandler().execute({'parameters':{'stock_code':'茅台','limit':5}}))"
python3 -c "from skills.position.handler import PositionHandler; print(PositionHandler().execute({'parameters':{'cash':50000,'holdings':[{'stock_code':'茅台','shares':100,'cost':1400}]}}))"
```
