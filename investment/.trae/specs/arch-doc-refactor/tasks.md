# architecture.md 拆分与瘦身 - 实施计划

## Task 1: 创建 docs/component/data.md
- **Status**: `completed`
- **Priority**: high
- **Depends On**: None
- **Description**: 提取「数据层」全部内容写入 component/data.md，加导航，修内部链接
- **Acceptance Criteria Addressed**: AC-2, AC-7, AC-8
- **Completion Evidence**:
  - `wc -l component/data.md` = 423 行（原段约 423 行，完整保留）
  - 首段含 `← 文档索引` + `architecture.md` 回链
  - 包含 `## 数据层（Data Layer）` 与 `## 分钟线采集架构`

## Task 2: 创建 docs/component/strategy.md
- **Status**: `completed`
- **Priority**: high
- **Depends On**: None
- **Description**: 提取「策略层」全部内容，加导航，修内部链接
- **Acceptance Criteria Addressed**: AC-2, AC-7, AC-8
- **Completion Evidence**:
  - 235 行，含 `## 策略层（Strategy Layer）` 与 `## 策略设计文档模板`

## Task 3: 创建 docs/component/risk.md
- **Status**: `completed`
- **Priority**: high
- **Depends On**: None
- **Description**: 提取「风控模型」+「舆情与另类数据」，加导航，修内部链接
- **Acceptance Criteria Addressed**: AC-2, AC-7, AC-8
- **Completion Evidence**:
  - 275 行，含 `## 风控模型（Risk Layer）` 与 `## 舆情与另类数据`

## Task 4: 创建 docs/internal/ 下三个实现文档 + component/rl.md
- **Status**: `completed`
- **Priority**: high
- **Depends On**: None
- **Description**: framework-review.md（170行）、engineering-track.md（71行）、sqlite-migration.md（333行）、rl.md（143行）
- **Acceptance Criteria Addressed**: AC-2, AC-7, AC-8
- **Completion Evidence**:
  - 4 个文件均存在且非空，首段均含导航回链

## Task 5: 从 architecture.md 删除已迁移段落并瘦身
- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 1, 2, 3, 4
- **Description**: 删除数据/策略/风控/舆情/RL/框架梳理/工程轨/SQLite 八个段落，保留总览+子目录索引
- **Acceptance Criteria Addressed**: AC-1, AC-7
- **Completion Evidence**:
  - `wc -l architecture.md` = 1390 行（≤ 1400）
  - 不再包含 `## 数据层` / `## 策略层` / `## 风控模型` / `## 代码框架梳理` 等标题

## Task 6: architecture.md 新增端到端全链路架构图
- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 5
- **Description**: 新增 mermaid flowchart 覆盖 数据→信号→决策→执行→反馈，标注 canonical 代码入口
- **Acceptance Criteria Addressed**: AC-3
- **Completion Evidence**:
  - architecture.md L1293 `## 端到端全链路架构图`，含 5 个 subgraph（数据感知/信号打分/决策倾向/执行记账/反馈进化）+ 约束表

## Task 7: architecture.md 新增「如何扩展」一节
- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 5
- **Description**: 新增因子 / 新增策略 / 新增数据源三小节，各含 5 步操作指引与代码落点
- **Acceptance Criteria Addressed**: AC-4
- **Completion Evidence**:
  - L1341 `## 如何扩展` + L1345 `### 新增因子` + L1353 `### 新增策略` + L1361 `### 新增数据源`

## Task 8: 更新 architecture.md 内部链接
- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 5, 6, 7
- **Description**: 将所有指向已迁移章节的 `architecture.md#xxx` 改为 `component/` 或 `internal/` 路径
- **Acceptance Criteria Addressed**: AC-5
- **Completion Evidence**:
  - `grep -c 'architecture.md#' architecture.md` = 0（无残留）

## Task 9: 更新外部文档链接 + README
- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 1, 2, 3, 4
- **Description**: 更新 design-spine.md、quant.md、quant-ui.md、development.md 中链接；README 新增 component/ 与 internal/ 索引表
- **Acceptance Criteria Addressed**: AC-6, AC-9
- **Completion Evidence**:
  - `grep 'architecture.md#数据层' docs/` 无残留（不含 architecture.md 自身）
  - README.md 含 component/data/strategy/risk 三条目

## Task 10: 全量链接校验
- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 8, 9
- **Description**: 校验所有 component/ internal/ 链接目标文件存在，关键锚点存在
- **Acceptance Criteria Addressed**: AC-5, AC-6
- **Completion Evidence**:
  - 所有 component/*.md 与 internal/*.md 链接目标文件均存在（17 处 OK）
  - 10 个关键锚点在目标文件中均存在
  - 无残留失效 architecture.md# 层链接

## Task 11: 总览可读性复核
- **Status**: `completed`
- **Priority**: medium
- **Depends On**: Task 5, 6, 7, 8
- **Description**: 通读 architecture.md 衔接处，清理多余空行，确认总览定位清晰
- **Acceptance Criteria Addressed**: AC-10
- **Completion Evidence**:
  - 评分：5/5。总览骨架清晰（定位→设计原则→控制论→产品金字塔→分层架构→技术栈→组件详图→分层模块→目录结构→Canonical入口→组件导航→全链路图→如何扩展→子目录索引），各组件入口在导航表中明确，读者可一键跳转
