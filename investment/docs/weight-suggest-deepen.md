# 权重建议深化方案（IC / OLS / 因子系数）

目标：把研究枢纽从「权重建议双轨」收口为**因子系数 = 组 OLS β → 收益分**，仍不自动写盘。

## 产品语义（现行）

**人工预定的规则分退出；全面拥抱数据驱动的回归模型。**

| 旧 | 新 |
|----|----|
| 人工预定、常非负归一的 `signal_config.weights` → 规则综合分 | 历史收益拟合的 **线性回归** → 收益分 ŷ |
| `heuristic_score` / 0–100 加权 | `ReturnScoreModel`：`intercept` + **可正可负的 β** + z |

现阶段模型是 **线性回归（OLS / 可选 Ridge）**。线性系数 β 即 **带符号的系数权重**：

```text
ŷ = α + Σ βᵢ · zᵢ
```

- **β > 0**：该因子抬升预期收益；**β < 0**：压制
- 量纲：因子 z 上 1σ → ŷ 百分点斜率（**不是**和为 1 的混合权）
- `|β|` 归一化仅供展示 / 遗留诊断，**不**驱动选股
- 无组 / 全局模型时 `score` 为空，**不**回退规则分
- 换非线性模型时仍换拟合器；选股真源始终是「模型预测分」，不再开人工权主轴

细则与退役说明见下文 P11 / Live。

## 目标链路（已合并）

```text
单票 OLS β 聚类  →  组池 OLS β（因子系数）  →  return_model → ŷ  →  人审 promote
```

遗留：`weight_suggest` / IC 小步权 / OOS 组权对照 已标 `deprecated_for_scoring`；展示权可由 `|β|` 派生，**不是**选股权。

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0–P3** | IC / 小步权 / OOS（研究遗留） | ✅ 已退役为选股主轴 |
| **P4** | 单票 OLS β 聚类 → 组内池 OLS → **因子系数 return_model** | ✅ |
| **P4.1** | 组内 OOS 对照（遗留诊断） | ✅ 轻量 |
| **P4.2** | 导出 diff（遗留；`promote_ready` 恒否） | ✅ 轻量 |
| **P5** | 分组 score：组因子系数 → **ŷ 组内排序** | ✅ |
| **P6** | 各组组内 Top-N **合成候选簿** + 分池 vs 全局 Top-K 对照回测 | ✅ 轻量（Top-N 默认 10） |
| **P7** | `code→cluster→return_model` **映射产物**（weights 由 \|β\| 可选派生） | ✅ |
| **P8** | 分池候选簿 **确认落账**（永不写 signal_config） | ✅ |
| **P9** | 多权打分（遗留诊断） | ✅ 轻量 |
| **P10** | 权重坐标网格对照（遗留；`promote_ready` 恒否） | ✅ 轻量 |
| **P11** | **因子系数 = 组 OLS β → 收益分**；规则分已全局退役 | ✅ |
| **P11.1** | 回测页固定收益分 + ŷ 模型草稿/promote | ✅ |
| **L0** | live 映射晋升 / 回滚（门禁=return_model） | ✅ |
| **L1** | `score_stock` 主分=ŷ（映射组/全局因子系数） | ✅ |
| **L2** | `rank_cluster_pools` + 纸面 `cluster_mode` 调仓 | ✅ |
| **L3** | 草稿/日更刷新簿 + 覆盖率·陈旧健康检查 | ✅ |
| **L4** | execution 只读 `cluster_book`（不写全局 weights） | ✅ |

## 研究枢纽定位（股票分组）

**目的**：用聚类找出 **OLS 表现相似** 的股票 → **同组共用一套因子系数**；**不同组各自 β → ŷ**。

```text
股票分组 → 宇宙=全部观察池；OLS β·complete·目标 k≈N/5（4～10）+ 超大组二分
一组一表 → G 标题含成员；主列「因子 / 系数β」（z 上 ŷ% 斜率，可负，非归一化权）
未入组   → 数据不足单独提示；纸面持仓映射另作落地参考
```

进阶（折叠）：影子对照 / 用分组打分 / 按分组调纸面仓 —— **不是**枢纽日常主路径。
探针（折叠）：单票 vs 所在组 —— 核对该票是否仍适合本组建模；不冲组表。

## P4–P9 · 实现链路（支撑上述）

```text
宇宙 = 全部观察池
  → 逐票 OLS β → 缩尾+z-score → complete·目标k≈N/5 → 超大组二分
  → 多票组池 OLS → return_model（因子系数）→ |β| 可选派生展示权
  → holdings_assignment / 组内 ŷ 排序 / 分池簿
  → UI：一组一表（系数β）；可选晋升 live
```

