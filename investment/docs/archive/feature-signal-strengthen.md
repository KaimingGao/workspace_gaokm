# 数据·信号特征同构深化补强（X 轨）

[← 文档索引](../README.md) · 数据域 [data-layer.md](../data-layer.md) · 舆情 [sentiment-layer.md](../sentiment-layer.md) · 已收口 D [data-layer-strengthen.md](data-layer-strengthen.md) · E [evidence-strengthen.md](evidence-strengthen.md) · Y [yhat-strengthen.md](yhat-strengthen.md) · N6 [n6-live-gate.md](../n6-live-gate.md)

**规划日期**：2026-08-05 · **落地**：2026-08-05  
**定位**：在 D/S/E/Y 与分组 live 已落地之后，补强 **「数据 → 信号 → 回归前瞻」** 链路上的特征侧短板——让 **live 打分用的 X 与研究/OLS/回测估 β 用的 X 同构**，并把仍缺的数据源做成可验收覆盖，而不是再堆启发式权重。  
**命名**：**X** = Feature-X / isomorphism（特征同构）；与 D（存储·PIT 工具）、Y（ŷ 生产硬化）区分。  
**终点**：回归估出的 β 能诚实落到线上 ŷ；财务/指数/稀疏因子有可见覆盖与门禁。**本轨不写 OMS / Tick / 全市场数仓 / NN→生产分 / 舆情进 ŷ（无历史面板前）**。

### 落地索引（2026-08-05）

| 阶段 | 关键落点 |
|------|----------|
| X0 | `live_features.resolve_live_fundamentals` · `score_stock` 接 PIT · `fundamentals_pit` 响应字段 |
| X1 | `fetch_live_index_bars` · `score_bars(..., index_bars=)` · `index_meta` |
| X2 | `sample_ops` ann_missing 统计 · DQ / maturity `x_track` |
| X3 | `factor_health` · `GET /api/ops/factor-health` · weight diff promote 拦截 |
| X4 | `fit_gap.quality_policy` · panel `respect_regime` |
| X5 | `fundamentals_depth` · 分数悬浮「特征同构」· 舆情仍 prior |
| 测 | `tests/test_x_track.py` · `ASSET_V=p681` |

**后继**：[beta-regression-strengthen.md](beta-regression-strengthen.md)（B0–B5 **已落地**）。

---

## 0. 一句话目标

```text
成功画像（X 轨终点）
  · live score_stock 财务按「今日可交易 as_of」PIT，与 panel / topk / OLS 同源 resolve
  · live 传入 index_bars；relative_strength / idio_momentum 不再系统性降级
  · 财务多期 real_multi_coverage 可巡检、可催办；ann_missing 不冒充干净 PIT
  · money_flow / 稀疏因子：要么有真数据源，要么从生产权重与 promote 面显式剔除
  · 成熟闸门含「特征同构」硬/软项；研究↔纸面 fit-gap 可归因到 X 而非只怪成本
```

因果主轴（与 [design-spine](../design-spine.md) 一致）：

```text
获取数据 → 提取信号(X) → 回归估 β → ŷ 前瞻 → 动作(仓位/先验)
                ▲ 本轨焦点
```

---

## 1. 背景：已收口 vs 仍缺

| 轨 / 能力 | 状态 | 说明 |
|-----------|------|------|
| **D0–D4** | ✅ | ann 入库 · source_audit · 复权 · 日历 · DQ 中心 |
| **E2** | ✅ | 研究侧 IC/OLS **已走**财务 PIT |
| **Y0–Y5** | ✅ | ŷ 口径 · 衰减 · 成本 · 暴露 · 拟合 · 样本 |
| **舆情 prior** | ✅ | S 不进 ŷ；gate / scale_holds 旁路 |
| **仍缺（本轨）** | ✅ 已落地 | live 财务 PIT · live index · 覆盖运营 · 伪因子 · 同构门禁 |

### 相对专业：路径内缺口 → 排期

| 优先级 | 缺陷 | 污染什么 | X 阶段 |
|--------|------|----------|--------|
| **P0** | Live 财务 `fetch_score_fundamentals` 无 as_of，研究按决策日 PIT | 估的 β ≠ 线上 X → 虚假 ŷ | **X0** |
| **P0** | Live `score_bars` 不传 `index_bars`；RS / idio 降级 | 组权里仍有相对强度权重却打不出同质特征 | **X1** |
| **P1** | `real_multi_coverage` / 空财务靠人盯；ann_missing 前视残留 | 样本不诚实 · IC 美颜 | **X2** |
| **P1** | `money_flow` 无真流入数据；OLS 常 sparse 仍占 config 位 | 名存实亡因子 · promote 噪音 | **X3** |
| **P2** | quote_fallback 回测可跑、live 门禁；regime 面 ≠ 全因子面板 | 回测–纸面不可比 | **X4** |
| **P2** | 新闻无历史面板；港美财务浅 | 另类/跨境进不了回归主轴（边界内只做诚实标注） | **X5** |

