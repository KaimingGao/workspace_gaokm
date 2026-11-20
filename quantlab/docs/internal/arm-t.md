# Arm·T 技术方案

研究轨 **Scheme LT**（LightGBM vs Transformer）的右臂：双支路晚融合（序列 Transformer ∥ 因子 MLP）。对照左臂 **Arm·L**（现网 LightGBM · 全量）。`live_allowed=False`，不写生产 score。

UI 入口：`/yhat-t`（**Transformer 工厂**；旧 `/yhat-tf` 重定向）。契约见 [quant-ui.md · 模型中心](../quant-ui.md#yhat-t--yhat-l-模型中心双工厂)。  
长期拆分（工厂拟合 vs 枢纽注册 live/回测）见 **[model-factory-hub.md](model-factory-hub.md)**。跑次版本在 `live/yhat_t/versions/{head}/`（Arm·L 为 `yhat_l`）；选中只回看参数、指标和日志。删除只去掉该条归档和日志副本，不改磁盘上的当前拟合，不写 live。若删掉的是概览正在指示的那次失败，概览不再显示这次失败；列表里还有版本时，改看剩下的最新一条。

---

## 1. 定位

| | **Arm·L** | **Arm·T** |
|--|--|--|
| 模型 | 现网 LightGBM / LambdaRank | `SeqFactorTransformer` |
| 输入 | 因子面板全量列 | 日线短序列 + gap +（可裁列的）因子支路 |
| 样本 | **固定全量**（对齐 live） | 默认相对训练段 **10% subsample** |
| 角色 | Holdout **硬闸基线** | 候选；须过相对 L 的 CS rank IC 硬门 |
| 归一 | live `fit_*_lgb_matrix` 或立方 CS-z | 因子支路同 **L2 立方截面 CS-z**；序列侧 train 分位 clip |

一次 Holdout 只训一个 head（`oo` / `tc` / `oo_rank` / `tc_rank`）。`/yhat-t` 只跑 Arm·T，`/yhat-l` 只跑 Arm·L；双臂只留在 CLI `--factory LT`。seed=42。

晋级边界（页面草稿/晋级已下线；正式对照与 live 禁令仍有效，见 [model-factory-hub.md](model-factory-hub.md)）：

> 正式对照须 `gate_pass` + T full 因子 + T 采样 100% + 共享评测；**禁止 live / active**。工作配置随拟合意图隐式同步，不再暴露「存草稿 / 晋级研究」。

---

## 2. 端到端数据流

```text
日线 bars (lookback)
    ├─ build_seq_panel  → SeqPanel  X(N,L,F), gap(N,), y, dates, codes
    └─ build_factor_panel → FactorPanel 全量 (aoo_full / atc_full)  ← Arm·L 用这份
            │
            ▼
    slice_arm_t_panel （仅 T：aoo_core / atc_core 可选）
            │
            ▼
    align_seq_and_factor  按 (code,date) 内连接；y 以 seq 为准
            │
            ▼
    丢掉早于第一份可读 open 立方的行（起点之后的缺日留下）
            │
            ├─ 共享日历 shared_cal → resolve_ridge_split (Holdout 窗)
            │
            ├─ Arm·L：全量 fac 训；OOS 缩到 shared_keys
            │
            └─ Arm·T：_run_arm_t
                      cs_z_matrix_prefer_cube(fac, clock="open")  ← 剩下的面板缺立方即失败
                      train_seq_factor_transformer (seed=42)
                      predict_seq_factor_transformer on holdout
            │
            ▼
    daily_cs_ic_report / sign_hit → hard_gate_vs_baseline(L, T)
            │
            ▼
    summary.json + ingest_holdout_summary → desk last metrics
```

**关键不变量**

1. 因子面板 **先建全量**：Arm·L 对齐 live；core / full 只在 Arm·T 开跑前裁列。构建日志按当前臂写明谁用全量、T 裁成哪一包，T 单臂不标成 Arm·L。
2. Gate / OOS 用 **seq∩fac 共享键** 与同一 `eval_start`，避免 L/T 评测窗错位。
3. Arm·L 训练仍可吃共享日历末端之前的全量 fac 早期日（含 seq 尚未覆盖的日）。
4. `align_seq_and_factor`：同 `(code,date)` 标签偏差 > `1e-3` 时以 **seq.y 覆盖 fac.y**。
5. 对齐后只丢掉早于第一份可读 open 立方的行。起点与截面标准化同一读法（能列出 μ/σ）；跨度开头读失败的文件跳过，不算起点。起点当天及之后的缺日留下，截面标准化报 `MaterializeRequired`，**不做**列标准化回退。一份可读立方都没有时面板原样留下；日期全部早于起点时清空面板。共享日历在这次丢行之后再切，Holdout 末端不变。

---

## 3. 双支路晚融合

实现：`core/research/yhat_transformer/model.py` 的 `SeqFactorTransformer`。两条支路各自算完，只在最后一层线性头之前拼接。序列编码器看不到因子，因子 MLP 也看不到序列；中间没有交叉注意力。

```text
x_seq (B, L, F) ── Linear(F→d_model) ── Pre-LN Encoder × nlayers
                                      └── 末步 h[:, -1, :] (B, d_model)
gap   (B,)     ────────────────────────── concat ── seq_h (B, d_model+1)
                                                              \
x_fac (B, d_factor) ── LayerNorm ── Linear ── GELU ── Dropout ── fac_h (B, fh)
                                                              /
                                    cat ── LayerNorm ── Linear ── ŷ (B,)
```

标量头没有激活。回归头外面用 SmoothL1；排序头外面用 LambdaRank（日内减均值）+ 日 z 保值，网络本身不包 softmax / sigmoid。

### 3.1 序列支路

`SeqTransformer.encode`，`with_head=False`（不建未使用的死头）。

| 步 | 张量 | 说明 |
|--|--|--|
| 输入 | `(B, L, F)` | L = 决策日前 L 个完整日；F 由 `seq_feat_set` 决定，默认 seq16 |
| 投影 | `(B, L, d_model)` | 一层 `Linear`。**不加位置编码**，时间顺序只靠 token 排列和末步池化 |
| 编码 | 同形 | `TransformerEncoder` × `nlayers`。每层 Pre-LN（`norm_first=True`）：自注意力残差，再 GELU 前馈残差；`batch_first=True`。无 padding mask，窗口内全注意力 |
| 池化 | `(B, d_model)` | 只取末步 `h[:, -1, :]`，不做均值池化、不设 CLS |
| 缺口 | `(B, d_model+1)` | `gap` 是今开/昨收 − 1 的原始标量，**不进编码器**，拼在末步后面。缺省时填 0 |

前馈宽度 `dim_feedforward = max(64, d_model×2)`。`d_model` 必须能被 `nhead` 整除。进 `Linear` 之前的 1%/99% 分位 clip 在训练预处理里做，不在这个模块里。

### 3.2 因子支路

截面向量，不是序列。列已经过 L2 立方 CS-z（见 §5.3）。

```text
(B, d_factor) → LayerNorm → Linear(d_factor → fh) → GELU → Dropout → (B, fh)
```

`fh = max(16, factor_hidden)`，desk 上 `factor_hidden` 对齐 `d_model`。只有这一层隐层，没有按因子各出一条系数。

### 3.3 融合头

```text
(B, d_model + 1 + fh) → LayerNorm → Linear(· → 1) → squeeze → (B,)
```

默认 `d_model=64`、`fh=64` 时，融合维是 **129**（64 维序列末步 + 1 维 gap + 64 维因子隐层）。

| 参数 | 默认（CLI / desk） | 说明 |
|--|--|--|
| `d_seq_in` | 16（seq16） | 来自面板 `X.shape[-1]` |
| `d_factor` | 裁列后宽 | 必填；core / full 只改这一维 |
| `d_model` | **64** | 须被 `nhead` 整除 |
| `nhead` | 4 | 每头 `d_model / nhead` |
| `nlayers` | **3** | 只堆在序列支 |
| `dim_feedforward` | `max(64, d_model×2)` | 默认 128 |
| `factor_hidden` / `mlp_hidden` | 对齐 `d_model` | 因子支；`max(16, …)` |
| `dropout` | 0.1 | 编码器与因子支 Dropout 共用 |
| 融合维 `head_in` | `d_model + 1 + fh` | 默认 129 |

模型中心拉起的成功拟合，在 Holdout 预测之后把 CPU `state_dict` 和序列 clip 写入 `{版本号}.weights/`（`state.pt` + `meta.json`）。直接跑脚本、没有 `YHAT_FACTORY_RUN_ID` 时不写。页面回看仍只有结构、训练设置和 OOS，没有逐因子表。加载用 `load_factory_weights`。本页不写研究套 / 执行套。

---

## 4. 序列面板（PIT）

实现：`seq_dataset.py`。

**决策日 T**

- 序列 = 完整日 `[T−L … T−1]`（今收 / 今高 / 今低 / 今量 **不进** 序列）
- 入参按日期排序。窗口内日期必须严格递增，且每一根都早于 T；乱序不会把更晚的 K 线切进特征
- 附加通道 `gap_t` = 今开 / 昨收 − 1（百分点）
- 标签 oo：`open[T+h]/open[T]−1`
- 标签 τc：同日 `close/open−1`（本期 τ=开盘；horizon 忽略）

| 项 | 默认 |
|--|--|
| `seq_len` L | 20（≥5） |
| `feat_set` | **seq16**（嵌套：seq8 ⊂ 16 ⊂ 32 ⊂ 64） |
| 缓存 | `data/experiments/yhat_transformer/cache/seq_*.npz` |
| workers | `SEQ_PANEL_WORKERS` 默认 4；股票 &lt;16 则 1 |

seq16 在经典 8 维价量摘要上追加影线 / 位置 / 量变 / 缺口回补；不做 lag 拼维（时间轴 L 已覆盖多日）。

---

## 5. 因子支路

实现：`factor_panel.py`。

### 5.1 构建（全量，供 L 与后续裁切）

| Head | 构建 | 指纹 |
|--|--|--|
| oo / oo_rank | `build_oo_factor_panel` → 枢纽 `assemble_oo_panels` | full_aoo |
| tc / tc_rank | `build_tc_factor_panel` → 枢纽 `collect_tau_compact` | atc_full |

磁盘缓存键是 `facv3` 加每只股票的价量指纹（`bars_fingerprint`）。τc 的 kind 是 `tc_compact_v2`（`tc_compact` 是丢掉长湖前缀的短面板，不再命中）。固定 lookback 下滑窗、复权或中间一根价量改动都会 miss。oo 单票失败由枢纽组装抬异常，不落半成品。缺测在面板里保持 NaN。

`assemble_oo_panels` 在非物化路径会尝试把 oo 面板落成 open 立方。面板没有 τc 列，已有 open 立方时不覆盖。截面 z 缺的是列时，报错给出本地跨度，并要求重跑日线物化补缺列的日子。

### 5.2 `slice_arm_t_panel`（仅 Arm·T）

> Arm·L 始终全量，不走这里。点了 core 但裁不窄时 **显式 ValueError**，避免静默退回全量。

| Head | desk 默认 | CLI | Arm·T 列宽 |
|--|--|--|--|
| oo / oo_rank | `full_aoo=False` | 无 `--full-aoo` | **aoo_core**（GAP+YK+LAG 消融） |
| oo / oo_rank | `full_aoo=True` | `--full-aoo` | aoo_full |
| tc / tc_rank | `full_atc=True` | 默认 | **atc_full** |
| tc / tc_rank | `full_atc=False` | `--atc-core` | atc_core（GAP+YK+LAG） |

裁列只改列宽，**不改** CS-z 路径。

### 5.3 CS-z（与 Arm·L 对齐）

`_run_arm_t` 内：

1. `fac.X` 拷出 float64 后置空面板，减轻峰值 RSS
2. `cs_z_matrix_prefer_cube(..., clock="open")`
3. 排序头再裁 ±`LAMBDARANK_Z_CLIP`（对齐 LambdaRank）；回归头不裁
4. 非有限值 → 0（截面均值），压 float32 进 Transformer。缺测在这一步之前保持 NaN，不先写成原始 0

训练函数内 **不再** 做训练段列标准化。

---

## 6. 训练协议

实现：`train.py` · `train_seq_factor_transformer`；编排：`_run_arm_t`。

| 项 | 行为 |
|--|--|
| **seed** | 固定 **42** |
| **epochs** | 默认 **12**；跑满，不提前停 |
| **保存** | 最后一个 epoch 的权重 |
| **eval** | 每个 epoch 结束后用 Holdout 做一次 `no_grad` 测试（Rank IC / hit / Top10% y；排序头另打 NDCG@30）。不反传，不选权重，不改学习率日程 |
| **optimizer** | AdamW，`lr=1e-3`，`weight_decay=1e-4` |
| **LR** | cosine + warmup；`TRANSFORMER_WARMUP_STEPS` 默认 100；`min_ratio=0.01` |
| **grad clip** | 1.0 |
| **batch** | 默认 32；范围 16–1024 |
| **subsample** | `t_sample_frac` 默认 **0.1**；≥1 → 不截断。点损失行级；排序头 **整日组**，一组放不进剩余名额即停。抽样只作用在训练日，Holdout 不进训练 |
| **序列预处理** | train 行 1%/99% 分位 clip（最多 2 万行估分位） |
| **线程** | `TORCH_NUM_THREADS` 默认 **4**；interop=1；BLAS/OMP 仍为 1 |
| **取数** | numpy 切片再 `as_tensor`（避免大张量高级索引 OpenMP SIGSEGV） |

### 损失

| task | loss | 说明 |
|--|--|--|
| regression（oo / tc） | `smooth_l1`（β=1） | |
| rank（oo_rank / tc_rank） | LambdaRank + 日 z 保值 | 序：日内减均值后的 LambdaRank；值：`λ=0.3`·SmoothL1(ŷ, 当日标签截面 z±3) |

标签与 Arm·L 相同：收益越高名次越大，`label_gain` 就是名次本身。权重按当前排序计算后断开梯度，只沿分数差反传。`truncation=30`，IDCG 只累加这前 30 名。权重再除以 `0.01+|分数差|`，整天 λ 质量乘 `log2(1+S)/S`（LightGBM `lambdarank_norm`）。因子支路裁 ±`LAMBDARANK_Z_CLIP`。

Arm·L 排序树：120 棵、`max_depth=5`、`num_leaves=32`、`subsample=0.85`、`colsample=0.9`、L2=1。

排序头另加保值项：把 ŷ 拉向当日标签截面 z，钉住可与 `y_*>0` / rank 阈值比较的零点。LambdaRank 在日内减均值后的分数上算，保值项用原始 ŷ。混合损失只在训练日反传。Holdout 每个 epoch 另打 NDCG@30，只记日志。

---

## 7. HeadSpec

真源：`heads.py`。

| id | task | loss | gate | 名义 factor_pack |
|--|--|--|--|--|
| `oo` | regression | smooth_l1 | `cs_rank_ic` | aoo_full |
| `tc` | regression | smooth_l1 | `cs_rank_ic` | atc_full |
| `oo_rank` | rank | lambdarank | `gate_lift` | aoo_full |
| `tc_rank` | rank | lambdarank | `gate_lift` | atc_full |

实际 T 列宽由 `full_aoo` / `full_atc` 决定；harden 后写成 `aoo_core|aoo_full|atc_core|atc_full`。τc 仅支持 `--tau-end 09:30`。

---

## 8. 评估与硬闸

### 8.1 指标

`daily_cs_ic_report` → `cs_ic` / `cs_icir`、`cs_rank_ic` / `cs_rank_icir`、`cs_top_mean_y`、`cs_ls_mean_y`；另加 `pooled_ic`、`sign_hit`（deadband **0.05**）。

工厂卡与枢纽因子表同名同数：Rank IC = `cs_rank_ic`，ICIR = `cs_icir`（日频 Pearson 均值/标准差），命中 = `sign_hit`，Top10% y = `cs_top_mean_y`。三位小数。硬闸里的 ICIR 仍是 Rank ICIR（`cs_rank_icir`），不写在这张卡上。

### 8.2 `hard_gate_vs_baseline`（相对 Arm·L）

实现：`metrics.py`。

- 绝对 lift：`cs_rank_ic(T) − cs_rank_ic(L) ≥ 0.01`，**或**
- 相对 lift：≥ **20%**
- 若基线 Rank ICIR &gt; 0 且候选 Rank ICIR &lt; 基线 × (1 − **0.10**) → 失败（`cs_rank_icir`，不是卡片上的 Pearson ICIR）

Holdout 始终写 `hard_gate_vs_arm_L.T`；L 或 T 失败也写 `pass=false` + reasons。顶层展示指标优先 Arm·T；Gate 始终取 `hard_gate.T`。

### 8.3 正式对照 `compute_parity`（晋级）

须同时满足：

1. T 因子全量：`full_aoo`（oo）或 `full_atc`（tc）
2. `t_sample_pct ≥ 99.999`（100%）
3. `eval_shared`（共享键 + 两侧 OOS + 同 `eval_start`）

再叠加：`gate_pass=true`，且 draft 与 last 的协议键一致（`_PROMOTE_PROTOCOL_KEYS`）。

---

## 9. 默认超参

| 参数 | CLI / desk 默认 |
|--|--|
| pool / watching_limit | 800 |
| lookback | 900（`oo_rank` desk **700**） |
| holdout_trading_days | 60 |
| seq_len / seq_feat_set | 20 / seq16 |
| d_model / nhead / nlayers | **64 / 4 / 3** |
| mlp_hidden | = d_model |
| dropout / epochs | 0.1 / 12 |
| batch_size / lr | **32** / **1e-3** |
| t_sample_pct | **10**（frac 0.1） |
| device | cpu |
| seed | **42** |
| full_aoo / full_atc | False / True |

边界：`d_model≥16`；`nlayers 1–6`；`batch 16–1024`；`epochs 1–40`；`t_sample_pct (0.1,100]`；`d_model % nhead == 0`。

---

## 10. 入口与文件地图

| 路径 | 职责 |
|--|--|
| `scripts/run_yhat_transformer_holdout.py` | Holdout 主入口：`main` → `_run_arm_l` / `_run_arm_t` |
| `core/research/yhat_tf_desk.py` | desk 参数、`build_holdout_argv`、子进程 env、ingest、晋级 |
| `…/yhat_transformer/model.py` | `SeqTransformer` / `SeqFactorTransformer` |
| `…/train.py` | 训练 / 推理 |
| `…/seq_dataset.py` | PIT 序列面板 |
| `…/factor_panel.py` | 因子面板、`slice_arm_t_panel`、`align_seq_and_factor` |
| `…/heads.py` / `metrics.py` | HeadSpec / 硬闸 |
| `core/research/materialize/cube_z.py` | `cs_z_matrix_prefer_cube` |
| `tests/test_yhat_transformer.py` | CS-z、slice、holdout-by-epoch 等 |
| `docs/quant-ui.md` § `/yhat-t` · `/yhat-l` | UI 契约 |

**启动链**：UI 拟合 → `build_holdout_argv` → `Popen(python -u script…)`；env：`OMP/MKL/OPENBLAS/HORIZON_LGB_NUM_THREADS=1`，`TORCH_NUM_THREADS=4`；macOS `KMP_DUPLICATE_LIB_OK=TRUE`。

```bash
PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_yhat_transformer_holdout.py --head oo
```

---

## 11. 内存与稳定性

- Arm·T：**numpy batch**；序列面板磁盘缓存；冷启动最多 4 进程建面板
- CS-z 前释放 `fac.X`；训练前清空 seq 大数组，避免双份常驻
- 8GB 建议：`d_model≤64`，下调 lookback / 池 / 采样%
- 按日志阶段定位崩溃：model build / buffers / fit start / step（见 desk `_arm_t_log_has`）

---

## 12. 明确不做

1. **不进生产 score**（无 promote-live / active）
2. **不改写** 研究枢纽里 ŷ_τ30…90 的套槽模型。四头 ŷ 由枢纽选臂后读本厂版本
3. 默认 **10%** 训练采样；L 全样本
4. 因子 core/full **只作用于 T**
5. Holdout 上的 epoch 指标 **不选权重**
6. 不做多 seed 搜索（seed=42）。页内单臂；双臂只留 CLI `--factory LT`
7. τc 仅开盘锚；不做其它 τ 网格
8. 不写套槽模型文件，不做例行调度。本页不挂历史回测。枢纽选中 Arm·T 时，历史回测读本厂已启用的版本。离线收益在回测中心 `/yhat-bt`，成交固定 09:30。本页不再放离线回测（见 [model-factory-hub.md](model-factory-hub.md) §3.4）

---

## 修订

| 日期 | 说明 |
|--|--|
| 2026-11-14 | 初稿：对齐 `yhat_transformer` + Holdout + desk 现行实现 |
| 2026-11-14 | §3 补全双支路晚融合：支路张量、末步池化、gap 在编码器之外、融合头 129 维 |
| 2026-11-15 | §4 序列窗口须严格早于 T；§5 因子缓存改为价量指纹，缺测留到 CS-z 之后再写成 0；§6 整日组抽样放不进即停 |
| 2026-11-15 | 对齐后只切早于 open 立方起点的前缀；起点之后缺立方仍失败 |
| 2026-11-15 | 立方起点改为第一份能读出 μ/σ 的文件；开头坏文件跳过，日期全部早于起点则清空面板 |
| 2026-11-16 | 排序头改为 LambdaRank 成对损失（`lambdarank_norm`，IDCG 只累加截断名次） |
| 2026-11-16 | 配方执行（研究套 / 执行套）归研究枢纽 |
| 2026-11-16 | 离线历史回测按版本号加载本页制品并预估，不写入套槽 |
| 2026-11-16 | 模型中心拉起的成功拟合按版本号保存 `state.pt` 与序列 clip |
| 2026-11-17 | 排序头混合损失：LambdaRank（日内减均值）+ `λ=0.3` 日 z 保值；权重 meta 记 `rank_value_weight` |
| 2026-11-17 | 去掉验证集与早停：跑满 epochs，保存最后一版权重；每个 epoch 用 Holdout 做一次测试。`patience` 不再传入 |
| 2026-11-17 | 研究枢纽四头复用本页拟合卡。参数只读来自配方。见 [model-factory-hub.md](model-factory-hub.md) §4.3 |
| 2026-11-17 | 枢纽四头拟合改为调用本工厂训练；权重仍在工厂版本，不写研究套 / 执行套 |
| 2026-11-18 | 已从版本列表删掉的失败不再出现在概览；有剩余版本时改看最新一条 |
| 2026-11-18 | 删掉的成功跑次同样退出概览和拟合日志；日志改看列表里还在的最新一条 |
| 2026-11-19 | oo 因子面板不再覆盖已有 open 立方；缺 τc 列按缺列补日线物化 |
| 2026-11-19 | 本页离线回测下线，同臂与跨厂都在 `/yhat-bt` |
| 2026-11-19 | 工厂排序头 truncation 改为 30；Arm·L 树改为 120 棵、深度 5、叶子 32、subsample 0.85、colsample 0.9、L2=1。Holdout 改打 NDCG@30 |
| 2026-11-20 | 训练 batch 上限改为 1024。页面、`harden_fit_params` 和训练入口同一边界 |
