# 观察池日频 +1 趋势预判

[← 文档索引](README.md) · 产品主轴 [design-spine.md](design-spine.md) · 因子研究见 [quant.md](quant.md)

**定位**：研究探针——观察池个股在**日频决策**，预判**下一交易日收盘**方向（horizon=1）。  
**不做**：半天交易、指数专用看板、OMS、改写生产 `stance` / `signal_config`。

---

## 标签与决策

| 项 | 约定 |
|----|------|
| 决策时点 | 交易日 \(t\) 收盘后；**盘中**若入库日线仍停在 \(t-1\)，用现价暂估 \(t\) 日 bar 再决策 |
| 标签 | \(r_{t+1}=(C_{t+1}/C_t)-1\)（历史评估只用已收盘 bar） |
| 三分类 | 涨 / 跌 / 平；默认 \(\|r\|<0.5\%\) 为平（`flat_band_pct`） |
| 信号 | 复用 `score_bars`；`score≥60→up`，`≤45→down`，其余 `flat` |

### 时点对齐（勿假对照）

| 情形 | `decision_phase` | 表格「次日」含义 |
|------|------------------|------------------|
| 日线已含今日，或盘中用现价补了今日 | `bar_for_next` / `session_for_tomorrow` | 偏多/偏空/中性（即下一交易日）；勿用今日涨跌评判 |
| 日线停在昨收且无现价 | `prior_close_for_today` | **今·** 昨收→今日（可与涨跌对照，属事后核对，非明日前瞻） |

---

## 评估

- **hit_rate**：预判标签 vs 实现标签（含 flat）
- **directional_hit_rate**：双方皆非 flat 时的方向命中
- **momentum_baseline**：用昨收→今收方向猜今收→明收
- **IC**：score 与 \(r_{t+1}\) Pearson

历史命中 **≠** 明日保证；无外盘/隔夜特征，集体下跌仍可能无预告。

---

## 入口

| 层 | 路径 |
|----|------|
| 核心 | `core/backtest/next_day_trend.py` |
| API | `POST /api/quant/next-day-trend` |
| UI | 数据中心观察主表「次日」列（名单加载后自动填充） |
| 测 | `tests/test_next_day_trend.py` |

---

## 延后

- 半日（上午→下午 / 下午→次日上午）需分钟或半日 bar  
- 外盘、期指、广度隔夜特征  

**合规**：输出为研究倾向，非投资建议，不代客下单。
