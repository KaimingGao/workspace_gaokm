# Arm·T 技术方案

研究轨 **Scheme LT**（LightGBM vs Transformer）的右臂：双支路晚融合（序列 Transformer ∥ 因子 MLP）。对照左臂 **Arm·L**（现网 LightGBM · 全量）。`live_allowed=False`，不写生产 score。

UI 入口：`/yhat-t`（**Transformer 工厂**；旧 `/yhat-tf` 重定向）。契约见 [quant-ui.md · 模型中心](../quant-ui.md#yhat-t--yhat-l-模型中心双工厂)。  
长期拆分（工厂拟合 vs 枢纽注册 live/回测）见 **[model-factory-hub.md](model-factory-hub.md)**；阶段 A 将下掉草稿→晋级、改为跑次版本下拉。

---

## 1. 定位

| | **Arm·L** | **Arm·T** |
|--|--|--|
| 模型 | 现网 LightGBM / LambdaRank | `SeqFactorTransformer` |
| 输入 | 因子面板全量列 | 日线短序列 + gap +（可裁列的）因子支路 |
| 样本 | **固定全量**（对齐 live） | 默认相对训练段 **10% subsample** |
| 角色 | Holdout **硬闸基线** | 候选；须过相对 L 的 CS rank IC 硬门 |
| 归一 | live `fit_*_lgb_matrix` 或立方 CS-z | 因子支路同 **L2 立方截面 CS-z**；序列侧 train 分位 clip |

一次 Holdout 只训一个 head（`oo` / `tc` / `oo_rank` / `tc_rank`）；固定双臂 L+T、seed=42。

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
    丢掉没有 L2 的早期行（与 Arm·L 拟合前丢行同口径）
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

1. 因子面板 **先建全量**：Arm·L 对齐 live；core / full 只在 Arm·T 开跑前裁列。
2. Gate / OOS 用 **seq∩fac 共享键** 与同一 `eval_start`，避免 L/T 评测窗错位。
3. Arm·L 训练仍可吃共享日历末端之前的全量 fac 早期日（含 seq 尚未覆盖的日）。
4. `align_seq_and_factor`：同 `(code,date)` 标签偏差 > `1e-3` 时以 **seq.y 覆盖 fac.y**。
5. 早于立方起点、没有 L2 的行在对齐后丢掉。剩下的面板仍缺立方：`MaterializeRequired`，**不做**列标准化回退。

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

标量头没有激活。回归头外面用 SmoothL1，排序头外面用 ListNet，网络本身不包 softmax / sigmoid。

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

权重只活在当次训练的 `state_dict` 里，预测后不落盘。回放因此没有逐因子表，只能展示这条网络的结构、训练设置和 Arm·T 自己的 OOS。

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
| tc / tc_rank | `build_tc_factor_panel`（`collect_tau_open_panel`，`z ∪ raw_atc`） | atc_full |

磁盘缓存键是 `facv3` 加每只股票的价量指纹（`bars_fingerprint`）。固定 lookback 下滑窗、复权或中间一根价量改动都会 miss。τc 有股票失败时不写缓存。oo 单票失败由枢纽组装抬异常，不落半成品。缺测在面板里保持 NaN。

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
| **epochs** | 默认 **12**；patience **6** |
| **早停** | 按 **val_loss**；`holdout_idx` **只打指标，不参与选权** |
| **optimizer** | AdamW，`lr=1e-3`，`weight_decay=1e-4` |
| **LR** | cosine + warmup；`TRANSFORMER_WARMUP_STEPS` 默认 100；`min_ratio=0.01` |
| **grad clip** | 1.0 |
| **batch** | 默认 32；地板 `max(16, batch_size)` |
| **val** | 训练日历末 **20%** 日（`val_frac` 钳在 0.1–0.4）；**先切 val 再 subsample train** |
| **subsample** | `t_sample_frac` 默认 **0.1**；≥1 → 不截断。点损失行级；listwise **整日组**，一组放不进剩余名额即停 |
| **序列预处理** | train 行 1%/99% 分位 clip（最多 2 万行估分位） |
| **线程** | `TORCH_NUM_THREADS` 默认 **4**；interop=1；BLAS/OMP 仍为 1 |
| **取数** | numpy 切片再 `as_tensor`（避免大张量高级索引 OpenMP SIGSEGV） |

### 损失

| task | loss | 说明 |
|--|--|--|
| regression（oo / tc） | `smooth_l1`（β=1） | |
| rank（oo_rank / tc_rank） | ListNet | 相关度 = 日截面 z，再 clip ±3.0 |

直接对百分点收益 softmax 会退化成涨停 one-hot，截面其余梯度消失，Holdout rank IC / NDCG 学不出来——故用截面 z。

---

## 7. HeadSpec

真源：`heads.py`。

| id | task | loss | gate | 名义 factor_pack |
|--|--|--|--|--|
| `oo` | regression | smooth_l1 | `cs_rank_ic` | aoo_full |
| `tc` | regression | smooth_l1 | `cs_rank_ic` | atc_full |
| `oo_rank` | rank | listwise | `gate_lift` | aoo_full |
| `tc_rank` | rank | listwise | `gate_lift` | atc_full |

实际 T 列宽由 `full_aoo` / `full_atc` 决定；harden 后写成 `aoo_core|aoo_full|atc_core|atc_full`。τc 仅支持 `--tau-end 09:30`。

---

## 8. 评估与硬闸

### 8.1 指标

`daily_cs_ic_report` → `cs_ic` / `cs_icir`、`cs_rank_ic` / `cs_rank_icir`、`cs_top_mean_y`、`cs_ls_mean_y`；另加 `pooled_ic`、`sign_hit`（deadband **0.05**）。

### 8.2 `hard_gate_vs_baseline`（相对 Arm·L）

实现：`metrics.py`。

- 绝对 lift：`cs_rank_ic(T) − cs_rank_ic(L) ≥ 0.01`，**或**
- 相对 lift：≥ **20%**
- 若基线 ICIR &gt; 0 且候选 ICIR &lt; 基线 × (1 − **0.10**) → 失败

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
| dropout / patience / epochs | 0.1 / 6 / 12 |
| batch_size / lr | **32** / **1e-3** |
| t_sample_pct | **10**（frac 0.1） |
| device | cpu |
| seed | **42** |
| full_aoo / full_atc | False / True |

边界：`d_model≥16`；`nlayers 1–6`；`batch 16–256`；`epochs 1–40`；`t_sample_pct (0.1,100]`；`d_model % nhead == 0`。

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
- `t_sample_pct=100` 且 `d_model≥256` → OOM 告警
- 8GB 建议：`d_model≤64`，下调 lookback / 池 / 采样%
- 按日志阶段定位崩溃：model build / buffers / fit start / step（见 desk `_arm_t_log_has`）

---

## 12. 明确不做

1. **不进生产 score**（无 promote-live / active）
2. **不替代** `/quant` 研究枢纽落盘的 LightGBM / LambdaRank 选股链
3. 默认 **10%** 训练采样；L 全样本
4. 因子 core/full **只作用于 T**
5. Holdout 上的 epoch 指标 **不选权重**
6. 不做多 seed / 多臂组合搜索（固定 L+T、seed=42）
7. τc 仅开盘锚；不做其它 τ 网格

---

## 修订

| 日期 | 说明 |
|--|--|
| 2026-11-14 | 初稿：对齐 `yhat_transformer` + Holdout + desk 现行实现 |
| 2026-11-14 | §3 补全双支路晚融合：支路张量、末步池化、gap 在编码器之外、融合头 129 维 |
| 2026-11-15 | §4 序列窗口须严格早于 T；§5 因子缓存改为价量指纹，缺测留到 CS-z 之后再写成 0；§6 listwise 抽样放不进即停 |
| 2026-11-15 | 对齐后丢掉没有 L2 的早期行，与 Arm·L 同；评测窗仍缺立方则失败 |
