# 量化交易工作台 · 产品介绍

> A 股短线量化研究 + 模拟交易平台。核心链路：**选股 → 调仓 → 做T**，覆盖因子研发、ŷ 打分、横截面排序、组合再平衡、日内做T 全流程。

---

## 一、产品概述

本系统是一个面向 A 股短线交易的量化研究与模拟交易平台，采用"单进程 Python 后端 + FastAPI Web 前端"架构，核心能力包括：

| 模块 | 定位 | 核心产出 |
|------|------|----------|
| **选股（Signal）** | 横截面因子打分 + 双预测头融合 | 候选池 Top N 股票及 ŷ 评分 |
| **调仓（Rebalance）** | 基于目标权重的组合再平衡 | 开/加/减/平仓交易单 |
| **做T（T0）** | 底仓日内高抛低吸 | 正向/反向做T 建议与盈亏 |
| **模拟盘（Paper）** | 撮合 + 持仓 + 现金 + 手续费 | 净值曲线、交易记录、做T 盈亏 |
| **Web 工作台** | 可视化研究 + 操作 + 复盘 | 因子 IC、ŷ 曲线、调仓报告、做T 分析 |

技术栈：Python 3.11+ / FastAPI / 自研数据层（AkShare + 本地缓存）/ 原生 JS 前端（无构建工具）。

---

## 二、Web 前端说明

前端采用 **服务端渲染 HTML 面板 + 原生 JS 模块** 的轻量架构，无前端构建工具。入口 `web/app.py`，面板 HTML 在 `web/static/partials/`，JS 按功能域拆分于 `web/static/js/`。

| 面板 | 主要功能 |
|------|----------|
| 数据中心（Watching） | 观察名单、打分展示、建仓入口 |
| 交易执行（Paper/Follow） | 持仓、调仓、做T、规则、日志、北极星 |
| 历史回测（Replay） | rank_lots 指标、分票贡献、成交账 |
| 策略中心（Strategy） | 策略组合、先验旁路 |
| 研究枢纽（Quant） | 因子、ŷ、聚类、回测、运维、日报 |
| 仪表盘（Dashboard） | 净值曲线、KPI、风险指标 |
| 平台（Platform） | 调度、审计 |
| AI 抽屉（⌘K） | LLM 模型 + 自然语言命令 |

实时通信：WebSocket `/ws/live`（行情 / ŷ / 做T / 持仓推送）+ `job_poll.js` 轮询长任务。

> 各面板详细功能与 UI 契约见 [quant-ui.md](quant-ui.md)。

---

## 三、选股策略说明

选股采用 **多因子横截面打分 + 双预测头融合**，输出候选股票排序。核心流程：观察池 → 逐股 `score_stock()`（取日线 → 因子计算 → `score_bars()` → `predicted_score`）→ 横截面排序（质量门禁 → Top N）。

- **因子库**：`core/signal/factors/`（动量 / 量价 / 相对强弱 / 波动 / 反转 / 基本面 / 情绪 / 风险 / 流动性等）；元数据由 `factors/meta/` 管理。
- **生产打分**：组 OLS/Ridge 的 ŷ%（非 0–100 规则加权）；`heuristic_score` 仅研究对照。
- **双预测头**：`core/signal/dual_score/` 维护 ŷ_oo（主排序 / 入池地板）、ŷ_τc（调仓 ranking + 做 T 估 C_τ）、ŷ_co（叠进 ŷ_τc）；调仓 ranking 与盘中 ŷ_trade 权重不同。
- **质量门禁**：`production_ok` 过滤数据不完整 / 因子缺失；未通过降级为 heuristic 分。

> 因子清单、双预测头公式、买入闸 / EOD gate 等细节见 [quant.md · ŷ 全链路](quant.md#predicted_scoreŷ全链路) 与 [quant.md · 双层 ŷ](quant.md#25-双层-predicted_scoreŷ_oo--ŷ_τ)。

---

## 四、调仓策略说明

> 详细数学推导与参数见 [rebalance-logic.md](./rebalance-logic.md)。

调仓按 **ranking**（ŷ_oo 几何剩余 + ŷ_τc∘ŷ_co，基准 τ→open[T+1]）决定买卖：ranking 过入场门槛则开/加仓，未过则清仓。金额按 `lot_base_amount` / `lot_strong_amount`（缺省 1 万 / 2 万）换算整手，现金不够缩到整百。

- **实现**：`core/paper/rebalance/rank_lots.py`，观察池与历史回测共用。
- **风控**：T+1（当日买入不可卖）、持仓市值上限（live 默认 15 万）、OOS 失败组禁新开、手续费/滑点按成本模型扣除。
- **模式**：预演（dry-run，只出清单）/ 确认落账 / 历史回测。

---

## 五、做T 策略说明

> 现行配方 **v6 收盘带宽**；规则、参数与盈亏计算见 [t0-logic.md](./t0-logic.md)。

做 T 是在**已持底仓**上的日内 timing overlay，不改变选股结构。正 T = 先买后卖，反 T = 先卖后买。

- **选向**：每 5 分钟用前缀估 ŷ_τc → C_τ；C 破上带反 T、破下带正 T（须过 ŷ_τw 入场闸）。
- **执行**：leg1 = 触发根收盘价；leg2 目标冻结为 C_τ，也可由止损 / 锁赢 / 追价 / 收盘强平完成。11:00 后不开新腿。
- **盈亏**：已实现 round-trip 价差（手续费后），与调仓 PnL 分开归因。
- **与调仓关系**：ŷ_τ 模型共用但决策不重复（调仓是买入闸，做 T 是 ŷ_τw 开腿入场）；未平腿时调仓跳过该票卖出。

实现：`core/t0/`（`close_band.py` · `score_policy.py` · `slots.py`）· Follow Worker 5 分钟盯盘；缺分钟跳过该日。

---

## 六、关键代码索引

| 功能 | 位置 |
|------|------|
| 选股打分 | `core/signal/score_stock.py`, `core/signal/scorer.py` |
| 因子库 | `core/signal/factors/` |
| 双预测头 | `core/signal/dual_score/` |
| 横截面排序 | `core/signal/cross_section.py` |
| 调仓引擎 | `core/paper/rebalance/rank_lots.py`, `watching_matrix.py` |
| 做T 引擎 | `core/t0/`（`close_band.py` · `score_policy.py` · `slots.py`） |
| 模拟盘撮合 | `core/execution.py`, `core/paper/` |
| Web 后端 | `web/app.py`, `web/routers/` |
| Web 前端 | `web/static/partials/`, `web/static/js/` |
| 数据层 | `core/data/`, `core/ports/market.py` |

---

## 七、相关文档

- [量化研究指南](./quant-ui.md) — Web 量化研究台使用说明
- [调仓数学说明](./rebalance-logic.md) — 调仓策略的完整推导
- [做T 策略说明](./t0-logic.md) — 做T 规则与盈亏计算详解
- [ŷ 全链路规范](./quant.md) — 双预测头与打分链路
- [文档索引](./README.md)
