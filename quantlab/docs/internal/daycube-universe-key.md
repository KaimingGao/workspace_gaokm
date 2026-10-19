# DayCube 宇宙键：物化与回测对齐

[← 文档索引](../README.md) · 分层契约见 [feature-lake-daycube.md](./feature-lake-daycube.md)

> 状态：**现行协议**（2026-10-19）  
> 范围：分钟 / 开盘 L2 的 `universe_hash`，以及做 T 回测读立方失败时的打分回退  
> 不覆盖：重拟合、改模型系数、改观察池名单

---

## 1. 结论

物化和回测对不齐，差的是 **DayCube 的目录键**，不是观察池中途换了一份名单。

读取方一直用观察池全名单做 `universe_hash`。旧的分钟写入用的是「当天拼出特征行的子集」。观察池 800 只没变，其中两只当天没有 L1 行，写入键就少了这两只，文件落到另一个目录。回测按 800 只去找，miss。

现行协议：

- **键** = 本次请求的完整代码名单（物化传入的观察池，回测读取的 `_watching_codes_for_z()`）。
- **矩阵** = 当天有特征行的代码。没有 `oo` 也没有 `tc` 的代码不进 `X_raw`，不进 μ/σ。
- 缺行只影响矩阵行数，**不再改哈希**。

盘上已经按旧键写下的分钟立方不会自动搬家。协议改完之后，要让快路径命中，需要再跑一次分钟 L2。概率本身可以不靠这次重跑：立方 miss 时，做 T 打分改用同钟 L1 做截面 z。

---

## 2. 当时看到的现象

做 T 回测里，飞龙股份 `002536`、*ST美丽 `000010` 在 2026-09-28 至 2026-10-16 的窗头概率贴在个位数，例如 `3.0%(-0.45%)`、`0.4%(-1.26%)`。`ŷ_τw` 几乎总是 `-5`，方向几乎总是「反」。

单元格格式是 **预估上涨概率（真实窗口收益）**。括号里是收益，不是概率。`ŷ_τw` 是五个窗头相对中点 47% 的投票和：五头都低于 42% 时，每头投 −1，和为 −5。

模型是 2026-10-18 拟合的 logistic ridge（`t{N}_ridge_v4`，`zscore_scope=cross_section`）。样本外上涨率约 45%，典型概率在大约 33%–67%。个位数概率不是模型拟合出来的分布，是推理时把 **未标准化的 raw 水平** 当成了 z。

最大的系数在 `raw_ath_CLOSE0`（约 −1.5）和 `raw_ath_SESSION_REMAIN`（t30 约 −0.009，t90 约 −0.018）。`CLOSE0` ≈ 昨收/现价 ≈ 1，`SESSION_REMAIN` 在 09:35 约为 235 分钟。这两列若原样进 logit，再过 sigmoid，就会复现用户看到的个位数概率。

训练时截面组是 `date|τ`（同一天、同一钟）。同一钟上 `SESSION_REMAIN` 对全池是常数，正确的 z 是 0。`CLOSE0` 的截面标准差大约是 0.015，z 是「偏离 1 多少个截面标准差」，不是把 1.0 本身送进模型。

---

## 3. 两层物化，谁在被读

| 层 | 路径 | 键 | 这次回测 |
|----|------|----|----------|
| L1 Feature Lake | `data/cache/feature_lake/v{schema}/{family}/{code}.npz` | 家族 + 代码 | 有行的票已被打分回退读到 |
| L2 DayCube | `data/cache/day_cubes/v{schema}/{universe_hash}/{clock}/{date}.npz` | schema + 宇宙哈希 + 钟 + 日 | 分钟钟按 800 只去找，miss |

L1 的 `tc_0935` 这类家族里已经有 `raw_ath_*`。立方 miss 之后，打分用的就是这些行。所以「概率修一下」不要求先重跑物化。

L2 是快路径：命中则直接用立方里的 μ/σ，不再现场扫同伴。旧分钟立方写在错误的哈希目录下，这条快路径没有被用上。下次再用同一份 800 只跑 L2，开头的 `load_day_cube` 也按 800 只找，同样找不到旧文件，会再算一遍。

开盘瘦立方是另一条写入：`persist_tau_pool_as_cubes` 用传入的完整名单做哈希，矩阵里往往只有 `gap_pct`。所以 `7b6cc7befdf3/open/` 能被回测读到，列数是 1，不能拿来做窗头 z。

---

## 4. 键怎么算

```text
universe_hash(codes) = sha1( ",".join(sorted(unique(codes))) ) 的前 12 位十六进制
目录 = {DAY_CUBE_DIR}/v{FEATURE_SCHEMA}/{universe_hash}/{clock}/{date}.npz
```

