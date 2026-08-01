# 权重建议深化方案（IC / OLS / 配权）

目标：把研究枢纽「建议」从**单票启发式**推进到更接近专业研究链路的**可审证据摘要**，仍不自动写盘。

## 目标链路

```text
截面 IC / ICIR  →  （弱证据）OLS β  →  约束小步 Δ  →  OOS / 回测门禁  →  人审 promote
```

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0** | 默认用研究池 **截面 IC + ICIR** 驱动建议；\|ICIR\| 门槛 + 按 ICIR 缩放步长；表内展示 ICIR / 证据来源 | ✅ |
| **P1** | IC 弱时 OLS 回退 / 近零降权；对齐 CS-IC 输入 | ✅ |
| **P2** | 建议权 vs 当前权的 **OOS Top-K 门禁**（过门 → `promote_ready`） | ✅ |
| **P3** | 组内权重上限、零权冻结、与 live regime 白名单软提示 | ✅ 轻量 |

## P0/P1 规则

对每个 `signal_config.weights` 因子：

1. **强截面证据**：`n` 足够，且 `|IC| ≥ 0.03`，且 `|ICIR| ≥ 0.25`  
   → `δ = sign(IC) · 0.03 · clamp(|ICIR|/0.5, 0.5, 1.5)`
2. **否则 OLS**：`|β| ≥ 0.05` → `±0.02`
3. **否则近零 IC** → `−0.015`
4. **零权冻结**：原权重为 0 的因子（如 `money_flow`）不复活
5. **组内上限**：`factor_groups` 各组和 ≤ 0.45，超限等比压缩后归一化
6. 相关冗余 / 白名单外上调 → `constraint_warnings`

## P2 OOS 门禁

- 同一研究池、同一 Top-K 参数，分别用当前权 / 建议权跑 `backtest_topk`（经 `signal_config_overlay`）
- 权益曲线后 30% 为 OOS（`split_oos_summary`）
- **过门**：建议 OOS ≥ 当前 OOS − `oos_tol_pp`（默认 1pp），且不新增 OOS 失败旗标
- `promote_ready = passed ∧ ¬skipped`；导出 diff 带 `apply_note`；未过门时 UI 二次确认

## 非目标

- 自动写入 `signal_config.json`
- 完整均值方差 / 风险平价求解
- raw OLS β → 权重占比

## 验收

- `ic_mode=cs_ic` 时表含 ICIR；建议响应含 `oos_gate` / `promote_ready`
- 单测：ICIR 门槛、组上限、overlay、OOS 门禁 mock
- `ASSET_V` 与页内说明同步
