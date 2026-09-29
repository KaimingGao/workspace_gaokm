# Alpha158 原生重写（路径 B）实施 Plan

> 路径 B：原生重写到 `core/signal/factors/alpha158.py`，**不引入 pyqlib 依赖**。所有公式用纯 numpy 实现，与现有 factor registry / 面板 / Ridge / LightGBM / LambdaRank 完全复用。

## 1. 设计决策与权衡

### 1.1 注册策略：聚合单注册（A 方案）
- **方案**：将整个 Alpha158 作为**单个 factor** 注册为 `alpha158`，`score_alpha158(bars) -> (50.0, meta_dict)` 返回 158 个数值字段。
- **理由**：
  1. 不污染注册表（避免 158 个新 factor name，冲击现有 `_research_sub_scores` / IC 实验的颗粒度）
  2. 现有 `_research_sub_scores` 会自动把 `meta` 数值字段以 `raw_alpha158_<FIELD>` 形式注入 row，**零改动**接入 Ridge/LightGBM/LambdaRank 训练管线
  3. 生产路径 `compute_configured_factors` 通过 `omit_sub_score=True` 跳过 sub_score 加权（避免 158 维信号污染主分），但 meta 字段依然更新
- **sub_score 取值**：固定 50.0（中性），并设 `omit_sub_score=True`，让生产路径跳过加权；研究路径只消费 raw 字段，不读 sub_score

### 1.2 公式实现：纯 numpy + 单 bars 输入
- 输入：`bars: List[dict]`（与现有因子完全同构，最低 60+1 根历史）
- 字段访问：`bar["close"] / ["open"] / ["high"] / ["low"] / ["volume"]`（与现有因子一致）
- **不依赖 VWAP**：项目 bars 中无 vwap 字段，Alpha158 price 部分的 VWAP 用 `(high+low+close)/3` 近似（或全部跳过 VWAP，只保留 OPEN/HIGH/LOW/CLOSE，9+3+145=157 个；缺一个用 `CLOSE0=1.0` 常量补足至 158）。**首选近似 VWAP**，保持因子集完整性
- 滚动窗口：`windows=[5, 10, 20, 30, 60]`（与 Qlib 默认一致）
- 历史要求：最少 61 根（60 用于滚动 + 1 当日），低于此返回 50.0 + `omit_sub_score=True`

### 1.3 数值安全
- 所有分母加 `1e-12` 防零除（与 Qlib 一致）
- 对数：`np.log(volume + 1)`（与 Qlib 一致）
- 滚动统计：用 `numpy` 滑动窗口（np.std / np.mean / np.corrcoef），窗口不足返回 None
- 异常隔离：单因子异常不阻塞，None 字段进 row 后由 Ridge/LightGBM 的 fillna 处理

## 2. Alpha158 完整因子清单（158 个）

### 2.1 kbar（9 个）— K 线形态
| 字段 | 公式 |
|---|---|
| KMID | (close-open)/open |
| KLEN | (high-low)/open |
| KMID2 | (close-open)/(high-low+1e-12) |
| KUP | (high-max(open,close))/open |
| KUP2 | (high-max(open,close))/(high-low+1e-12) |
| KLOW | (min(open,close)-low)/open |
| KLOW2 | (min(open,close)-low)/(high-low+1e-12) |
| KSFT | (2*close-high-low)/open |
| KSFT2 | (2*close-high-low)/(high-low+1e-12) |

### 2.2 price（4 个）— 当日价相对收盘
| 字段 | 公式 |
|---|---|
| OPEN0 | open/close |
| HIGH0 | high/close |
| LOW0 | low/close |
| VWAP0 | vwap/close（vwap 近似为 (high+low+close)/3） |

### 2.3 rolling（29 操作 × 5 窗口 = 145 个）
窗口 `d ∈ [5, 10, 20, 30, 60]`：

