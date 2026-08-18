# Alpha / IC 补强（P0–P3）

[← 文档索引](README.md) · ŷ 主链 [predicted-score-chain.md](predicted-score-chain.md) · 北极星 [design-spine.md](design-spine.md)

**状态**：主干已接线（2026-08）。默认不静默改组 β / 不自动 promote。

---

## 目标

1. **分账**：绝对收益 ≠ α；报告必须能拆「β 腿 / 超额腿」。
2. **主 IC**：日频截面 **Spearman**；时序 Pearson / 全样本回放为辅并打标签。
3. **信号更像 α**：启发式路径启用行业/市值残差；ŷ 路径可选 `yhat_residual`（默认关）。
4. **敞口闸**：regime 仓位缩放可关；不把指数涨跌塞进选股因子。

---

## 已落地

| 阶段 | 交付 | 开关 / 入口 |
|------|------|-------------|
| **P0** | 纸面北极星 `benchmark_excess` · `alpha_beta_legs`；跟盘 KPI 显示超额/IR | `core/alpha_excess.py` · `north_star.build_north_star_report` |
| **P0+** | OOS 门禁附 `delta_excess_pp`；TopK `attach_benchmark_excess` | `bt_excess_attach` · `weight_oos_gate` |
| **P0++** | 回测导出强制「收益分账」；replay/中性化臂挂 `alpha_beta_legs`；OOS tip 显示 Δ超额 | `quant_report_export` · `quant_service_replay` · ols/suggest UI |
| **P1** | `ic_contract`；截面 IC 主字段改 Spearman；组 holdout IC 标 `chrono_pearson` | `ic_contract` · `factor_cs_ic` · `partition_loss` · `cluster_oos` |
| **P2a** | 启发式中性化接 industry/size；`yhat_residual` 可选 | `cross_section.yhat_residual`（默认 false） |
| **P2a+** | ŷ 残差影子 API + 复盘「ŷ残差对照」 | `POST /api/quant/yhat-residual/shadow` |
| **P2b** | `y_spec.excess_mode=index`；面板 `excess_mode=` | 研究臂 |
| **P2b+** | 绝对 vs 超额标签影子 API +「超额标签对照」 | `POST /api/quant/excess-mode/shadow` |
| **P3** | `regime.apply_position_scale` | 默认 true |

---

## 验收清单

- [ ] 跟盘北极星能看到「超额 / IR」（有指数数据时）
- [ ] 因子截面 IC 响应含 `primary_ic_kind=cs_spearman`；UI 文案含「主IC=截面Spearman」
- [ ] 组 holdout / k 选摘要含 `mean_yhat_ic_kind=chrono_pearson`
- [ ] OOS 门禁结果含 `delta_excess_pp` / `excess_compare`；tip 显示 Δ超额
- [ ] 回测 MD/导出含「收益分账（近似）」；KPI 超额副标题可含 β腿
- [ ] 复盘「ŷ残差对照」与「超额标签对照」均可跑通（不写盘）
- [ ] `yhat_residual=true` 时 meta 有 `yhat_residual.applied`
- [ ] `excess_mode=index` 面板 y 为股−指
- [ ] `apply_position_scale=false` 时 optimize scale=1

---

## 明确不做（本轮）

- 股指对冲 / 多空市场中性
- rem ŷ 升主排序
- 静默把 `excess_mode=index` 或 `yhat_residual` 写成生产默认
- 用全样本 IC 替代 holdout 选模

---

## 建议下一步

1. 人审跑「ŷ残差对照」+「超额标签对照」；优则分别开 `yhat_residual` / `excess_mode=index`  
2. 观察 OOS tip 的 Δ超额是否与 ΔOOS 同向（不同向时优先信超额）  
3. 大宇宙 PIT 能开则开，报告红字「非 PIT」
