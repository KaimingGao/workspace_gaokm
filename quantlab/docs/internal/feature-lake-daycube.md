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
2. **增量优先**：日线 topup 只追加末几根时只重算尾窗；lookback 变长且旧窗是新 bars 后缀时只补更早日期；禁止常态「整票推倒」。  
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

- `write_stock_panel` / `load_stock_panel` / `sync_from_matrix`：整票覆盖写穿（热路径先内存 `merge_matrix_rows`）  
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

进程内 `_MEMORY` 缓存当日 cube。open 的 τ 池字段由物化 `attach_slim` 并进已有立方的 `slim`（不改 μ/σ）。组 Cube 时 `enrich_day_matrix_cross_section` 用当日 `gap_pct` / `ret_open_to_tau` 补截面列（`sector_gap_breadth` / `theme_day` / `gap_vs_sector` 等）进 `X_raw` 与 μ/σ，并镜像 `z_τh_*` / `z_τc_*` / `z_co_*`。旧的 `persist_tau_pool_as_cubes` 会用只有 `gap_pct` 的矩阵盖掉特征立方，已删除。列数不足 2 的旧文件在覆盖检查里算缺失。

**宇宙键**：`universe_hash` 用本次请求的完整代码名单（观察池），不用「当天有特征行的子集」。缺行代码不进 `X_raw` / μ/σ。读写不一致时立方 miss。说明与 2026-10-19 的 800/798 分叉见 [daycube-universe-key.md](./daycube-universe-key.md)。

开关：`QUANTLAB_DAY_CUBE=0` 不落盘（内存构建仍可用）。

### 3.4 指纹

| 模块 | 职责 |
|------|------|
| `panel_fingerprint.py` | `bars_fingerprint` / `make_fingerprint` / `*_head_key`（L1 与增量共用） |
| Feature Lake | 训练矩阵与按日特征的唯一写穿 / 主读 |

桥接：`lake_bridge.load_training_matrix` / `sync_panel_to_lake` / `invalidate_l1_many`。

---

## 4. 增量与失效

### 4.1 尾窗增量与历史前缀（`incremental_panel.py`）

panel meta 额外写入：

- `bars_fp`：当前全窗 bars 指纹  
- `stem_fp`：去掉末根后的指纹  
- `end_date`：末根日期  
- `n_bars`：写入时 bars 根数（后缀匹配提示）

判定顺序：`tail_append_days` → 不成再 `history_extend_prefix_bars`：

| 情况 | 返回 | 行为 |
|------|------|------|
| `fingerprint(bars[:-k]) == meta.bars_fp` | k≤16 | 尾窗 `keep_dates` 重算 + merge |
| `fingerprint(bars[:-1]) == meta.stem_fp` | 0 | 仅改末根，同样修尾 |
| `fingerprint(bars[i:]) == meta.bars_fp` 且 `i>0` | 前缀根数 | 只算更早决策日（+接合 pad）再 overlay merge |
| 中间根变化 / 复权 / 口径变 | `None` | 全量 collect |

已接线：**oo / τc(open) / co / oo_rank**。  
`collect_*` 支持 `keep_dates`（oo 原有；τc open / co 已补）。

`write_stock_panel`：若盘上已有更长且同末日的真后缀面板，拒绝用短窗覆盖（防 lookback 误缩写穿）。

### 4.2 bars topup 失效与 L2 回收

`quant/research/bars_status.py` · `refresh_bars_only`：只对价量指纹变了的票调用 `invalidate_l1_many`。指纹未变不删 L1，也不动 L2。没有 L1 meta 的票不删。

删 L1 之前先读出该票各 family 的日期。这些日期在当前 schema 的每个宇宙、每个钟上的立方一并删除（`.npz` 与 `.meta.json`），并清掉进程内立方缓存。某个 family 读失败时，已经读到的日期照样作废；一个日期都读不到时，当前 schema 下的立方全部作废。schema 不符或缺少 meta 时仍从 ``.npz`` 读日期。删除只碰立方根目录里面的 `YYYY-MM-DD` 文件，符号链接只摘掉链接本身。

只有物化写入里的 L1 写穿（`write_stock_panel`）会在 `fingerprint` 或 `bars_fp` 相对旧 meta 有变化时，把特征行变了的日期交给同一套作废。两者都没变则不碰 L2。回测和拟合不写生产湖，也不拆立方。物化整段 L1 用 `l2_invalidation_batch` 把日期攒起来，结束时删一次盘，接着跑的 L2 会把缺的日期建回来。