实现：`core/research/feature_registry.py` 的 `universe_hash`。排序去重，与名单顺序无关。多一只或少一只，前 12 位就换成另一条路径。读取不做「差不多就行」的子集匹配。`load_day_cube` 用调用方传入的名单重算哈希，再对 meta 里的 `universe_hash`；不一致就返回空。

做 T 读取：

```text
_watching_codes_for_z()  → 观察池全名单
load_day_cube(date, watching_codes, clock=τ)
```

钟点先试 `HH:MM`，再试去掉冒号的安全名（如 `0935`）。τ 不是开盘时，**不**把 open 立方当作窗头成功。

---

## 5. 观察池没变，键为什么还是分叉

2026-10-16 的观察池是 800 只，`watching.json` 的 `updated_at` 为 `2026-10-16T20:37:48`，`max_size` 为 800。物化开头拿到的也是这 800 只，并且先用这 800 只去 `load_day_cube`。找不到，才从 L1 组装。

组装循环在 `materialize_l2`：当天既没有 `oo` 也没有 `tc` 的代码直接跳过，不进 `items`。随后 `ensure_day_cube_for_items` → `build_or_load`。**旧实现**对 `code_feats` 的键做哈希，也就是「当天有行的子集」。

被丢掉的是两只：

| 代码 | 观察池来源 | 落盘名称 | 现货快照（2026-10-13 `spot_a_em`） | L1 |
|------|------------|----------|--------------------------------------|----|
| `001232` | 2026-10-16 19:52 扩容补进 800，来源标「扩容」，名称空 | 空 | 嘉立创 | `oo` 的 `n_rows=0`，无 `tc` / `tc_0935` |
| `688825` | 手动，示例观察池里也有 | 长鑫科技 | 长鑫科技 | 同上 |

两只都在现货快照里，不是退市后残留的废代码。特征湖是空行，所以分钟组装把它们丢掉。剩下 798 只。

| 名单 | 哈希 | 盘上 |
|------|------|------|
| 观察池 800 | `7b6cc7befdf3` | `open/` 瘦立方（约 1 列）；没有 `0935/` |
| 有行的 798 | `75c09c954be1` | `0935/` 等分钟钟，约 300 列；`2026-10-16` 的 `n_codes=798` |

798 相对 800，只少 `001232` 和 `688825`。立方里没有多出来的代码。

`001232` 旁边是一串同样标成「扩容」、名称空白的 `001229`–`001236`。研究宇宙 `research_universe.json` 由本地日 K 仓灌入（约 1208 只）。观察池扩到 800 时，候选顺序是暂存 → 示例观察池 → 研究宇宙。`001232` 不在示例观察池里，是从研究宇宙按代码顺序补上的。`688825` 不是这批补位。

专用码表 `data/store/a_code_name.json` 不在盘上。观察池名称列对扩容票是空的，主表股票列会显示成「—」，看起来像股票表里没有这只。现货快照里名称是有的。

---

## 6. 旧协议下，分钟 L2 为什么等于白做

回测和下一轮物化的查找键都是 800 只的 `7b6cc7befdf3`。文件在 `75c09c954be1`。两边都 miss。

同一轮物化写出的 L1 没有白做。开盘瘦立方写在 800 只的键下，那部分对得上，但列不够窗头。

把旧 npz 复制到新目录也不够：`load_day_cube` 会核对 meta 里的 `universe_hash`。旧 meta 写的是 798 的哈希，复制后仍然 miss。

---

## 7. 现行键协议

和开盘瘦立方同一条原则：哈希跟请求名单走，矩阵跟有行的票走。

| 调用 | 传入的完整名单 |
|------|----------------|
| `materialize_l2` 非 open 钟 | 本次物化的 `code_list` |
| `warmup_watching_day_cubes`（open 全特征立方） | 本次预热的 `code_list` |
| `_align_item_to_watching_pool` 在立方 miss 后重建 | `_watching_codes_for_z()` |

`build_day_cube(..., universe_codes=)` / `ensure_day_cube_for_items(..., universe_codes=)`：

- `universe_hash` = `universe_hash(universe_codes)`。
- `codes` / `X_raw` 只含有特征字典的代码。
- 没传 `universe_codes` 时，哈希退回矩阵里的代码（单测和「这批 items 就是宇宙」的调用保持原样）。

因此再跑分钟 L2 时：`001232`、`688825` 仍会被组装循环跳过，矩阵仍约 798 行，目录变成 `7b6cc7befdf3/{clock}/`。`load_day_cube(观察池)` 命中。μ/σ 不含这两只空行，避免用一个虚构的 0 去拉偏截面。

