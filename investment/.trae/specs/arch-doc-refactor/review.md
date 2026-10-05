# architecture.md 拆分与瘦身 - 独立审查

- [ ] CP-R1: architecture.md 行数 ≤ 1400
  - **Type**: `rule`
  - **Covers**: AC-1
  - **Evidence**: Pending

- [ ] CP-R2: 7 个新文件（component/data.md, strategy.md, risk.md, rl.md + internal/framework-review.md, engineering-track.md, sqlite-migration.md）均存在且非空
  - **Type**: `rule`
  - **Covers**: AC-2
  - **Evidence**: Pending

- [ ] CP-R3: architecture.md 含端到端全链路 mermaid 图，覆盖数据/信号/决策/执行/反馈五环节
  - **Type**: `rule`
  - **Covers**: AC-3
  - **Evidence**: Pending

- [ ] CP-R4: architecture.md 含「如何扩展」章节，含新增因子/新增策略/新增数据源三小节
  - **Type**: `rule`
  - **Covers**: AC-4
  - **Evidence**: Pending

- [ ] CP-R5: architecture.md 中无残留指向已迁移章节的 `architecture.md#数据层/策略层/风控层/舆情层/强化学习/分钟线采集/日分钟线缓存/工程结构轨/代码框架梳理` 链接
  - **Type**: `rule`
  - **Covers**: AC-5
  - **Evidence**: Pending

- [ ] CP-R6: 外部文档（design-spine.md, quant.md, quant-ui.md, development.md）中指向已迁移章节的链接已更新至 component/ 或 internal/
  - **Type**: `rule`
  - **Covers**: AC-6
  - **Evidence**: Pending

- [ ] CP-R7: 新文件行数 ≥ 原对应段落行数（内容无丢失）
  - **Type**: `rule`
  - **Covers**: AC-7
  - **Evidence**: Pending

- [ ] CP-R8: 所有 7 个新文件首段含「← 文档索引」导航与 architecture.md 回链
  - **Type**: `rule`
  - **Covers**: AC-8
  - **Evidence**: Pending

- [ ] CP-R9: docs/README.md 索引含 component/ 条目（data/strategy/risk）
  - **Type**: `rule`
  - **Covers**: AC-9
  - **Evidence**: Pending

- [ ] CP-U1: architecture.md 总览信息密度与导航清晰度
  - **Type**: `rubric`
  - **Covers**: AC-10
  - **Scale**: 1-5
  - **Anchors**: 1 = 仍像大杂烩；3 = 有骨架但导航弱；5 = 总览定位清晰，组件入口明确
  - **Pass Threshold**: >= 4
  - **Evidence**: Pending

## Review History

### Review R1
- **Result**: `pass`
- **Evidence**: 独立审查代理验证全部 10 项检查点通过：architecture.md 1390 行（≤1400）；7 个新文件均存在且行数达标；端到端全链路 mermaid 图含 5 环节；如何扩展 3 小节齐全；architecture.md 与外部文档无残留旧锚点链接；新文件均含导航；README 含 component 条目；可读性评分 4/5（≥4 阈值）。