**并行与作废**：日线 `tc`/`co`/`oo` 与 τ 网格冷路径走 **ProcessPool**（绕过 GIL）。spawn 不复制 contextvar。外层 `l2_invalidation_batch` 用 `_BATCH` 攒批，进入时关掉本进程 defer。进程工人 stale **defer**：正常返回 `(pack, stale)`；`MaterializeRequired` 则挂到 `payload['_deferred_l2_stale']`，父进程 `apply_stale_from_exc` 再 `note`。`drop_dates` 对大批日期先 `listdir` 再 `unlink`。

冷目录回收 `prune_cold` 发生在 `invalidate_l1_many` 之后，以及 `materialize_l2` 成功结束之后。保留观察池读取名单和拟合名单的 `universe_hash`；这次物化若用了另一份不少于 2 只的 `codes`，该哈希也保留到本轮结束。其余宇宙目录，以及 `v{FEATURE_SCHEMA}` 以外的版本目录，整棵删除。观察池或拟合名单读取抛错、或两边都不足 2 只时，不回收冷目录。任务取消或 L2 未成功时不回收。作废指定日期只丢掉这些日期的进程内立方。

开关：`QUANTLAB_DAY_CUBE_GC=0` 关闭日期作废和冷目录回收。`QUANTLAB_DAY_CUBE=0` 时本来不落盘，回收也不做。`python -m unittest` / pytest 未设置该变量时不扫本机立方。

---

## 4.5 物化管道 vs 消费管道（独立 L1/L2）

**定位**：L1 = 票级 raw（+y）；L2 = 日截面增广（μ/σ、slim），**不取代 L1**。

```text
日线更新 → 日线「物化 L1/L2」（oo/tc/co/oo_rank/tc_rank · open Cube + slim）
分钟 Ready → 分钟线「物化 L1/L2」（tc_HHMM 网格 · 按钟 Cube；oo/co 可空）
  → 调仓回测 / ŷ_oo 拟合 / 出分：硬要 oo + open
  → 做 T 回测 / 盘中 ŷ_τw（≥09:35）：硬要 tc_HHMM + 同钟 Cube
```

| 入口 | 覆盖硬依赖 | 缺覆盖提示 |
|------|------------|------------|
| 做 T 回测 | `tc_HHMM` + 分钟钟 L2 | 分钟线物化 |
| 调仓回测 / 出分 / ŷ_oo | oo/tc/co + open L2 | 日线物化 |

产品入口打开 ``QUANTLAB_MATERIALIZE_CONSUMER``。缺覆盖返回 ``materialize_required``；``request`` 按缺口分流（仅 τ → ``do_l1_tau_grid``；仅 open → ``do_l1``）。特征与截面 μ/σ 只读已写好的 L1/L2。分钟 L2 生产允许空 oo/co panel（空 dict ≠ 取消）。调仓出分读会话日 L1（开盘 ``oo/tc/co``，更晚再加 ``tc_HHMM``）和同钟 L2。

L1 存的是特征行和拟合标签 y，不存可成交价格。消费方仍读日线或分钟线的只有这三件事：

| 用途 | 读什么 |
|------|--------|
| 调仓回测成交 | 日 K 开盘价、指定钟的分钟价、涨跌停 |
| 拟合核对 | 用当前 K 线指纹对比 L1 meta 的 ``bars_fp``；对得上则用湖里的 X 和 y |
| 调仓出分下单 | 现价或开盘价，用来算股数 |

| 入口 | 说明 |
|------|------|
| `POST /api/quant/materialize` | 后台 Job；`do_l1_tau_grid` 写 τ 网格 L1；轮询 `GET /api/jobs/materialize-l1l2`。入参只有一个 **`lookback`**（默认 1000）：日线 L1 与 open L2 共用；分钟钟 L2 用内部近端短窗（`L2_MINUTE_LOOKBACK=60`） |
| `GET /api/quant/materialize/catalog` | family / clock / `suggested_tau_clocks` / `tau_grid` |
| `core/research/materialize/run_materialize` | 同步编排（L1 → τ 网格 L1 → L2） |

| 环境变量 | 默认 | 含义 |
|----------|------|------|
| `QUANTLAB_MATERIALIZE_CONSUMER` | 关 | 产品入口打开：缺 L1/L2 返回 ``materialize_required``，不现算特征、不冷建立方 |
| `QUANTLAB_MATERIALIZE_REQUIRE_CUBE` | 关 | 更早的窄开关：出分/warmup 的 Cube miss 不 ensure。产品路径以消费开关为准 |
| `QUANTLAB_FIT_USE_DAY_CUBE` | 关 | 消费开关关闭时，拟合/OOS 仍可单独打开以读 L2 |

