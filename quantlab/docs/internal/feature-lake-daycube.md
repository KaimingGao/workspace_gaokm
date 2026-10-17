# Feature Lake（L1）与 DayCube（L2）方案说明

[← 文档索引](../README.md) · 行情缓存见 [bars-update-cache.md](./bars-update-cache.md) · 因果主轴见 [design-spine.md](../design-spine.md) · ŷ 链路见 [quant.md](../quant.md)

> 状态：**已落地（现行实现）**  
> 范围：训练 / 历史回测 / live 调仓 / T0 共用的截面特征物化层  
> 不覆盖：全市场数仓、Tick、财务完整 PIT、分布式集群

---

## 1. 问题与目标

### 1.1 根因

模型训练、历史回测、live 调仓曾在「构建截面数据」上耗时过大。根因不是单点算力，而是：

| 路径 | 旧行为 | 代价 |
|------|--------|------|
| 训练 | 按票 `collect_*` → 堆叠 → 日截面 z / breadth | 一根新 bar → 整票全窗 fingerprint miss → 全历史重算 |
| 回测 | 每日切窗 `score_window_as_item` → 池内 rescore | O(天 × 票) 重复建特征；冷路径不读 L1 |
| Live | 单票 `score_stock` 现算 → `rescore_fitted_cross_section` | 单票看不到同伴 → **双遍** predict；τ 池进程内临时拼 |

三套语义相近，**物化形态不同**，「临时构建大规模截面」在三条链路上反复出现。

### 1.2 目标

1. **一次物化、三处消费**：训练 / 回测 / live 读同一套 L1 特征行与 L2 日截面统计。  
2. **增量优先**：日线 topup 只追加末几根时，只重算尾窗决策日，禁止常态「整票推倒」。  
3. **单遍截面 ŷ**：池齐后用 DayCube μ/σ 打 z 再 predict；调仓扫池可先只抽特征。  
4. **口径可审计**：截面宇宙绑 `universe_hash`；主头 / `zscore_universe=pool` 时 tier / `tradable_codes` 只挡写分与开仓、不缩小 z 池；排序头 `tier_a` 先缩 A 再算 z（见 oo/tc_rank）。  
5. **可回滚**：环境变量关断即回旧路径，无需删库。

### 1.3 非目标

- 不上 Spark / Ray 全市场重算  
- 不改 OMS、不接实盘下单  
- 不在本层完成财务完整 PIT  

---

## 2. 总体架构

```text
L0  Bars Store（bars.db / 增量拉取）          ← 已有
        │ bars topup / 复权改写
        ▼
L1  Feature Lake     code × date → raw 特征行（无同伴）
        │ 按日 group + universe
        ▼
L2  DayCube          date × universe_id × clock
                     → μ/σ、breadth、gap slim、可选 X_raw
        │
        ├─ Fit Job           stack 多日 / 读 L1 写穿 → Ridge / rank
        ├─ paper_replay      prefetch L1 → 按日 hydrate → batch ŷ
        ├─ watching_matrix   defer 特征 → DayCube 单遍 ŷ
        └─ T0 / run_daily    读当日 cube slim（τ 截面池）
```

### 2.1 分层职责

| 层 | 存什么 | 不存什么 | 谁写 | 谁读 |
|----|--------|----------|------|------|
| **L0** | OHLCV 日线/分钟 | 因子 | bars refresh | 一切上游 |
| **L1** | 单票因果特征行 + 可选 y | 同伴统计（z/breadth） | collect / panel 写穿 | fit、replay hydrate |
| **L2** | 日截面 μ/σ、slim、宇宙指纹 | 长期全历史堆叠矩阵 | fit assemble、batch_score、tau pool | rescore、T0 seed、live |

### 2.2 不变式（契约）

1. **CS μ/σ 宇宙** = watching（live/T0 再 ∪ holdings），上限 `WATCHING_MAX_SIZE`；`score_codes` / tier **不缩小 z 池**。  
2. L1 行必须 **因果**：决策日 t 的特征不得含 t 收盘后信息（沿用 oo / τ / co 定义）。  
3. Cube 键 = `(FEATURE_SCHEMA, universe_hash, clock, date)`；宇宙不一致 → miss。  
4. HTTP / 调仓请求路径 **禁止** 全宇宙 `collect_*`；只允许 hydrate miss 的少量票。  
5. Cache 是加速，Store 是契约：fingerprint miss 应尽量「修尾」，不是无脑推倒。

---

## 3. 数据契约

### 3.1 Feature Registry

模块：`core/research/feature_registry.py`

| 项 | 说明 |
|----|------|
| `FEATURE_SCHEMA` | 特征语义不兼容时人工 +1 → L1/L2 全量 miss |
| `registry_fingerprint(names, family)` | 列名表指纹 |
| `universe_hash(codes)` | 排序去重后的宇宙短哈希 |

