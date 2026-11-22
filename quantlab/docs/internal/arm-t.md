# Arm·T 技术方案

研究轨 **Scheme LT**（LightGBM vs Transformer）的右臂：序列 Transformer。对照左臂 **Arm·L**（现网 LightGBM · 全量）。`live_allowed=False`，不写生产 score。旧结构权重不再加载。

UI 入口：`/yhat-t`（**Transformer 工厂**；旧 `/yhat-tf` 重定向）。契约见 [quant-ui.md · 模型中心](../quant-ui.md#yhat-t--yhat-l-模型中心双工厂)。  
长期拆分（工厂拟合 vs 枢纽注册 live/回测）见 **[model-factory-hub.md](model-factory-hub.md)**。跑次版本在 `live/yhat_t/versions/{head}/`（Arm·L 为 `yhat_l`）；选中只回看参数、指标和日志。删除只去掉该条归档和日志副本，不改磁盘上的当前拟合，不写 live。若删掉的是概览正在指示的那次失败，概览不再显示这次失败；列表里还有版本时，改看剩下的最新一条。

---

## 1. 定位

| | **Arm·L** | **Arm·T** |
|--|--|--|
| 模型 | 现网 LightGBM / LambdaRank | `ArmTModel` |
| 输入 | 因子面板全量列 | 因子序列（默认 60 步，末步是决策日）+ gap |
| 样本 | **固定全量**（对齐 live） | 默认相对训练段 **100%**（不截断；可调低） |
| 角色 | Holdout **硬闸基线** | 候选；须过相对 L 的 CS rank IC 硬门 |
| 归一 | live `fit_*_lgb_matrix` 或立方 CS-z | 序列因子列走 **L2 立方截面 CS-z**；`age_td` 与 gap 不参与 |

一次 Holdout 只训一个 head（`oo` / `tc` / `oo_rank` / `tc_rank`）。`/yhat-t` 只跑 Arm·T，`/yhat-l` 只跑 Arm·L；双臂只留在 CLI `--factory LT`。seed=42。

晋级边界（页面草稿/晋级已下线；正式对照与 live 禁令仍有效，见 [model-factory-hub.md](model-factory-hub.md)）：

> 正式对照须 `gate_pass` + 序列 full + T 采样 100% + 共享评测；**禁止 live / active**。工作配置随拟合意图隐式同步，不再暴露「存草稿 / 晋级研究」。

---

## 2. 端到端数据流

```text
日线 bars (lookback)
    ├─ 旧列宽 build_seq_panel → SeqPanel  X(N,L,F), gap(N,), y, dates, codes
    ├─ seq_core / seq_full：sequence_from_factor_panel → 窗口视图
    │     因子行只存一份，样本是 [T−(L−1) … T] 共 L 个行号，另附 age_td。不展开 (N,L,F)
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
                      cs_z_factor_sequence(seq, clock="open")  ← 序列因子列；缺立方即失败
                      train_arm_t (seed=42)
                      predict_arm_t on holdout
            │
            ▼
    daily_cs_ic_report / sign_hit → hard_gate_vs_baseline(L, T)
            │
            ▼
    summary.json + ingest_holdout_summary → desk last metrics
```

**关键不变量**

1. 因子面板 **先建全量**：Arm·L 对齐 live；Arm·T 只按序列档 `seq_core` / `seq_full` 裁列，再切成含决策日的窗口。构建日志按当前臂写明谁用全量、T 裁成哪一包，T 单臂不标成 Arm·L。
2. Gate / OOS 用 **seq∩fac 共享键** 与同一 `eval_start`，避免 L/T 评测窗错位。
3. Arm·L 训练仍可吃共享日历末端之前的全量 fac 早期日（含 seq 尚未覆盖的日）。
4. `align_seq_and_factor`：同 `(code,date)` 标签偏差 > `1e-3` 时以 **seq.y 覆盖 fac.y**。
5. 对齐后只丢掉早于第一份可读 open 立方的行。起点与截面标准化同一读法（能列出 μ/σ）；跨度开头读失败的文件跳过，不算起点。起点当天及之后的缺日留下，截面标准化报 `MaterializeRequired`，**不做**列标准化回退。一份可读立方都没有时面板原样留下；日期全部早于起点时清空面板。共享日历在这次丢行之后再切，Holdout 末端不变。

---

## 3. 模型

实现：`core/research/yhat_transformer/model.py` 的 `ArmTModel`。只有序列编码器。末步做 LayerNorm，再拼 gap，线性头出标量。没有因子 MLP。

```text
x_seq (B, L, F+1) ── Linear ── sin PE ── Pre-LN Encoder × nlayers
                                            └── 末步 LayerNorm ── 拼 gap ── Linear ── ŷ
```

标量头没有激活。回归头外面用逐点 MSE；排序头外面用 LambdaRank，不减日内均值。网络本身不包 softmax / sigmoid。

### 3.1 编码器

`SeqTransformer.encode`，`with_head=False`（不建未使用的死头）。

| 步 | 张量 | 说明 |
|--|--|--|
| 输入 | `(B, L, F+1)` | `seq_len` L 就是进编码器的步数，末步是决策日。F 是这一头因子 core / full（oo 65/222，开盘 τc 61/218），末列是 `age_td`，当天为 0 |
| 投影 | `(B, L, d_model)` | 一层 `Linear`，再对每一个位置加正弦位置编码，包括决策日。位置 0 是最旧一天 |
| 编码 | 同形 | `TransformerEncoder` × `nlayers`。每层 Pre-LN（`norm_first=True`）：自注意力残差，再 GELU 前馈残差；`batch_first=True`。无 padding mask，窗口内全注意力 |
| 池化 | `(B, d_model)` | 只取末步 `h[:, -1, :]`，不做均值池化、不设 CLS |
| 缺口 | 不进编码器 | `gap` 是今开/昨收 − 1 的原始标量。末步 LayerNorm 之后再拼。缺省时填 0 |

前馈宽度 `dim_feedforward = max(64, d_model×4)`。`d_model` 必须能被 `nhead` 整除。因子列的截面 z 在训练预处理里做，不在这个模块里。`age_td` 与 gap 不进这套 z。

### 3.2 输出头

末步 LayerNorm，拼 gap，再 `Linear(d_model+1 → 1)`。

| 参数 | 默认（CLI / desk） | 说明 |
|--|--|--|
| `d_seq_in` | 因子列宽 + `age_td` | oo core/full 66/223，开盘 τc 62/219 |
| `d_model` | **128** | 须被 `nhead` 整除 |
| `nhead` | 4 | 每头 `d_model / nhead` |
| `nlayers` | **2** | 堆在编码器上 |
| `dim_feedforward` | `max(64, d_model×4)` | 默认 512 |
| `dropout` | 0.1 | 编码器 Dropout |
| 头输入 | `d_model + 1` | 末步 LayerNorm 之后拼 gap |

模型中心拉起的成功拟合，在 Holdout 预测之后把 CPU `state_dict` 和序列 clip 写入 `{版本号}.weights/`（`state.pt` + `meta.json`）。直接跑脚本、没有 `YHAT_FACTORY_RUN_ID` 时不写。页面回看仍只有结构、训练设置和 OOS，没有逐因子表。加载用 `load_factory_weights`。本页不写研究套 / 执行套。

---

## 4. 序列面板（PIT）

序列在 `factor_panel.sequence_from_factor_panel`。`seq_dataset.py` 只留窗口视图和决策日缺口。

**决策日 T**

- `seq_core` / `seq_full`：序列 = 该股票因子行 `[T−(L−1) … T]`，共 L 步，列就是这一头的因子 core 或 full。末步是决策日当天，和前面每一天一起进编码器、一起加位置编码。T 之前的行仍须早于 T
- 入参按日期排序。窗口内日期必须严格递增。只有末步等于 T
- 附加通道 `gap_t` = 今开 / 昨收 − 1（百分点），另附在编码器之外
- 标签 oo：`open[T+h]/open[T]−1`
- 标签 τc：同日 `close/open−1`（本期 τ=开盘；horizon 忽略）

| 项 | 默认 |
|--|--|
| `seq_len` L | 60（5–60）。因子序列这 L 步含决策日。拟合实验卡可改，默认 60 |
| `feat_set` | **seq_core** / **seq_full**，列宽对齐这一头的因子 core / full |
| 存储 | 窗口视图：因子行一份，样本只存 L 个行号 |

每一天用这一头因子 core 或 full 的同一套列：oo 是 65 / 222，开盘 τc 是 61 / 218。决策日 T 的序列是该股票因子行 `[T−(L−1) … T]`，共 L 步，末步是当天，`age_td` 为 0。权重 meta 写 `seq_through_decision=true`。列宽只由序列档决定，默认 core。`seq8` / `seq16` / `seq32` / `seq64` 已下线，草稿里遇到会改回 `seq_core`。序列在因子列之后加 `age_td`。因子列在进编码器之前走开盘立方截面 z，缺测写成 0；排序头再裁 ±`LAMBDARANK_Z_CLIP`。`age_td` 是距决策日的交易日数 / (L−1)，当天为 0，窗口最旧一天为 1，不做截面 z。gap 仍是今开/昨收 − 1，按训练行 1%/99% clip。权重 meta 写 `seq_cs_z=true`。

---

## 5. 因子面板

实现：`factor_panel.py`。

### 5.1 构建（全量，供 L 与后续裁切）

| Head | 构建 | 指纹 |
|--|--|--|
| oo / oo_rank | `build_oo_factor_panel` → 枢纽 `assemble_oo_panels` | full_aoo |
| tc / tc_rank | `build_tc_factor_panel` → 枢纽 `collect_tau_compact` | atc_full |

四头都读数据中心**日线物化**，不读分钟 τ 网格（322 列）。ŷ_oo / ŷ_oo_rank 是 oo 全量 **222** 列（16 个 `z_oo_*` + 206 个 `raw_aoo_*`）。ŷ_τc / ŷ_τc_rank 是开盘 τc 全量 **218** 列（8 个 `z_τc_*` + 210 个 `raw_atc_*`）。Arm·L 与 Arm·R 训全量；Arm·T 可在开跑前裁成 core。指纹命中直接用 L1。工厂不在物化写入上下文里，未命中的票只在内存里用日线重算，不写回湖。截面 z 读 open 立方。

磁盘缓存键是 `facv3` 加每只股票的价量指纹（`bars_fingerprint`）。τc 的 kind 是 `tc_compact_v3`（开盘面板丢掉 15 个分钟 `z_τc_*` 和 14 个 `raw_atc_*` 前缀；`Z_TC_FIT_FEATURES` 本身不改，盘中钟仍用这些列）。固定 lookback 下滑窗、复权或中间一根价量改动都会 miss。oo 单票失败由枢纽组装抬异常，不落半成品。缺测在面板里保持 NaN。开盘 full 218，core 61。隔夜锚和 `tau_lag1` / `tau_ma5` 保留。

`assemble_oo_panels` 在非物化路径会尝试把 oo 面板落成 open 立方。面板没有 τc 列，已有 open 立方时不覆盖。截面 z 缺的是列时，报错给出本地跨度，并要求重跑日线物化补缺列的日子。

### 5.2 `slice_arm_t_panel`（仅 Arm·T 序列档）

> Arm·L 始终全量，不走这里。序列档选了 core 但裁不窄时 **显式 ValueError**，避免静默退回全量。

新训练的列宽看 `seq_feat_set`：`seq_core` 或 `seq_full`。默认 `seq_core`（oo 65，开盘 τc 61）。`full_aoo` / `full_atc` 仍写在拟合参数里，给 Arm·L 和没有 `seq_feat_set` 的旧草稿，不改变编码器输入。

裁列只改列宽，**不改** CS-z 路径。

### 5.3 CS-z（与 Arm·L 对齐）

`_run_arm_t` 内，只标准化序列里的因子列：

1. 读完因子列数后清空 `fac.X`，减轻峰值 RSS。因子矩阵不进模型
2. `cs_z_factor_sequence`：开盘立方，早于立方起点的行写 0。`age_td` 不参与
3. 排序头再裁 ±`LAMBDARANK_Z_CLIP`（对齐 LambdaRank）；回归头不裁
4. 非有限值 → 0。缺立方报 `MaterializeRequired`，不做列标准化回退

训练函数内 **不再** 做训练段列标准化。截面 z 之后的序列因子列也不再做 1%/99% clip。

---

## 6. 训练协议

实现：`train.py` · `train_arm_t`；编排：`_run_arm_t`。

| 项 | 行为 |
|--|--|
| **seed** | 固定 **42** |
| **epochs** | 默认 **20**；跑满，不提前停 |
| **保存** | 最后一个 epoch 的权重 |
| **eval** | 每个 epoch 在 Holdout 上做一次 `no_grad` 测试。排序头打 Rank IC / Pearson ICIR / Rank ICIR / NDCG@30 / Top10% y。回归头同一行再打 MSE。训练日用到 Holdout 隔离之前的全部交易日。不反传，不选权重，不改学习率日程 |
| **曲线** | 步日志和每个 epoch 追加 `{head}_fit.curve.jsonl`（`YHAT_FIT_CURVE`）。运行台卡片下面画两张图，各自有坐标轴。训练损失左轴是步均 `avg`，底轴是 step。Holdout 左轴是 Rank IC；排序头右轴是 NDCG@30，回归头右轴是 MSE；底轴是 epoch。跑完随版本归档。只显示，不选权重 |
| **optimizer** | AdamW，`weight_decay=1e-3`。`bias` 与 LayerNorm 的 `weight` 为 0。初始学习率见下 |
| **LR** | cosine + warmup；`TRANSFORMER_WARMUP_STEPS` 默认 100；`min_ratio=0.01`。沪深300从头训练的峰值是 **1e-4**，暖机后到这个值，再余弦降到 1e-6。大池预训练峰值仍是 **1e-3**。两段连着训时，微调段改用 1e-4。页面没有这个旋钮；草稿里的 1e-3 / 1e-4 随截面换。请求里写明的其它学习率保留 |
| **grad clip** | 1.0 |
| **batch** | 默认 2048；范围 16–4096 |
| **subsample** | `t_sample_frac` 默认 **1**（不截断）；小于 1 才抽训练日。点损失行级；排序头 **整日组**，一组放不进剩余名额即停。抽样只作用在训练日，Holdout 不进训练 |
| **序列预处理** | 因子列：开盘立方截面 z，缺测写 0；排序头再裁 ±`LAMBDARANK_Z_CLIP`。`age_td` 不做 z。gap 按 train 行 1%/99% clip。因子序列不展开成 `(N,L,F)` |
| **线程** | `TORCH_NUM_THREADS` 默认 **4**；interop=1；BLAS/OMP 仍为 1 |
| **取数** | numpy 切片再 `as_tensor`（避免大张量高级索引 OpenMP SIGSEGV） |

### 损失

| task | loss | 说明 |
|--|--|--|
| regression（oo / tc） | `mse` | 百分点上的逐点均方 |
| rank（oo_rank / tc_rank） | LambdaRank | 只比较名次，不减日内均值，不拟合分数数值 |

回归头是逐点 MSE，对批取均值。预测和标签都是百分点。`bias` 与 LayerNorm 的 weight 不衰减。

排序头标签与 Arm·L 相同：收益越高名次越大，`label_gain` 就是名次本身。权重按当前排序计算后断开梯度，只沿分数差反传。`truncation=30`，IDCG 只累加这前 30 名。整天 λ 质量乘 `log2(1+S)/S`（LightGBM `lambdarank_norm`）。不再除以 `|分数差|`，也不在损失里做日内减均值。序列因子列裁 ±`LAMBDARANK_Z_CLIP`。

Arm·L 排序树：120 棵、`max_depth=5`、`num_leaves=32`、`subsample=0.85`、`colsample=0.9`、L2=1。

排序损失只在训练日反传。每个 epoch 的 Holdout 测试另打 NDCG@30，只记日志。训练结束后的完成行再打一次同一份最后权重的 Holdout。

---

## 7. HeadSpec

真源：`heads.py`。

| id | task | loss | gate | 名义 factor_pack |
|--|--|--|--|--|
| `oo` | regression | mse | `cs_rank_ic` | aoo_full |
| `tc` | regression | mse | `cs_rank_ic` | atc_full |
| `oo_rank` | rank | lambdarank | `gate_lift` | aoo_full |
| `tc_rank` | rank | lambdarank | `gate_lift` | atc_full |

实际 T 列宽由 `seq_feat_set` 决定，默认 `seq_core`。`full_aoo` / `full_atc` 仍随拟合参数留下，harden 后写成 `aoo_core|aoo_full|atc_core|atc_full`，不进编码器。τc 仅支持 `--tau-end 09:30`。

---

## 8. 评估与硬闸

### 8.1 指标

`daily_cs_ic_report` → `cs_ic` / `cs_icir`、`cs_rank_ic` / `cs_rank_icir`、`cs_top_mean_y`、`cs_ls_mean_y`；另加 `pooled_ic`、`sign_hit`（deadband **0.05**）。

排序头卡片：Rank IC = `cs_rank_ic`，ICIR = `cs_icir`（日频 Pearson 均值/标准差），第三格 NDCG@30，Top10% y = `cs_top_mean_y`。三位小数。

回归头卡片五格：MSE = Holdout 逐点 `(ŷ−y)²` 的均值，预测和实现都是百分点，不截断。Rank IC = `cs_rank_ic`，ICIR = `cs_icir`，命中 = `sign_hit`（deadband 0.05），Top10% y = `cs_top_mean_y`。Rank IC、ICIR、Top10% y 与排序头同一口径，三位小数。概览闸看 MSE。硬闸仍用 Rank IC / Rank ICIR。拟合曲线的 epoch 点只留 `rank_ic`、`ndcg30`、`mse`。

### 8.2 `hard_gate_vs_baseline`（相对 Arm·L）

实现：`metrics.py`。

- 候选 `cs_rank_ic` ≤ 0 → 失败
- 绝对 lift：`cs_rank_ic(T) − cs_rank_ic(L) ≥ 0.01`，**或**相对 lift ≥ **20%**
- 基线 Rank ICIR &gt; 0 时，候选不得低于基线 × (1 − **0.10**)
- 基线 Rank ICIR ≤ 0 时，候选 Rank ICIR 必须 &gt; 0
- 非正式对照（因子不是全量、采样不是 100%、或评估窗不共享）写 `applicable=false` 且 `pass=false`

惩罚用的是 `cs_rank_icir`。卡片上的 ICIR 仍是 Pearson `cs_icir`。排序头卡片第三格是 NDCG@30。

Holdout 始终写 `hard_gate_vs_arm_L.T`；L 或 T 失败也写 `pass=false` + reasons。顶层展示指标优先 Arm·T；Gate 始终取 `hard_gate.T`。

### 8.3 正式对照 `compute_parity`（晋级）

须同时满足：

1. 序列档是 full：`seq_feat_set=seq_full`。旧草稿没有这一项时，仍看 `full_aoo` / `full_atc`
2. `t_sample_pct ≥ 99.999`（100%）
3. `eval_shared`（共享键 + 两侧 OOS + 同 `eval_start`）

再叠加：`gate_pass=true`，且 draft 与 last 的协议键一致（`_PROMOTE_PROTOCOL_KEYS`）。

---

## 9. 默认超参

| 参数 | CLI / desk 默认。数在 `heads.py` |
|--|--|
| 截面 | 沪深300（成分数写入 `pool_n`） |
| lookback | 1000（四头相同） |
| holdout_trading_days | 60 |
| seq_len / seq_feat_set | 60 / seq_core |
| d_model / nhead / nlayers | **128 / 4 / 2** |
| dropout / epochs | 0.1 / 20 |
| batch_size / lr | **2048** / 沪深300 **1e-4**，大池 **1e-3** |
| t_sample_pct | **100**（frac 1，不截断） |
| device | cpu |
| seed | **42** |
| full_aoo / full_atc | True / True（不进编码器；序列档另看 `seq_feat_set`） |

边界：`d_model≥16`；`nlayers 1–6`；`batch 16–4096`；`epochs 1–40`；`seq_len 5–60`；`t_sample_pct (0.1,100]`；`d_model % nhead == 0`。

---

## 10. 入口与文件地图

| 路径 | 职责 |
|--|--|
| `scripts/run_yhat_transformer_holdout.py` | Holdout 主入口：`main` → `_run_arm_l` / `_run_arm_t` |
| `core/research/yhat_tf_desk.py` | desk 参数、`build_holdout_argv`、子进程 env、ingest、晋级 |
| `…/yhat_transformer/model.py` | `SeqTransformer` / `ArmTModel` |
| `…/train.py` | 训练 / 推理 |
| `…/fit_curve.py` | 拟合曲线 jsonl：步均损失、epoch Holdout |
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

- Arm·T：**numpy batch**。因子序列只存因子行加窗口行号，按 batch 取 `L` 天，不常驻 `(N, L, F)`。历史回测打分沿用这份视图
- CS-z 前释放 `fac.X`
- 8GB 建议：`d_model≤64`，下调 lookback / 池 / 采样%
- 按日志阶段定位崩溃：model build / buffers / fit start / step（见 desk `_arm_t_log_has`）

---

## 12. 明确不做

1. **不进生产 score**（无 promote-live / active）
2. **不改写** 研究枢纽里 ŷ_τ30…90 的套槽模型。四头 ŷ 由枢纽选臂后读本厂版本
3. 采样可以调低；默认是 **100%**。L 始终全样本
4. 因子 core/full **只作用于 T**
5. Holdout 上的 epoch 指标 **不选权重**
6. 不做多 seed 搜索（seed=42）。页内单臂；双臂只留 CLI `--factory LT`
7. τc 仅开盘锚；不做其它 τ 网格
8. 不写套槽模型文件，不做例行调度。本页不挂历史回测。枢纽选中 Arm·T 时，历史回测读本厂已启用的版本。离线收益在回测中心 `/yhat-bt`，成交固定 09:30，版本列表只收研究套。本页不再放离线回测（见 [model-factory-hub.md](model-factory-hub.md) §3.4）

---

## 修订

| 日期 | 说明 |
|--|--|
| 2026-11-22 | 训练函数改为 `train_arm_t` / `predict_arm_t`。去掉 SmoothL1 旁路、`mlp_hidden` 和因子支路参数。权重键不变 |
| 2026-11-22 | 模型类由 `SeqFactorTransformer` 改名为 `ArmTModel`。权重键不变 |
| 2026-11-22 | 默认序列改为 60 日。前馈改为 `4×d_model`。回归头改回 MSE。AdamW 衰减改为 1e-3；bias 与 LayerNorm.weight 为 0 |
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
| 2026-11-20 | 四头因子明确读日线物化：oo 222、开盘 τc 218；不读分钟 322 列 |
| 2026-11-20 | 排序头去掉日 z 保值。损失只留日内减均值后的 LambdaRank，不再写 `rank_value_weight` |
| 2026-11-20 | 回归头对预测为正、实现为负的样本再计一次。权重 meta 记 `regression_false_open` |
| 2026-11-21 | arch=2：正弦位置编码、`age_td`、因子支路去掉输入 LayerNorm、gap 放在融合 LayerNorm 之后。序列缺失改填训练列中位数。回归错开项改为同一分母下的 2×。排序头去掉 `|分数差|` 分母。硬闸要求候选 Rank IC 为正，非正式对照不再 pass |
| 2026-11-21 | 结构图、损失表与实现对齐。`false_open_weight` 记实际倍数 2 |
| 2026-11-21 | 回归损失不再接一个空的 `loss_fn` |
| 2026-11-21 | 回归头去掉错开样本的 2 倍权重。预估水平不再被单边压到 0 以下 |
| 2026-11-21 | 回归头改为真实为正、预测为负的样本权重 2（`missed_up=2`）。猜涨猜错不加罚 |
| 2026-11-21 | 回归头回到逐点损失。漏涨样本不再单独加权，日志和权重 meta 不再写 `missed_up_weight` |
| 2026-11-21 | 回归头改为逐点平方误差。最后一层线性头的偏置退出权重衰减 |
| 2026-11-21 | 回归头改为 SmoothL1 β=5。平方误差让涨跌停主导梯度，Holdout Rank IC 从约 0.07–0.09 掉到 0.02 附近 |
| 2026-11-21 | 拟合默认对齐当前配置：训练窗 1000、d_model 256、2 层、2 epoch、采样 100%、全量因子、沪深300 |
| 2026-11-21 | 缺字段按当前默认读：采样 100%、oo 全量因子。盘上写明的 10% 或 core 仍按原值 |
| 2026-11-21 | 去掉训练尾部探针。每个 epoch 在 Holdout 上测一次，不选权重；这 20 个交易日回到拟合 |
| 2026-11-21 | 回归头卡片改看斜率、截距、命中、正侧实现。两头 Holdout 对齐后另写过闸比例和过闸实现。硬闸仍用 Rank IC |
| 2026-11-22 | 回归头运行台在斜率、截距、命中、正侧实现之后补 Rank IC 与 ICIR |
| 2026-11-22 | 回测中心版本列表只收研究套。执行套留在枢纽执行框 |
| 2026-11-22 | 回归头运行台去掉指标下的过闸比例和过闸实现 |
| 2026-11-22 | 运行台卡片下面画训练损失与 Holdout 曲线。序列写在 `{head}_fit.curve.jsonl`，随版本归档 |
| 2026-11-22 | 拟合曲线补坐标轴。损失左轴是 avg、底轴是 step；Holdout 左轴是 Rank IC，右轴是 NDCG@30 或斜率，底轴是 epoch |
| 2026-11-22 | 因子序列不再展开成 `(N,L,F)`。窗口行号指回因子行，中位数和分位写回这份窄表，训练和历史回测都按 batch 取窗口 |
| 2026-11-22 | 序列里的因子列改为开盘立方截面 z，与因子支路相同；`age_td` 与 gap 不做截面 z。排序头裁 ±3。旧权重无 `seq_cs_z` 仍走分位 clip |
| 2026-11-22 | 因子序列窗口接到决策日，末步 `age_td` 为 0。权重 meta 写 `seq_through_decision`；没有这个标记的已落盘权重仍停在 T−1 |
| 2026-11-22 | arch=3 只留序列支路，新训练不再建因子 MLP。默认 `seq_len` 改为 30。arch=1/2 权重仍按双支路加载。正式对照在写了 `seq_feat_set` 时改为要求序列 full |
| 2026-11-22 | `seq_len` 改为进编码器的步数，含决策日。默认 30 步是 `[T−29 … T]`。末步与其余位置一起加正弦位置编码。`age_td` 分母改为 L−1 |
| 2026-11-22 | 去掉 arch=1/2 双支路加载，以及停在 T−1 的窗口。训练和打分只走序列编码器 |
| 2026-11-22 | 沪深300拟合的初始学习率改为 1e-4。大池预训练仍是 1e-3。脚本开跑前按截面改峰值，微调段同样是 1e-4 |
| 2026-11-21 | 回归头副标题改为 smooth_l1。枢纽和 Arm·L / Arm·R 概览闸改看斜率。排序头仍是 Rank IC |
| 2026-11-22 | 训练 batch 上限改为 4096。点拟合不再把 4096 收成 1024 |
| 2026-11-22 | 回归头运行台改为 MSE、Rank IC、ICIR、命中、Top10% y。概览闸看 MSE。Holdout 曲线右轴改为 MSE |
| 2026-11-22 | 拟合曲线 epoch 字段改为 `mse`，不再读 `slope`。去掉未再展示的过闸行和 `level_gate` |
| 2026-11-22 | 拟合实验卡露出序列长度。默认 60，范围 5–60 |
| 2026-11-22 | Arm·T 拟合默认改为 d_model 128、batch 2048、epochs 20。长度 60、层数 2、训练窗 1000、Holdout 60 |
| 2026-11-22 | 这套拟合缺省收到 `heads.py`。desk、CLI、训练入口和页面用同一份 |
| 2026-11-22 | 回测缺了序列长度或网络宽度时改用这套缺省。序列档提示改为 `[T−(L−1) … T]` |