**PIT（ŷ_τ*）**：拟合 CS 分组键为 ``date|τ``（`feature_standardize.normalize_cs_group_key`），同日多钟不混组；LGB / Ridge / live `_cs_z_horizon_feats` 均走该键。`apply_day_cube_mu_sigma_matrix` 按组钟读 L2。改完须**重拟合**窗头五头。

**轻量前缀**：`rescore_scores_at_fixed_prefix` 在 `open_snap` 已有分时跳过整票 `resolve_scores_for_code`（`_score_source=prefix_light`），只注入分钟包并重算辅头。开轮与 `close_band_scan` 经 `prefix_snaps_by_hm` 复用同 hm 前缀，避免二算。

**按钟 L2 / τ 网格 L1**：分钟线 Ready 后点「物化 L1/L2」；`do_l1_tau_grid` + `suggested_tau_clocks`；无对应 L1 则跳过该日。

**OOS 口径**：`predict_ridge_matrix` / `predict_p_up_rows` 在 `FIT_USE_DAY_CUBE=1` 时经 `cs_z_*_prefer_cube` 与训练同读 L2。

模块：`core/research/materialize/`（`catalog` / `l1_jobs` / `l2_jobs` / `cube_z` / `orchestrate`）。

---

## 5. 三条消费链路

### 5.1 训练（Fit）

产品拟合打开消费开关后：L1 指纹命中则用湖里的 X 和 y；未命中返回 ``materialize_required``，不 collect、不写湖、不冷建立方。截面 z 在消费开关或 ``FIT_USE_DAY_CUBE`` 打开时读 L2。覆盖检查用 ``scorable_bar_dates``：跳过特征垫窗（``min_history``≈``ALPHA_OO_PANEL_WINDOW``）和凑不满截面人数的早期日，避免把 lookback 之前的垫根误报成缺日。

不设该开关时（单测、脚本）仍走下面的旧路径：指纹命中返回；尾窗 ``keep_dates`` merge；全量 collect 后写 L1，oo 再物化 DayCube。

### 5.2 历史回测（paper_replay）

产品回测先按评分窗做 L1/L2 覆盖检查，不够则整次回测返回 ``materialize_required``。通过后按日从 L1 组装特征，μ/σ 只加载已有 L2。单票缺行（停牌、次新）跳过，不把整窗打成失败。成交价、涨跌停仍用已经载入的日 K / 分钟 K。不把 τ 池缺口写回立方。

不设消费开关时，L1 未命中才 ``score_window_as_item``，立方缺失才在内存里建 μ/σ。

### 5.3 Live 调仓（watching_matrix）

产品调仓（``PaperService.rebalance``）、数据中心观察摘要、持仓表 ŷ 打开消费开关：会话日 L1 组装特征，同钟 L2 提供 μ/σ，缺覆盖返回 ``materialize_required``。报价和落账价格另读行情或日线。观察摘要按整池宇宙哈希读立方，不按前端分批的子集。

不设消费开关时仍 ``score_one`` 抽特征，再 ``rescore_fitted_cross_section_batched``。``QUANTLAB_BATCH_SCORE=1``（默认）时 ``defer_cs_predict`` 自动开启。

### 5.4 T0 / 日循环

- `build_tau_pool_watching_holdings`：当日 cube slim 命中则跳过扫 bars  
- `run_daily_cycle`：启动时 `_try_hydrate_tau_xs_from_day_cube` 预热进程缓存  
- **开盘截面 ŷ**（`_align_item_to_watching_pool`）：`load_day_cube` 命中则单票 rescore，μ/σ 只信立方。消费开关打开时，立方 miss 不从 bars 灌同伴、也不 ensure。开关关闭时，miss 才用 L1 再 bars 建立方  
- **做 T 持仓回测**：产品入口先做覆盖检查，不够则 ``materialize_required``。开关关闭时，``run_t0_backtest_for_holdings`` 在逐票 walk 前 ``warmup_watching_day_cubes``（prefetch L1 + 评估窗 ensure/persist DayCube）。分钟成交路径仍读 5m  
- 前缀 ŷ_τc / ŷ_τ*：优先同一 DayCube `apply_cube_z_to_dicts`（须覆盖窗头特征，拒 slim gap-only）；cube miss 时池内 leave-one-in：剥开盘同伴分钟占位，稀疏列保留 stamped（池外不把持仓并进 μ/σ）  
- `rescore_fitted_cross_section`：`day_cube` 宇宙 ≥2 时允许 `len(items) >= 1`

