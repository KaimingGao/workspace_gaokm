# core/signal/factors

可插拔因子实现，由 `factor_registry` 统一注册。

## 生产因子（20）

| 文件 | 因子 | 说明 |
|------|------|------|
| `momentum.py` | 动量 | 近 3/5 日涨跌幅 |
| `volume_price.py` | 量价 | 量比 + 涨跌方向 |
| `relative_strength.py` | 相对强弱 | 相对指数超额（可 fallback） |
| `volatility.py` | 波动 | ATR% 惩罚 |
| `reversal.py` | 反转 | 温和回调区 contrarian |
| `liquidity.py` | 流动性 | 成交额活跃度比 |
| `value.py` | 估值 | PE/PB 适中区间（需 fundamentals） |
| `quality.py` | 质量 | ROE only（增速见 growth） |
| `technical_pattern.py` | 技术形态 | 均线/突破/形态 |
| `weekly_confirm.py` | 周线确认 | 日线聚合周线趋势 |
| `ma_slope.py` | 均线斜率 | 多周期斜率与发散 |
| `gap_risk.py` | 跳空风险 | 隔夜跳空幅度 |
| `alt_sentiment.py` | 另类情绪 | 舆情快照（已并入权重，无硬编码 adj） |
| `size.py` | 规模 | log(市值) 适中区间 |
| `earnings_yield.py` | 盈利收益率 | EP=1/PE_TTM |
| `growth.py` | 成长 | 盈利/营收增速 |
| `dividend.py` | 股息 | dividend_yield |
| `money_flow.py` | 资金流 | 真净流入口 / OHLCV MFI **proxy**（默认权重 0，须人审启用） |
| `amihud.py` | 非流动性 | \|ret\|/amount 冲击代理 |
| `idio_momentum.py` | 特异动量 | 对指数回归残差 |

权重见 `data/signal_config.json`；截面 `industry_residual` + `size_residual` 默认开。新增因子须同步 registry + 配置 + IC 实验。

目录内另有仓位/成本/止损等模块（`kelly` / `cost` / `risk` 等），**不是** alpha 注册因子。

## 相关文档

- [quant.md](../../../docs/quant.md)
- [架构总览 · 子目录索引](../../../docs/architecture.md#子目录-readme-索引)