- 入口：研究枢纽「跑分组」/ `POST /api/quant/factor-ols-clusters`
- **真源**：`ReturnScoreModel`（`intercept` + `coefficients` + `z_*`）
- **promote 门禁**：至少 2 只带 `return_model.coefficients`；weights 可选由 `|β|` 派生
- **不写** `signal_config.weights`（硬边界）
- 量纲：系数是 z 上的 ŷ 百分点斜率，**不是**归一化权重
- `signal_config.weights` 保留默认值仅供 `score_bars` 因子管线（不产出规则分）

## P0/P1 规则（遗留；已非选股主轴）

对每个 `signal_config.weights` 因子的旧小步建议逻辑仍见于 `weight_suggest.py`，但分组路径已改为 `|β|` 派生展示权并标 `deprecated_for_scoring`。

## P2 OOS 门禁（研究对照）

**产品语义**（与门禁 `note` / 研究枢纽文案同源）：

| 臂 | 角色 |
|----|------|
| `heuristic_score` | 全局人工预定义线性加权排序 · **对照基线** |
| `predicted_score`（ŷ） | 研究枢纽回归模型 · **选股实现方案** |

研究枢纽定位：寻找更优 ŷ，以提升历史回测收益与交易执行选股准确率。

- 切分：同一研究池 Top-K 权益曲线 **后 30%** 为 OOS
- 过门：研究臂 OOS ≥ 基线 OOS − `oos_tol_pp`（默认 1pp）
- 过门 ≠ 自动 promote；不写 `signal_config`

## 非目标

- 自动把组权 / β 写入 `signal_config.weights`
- 把 β 强行写成「和为 1 的生产权重」再当 ŷ 用
- raw OLS β → 权重占比当选股主轴
- 坐标搜索结果自动 promote

## P10 · 权重坐标搜索对照（遗留）

回答「是目标函数错了，还是权重本身救不了」：同一宇宙并列三臂；`promote_ready` 恒否。

## P11 · 因子系数 → 收益分

```text
跑分组 → 组内池 OLS β
  → cluster.return_model（因子系数）
  → 组内 predicted_ranking / promote 后 live code_map.return_model
  → rank_mode=predicted_score 时按各组 ŷ 排序
```

| 入口 | 说明 |
|------|------|
| `rank_mode=predicted_score` | 优先分组 β；否则全局模型 |
| `POST /api/quant/rank-mode-compare` | 遗留对照 |
| `core/signal/return_score.py` | `ReturnScoreModel` |
| `core/signal/factor_coefs.py` | `|β|` → 展示权 |

系统默认主路径仅为 `predicted_score`；规则分（`heuristic_score`）已全局退役，不写入 signal_item、不参与排序/stance/调仓。无全局/分组系数时 `score` 为空。

### P11.1 · 回测页与模型产物

| 入口 | 说明 |
|------|------|
| 回测页 | 固定排序 · 收益分 |
| 「拟合ŷ模型」 | 全局草稿（无分组时回退） |
| 枢纽 promote | `code_map.return_model` 必填；weights 可由 `|β|` 派生 |
| `ASSET_V` | p614 |

## 验收

1. 跑分组后表内只见「因子系数 β」，无双轨「权 vs β」主叙事
2. promote 仅依赖组 `return_model`；live 打分/簿排序用 ŷ
3. 无系数时不回退规则综合分

---

## Live 路线 · 分组进生产打分（已落地）

### 产品硬约束（不可破）

1. **不**把各组权合并成一份全局 `signal_config.weights` 后假装「已分组」
2. **不**自动写 `signal_config.weights`
3. 全局 `signal_config` 继续服务「未映射票」因子管线；分组因子系数走**独立活产物**
4. 中性化在 `predicted_score` 模式下跳过（与拟合面板一致）

### 目标架构

```text
[人审] β 分组产物
   → promote → data/live/cluster_weights_active.json  (code→cluster→return_model；weights 可选派生)
   → score_stock(code): 组/全局 return_model → ŷ（主分）
   → rank_cluster_pools / paper rebalance（可选 cluster_mode）
```

### 打分语义

```text
score_bars → sub_scores（ŷ 输入；可顺带算加权总分，不挂产品面）
ŷ = ReturnScoreModel.predict(sub_scores)   # 选股真源 = predicted_score
# 规则分 heuristic_score 已全局退役，不写入、不排序
```

### 明确不做

- 自动写 `signal_config.weights`
- 把 β 归一化成生产权再当 ŷ
- 无系数时回退规则综合分

### 验收（Live）

- promote 门禁=`return_model.coefficients`；weights 可缺或 `|β|` 派生
- active 时主分=ŷ
- 单测：`test_factor_coefs` · `test_cluster_live` · `test_cluster_group_score` · `test_return_score`
- `ASSET_V=p614`