---

## 6. 单遍截面打分

模块：`core/research/batch_score.py` + `cross_section_rescore.py` + `return_score.py`

1. `features_by_code_from_items`：合并 `sub_scores`、`features_tau`、`features_co`、`raw_*`  
2. `ensure_day_cube_for_items` → 落盘 + 内存  
3. `rescore_fitted_cross_section(..., day_cube=)`：  
   - ŷ_oo：`apply_predicted_scores(..., day_cube=)`  
   - ŷ_τ / ŷ_co：`apply_cube_z_to_dicts` 后 predict  
   - 有可用 DayCube 时允许单票 items（T0 对齐快路径）

与 `cross_section_zscore_dicts` 在同批 raw 上应对齐（单测覆盖）。

---

## 7. 环境变量与路径

| 变量 | 默认 | 含义 |
|------|------|------|
| `QUANTLAB_FEATURE_LAKE` | 开 | `0` 关闭 L1 |
| `QUANTLAB_FEATURE_LAKE_DIR` | `paths.FEATURE_LAKE_DIR` | L1 根 |
| `QUANTLAB_DAY_CUBE` | 开 | `0` 关闭 L2 落盘 |
| `QUANTLAB_DAY_CUBE_DIR` | `paths.DAY_CUBE_DIR` | L2 根 |
| `QUANTLAB_DAY_CUBE_GC` | 开 | `0` 关闭 L2 日期作废与冷目录回收 |
| `QUANTLAB_BATCH_SCORE` | 开 | `0`：不建 cube、调仓不 defer，走旧双遍 |
| `QUANTLAB_MATERIALIZE_CONSUMER` | 关 | 产品入口打开后只读 L1/L2；缺覆盖返回 ``materialize_required`` |
| `QUANTLAB_FIT_USE_DAY_CUBE` | 关 | 消费开关关闭时，拟合/OOS 仍可单独读 L2 |
| `QUANTLAB_MATERIALIZE_REQUIRE_CUBE` | 关 | 更早的窄开关：Cube miss 不 ensure。产品路径以消费开关为准 |

路径常量：`core/paths.py` → `FEATURE_LAKE_DIR` / `DAY_CUBE_DIR`。

---

## 8. 模块与测试索引

### 8.1 核心模块

| 模块 | 职责 |
|------|------|
| `core/research/feature_registry.py` | schema / universe_hash |
| `core/research/feature_lake.py` | L1 读写 |
| `core/research/day_cube.py` | L2 构建/落盘/z |
| `core/research/day_cube_gc.py` | L2 过期日期作废、冷宇宙与旧 schema 回收 |
| `core/research/lake_bridge.py` | 训练读 L1、写穿、失效 |
| `core/research/panel_fingerprint.py` | bars / make 指纹 |
| `core/research/incremental_panel.py` | 尾窗判定与 merge |
| `core/research/batch_score.py` | 池级 cube + batched rescore |
| `core/research/lake_replay.py` | 回测 L1 hydrate + coverage 预热；盘中前缀 `tc_HHMM` |
| `core/research/materialize/` | 独立 L1/L2 Job；`horizon_names` / `cube_z` / flags / catalog / `l1_jobs.materialize_l1_tau_grid` |
| `core/signal/score_stock.py` | `defer_cs_predict` |
| `core/signal/cross_section_rescore.py` | day_cube 版 z |
| `core/signal/return_score.py` | OO predict 吃 day_cube |

### 8.2 接线点

| 路径 | 文件 |
|------|------|
| oo collect | `oo_ridge_compact.py` |
| τc / co collect | `panel_matrix.py` |
| oo_rank | `oo_rank_panel.py` |
| ŷ_τ* / LGB | `horizon_ridge.py` · `horizon_prob.py` · `horizon_lgb.py` |
| 回测 | `paper_replay.py` · `replay_scoring.py` |
| 调仓 | `watching_matrix.py` |
| T0 | `t0/score_policy.py` · `t0/slots.py`（扫槽复用） |
| 日循环 | `paper/cycle.py` |
| bars 失效 | `quant/research/bars_status.py` |
| 物化 API | `quant_service_factors.py` · `web/routers/quant_cluster.py` |

