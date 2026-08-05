# 舆情与另类数据（Sentiment / Alt-Data）

[← 文档索引](README.md) · 数据入口见 [data-layer.md](data-layer.md) · 因子侧见 [strategy-layer.md](strategy-layer.md) · [quant.md](quant.md)

**定位（现行契约）**：规则舆情 **S** 是与 `predicted_score`（ŷ）**正交的先验旁路**，不是可回测、可求 β 的数据驱动因子。

```text
ŷ = ReturnScoreModel(价量 / 财务 / …)   ← 唯一生产排序轴
S = score_headlines(当日标题)            ← 先验
action = policy(ŷ, S)                    ← warn / 拦新开仓 / 缩仓；不改 ŷ
```

本仓库 **已有**：标题拉取与缓存；观察徽章；`sentiment_prior`（off / risk / gate）；标题 history jsonl（供将来诊断，非主回测）。  
**没有**：完整历史 news 面板、用 S 拟合 OLS β、LLM 写分、主回测注入舆情。

LLM 可**解读**标题；**不得**写入 `sub_scores` / ŷ。`sentiment.include_in_score` **恒保持 false**（硬闸）；行为由 `sentiment.prior.mode` 控制。

---

## 先验 policy（live）

| `prior.mode` | 行为 |
|--------------|------|
| `off` | 徽章 + 可选 `risk_hints`；不改目标簿 |
| `risk` | 强 bearish → warnings / UI；不改目标簿 |
| `gate` | 强 bearish → 跳过新开仓（`block_new_buys`）或目标仓 ×`scale_buy_pct`；若 `scale_holds` 则已持仓同步缩至同比例；**ŷ 不变** |

**Web**：策略中心「舆情先验」三态开关 → `POST /api/signal/config/sentiment-prior`（人审写盘；强制 `include_in_score=false`）。

配置见 `signal_config.sentiment`；实现见 `core/sentiment_prior.py` · 调仓接入 `paper_rebalance`。

---

## 为何不是因子

缺可按决策日切片的历史 news → 无法合格 as_of 回测 → 估不出可信 `alt_sentiment` β。  
因此 **不进** 主回测 / OLS / promote；history 仅作将来诊断 IC，不支撑「开闸进 ŷ」。

---

## 在架构中的位置

```text
日线/财务 ──► ŷ（排序真源）
标题/规则 ──► S（先验）──► policy(ŷ,S) ──► 开仓/仓位
LLM ────────► 叙事（不进分）
```

| 层 | 关系 |
|----|------|
| Alpha / ŷ | 不含 S |
| [risk-layer](risk-layer.md) | S → warn / gate / scale |
| 投顾 | `news` 供解读；不参与 stance / sub_scores |

---

## 本仓库对照

| 能力 | 现状 |
|------|------|
| 规则情绪 S | **有** · `score_headlines`（强度=`polar×标题覆盖`，防稀疏负向虚高） |
| 先验 policy | **有** · `role=prior` · `prior.mode` |
| Live 进 ŷ | **否** · `include_in_score=false` · X5 边界诚实 |
| 调仓旁路 | **有** · skip / scale；`predicted_score` 不改写 |
| 主回测注入 S | **否** |
| OLS 求 S 的 β | **否** |
| LLM 分析 | **有** · 叙事；不得进 `sub_scores` |

---

## 相关代码速查

| 能力 | 路径 |
|------|------|
| 先验 policy | `core/sentiment_prior.py` |
| Live 打分 | `core/signal/score_stock.py`（输出 `sentiment_prior`） |
| 调仓接入 | `core/paper_rebalance.py` |
| 硬闸 | `sentiment.include_in_score` |
| 观察 API | `web/routers/watching.py` |

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [risk-layer.md](risk-layer.md) | 防守侧输入 |
| [strategy-layer.md](strategy-layer.md) | ŷ 排序真源 |
| [yhat-strengthen.md](yhat-strengthen.md) | ŷ 门禁 |
| [design-spine.md](design-spine.md) | 先可信再变厚 |