### 3.2 L1 Feature Lake

模块：`core/research/feature_lake.py`  
目录：`FEATURE_LAKE_DIR`（默认 `data/cache/feature_lake`）

```text
{FEATURE_LAKE_DIR}/v{FEATURE_SCHEMA}/{family}/{code}.npz
{FEATURE_LAKE_DIR}/v{FEATURE_SCHEMA}/{family}/{code}.meta.json
```

| Family | 来源 |
|--------|------|
| `oo` | ŷ_oo compact collect |
| `tc` / `tc_rank` | ŷ_τc 开盘（`tau_hm=open`） |
| `tc_HHMM` / `tc_rank_HHMM` | ŷ_τc 分钟钟（如 `tc_1030`）；与开盘分目录，互不覆盖 |
| `co` | ŷ_co compact |
| `oo_rank` | ŷ_oo_rank 面板 |

`family_for_tc(tau_hm=, rank=)`（`lake_bridge`）生成上表名字；bars 失效时 `invalidate_l1_for_code` 会扫盘上全部 `tc*` family。

载荷：`X[n,p]`、`dates[n]`、可选 `y[n]`；meta 含 `fingerprint`、`names`。

API 要点：

- `write_stock_panel` / `load_stock_panel` / `sync_from_matrix`  
- `upsert_tail`：按日 merge（`keep_from` 之前保留）  
- `load_day_rows(family, codes, date)`：多票同日特征 dict  
- `invalidate_code`：bars 变更后强制 miss  

开关：`QUANTLAB_FEATURE_LAKE=0` 绕过。

### 3.3 L2 DayCube

模块：`core/research/day_cube.py`  
目录：`DAY_CUBE_DIR`（默认 `data/cache/day_cubes`）

```text
{DAY_CUBE_DIR}/v{FEATURE_SCHEMA}/{universe_hash}/{clock}/{date}.npz
{DAY_CUBE_DIR}/v{FEATURE_SCHEMA}/{universe_hash}/{clock}/{date}.meta.json
```

| 字段 | 含义 |
|------|------|
| `codes` / `names` / `X_raw` | 当日对齐矩阵 |
| `z_mean` / `z_std` | 列向截面 μ/σ（与 `cross_section_zscore_matrix` 单组语义对齐） |
| `slim` | τ 池字段：`pool_gaps`、`sector_gap_breadth`、`gaps_by_code`、`ref_by_code` 等 |
| `clock` | `open` / `eod` / `tau_HHMM` |

进程内 `_MEMORY` 缓存当日 cube；`persist_tau_pool_as_cubes` 把 `build_tau_pool_by_date` 结果写入 slim。

开关：`QUANTLAB_DAY_CUBE=0` 不落盘（内存构建仍可用）。

### 3.4 指纹

| 模块 | 职责 |
|------|------|
| `panel_fingerprint.py` | `bars_fingerprint` / `make_fingerprint` / `*_head_key`（L1 与增量共用） |
| Feature Lake | 训练矩阵与按日特征的唯一写穿 / 主读 |

桥接：`lake_bridge.load_training_matrix` / `sync_panel_to_lake` / `invalidate_l1_many`。

---

## 4. 增量与失效

### 4.1 尾窗增量（`incremental_panel.py`）

panel meta 额外写入：

- `bars_fp`：当前全窗 bars 指纹  
- `stem_fp`：去掉末根后的指纹  
- `end_date`：末根日期  

判定 `tail_append_days`：

| 情况 | 返回 | 行为 |
|------|------|------|
| `fingerprint(bars[:-1]) == meta.bars_fp` | k=1（可推广到 ≤16） | 尾窗 `keep_dates` 重算 + merge |
| `fingerprint(bars[:-1]) == meta.stem_fp` | 0 | 仅改末根，同样修尾 |
| 中间根变化 / 复权 | `None` | 全量 collect |

已接线：**oo / τc(open) / co / oo_rank**。  
`collect_*` 支持 `keep_dates`（oo 原有；τc open / co 已补）。

### 4.2 bars topup 失效

`quant/research/bars_status.py` · `refresh_bars_only`：对本批整窗/缺口票调用 `invalidate_l1_many`，下次拟合/回测重算 L1。

---

## 5. 三条消费链路

### 5.1 训练（Fit）

```text
load_portfolio_stock_bars
  → assemble_oo_panels / collect_tau_compact / collect_co_compact / oo_rank
      ├─ L1 fingerprint hit → 返回
      ├─ stem 增量 → keep_dates collect → merge → sync L1
      └─ 全量 collect → sync L1
  → （oo）cubes_from_stacked_panel 物化多日 DayCube
  → Ridge / LambdaRank（cross_section_zscore_matrix 或读 cube）
```

