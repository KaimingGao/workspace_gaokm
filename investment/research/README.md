# research

研究 CLI 入口（daily 编排、纸面、watching、量化实验）。

## Idea 标准路径（≤5 步）

Web 优先；CLI 为离线/批处理备胎。

1. **改一参**：策略页 Monaco 编辑 `weights` / `rank.min_score` 等白名单键 →「校验 / Diff」→「保存草稿」（不写生产）
2. **回测**：回溯页设 `lookback` / `top_k` →「Top-K 回测」看净值与质量文案
3. **扫描表**：「跑网格」→ 热力/表 →「应用最优到回测表单」→ 再回测确认
4. **因子建议（可选）**：策略页「分析 IC / 权重」→「生成反馈建议」→ 补丁进编辑器
5. **人审晋升**：确认 diff 后「人审晋升到生产」→ 复跑回测 / 纸面日更验证

CLI 等价：`portfolio_backtest_run.py` · `backtest_scan.py` · `signal_diff_export_run.py`（晋升仍走 Web 或手工合并 `signal_config.json`）。

## 验证包（V4）

一键导出「可复现验证结论」：配置快照 + 回测 metrics + 源审计 + 成本 + 暴露 + 指纹。

| 入口 | 说明 |
|------|------|
| Web / API | `GET`/`POST` 验证包（平台 `export_validation_pack`）；与页内块序一致 |
| 样本状态 | `python3 research/sample_ops_run.py status` · `GET /api/ops/sample-status` |
| 成熟闸门 | `GET /api/ops/maturity-gate`（硬/软项；拟合 unavailable 不放行） |
| 落差归因 | `POST /api/ops/fit-gap`（平台按钮 · 历史回测自动附卡） |
| 财务预热 | 平台「预热财务多期」或 `fundamentals_warmup` / `ingest-history` |

第三人应能用导出包 + 同版 `signal_config` 复跑关键 metrics；TTM 须区分 real / seeded。

## 主要脚本

| 脚本 | 说明 |
|------|------|
| `daily_run.py` | preset 编排（advisor / quant / full） |
| `paper_run.py` | 纸面观察池 CLI |
| `watching_run.py` | watching 初始化与 refresh |
| `cross_section_run.py` | 横截面 Top N |
| `portfolio_backtest_run.py` | 组合回测 |
| `paper_rebalance_run.py` | 纸面调仓（显式 opt-in） |
| `t0_backtest_run.py` | 底仓做 T 日线代理回测（非实盘） |
| `quant_export_run.py` | 报告 MD/HTML 导出 |
| `sector_map_sync_run.py` | 观察池 ↔ `sector_map.json` 覆盖对齐 / dry-run |
| `sample_ops_run.py` | 样本运营：`status` · `ingest-history` · `prune-densified` · `persist-history` · `seed-ttm` · `seed-ladder` · `densify-snapshots` |
| `split.py` | 时序切分（`core/backtest` 亦用） |

## 约定

- 业务逻辑在 `core/` 与 `quant/`；此处为薄 CLI wrapper
- 统一 `import quant.*` / `core.*`
- **禁止**脚本静默覆盖 `signal_config.json`；草稿见 `core/signal_config_draft.py`

## 相关文档

- [quant-ops.md](../docs/quant-ops.md)
- [upgrade-refactor-plan.md · R2](../docs/upgrade-refactor-plan.md)
- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