覆盖门槛没变：有行的票数低于 `min_lake_frac`（默认一半，至少 2）时，这一天仍跳过，不写一个只有两三行、却盖着 800 只哈希的立方。

---

## 8. 做 T 打分在立方 miss 时

`_cs_z_horizon_feats`（`core/t0/score_policy.py`），τ 不是开盘：

1. 用观察池全名单加载 `clock=τ` 的立方。列覆盖窗头入模名，则用立方 μ/σ。
2. 覆盖失败：读同钟 L1（`tc_HHMM`，`_tau_clock_peer_rows`）。同伴不少于 2，则用这些行做截面 z。不回退到 open 立方，也不用开盘池里的行冒充这一钟。
3. 某一列在同伴里不足 2 个有限值：这一列不做 z。入模名里以 `raw_ath_` 开头、又没进 z 的列，记 0（当天均值）。稀疏的裸分钟键（如 `ret_open_to_tau`）保留原值，避免各钟被压成同一个 z。
4. 同伴仍不足 2：整行里未覆盖的 `raw_ath_*` 记 0。

开盘池缓存来自 open 的 `tc`，没有这一钟的 `raw_ath_*`。旧回退把 `CLOSE0≈1`、`SESSION_REMAIN≈235` 原样送进模型，就是个位数概率的直接原因。L1 回退和「未覆盖的 `raw_ath_*` 记 0」修的是这一层。要看到新概率，重跑的是做 T 回测，不是必须先重跑物化。

---

## 9. 重跑还是改键

两件事解决的不是同一个缺口。

| 动作 | 结果 |
|------|------|
| 只重跑分钟 L2，写入仍哈希「有行子集」 | 新文件还在 `75c09c954be1`，回测仍 miss |
| 只改键协议，不重跑 | 读取和**下一次**写入对齐；已有旧目录不动 |
| 先改协议，再跑分钟 L2 | 新文件写到观察池哈希下，快路径命中 |

协议已经改成第三行。本地 `day_cubes/v1/` 只剩当前观察池 `7b6cc7befdf3`（开盘瘦立方）。旧键 `75c09c954be1` 的分钟立方已不在盘上。以后宇宙再变，冷 hash 按 [feature-lake-daycube.md](./feature-lake-daycube.md) §10.4 清理。

重跑分钟 L2 的前提仍是：日线和对应分钟 Ready，τ 网格 L1 已在。空行的两只不会因为重跑就自动长出 `tc_HHMM`；除非日 K / 分钟先补上，否则矩阵里继续没有它们，只是键不再被它们拆开。

---

## 10. 怎么核对

某一天、某一钟，例如 `2026-10-16`、`09:35`：

1. 观察池名单做 `universe_hash`，应等于回测 `load_day_cube` 用的那 12 位。
2. 该目录下应有 `{clock}/{date}.meta.json`。`universe_hash` 与第 1 步相同。`n_codes` 可以小于名单长度（缺行的票不在矩阵里）。
3. `n_cols` 应覆盖窗头入模列。只有 `gap_pct` 的 open 瘦立方不算窗头命中。
4. 做 T 日志里，这一钟应出现 `horizon cs-z cube hit ... clock=09:35`（或 `0935`）。没有这行时，走的是同钟 L1，而不是 open。

单测：

- `tests/test_day_cube.py`：`test_missing_row_stays_on_requested_universe_hash`
- `tests/test_materialize_l1l2.py`：`test_missing_lake_row_keeps_requested_universe`
- `tests/test_horizon_cs_z_live.py`：立方 miss 时用同钟 L1；未覆盖的 `raw_ath_*` 记 0；稀疏 `ret_open_to_tau` 保留

---

## 11. 代码位置

| 职责 | 位置 |
|------|------|
| 哈希 | `core/research/feature_registry.py` `universe_hash` |
| 建立方 / 落盘 / 按名单加载 | `core/research/day_cube.py` `build_day_cube`、`build_or_load`、`load_day_cube`、`persist_tau_pool_as_cubes` |
| 从 items 建立方 | `core/research/batch_score.py` `ensure_day_cube_for_items` |
| 分钟 L2 跳过无行代码，并把完整名单传入哈希 | `core/research/materialize/l2_jobs.py` `materialize_l2` |
| open 预热同样传入完整名单 | `core/t0/score_policy.py` `warmup_watching_day_cubes` |
| 做 T 按观察池加载；miss 后再按观察池重建 | `core/t0/score_policy.py` `_cs_z_horizon_feats`、`_align_item_to_watching_pool` |
| 同钟 L1 同伴与 `raw_ath_*` 记 0 | `core/t0/score_policy.py` `_tau_clock_peer_rows`、`_impute_unzscored_raw_ath` |
