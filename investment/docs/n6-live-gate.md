# N6 真·实盘准入备忘（仅文档 · 无代码）

[← 深化补强 S 轨](validation-strengthen.md) · [V 轨](strategy-validation-upgrade.md) · [产品边界](design-spine.md#产品边界现行)

**状态**：策略验证阶段 **不启动** OMS。本文仅在 [成熟闸门](validation-strengthen.md#8-s4--成熟闸门收口) / `GET /api/ops/maturity-gate` 通过后，作为另立项材料草案。

## 前置（须全部成立）

1. `GET /api/ops/maturity-gate` 显示 `ready_for_n6_review=true`（或书面豁免单项）  
2. 至少一套策略完成 IS + OOS/WF + 成本对照 + 暴露巡检 +（建议）因子截面 IC  
3. 合规确认：主体资格、风险揭示、人工确认下单流程  

## 立项后才做（本仓库现行禁止）

- 券商 API / 柜台连通  
- 订单状态机、部分成交、撤单  
- 真资金划转与强平  

## 建议产品形态（草案）

- 默认 **人工二次确认** 下单（预填 → 跳转官方 App / 柜台）  
- 纸面与实盘账户隔离；实盘 KPI 另列，不替换纸面北极星分子直至稳定  

## 明确不做（即便立项）

- 代客自动全权交易、保证收益、无审计黑盒下单  