**边界外（不进 X 轨）**：全市场 ETL、多供应商 SLA 对账、Level2 资金流采购、LLM 写分、静默改 `signal_config.weights`、舆情强制进 ŷ。

---

## 2. 锁定原则

| # | 原则 |
|---|------|
| 1 | 生产选股真源仍是 **ReturnScoreModel → ŷ**；本轨只修 **X 的可得性与同构**，不改「启发式冒充 ŷ」 |
| 2 | **同一 resolve 函数**：live / panel / topk / OLS 财务与指数对齐共用 |
| 3 | 缺数据 → **中性 + 可观测警告**，禁止静默编造；覆盖不足进闸门而非假装有 α |
| 4 | 无 as_of 历史的另类（新闻）继续 **prior**，不进本轨「开闸进 ŷ」 |
| 5 | 永不静默写 weights；剔除/降权经人审或明确 promote 面提示 |
| 6 | 每阶段 ≥1 API/落盘 + ≥1 Web 可见 + ≥1 测试；改静态 bump `ASSET_V` |
| 7 | UI 遵守 [quant-ui-standard.md](../quant-ui-standard.md) |

---

## 3. 阶段总览（X0–X5）

```text
X0  Live 财务 PIT 同构（as_of=交易日）
        │
        ▼
X1  Live index_bars → RS / idio 同构
        │
        ▼
X2  覆盖巡检 · ann_missing 诚实 · 催办 ingest
        │
        ▼
X3  伪因子收口（money_flow 等）：有源或显式剔除
        │
        ▼
X4  质量门 / regime / fallback 研究↔live 对齐文档化+软闸
        │
        ▼
X5  边界诚实：新闻/港美「不可进 ŷ」标注 + 可选预热清单
```

| 阶段 | 主题 | 预估 | 状态 |
|------|------|------|------|
| **X0** | Live 财务 PIT | 0.5–1 d | **已落地** |
| **X1** | Live 指数特征 | 0.5 d | **已落地** |
| **X2** | 覆盖与 ann 诚实 | 0.5–1 d | **已落地** |
| **X3** | 伪因子收口 | 0.5 d | **已落地** |
| **X4** | 门禁/regime 对齐 | 0.5 d | **已落地** |
| **X5** | 边界标注与运营清单 | 0.5 d | **已落地** |

建议落地顺序：**X0 → X1 必做**（直接抬高 ŷ 可信度）；X2 与运营并行；X3–X5 可按带宽穿插。

---

## 4. 分阶段规格

### X0 · Live 财务 PIT 同构

**做**

- `score_stock` / 生产打分路径：`resolve_fundamentals_for_score(code, as_of=today_trade_date)`，与 `core/research/panel.py`、`topk_backtest`、`factor_report` 同源。
- `signal_config.fundamentals.pit_mode=as_of` **真正生效于 live**（今日配置已声明、live 未用）。
- `missing_as_of_policy`：无可用点 → 相关因子中性 + `data_quality` / warnings 可见。
- 单测：同 code、冻结 as_of，live resolve ≡ research resolve；无 ann 时行为符合 policy。

**验收**

- [x] Live 打分响应带 `fundamentals_pit: { as_of, source, ann_missing? }`
- [x] 策略/观察分数悬浮或 DQ 能看到「PIT as_of」而非仅 snapshot
- [x] `tests/test_x_track.py`（或并入既有）覆盖同构

**落点（预期）**：`core/signal/score_stock.py` · `fundamentals_bridge.py` · `fundamentals_pit.py`

---

### X1 · Live index_bars 同构

**做**

- Live `score_bars(..., index_bars=...)`：观察池/打分时拉取基准指数（与 research `cluster_panels` 同代码）。
- 失败时：显式 `no_index` 原因进 `data_quality`，避免静默 `last_change` 却仍用研究 β。
- 配置：`index_code` / 缓存策略与研究一致；可选短 TTL 缓存防打分风暴。

**验收**

- [x] 有指数时 idio_momentum / relative_strength 接入 live index_bars
- [x] 无指数时 warnings 含 `no_index`，成熟闸门软项可记

**落点**：`score_stock.py` · `cluster_panels` 抽公共 `fetch_index_bars_for_score` · factors RS/idio

---

### X2 · 覆盖巡检与 ann 诚实

**做**