### 8.3 测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_feature_lake.py` | L1 写读 / miss / merge 后写穿 |
| `tests/test_day_cube.py` | L2 z 对齐 / tau slim / 宇宙 miss |
| `tests/test_incremental_panel.py` | stem / 多日追加 / oo 增量 / lake hydrate |
| `tests/test_batch_score_cube_oo.py` | OO cube z ≡ standardize |
| `tests/test_defer_cs_predict.py` | defer 不写 ŷ、保留特征 |
| `tests/test_materialize_l1l2.py` | catalog / flags / orchestrate / 按钟 cube_z |
| `tests/test_cs_group_pit.py` | `date|τ` PIT · prefix_light |

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
| P6 | replay 暖路径：L1 进程缓存按日切片；**须 oo 行**才跳过 score_window；lake co 直用 | ✅ |
| P7 | 分钟 τ L1 clock 分族（`tc_HHMM`）；命中路径省昨收窗（y_hl 已下线） | ✅ |
| P8 | replay `fill_clock≠09:30`：预热 `tc_HHMM`，前缀 ŷ_τc 优先 L1 hydrate | ✅ |
| P9 | panel 退役过渡：miss → L1 同指纹回退 + 回填 panel（oo/τ/co/oo_rank） | ✅ |
| P10 | 训练读 `load_training_matrix`：L1 优先（过渡期曾有 panel 二级） | ✅ |
| P11 | 停 panel 双写；stem meta 进 L1；增量只读 L1 | ✅ |
| P12 | 抽出 `panel_fingerprint`；panel 磁盘 IO 退役 | ✅ |
| 扫尾 | 删除 `panel_disk_cache` / `PANEL_CACHE_DIR`；去掉无用 `head_key` | ✅ |
| M0 | 独立物化 Job + UI；`REQUIRE_CUBE` / `FIT_USE_DAY_CUBE` | ✅ |
| M1 | PIT `date|τ`；prefix_light；扫槽 `prefix_snaps_by_hm` | ✅ |
| M2 | oo/τc/co/ŷ_τ* 拟合读 L2；按钟 Cube（`date|τ`→clock；H3 起禁静默 open） | ✅ |
| M3 | 按钟 L2 / OOS 与 `FIT_USE_DAY_CUBE` 同口径；τ UI 已迁分钟线 | ✅ |
| M4 | 拟合主路径统一 `cs_z_*_prefer_cube`（oo/τc/co/ŷ_τ*/LGB） | ✅ |
| H0 | 契约：`horizon_names` / catalog `tau_grid` | ✅ |
| H1 | `materialize_l1_tau_grid`；分钟线「物化 L1/L2」 | ✅ |
| H2 | L2 按钟 `require_horizon_names` + UI 联动 | ✅ |
| H3 | 禁静默 open + 窗头 stitch L1（`_try_stitch_l1_tau_panel`） | ✅ |
| H4 | live `_cs_z_horizon_feats`：τ Cube 覆盖失败 → leave-one-in | ✅ |
| H5 | 测试 / §10.1b 窗头分支 / ASSET_V | ✅ |

---

## 10. 运维与排障

### 10.1 冷启动

首次拟合会写满 L1 +（oo）DayCube；第二次同宇宙应大量 hit L1。  
回测要吃到 L1，需先跑过对应 family 的日线物化。拟合只读已有面板，不覆盖生产湖。

### 10.1b L2 拟合口径切换（推荐顺序）

改完 PIT / Cube / `FIT_USE_DAY_CUBE` 后按序做，避免旧 β 混新 z：

1. **日线**：研究枢纽增量补齐（或强更）。  
2. **分钟**：做 T / ŷ_τ* 须 5m Ready（增量或强更）。  
3. **物化**（手动）  
   - **日线**「物化 L1/L2」：oo/tc/co + open L2；  
   - **分钟线** Ready 后「物化 L1/L2」：τ 网格 `tc_HHMM` + 按钟 Cube（Ready 不足禁用）。  
4. **开开关**（进程级，改后重启 Web / worker）：

```bash
export QUANTLAB_FIT_USE_DAY_CUBE=1
# 可选：Cube miss 禁止 bars 冷建
# export QUANTLAB_MATERIALIZE_REQUIRE_CUBE=1
```

5. **重拟合**：ŷ_oo → ŷ_τc / ŷ_co → 窗头 ŷ_τ30…τ90（及 LGB 影子头若在用）。  
6. **回测**：做 T / 调仓 replay；tip 各钟不应塌常数；日志 `horizon cs-z cube hit … clock=τ`（非一律 open）。

关回旧矩阵 CS-z：`unset QUANTLAB_FIT_USE_DAY_CUBE`（或 `=0`）后重拟合即可。

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