| 操作 | 公式 | 含义 |
|---|---|---|
| ROC | `close[-1]/close[-1-d]` | d 日价格变化比 |
| MA | `mean(close[-d:])/close[-1]` | d 日均价/最新价 |
| STD | `std(close[-d:])/close[-1]` | d 日波动率 |
| BETA | `slope(close[-d:], arange(d))/close[-1]` | d 日线性斜率 |
| RSQR | `rsquare(close[-d:], arange(d))` | 线性回归 R² |
| RESI | `residual(close[-d:], arange(d))/close[-1]` | 线性回归残差 |
| MAX | `max(high[-d:])/close[-1]` | d 日最高/最新价 |
| LOW | `min(low[-d:])/close[-1]` | d 日最低/最新价 |
| QTLU | `quantile(close[-d:], 0.8)/close[-1]` | 80% 分位/最新价 |
| QTLD | `quantile(close[-d:], 0.2)/close[-1]` | 20% 分位/最新价 |
| RANK | `rank_percentile(close[-1], close[-d:])` | 当日收盘在 d 日中分位 |
| RSV | `(close[-1]-min(low[-d:]))/(max(high[-d:])-min(low[-d:])+1e-12)` | 价格位置指标 |
| IMAX | `argmax(high[-d:])/d` | 最高价距今天数/d |
| IMIN | `argmin(low[-d:])/d` | 最低价距今天数/d |
| IMXD | `(argmax(high[-d:])-argmin(low[-d:]))/d` | 高低日差/d |
| CORR | `corrcoef(close[-d:], log(volume[-d:]+1))[0,1]` | 量价相关 |
| CORD | `corrcoef(close_ret[-d:], volume_log_ret[-d:])` | 量价变化相关 |
| CNTP | `mean(close_ret[-d:] > 0)` | 上涨日占比 |
| CNTN | `mean(close_ret[-d:] < 0)` | 下跌日占比 |
| CNTD | `CNTP - CNTN` | 涨跌差 |
| SUMP | `sum(pos_ret[-d:])/(sum(abs(ret[-d:]))+1e-12)` | 正收益累计/绝对总变化 |
| SUMN | `sum(neg_ret[-d:])/(sum(abs(ret[-d:]))+1e-12)` | 负收益累计/绝对总变化 |
| SUMD | `(SUMP - SUMN)/(sum(abs(ret[-d:]))+1e-12)` | 涨跌差比 |
| VMA | `mean(volume[-d:])/(volume[-1]+1e-12)` | d 日均量/最新量 |
| VSTD | `std(volume[-d:])/(volume[-1]+1e-12)` | d 日量波动/最新量 |
| WVMA | `std(abs(ret[-d:])*volume[-d:])/(mean(abs(ret[-d:])*volume[-d:])+1e-12)` | 量加权价格波动 |
| VSUMP | `sum(pos_vol_delta[-d:])/(sum(abs(vol_delta[-d:]))+1e-12)` | 量增加额/绝对总变化 |
| VSUMN | `sum(neg_vol_delta[-d:])/(sum(abs(vol_delta[-d:]))+1e-12)` | 量减少额/绝对总变化 |
| VSUMD | `(VSUMP - VSUMN)/(sum(abs(vol_delta[-d:]))+1e-12)` | 量差比 |

**总数**：9 + 4 + 145 = 158 ✓

## 3. 实施步骤

### Step 1：创建 `core/signal/factors/alpha158.py`
- 实现 29 个 rolling 操作辅助函数（每个接受 close/high/low/volume 数组 + 窗口 d，返回标量）
- 实现 9 个 kbar 字段 + 4 个 price 字段
- 主函数 `score_alpha158(bars: List[dict]) -> tuple[float, dict]`：
  - 校验 `len(bars) >= 61`，不足返回 `(50.0, {"omit_sub_score": True, "alpha158_insufficient_history": True})`
  - 把 bars 拆成 close/open/high/low/volume 数组
  - 计算所有 158 个字段，组装 meta dict
  - 返回 `(50.0, meta_with_omit_sub_score)`