- DQ / sample-status：突出 `real_multi_coverage`、空财务、`ann_missing` 占比；一键催办文案链到 `ingest-history` / warmup。
- PIT：`ann_missing=True` 的点在 live/研究统一标记；可选 policy「降权或剔除」进 config（默认先标记不静默）。
- 成熟闸门：覆盖阈值软/硬项与 Y5 样本项对齐，避免「财务空仍绿灯」。

**验收**

- [x] `/api/ops/data-quality` 可见 coverage + ann_missing + factor_health
- [x] 文档本轨收口

**落点**：`sample_ops.py` · `maturity_gate.py` · DQ API · 策略/平台样本入口

---

### X3 · 伪因子收口（money_flow 等）

**做**

- 盘点：权重>0 但无数据源 / OLS 常 sparse 的因子 → 产出「生产面因子健康」表。
- `money_flow`：无真净流入前 **默认权重保持 0**；promote/apply 若权重非 0 则强提示或拒绝。
- 可选：显式 `factor_status: disabled|proxy|live` 元数据进 registry，UI 只读展示。

**验收**

- [x] `GET /api/ops/factor-health`；money_flow 非 0 权重拦截 promote_ready
- [x] 测：`test_factor_health_blocks_money_flow_weight`

**不做**：采购 Level2 / 东财资金流全量仓（若未来做，单独立项，不混进 X3 默认范围）。

---

### X4 · 质量门 / regime / fallback 对齐

**做**

- 文档化矩阵：live 门禁 vs backtest bypass vs research `bypass_quality_gate`。
- Soft：回测报告默认挂 `quality_policy`；fit-gap 增加「X 政策不一致」归因条。
- Regime：研究面板可选 `respect_regime=True` 与 live `enabled_factors` 对齐的一键开关（默认研究仍全因子，但 OOS 对照必须跑一列「regime-aligned」）。

**验收**

- [x] fit-gap 出现 `quality_policy` 字段
- [x] 测：regime-aligned panel 与 enabled 集合一致

---

### X5 · 边界诚实（新闻 / 港美）

**做**

- 舆情：维持 prior；UI/文档写死「无历史面板 → 不可 OLS β」。
- 港美：因子可用性矩阵（有/无 ROE 等）；跨境池打分自动降权或禁用深度财务因子并警告。
- 可选运营：观察池「建议预热」清单（缺 history jsonl / 缺财务多期）。

**验收**

- [ ] 分数悬浮 / 策略指纹可见「财务深度：CN full / HK shallow」
- [ ] sentiment-layer 与本轨交叉链接

---

## 5. 与既有轨关系

```text
D 轨：造好 PIT 工具与 DQ 壳
E 轨：研究 IC/OLS 已接 PIT
Y 轨：ŷ 生产硬化、衰减、成本
X 轨：把「研究已会用的 X」接到 live，并清掉伪特征  ← 本文件
SP/舆情：动作旁路，不进 X 的 ŷ 开闸条件
```

| 依赖 | 说明 |
|------|------|
| X0 依赖 D0 | ann / resolve 已存在，live 未接线 |
| X1 依赖 research index 拉取 | 抽公共，避免复制粘贴漂移 |
| X2 依赖 D4 DQ | 加字段与催办，不重造中心 |
| X4 依赖 Y4 fit-gap | 归因条挂既有拟合面 |

---

## 6. 风险与回滚

| 风险 | 缓解 |
|------|------|
| Live PIT 后 ŷ 排序突变 | 影子对比：snapshot vs PIT 同池 Spearman；策略中心提示「X0 切换」 |
| 指数拉取拖慢打分 | 短缓存 + 并行；失败降级有标记 |
| 覆盖闸门过严导致无法调仓 | 先软项，再硬项；演示宇宙豁免规则沿用 maturity_gate |

回滚：`pit_mode` / `live_index_bars` 配置开关；默认新行为开启前可 shadow。

---

## 7. 建议开工切片（第一周）

1. **X0**：live 财务 PIT + 同构测 + 响应字段（最高 ROI）  
2. **X1**：live index_bars + RS/idio 对拍测  
3. **X2**：DQ 露出 coverage / ann_missing（半日）  

X3–X5 第二周按带宽；若只做一件事：**只做 X0**。

---

## 8. 文档与索引

落地后更新：

- [ ] 本文件阶段状态 → 已落地  
- [ ] [docs/README.md](../README.md) 增 X 轨一行  
- [ ] [data-layer.md](../data-layer.md) PIT 表：live 财务改为 as_of  
- [ ] [framework-review.md](../framework-review.md) 债务条：划掉「live snapshot 财务」  
- [ ] [sentiment-layer.md](../sentiment-layer.md) 交叉「进 ŷ 仍否」