- 调仓与训练 ŷ 不一致：检查是否同 `universe_hash`、是否 `cross_section` 模型、是否误关 `BATCH_SCORE`、是否只开了 `FIT_USE_DAY_CUBE` 未重拟合。  
- 回测未命中 L1：看日志 `replay open day lake hits=… miss=…`；确认 family 与决策日对齐。  
- 增量未触发：中间 bar 被复权改写 → 预期全量；看 meta 是否有 `bars_fp`/`stem_fp`/`n_bars`（旧分片无 stem 时需先全量写一次）。短窗→长窗应走历史前缀；若仍全量，核对末日是否一致、旧 `bars_fp` 是否为新 bars 后缀。
- 物化仍每票扫盘作废：确认在 `l2_invalidation_batch` 内，且进程工人 defer→父进程 `apply_deferred_stale` / `apply_stale_from_exc`（`MaterializeRequired` 挂 `_deferred_l2_stale`）。  
- 拟合 Cube miss 全回退矩阵：先物化 open（及所需 τ 钟）；确认 `QUANTLAB_DAY_CUBE` 未关、宇宙 hash 与 watching 一致。

### 10.4 磁盘

L1/L2 按 schema 分目录。观察池名单变化后，旧 `universe_hash` 目录不再被读取。`prune_cold` 会在日线失效和成功的 L2 物化结束时删掉这些冷目录，以及旧 schema 目录。当前观察池（读取键与拟合键）下的立方只按过期日期删除，不整目录清掉。

删掉的日期会使覆盖检查返回 `materialize_required`。用当前观察池再跑一次对应钟的 L2，快路径才会回来。整库作废仍是删 `data/cache/day_cubes`。临时停掉自动回收：`QUANTLAB_DAY_CUBE_GC=0`。

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

## 13. ŷ_τ\* 按钟 L1/L2 完整落地方案（H 轨）

> **目标**：ŷ_τ30…τ90 的截面 z **按决策钟 τ**（同日同伴、同一信息集），主路径吃 **分钟钟 L2**，不再依赖「整日 open Cube」或仅矩阵现算。  
> **非目标**：单票沿分钟时序做 z；把五头标签塞进 open 日 Cube。

### 13.1 现状（H0–H5 已落地）

| 层 | 现状 |
|----|------|
| 拟合分组 | `date|τ` PIT；窗头优先 stitch L1，miss 才全量 5m |
| L1 | open `tc` + 分钟线「物化 L1/L2」写 `tc_HHMM`（τc∪窗头列） |
| L2 | 日线物化 open；分钟线物化按钟（`require_horizon_names`，**不含 open**）；窗头网格自 09:35；`date|τ` 禁静默 open |
| live | `_cs_z_horizon_feats`：τ Cube 覆盖失败 → 同钟 L1，再 leave-one-in（不读 open）。键协议见 [daycube-universe-key.md](./daycube-universe-key.md) |
| 物化 UI | 日线 open · 分钟线 τ（Ready 门控；仅手动） |

### 13.2 口径契约（冻结）

1. **CS 组** = `(trade_date, τ)`，键 `YYYY-MM-DD|HH:MM`（开盘→仅日期 / `open`）。  
2. **μ/σ 宇宙** = 训练 watching / live `watching∪holdings`（与现 DayCube `universe_hash` 一致）。  
3. **禁止**同日跨 τ 混组；禁止用「未来钟」同伴分钟占位进 μ/σ（已有 strip / leave-one-in 纪律保留）。  
4. **L2 主键** = `(date, universe_hash, clock)`，`clock∈{open}∪{HHMM}`；窗头 **主读 `clock=τ`（≥09:35）**，禁止把 open 当主口径；产品消费下 miss → `materialize_required`（做 T 提示分钟线物化；调仓提示日线物化）。  
5. **五头共享特征行、分头标签**：同一 `(code, date, τ)` 特征只存一份；`y_t30…y_t90` 为可选多标签列（或拟合时现算标签、湖只存 X）。

### 13.3 存储设计

#### L1（票级）

**推荐（少分叉）**：扩展现有按钟族，不新建五套 family。