### 5.2 历史回测（paper_replay）

```text
build_tau_pool_by_date → seed + persist_tau_pool_as_cubes
prefetch_l1_coverage(codes)          # 进程内 code→dates + 整票 panel 缓存
for each day:
  批量按日切片 L1（吃内存缓存；开盘读 family=tc）
  hit → 组装 item（跳过 score_window_as_item 与昨收窗；缺口吃 quote）
  miss → score_window_as_item
  attach yhat heads（lake 的 features_co 直打 ŷ_co，不重建）
  rescore_fitted_cross_section_batched  # 建/读 DayCube，单遍 z+ŷ
```

### 5.3 Live 调仓（watching_matrix）

```text
seed τ 截面池（可短路由 DayCube slim）
并行 score_one(..., defer_cs_predict=True)   # 只特征，不写截面 ŷ
rescore_fitted_cross_section_batched         # DayCube μ/σ → ŷ_oo/τ/co
rank_lots / 账本
```

`defer_cs_predict` 在 `QUANTLAB_BATCH_SCORE=1`（默认）时自动开启。

### 5.4 T0 / 日循环

- `build_tau_pool_watching_holdings`：当日 cube slim 命中则跳过扫 bars  
- `run_daily_cycle`：启动时 `_try_hydrate_tau_xs_from_day_cube` 预热进程缓存  

---

## 6. 单遍截面打分

模块：`core/research/batch_score.py` + `cross_section_rescore.py` + `return_score.py`

1. `features_by_code_from_items`：合并 `sub_scores`、`features_tau`、`features_co`、`raw_*`  
2. `ensure_day_cube_for_items` → 落盘 + 内存  
3. `rescore_fitted_cross_section(..., day_cube=)`：  
   - ŷ_oo：`apply_predicted_scores(..., day_cube=)`  
   - ŷ_τ / ŷ_co：`apply_cube_z_to_dicts` 后 predict  

与 `cross_section_zscore_dicts` 在同批 raw 上应对齐（单测覆盖）。

---

## 7. 环境变量与路径

| 变量 | 默认 | 含义 |
|------|------|------|
| `QUANTLAB_FEATURE_LAKE` | 开 | `0` 关闭 L1 |
| `QUANTLAB_FEATURE_LAKE_DIR` | `paths.FEATURE_LAKE_DIR` | L1 根 |
| `QUANTLAB_DAY_CUBE` | 开 | `0` 关闭 L2 落盘 |
| `QUANTLAB_DAY_CUBE_DIR` | `paths.DAY_CUBE_DIR` | L2 根 |
| `QUANTLAB_BATCH_SCORE` | 开 | `0`：不建 cube、调仓不 defer，走旧双遍 |

路径常量：`core/paths.py` → `FEATURE_LAKE_DIR` / `DAY_CUBE_DIR`。

---

## 8. 模块与测试索引

### 8.1 核心模块

| 模块 | 职责 |
|------|------|
| `core/research/feature_registry.py` | schema / universe_hash |
| `core/research/feature_lake.py` | L1 读写 |
| `core/research/day_cube.py` | L2 构建/落盘/z |
| `core/research/lake_bridge.py` | 训练读 L1、写穿、失效 |
| `core/research/panel_fingerprint.py` | bars / make 指纹 |
| `core/research/incremental_panel.py` | 尾窗判定与 merge |
| `core/research/batch_score.py` | 池级 cube + batched rescore |
| `core/research/lake_replay.py` | 回测 L1 hydrate + coverage 预热；盘中前缀 `tc_HHMM` |
| `core/signal/score_stock.py` | `defer_cs_predict` |
| `core/signal/cross_section_rescore.py` | day_cube 版 z |
| `core/signal/return_score.py` | OO predict 吃 day_cube |

### 8.2 接线点

| 路径 | 文件 |
|------|------|
| oo collect | `oo_ridge_compact.py` |
| τc / co collect | `panel_matrix.py` |
| oo_rank | `oo_rank_panel.py` |
| 回测 | `paper_replay.py` · `replay_scoring.py` |
| 调仓 | `watching_matrix.py` |
| T0 | `t0/score_policy.py` |
| 日循环 | `paper/cycle.py` |
| bars 失效 | `quant/research/bars_status.py` |

### 8.3 测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_feature_lake.py` | L1 写读 / miss / upsert_tail |
| `tests/test_day_cube.py` | L2 z 对齐 / tau slim / 宇宙 miss |
| `tests/test_incremental_panel.py` | stem / 多日追加 / oo 增量 / lake hydrate |
| `tests/test_batch_score_cube_oo.py` | OO cube z ≡ standardize |
| `tests/test_defer_cs_predict.py` | defer 不写 ŷ、保留特征 |