### Step 2：注册到 `core/signal/factors/meta/registry.py`
- 顶部 `from core.signal.factors.alpha158 import score_alpha158`
- 添加 `_compute_alpha158(bars, **_kw): return score_alpha158(bars)`
- `_register("alpha158", "Alpha158因子集", _compute_alpha158, "微软 Qlib Alpha158 158 个 OHLCV 衍生因子（kbar+price+rolling），sub_score 不加权，仅暴露 raw_alpha158_* 字段供 Ridge/LTR 消费。")`

### Step 3：测试 `tests/test_alpha158.py`
- `test_alpha158_field_count`：验证正好 158 个字段（除 `omit_sub_score` 等）
- `test_alpha158_kbar_formulas`：用合成 bars 验证 KMID/KLEN/KUP/KLOW/KSFT 公式
- `test_alpha158_rolling_windows`：验证 5/10/20/30/60 窗口各 29 个字段
- `test_alpha158_insufficient_history`：bars < 61 时返回 omit_sub_score=True
- `test_alpha158_registry`：通过 `compute_factor("alpha158", bars)` 调用，确认 raw_alpha158_* 注入
- `test_alpha158_panel_integration`：调用 `_research_sub_scores`，确认 row 中出现 `raw_alpha158_KMID` 等字段
- `test_alpha158_end_to_end`：调用 `build_oo_rank_day_panels`（feature_mode="raw"），确认面板特征列扩至 158+ 维

### Step 4：IC 实验 + 回归验证
- `.venv/bin/python -m pytest tests/test_alpha158.py -v`
- 跑 `run_factor_experiment` 单因子 IC（vs horizon=1 forward return）
- 跑 `build_oo_rank_day_panels` + `fit_oo_rank_report`，对比引入 Alpha158 前后 OOS IC（Ridge/LightGBM/LambdaRank）

## 4. 风险与缓解

| 风险 | 缓解 |
|---|---|
| VWAP 缺失导致字段数不对 | 用 (high+low+close)/3 近似，保持 158 完整 |
| 158 维膨胀使 Ridge/LightGBM 过拟合 | 已有 LightGBM 后端 + cs_zscore 标准化；先用 cs_rank 模式做基准，再切 raw |
| sub_score=50 进入 z-score 中性化污染 | 用 `omit_sub_score=True`，`compute_configured_factors` 跳过 sub_scores |
| 滚动统计窗口不足返回 None | Ridge/LightGBM 的 fillna 处理；研究面板丢弃 None 行 |
| 与现有 momentum/volatility/volume_price 因子共线性 | 不去重，让 Ridge L2 正则 / LightGBM 自动选择；后续可加 collinearity 模块去冗余 |
| 单 bars 历史短（新股） | `len(bars) < 61` 整体跳过，与 Qlib 默认 `min_history` 行为一致 |

## 5. 验证清单（完成标准）

- [ ] `core/signal/factors/alpha158.py` 实现完成
- [ ] `registry.py` 注册 `alpha158`
- [ ] `tests/test_alpha158.py` 全过
- [ ] `compute_factor("alpha158", bars)` 返回 158 个 raw 字段
- [ ] `_research_sub_scores(window, factor_names=("alpha158",))` 把 raw_alpha158_* 注入 row
- [ ] `build_oo_rank_day_panels` 引入 Alpha158 后特征列 +158
- [ ] OOS IC 对比报告（Ridge/LightGBM/LambdaRank，引入前后）

## 6. 非目标（不做）

- 不引入 pyqlib 依赖
- 不重写 Alpha360（360 维价格序列，非因子化结构，下一阶段评估）
- 不替换现有 momentum/volatility 等因子，Alpha158 作为补充信号源
- 不实现 MLflow / ALSTM / Transformer（独立规划，不在此 plan 范围）
- 不修改 Web UI（Alpha158 通过 `factor_names` 参数自动可用，无需 UI 改动）