| 项 | 约定 |
|----|------|
| Family | 复用 `tc_HHMM`（`family_for_tc(tau_hm=τ)`）；开盘仍 `tc` |
| 行键 | `code × date`（每钟一个 family 目录，与现网一致） |
| `names` | ŷ_τc 列 ∪ 窗头列（`Z_TH_FIT_FEATURES` + `raw_ath_*` 等，去重） |
| `y` | **可选**：不强制五头 y 进湖；标签仍可由分钟在拟合时算（降体积）。若进湖：并列 `y_t30…y_t90` 或 meta 多 y |
| 指纹 | 日线 stem + **该日≤τ 分钟前缀**指纹（分钟变更 → 该钟 L1 miss） |
| 写穿点 | `collect_tau_compact(..., tau_hm=τ)` / 窗头 panel enrich 成功后 `sync L1`；物化 Job 新阶段 `materialize_l1_tau_grid` |

**备选（仅当 τc 与窗头列冲突严重）**：`th_HHMM` 专用族；live 组装时 merge `tc_*`+`th_*`。默认不走这条。

#### L2（截面）

| 项 | 约定 |
|----|------|
| 构建输入 | 该日该钟 L1 行 → `ensure_day_cube_for_items(..., clock=τ, universe_codes=请求的完整名单)`。哈希不随缺行缩小 |
| `names` | 必须覆盖窗头入模列（拒 slim gap-only；与现 prefix CS-z 拒 slim 一致） |
| 物化 | `clocks = ["open"] + suggested_tau_clocks`；缺 L1 跳过并计数 `days_skipped`；`align` 后矩阵 CS enrich 再算 μ/σ |
| 拟合读 | `cs_z_*_prefer_cube`：`date|τ` → `load_day_cube(day, clock=τ)`；miss → **不**默默用 open 当主成功（可配置 `fallback_open`，窗头默认 **关** 或打标 `open_fallback`）；旧 Cube 缺列 → 该列桶内 z |

> 窗头与 ŷ_τc(open) 分流：open Cube 继续服务 oo/τc(open)/co；**τ≠open 的窗头以按钟 Cube 为准。**

### 13.4 管道改造

```text
日线齐 → 分钟齐（5m Ready）
  → H1 物化 L1 τ 网格（观察池 × 训练钟 × tc_HHMM，列=τc∪窗头）
  → H2 物化 L2 按钟（读 L1 → align → CS enrich → Cube，拒 slim）
  → H3 窗头拟合：L1 hit 则 stack(date|τ) → prefer_cube(z) → dual fit → promote
         miss 行 → 5m 现算 + 写穿 L1（增量）
  → H4 live/T0/BT：前缀特征 → Cube(clock=τ) z → load t30…t90 模型
  → H5 开关与护栏：FIT_USE_DAY_CUBE=1；REQUIRE_CUBE 可选；单测 PIT+列覆盖
```

| 阶段 | 内容 | 状态 |
|------|------|------|
| **H0** | 契约：`horizon_l1_feature_names` / catalog `tau_grid` | ✅ |
| **H1** | `materialize_l1_tau_grid` + API `do_l1_tau_grid`；分钟线「物化 L1/L2」 | ✅ |
| **H2** | L2 `require_horizon_names`；按钟 Cube + 矩阵 CS enrich（open/分钟） | ✅ |
| **H3** | `date\|τ` 禁静默 open；窗头 `build_tau` 优先 stitch L1，relabel 分钟补标签 | ✅ |
| **H4** | live `_cs_z_horizon_feats`：τ 覆盖失败不静默 open → leave-one-in；hit 打 debug | ✅ |
| **H5** | §10.1b 窗头分支；`test_materialize_l1l2` / JS guards；UI 色调；Job stale 90min | ✅ |

### 13.5 API / UI