---

## 9. 落地阶段回顾

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | 契约：registry / lake / cube / paths / 开关 | ✅ |
| P1 | L1 双写（oo/τc/co/oo_rank） | ✅ |
| P2 | 训练物化 DayCube；CS z 可吃 cube | ✅ |
| P3 | paper_replay：tau cube + L1 hydrate + batch rescore | ✅ |
| P4 | watching_matrix batch + defer_cs_predict | ✅ |
| P5 | T0 / daily_cycle 读 cube slim | ✅ |
| 加深 | 尾窗增量（含多日）、OO 单遍 z、L1 prefetch、defer 调仓 | ✅ |
| 扫尾 | 去掉未用 API / 合并 oo 增量入口 / 静默 except→debug | ✅ |
| P6 | replay 暖路径：L1 进程缓存按日切片；命中跳过 score_window；lake co 直用 | ✅ |
| P7 | 分钟 τ L1 clock 分族（`tc_HHMM`）；命中路径省昨收窗（y_hl 已下线） | ✅ |
| P8 | replay `fill_clock≠09:30`：预热 `tc_HHMM`，前缀 ŷ_τc 优先 L1 hydrate | ✅ |
| P9 | panel 退役过渡：miss → L1 同指纹回退 + 回填 panel（oo/τ/co/oo_rank） | ✅ |
| P10 | 训练读 `load_training_matrix`：L1 优先（过渡期曾有 panel 二级） | ✅ |
| P11 | 停 panel 双写；stem meta 进 L1；增量只读 L1 | ✅ |
| P12 | 抽出 `panel_fingerprint`；panel 磁盘 IO 退役 | ✅ |
| 扫尾 | 删除 `panel_disk_cache` / `PANEL_CACHE_DIR`；去掉无用 `head_key` | ✅ |

---

## 10. 运维与排障

### 10.1 冷启动

首次拟合会写满 L1 +（oo）DayCube；第二次同宇宙应大量 hit L1。  
回测要吃到 L1，需先跑过对应 family 的拟合（或等价 collect 写穿）。

### 10.2 强制全量重算

```bash
# 关 L1/L2 临时回旧路径
export QUANTLAB_FEATURE_LAKE=0
export QUANTLAB_DAY_CUBE=0
export QUANTLAB_BATCH_SCORE=0

# 或抬 FEATURE_SCHEMA / 删对应 cache 目录
rm -rf data/cache/feature_lake data/cache/day_cubes
```

### 10.3 口径漂移

- 调仓与训练 ŷ 不一致：检查是否同 `universe_hash`、是否 `cross_section` 模型、是否误关 `BATCH_SCORE`。  
- 回测未命中 L1：看日志 `replay open day lake hits=… miss=…`；确认 family 与决策日对齐。  
- 增量未触发：中间 bar 被复权改写 → 预期全量；看 meta 是否有 `bars_fp`/`stem_fp`（旧分片无 stem 时需先全量写一次）。

### 10.4 磁盘

L1/L2 按 schema 分目录；宇宙变大时 DayCube 按 `universe_hash` 分叉，旧宇宙文件可定期清理冷 hash。

---

## 11. 设计取舍说明

| 决策 | 选择 | 理由 |
|------|------|------|
| L1 存储 | npy/npz 按 code（非先上 Parquet） | 与现 panel 栈一致、迁移成本低；接口已抽象 |
| 双宇宙 | live=`watching∪holdings`，训练=watching；cube 键含 hash | 避免小池偷用大池 μ/σ，也避免 live 为对齐扫 1000 票 |
| panel 磁盘退役 | IO stub；指纹独立模块 | 避免双仓；旧 import 不炸 |
| defer 仅调仓池 | 单票 tip/持仓仍即时 ŷ | 单票无同伴，即时分仍有展示价值 |
| 增量上限 16 根 | `tail_append_days(max_new=16)` | 覆盖常见 topup；更长缺口走全量更安全 |

---

## 12. 相关文档

- [行情更新、缓存与预处理](./bars-update-cache.md) §5.4–5.5、§10 环境变量  
- [design-spine.md](../design-spine.md) — 事实输入层含 L1/L2  
- [quant.md](../quant.md) — 日截面 Z 与物化层说明  
- [rebalance-logic.md](../rebalance-logic.md) — 横截面调仓消费方  
- [t0-logic.md](../t0-logic.md) — τ 截面宇宙与做 T  

---

**一句话**：用 **L1 单票增量特征仓 + L2 按日截面 Cube** 取代「每次临时拼截面」；训练写穿，回测/live/T0 复用，调仓先特征后单遍 ŷ，开关可回滚。
