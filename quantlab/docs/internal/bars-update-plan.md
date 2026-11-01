# 日线契约修补规划

[← 文档索引](../README.md) · **契约结论与细节以 [行情更新、缓存与预处理](./bars-update-cache.md) §8 为准**；本文只留改造顺序与剩余项。

> 状态：P1–P4 代码已落地；观察池日线 topup 已跑（798/800 齐 as-of，`000004` / `000016` 源侧停更已移出观察池）。全池 `mode=full` 仍可选（清从未重锚的旧复权头、补全 `amount`）。
> 运维脚本：`python3 scripts/run_bars_topup_once.py`（清同会话误写标记再 topup）。

## 一句话

记账先诚实 → 面板指纹看见中间 K 线 → 成交额进库 → 前复权重叠日变化就整段替换；已拼坏的仓靠一次 full 修掉，之后每天只补缺日。

## 锁定

| 做 | 不做 |
|----|------|
| 前复权在重叠日价位变化时整段替换 | 每个交易日对全池整窗覆盖 |
| `daily_bars.amount`；旧行空着继续用 `volume × close` | 分钟线加成交额；换 Parquet / 另起数仓 |
| 面板指纹覆盖窗内每一根；schema 升到 3 | 把开盘日打分缓存并进面板缓存 |
| `cache:stale` 算失败；有失败不写当日标记 | 用标记拦住下一次拉取 |
| 回测继续只看根数、忽略 TTL | 读仓时顺便刷新末根 |

## P1 · 刷新记账

`quant/research/bars_status.py` · `refresh_bars_only`：来源 `cache:stale`、空包、空 K 线计入 `failed_n`；`failed_n > 0` 不写当日标记。标记只表示「本会话日整批成功过」，供 `needs_force_latest_bars`，拉取前不读。测试 `tests/test_bars_status.py`。

## P2 · 面板指纹

指纹已迁至 `core/research/panel_fingerprint.py`；`bars_fingerprint` 覆盖日线与分钟线窗内每一根，`PANEL_CACHE_SCHEMA=3` 参与 fp 串；训练/回测只写读 L1 Feature Lake。测试 `tests/test_panels.py` / `tests/test_incremental_panel.py`。

## P3 · 成交额列

`daily_bars.amount` 可空。`core/store_bars_sqlite.py` 的 `_ensure_schema` 用 `PRAGMA table_info(daily_bars)` 检查，缺列则 `ALTER TABLE daily_bars ADD COLUMN amount REAL`；**不回填历史**，旧行保持 NULL 直到该日被成功写入覆盖。`core/bar_fields.bar_amount` 保持「有 `amount` 用它，没有则 `volume × close`」。JSON 后端已保留 `amount`。

## P4 · 前复权整段替换

规则：平时只补缺日（已有日不动）；远端重叠日开高低收与本地不一致 → 视为复权锚变了 → 再整窗拉一次并**替换**该 `(market, code, adjust_policy)` 的全部日线。`raw` / `hfq` 不走这套检测（默认 `qfq`）。

### 检测

`adapters/market/history.py`，增量拉取返回之后、`merge_bars_by_date` 之前：仅 `adjust=qfq` 且本地有末根；在远端结果找与本地末根同一天的 bar，找不到不判重锚；开、高、低、收任一相对差 > `1e-4` 或绝对差 > `0.01` → 重锚；第二次请求不带 `start_date`，`fetch_limit = max(请求 limit, len(本地) + 5)`。

### 替换，而不是再合并

仅当新序列根数 ≥ `min(本地根数, 本次请求 limit)` 的 90%，或 `date_min ≤ 本地 date_min`，才删除该键下全部日线再插入新序列；新序列更短且盖不住本地起点 → 不删库、返回 `cache:stale`（P1 计失败）。替换成功后仍按 `DAILY_BARS_MAX_KEEP` 裁剪。JSON 后端同规则：换文件内容为新序列。`QFQ_LONG_GAP_DAYS = 40` 保留为「很久没更新、重叠日检测不可靠」时直接整窗的入口。

### 已经拼坏的仓

末根已齐的序列不会进入 topup，重叠日检测也不会跑到它们；上线后做**一次** `mode=full`（走替换写），收盘后人工看着跑，失败票留在 `failed_count`，可再跑一轮。

### 测试

`tests/test_history.py`：重叠日一致只合并新日期；收盘价差一截 → 第二次无 `start_date` 拉取且库里不存在新序列 `date_min` 之前的 bar；第二次明显更短 → 库不被清空、来源 `cache:stale`；`adjust=raw` 时价格不同也不整段删除。

## 上线顺序

| 步 | 内容 | 数据影响 |
|----|------|----------|
| 1 | P1 记账 | 无；失败的刷新不再显示「今天已刷过」 |
| 2 | P2 指纹 | 下一次拟合重算面板；不删 `v2`，只是不再读 |
| 3 | P3 加列 | 打开库时 `ALTER`；旧行 `amount` 为空，Amihud 行为不变 |
| 4 | P4 代码 | 当日若无新 bar，topup 仍跳过已齐的票 |
| 5 | 一次 `mode=full` | 替换各票日线窗，写入 `amount`，末根对齐；面板下次拟合因指纹变化重算 |

## 验收命令

```bash
cd quantlab
python3 -m unittest tests.test_bars_status tests.test_bars_daily tests.test_history tests.test_panels tests.test_incremental_panel tests.test_store tests.test_data_service -v
```

全池 full 后抽几只近期分红票：本地首日至末日收盘与一次整窗前复权对齐；`amount` 在新窗内非空；`needs_force_latest_bars` 只在本批 `failed_count = 0` 时为否。

## 明确不改

- `load_portfolio_stock_bars` 的 `ignore_age` 和「根数不够才补远端」。
- 分钟线的同日整段替换、Ready 闸、新浪 5m。
- 研究服务拒绝 `quote_fallback`、24 小时日线 TTL、复权策略不一致则跳过缓存。
- `replay_score_cache` 的开盘日指纹算法。