| 入口 | 行为 |
|------|------|
| `POST /api/quant/materialize` | 增 `do_l1_tau_grid: bool`；`clocks` 已支持；families 不变 |
| catalog | `suggested_tau_clocks`；`flags`；说明「τ 网格 L1 须分钟 Ready」 |
| Job 槽 `materialize-l1l2` | stale/stuck **5400s**；`allow_zero_progress`（宏步钉 current=0 不按零进展误杀）；`try_start` 原子开槽；L2 跳过整池 Feature Lake prefetch |
| 日线卡 | 两块：L0 日 K 仓 + 物化卡「物化 L1/L2」= open L1（oo/tc/co）+ open Cube |
| 分钟线卡 | 两块：L0 5m 仓 + 物化卡 Ready 后「物化 L1/L2」= τ 网格 L1 + 按钟 Cube |
| τ 网格进度 | 文案 `L1 τ 网格 HH:MM（k/N）`：`N`=`suggested_tau_clocks` 钟数（默认 **18**=09:35…11:00 每 5m，不含 open/09:30）；Job `current/total` 仍钉宏步（如 0/2） |
| 状态条 | L0 与物化各有 chip+msg+progress；**蓝**=增量/强更，**黄**=物化（对齐按钮 primary/warn） |
| 刷新接管 | 页加载 **先** `resumeIfRunning`（物化条）**再** `refreshStatus`（L0 覆盖），未接管再 `syncJobSlot`；running 后台挂 wait；6h 内 failed 也画；`clearJobPanels` 清 sticky |
| waitJob | 见过本 id 后槽位被换 →「已被新任务替换」；前端等待 open **90min** / τ **150min**（勿再用 45min）；等满且槽仍 running → 提示刷新接管（勿标 FAIL） |
| waitMinuteJob | 增量 **60min** / 强更 5m **120min** 为软上限（勿再用 30min）；心跳不足 90s 则继续轮询，硬上限 **8h**；心跳停了且已过软上限 → WAIT + 刷新接管（勿标 FAIL）；页加载 `syncJobSlot` 可挂接 |
| open↔τ 同槽 | 对侧 running 时 POST 返回 `conflict`；UI **等待对侧结束再自动重试一次**（勿硬 FAIL） |
| 进度 | 宏步骤钉 `step/tot_steps`；子阶段只改 message；UI 隐藏 total>8 的票级微进度；失败文案用 `--danger` |
| **Track** | `result.track`（`build_materialize_track`）：`id`/`label`/`ok`/`elapsed_s`/`steps_ok`/`schema`/`headline` + `stages[]`（`id`/`status`/`elapsed_s`/`depends_on`/`metrics`）；Job 落盘与状态条优先展示 `headline` 与阶段链 |
| 失败配色 | 增量/强更失败：蓝族 + 红字；物化失败：黄族 + 红字 |
| 耗时参考 | 日线 open L1+L2 · 池≈800：约 **5–8 min**；τ 网格首轮（18 钟 L1+L2）可到 **数小时**；L1 指纹增量后会快很多；L2 宇宙键对齐后同池再跑可命中跳过（见 [daycube-universe-key.md](./daycube-universe-key.md)）；强更 5m · 800 票约 **30 min**（4 并发 · 开工间隔 2s；已 Ready 跳过） |
| 无自动物化 | 日线/分钟线均手动；无 auto-materialize 勾选 |
| 前缀滑条 | **ŷ_τc** 独立（默认 09:30）；**ŷ_τ30…90** 共用（默认 11:00）；localStorage / 拟合恢复按组隔离 |
| ŷ_τc 拟合进度 | `解析宇宙` → `拉日线` →（可选）`读5m` → `面板 k/N` → `截面广度` → `Ridge`；槽 `allow_zero_progress` |
| 窗头拟合 Job | 进度区分 `L1 hit / 5m miss / Cube hit`；CS-z 读 L2 须 `FIT_USE_DAY_CUBE=1` |

### 13.6 运维顺序（窗头专用）

1. 日线齐 → **分钟齐**（必须）。  
2. 物化：open L1/L2（主头）→ **τ 网格 L1** → **τ 钟 L2**。  
3. `export QUANTLAB_FIT_USE_DAY_CUBE=1`（重启）。  
4. 重拟合 t30…t90（及 LGB）并 promote。  
5. 做 T / 回测；日志确认 `clock=τ` Cube hit，而非一律 open_fallback。

### 13.7 风险与回滚

| 风险 | 缓解 |
|------|------|
| 磁盘：票×日×钟 | 只物化训练网格钟；保留上限与冷 hash 清理同 §10.4 |
| 分钟变更失效面大 | 指纹含分钟前缀；topup 分钟后 `invalidate` 对应 `tc_HHMM` |
| 列膨胀拖慢 Cube | `names` 白名单；slim 禁止用于窗头 z |
| 口径漂移 | 窗头默认不 open_fallback；或报告 `zscore_clock_source` |
| 回滚 | `FIT_USE_DAY_CUBE=0` → 矩阵 `date|τ` CS-z；物化开关不影响旧 5m 现算路径 |

### 13.8 明确不做（本轨）

- 不为 t30/t45/… 各建独立 L1 family（避免五倍目录）。  
- 不把窗头 μ/σ 写进 open Cube 冒充按钟。  
- 不在无 5m Ready 时假装 τ 网格物化成功。

---

**一句话**：用 **L1 单票增量特征仓 + L2 截面 Cube** 取代「每次临时拼截面」——开盘头吃 **open**；ŷ_τ\* / 做 T 前缀吃 **按钟（分钟）L2**；训练写穿，回测/live 复用，开关可回滚。
