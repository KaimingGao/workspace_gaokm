# Web UI · 全面优化升级方案

[← 文档索引](README.md) · 差距依据 [quant-ui-gap.md](quant-ui-gap.md) · 现行契约 [quant-ui-standard.md](quant-ui-standard.md) · 壳层简史 [quant-ui-refactor-plan.md](archive/quant-ui-refactor-plan.md) · 产品主轴 [design-spine.md](design-spine.md) · 工程节奏 [roadmap.md](roadmap.md)

**规划日期**：2026-07-29  
**目标画像**：把 Web 从「能用的研究工具页」升到 **聚宽 / QuantConnect 级研究台 + 纸面准实盘工作台**——不是 Bloomberg / 迅投级交易终端。  
**差距来源**：[quant-ui-gap.md](quant-ui-gap.md) §5.B（路径内）· §5.A（边界外不做）· §5.C（须先改契约）。

---

## 0. 锁定原则（全方案约束）

| # | 原则 | 含义 |
|---|------|------|
| 1 | **产品边界** | 现行一切买卖 = 模拟账户（**策略验证**）；禁止 OMS / Level2 / Algo 单 / 真·实盘，直至验证成熟后 N6 |
| 2 | **视觉契约** | 默认保持「报告块序 · 浅色 · 密度克制」；**不**默认改成暗色盯盘终端 |
| 3 | **侧栏六格** | 不擅自加第 7 主入口；新能力落入现有页「唯一任务」或折叠/进阶 |
| 4 | **技术栈节奏** | **W0–W2 坚持 vanilla**（见 Cursor `investment-web-ui`）；React / Dock / 全站 WS 仅 **W4 闸门后** |
| 5 | **后端并行** | UI 升级不替代 N 轨加深（财务 PIT · QP · 冲击成本）；缺后端的报告块标「待数据」不硬画假数 |
| 6 | **验收可演示** | 每阶段至少一条「打开页面能看见」的验收物 + 对应测试 |
| 7 | **对齐产品北极星** | 优先抬高纸面夏普/卡玛可见性、TTM（研究台吞吐）、回测–纸面拟合；勿用模块填空替代三项乘积（见 [design-spine · 产品北极星](design-spine.md#产品北极星)） |

**成功一句话（路径终点）**：研究员能在 Web 上完成「看数 → 跑回测看专业报告 → 建仓/调仓可审计 → 数据质量与风控一眼可查」，并能看见 **纸面风险调整收益与回测–纸面拟合**；图表与表格达到工业件水平；仍不冒充实盘终端。

---

## 1. 现状基线（已完成，不重复做）

| 阶段 | 状态 | 内容 |
|------|------|------|
| 壳 P0 | ✅ | 六模块侧栏 · 顶栏 · 深浅色 |
| AI P0.5 | ✅ | `/` 仪表盘 · ⌘K 抽屉 · `/chat` |
| 仪表盘 P1 / P1.5 | ✅ | 指标 · 待办 · 净值 · 五问 · 持仓切片 · A 股色 |
| 产品后端 P0–P2++ | ✅ | 五问 · 日更 · 限额 · 撮合近似 · 预算 lite 等（见 roadmap） |

**未完成的旧 P1 尾巴**（并入下方 W0）：

- [x] AI 抽屉结构化 artifacts → 跳转业务页（W0.4）  
- [x] 推送（页内告警铃 + 可选浏览器 Notification；出站文件/Webhook 已有）

### 落地进度（2026-07-29）

| 项 | 状态 |
|----|------|
| W0.2 API 降级 Banner + `apiFetch` | ✅ |
| W0.3 仪表盘 last-known / 刷新戳 | ✅ |
| W0.4 AI artifacts 跳转 | ✅ |
| W0.1 `paper/ops_ui.js` + `holdings_sort.js` | ◐ 持续 |
| W0.5 观察表分页（≥80） | ✅ |
| W1 Lightweight Charts + 成本/归因 | ✅ |
| W2 数据质量 / 风控折叠 | ✅ |
| **W2.5** 审计时间线 `/api/audit/timeline` | ✅ |
| **推送** 顶栏铃铛 + Notification | ✅ |
| W3.1–W3.5 | ✅ |
| W3.6 画线工具 | 跳过 |
| **W4** React 岛 / Dock / WS / 密度 | ✅ |
| **W5** 深化：实况净值 · 持仓分页 · T0 拆分 · 密度/命令 | ✅ |

---

## 2. 总览：四条工作流 + 一期闸门

```text
W0 工程债与降级 UX  ──►  W1 图表+回测报告  ──►  W2 质量+风控审计台
                                                      │
                         W3 研究工作台加深 ◄───────────┘
                                                      │
                         W4 可选现代化（契约闸门）◄─────┘ 仅当 W1–W2 验收通过
```

| 工作流 | 主题 | 消化 gap | 粗估 | 依赖 |
|--------|------|----------|------|------|
| **W0** | 工程债 · 降级 · AI 收尾 | B5、B6；旧 P1 | 1～1.5 周 | 无 |
| **W1** | 专业图表 · 回测报告 | B1、B2 | 2～3 周 | W0 图表挂载点稳定 |
| **W2** | 数据质量 · 风控/审计聚合 | B3、B4 | 2～3 周 | 现有 health / ops / DecisionRecord API |
| **W3** | 研究台加深（编辑·扫描·快捷） | §1.1、参数矩阵、键盘 | 3～4 周 | W1 报告组件可复用 |
| **W4** | React 岛 / Dock / WS（可选） | §5.C、技术栈 | 闸门后另估 | **须改 quant-ui-standard** |

**明确不做（策略验证阶段）**：OMS/EMS、TWAP/VWAP、Level2、Time&Sales、多账户矩阵、网关 CPU 监控、暗示真券商下单。验证成熟后再评估 N6。

---

## 3. W0 · 工程债与韧性（先稳再炫）

**目标**：主路径可维护、失败可感知；为 W1 腾出干净挂载点。

### 3.1 交付包

| ID | 项 | 落点 | 验收 |
|----|----|------|------|
| W0.1 | 拆分 `paper.js` | `web/static/js/paper/*`（holdings / rebalance / ops / boot） | 模拟页行为不变；`test_web_api` + 手工加减仓/调仓 |
| W0.2 | 网络 / API 降级 Banner | 顶栏或 `.page-main` 顶条 | 断 API / 5xx 显示醒目条；恢复后自动消；仍展示 last-known |
| W0.3 | 仪表盘刷新状态 | `dashboard.js` | 轮询失败不白屏；meta 提示「上次成功时间」 |
| W0.4 | AI 抽屉 artifacts | `ai_drawer.js` | 结构化结果带「去观察/模拟/回溯」跳转 |
| W0.5 | 主表虚拟化准备 | 共用 table helper（先观察/持仓行数阈值） | ≥200 行不卡顿或文档标明阈值；可先简化为分页 |
| W0.6 | 测试补强 | `tests/test_*web*` | dashboard health 聚合、drawer 跳转至少各 1 测 |

### 3.2 不做

引入 React、Dock、WebSocket、改默认暗色。

### 3.3 契约变更

基本无；Banner 复用告警边框色阶（standard 已有）。

---

## 4. W1 · 专业图表与回测报告

**目标**：对齐聚宽式「指标 → 曲线 → 表」；图表达到可交互工业件。

### 4.1 图表（gap B1）

| ID | 项 | 落点 | 验收 |
|----|----|------|------|
| W1.1 | 引入 **Lightweight Charts**（CDN 或 vendor） | 观察详情 K 线替换自绘 | 十字光标 · 时间/价格轴 · 缩放拖拽 |
| W1.2 | 叠加 MA（5/10/20） | 同上 | 可开关 |
| W1.3 | 仪表盘 / 模拟净值曲线迁移 | `#dash-equity-chart` · follow 曲线 | 与现 snapshots 数据一致；tooltip 显示日期与权益 |
| W1.4 | 回测权益 / 基准对照 | `/replay` | 策略线 + 可选基准；图例清晰 |

画线工具（趋势线/斐波那契）**降级为 W3+**，不挡 W1 验收。

### 4.2 回测报告专业化（gap B2）

| ID | 项 | 落点 | 验收 |
|----|----|------|------|
| W1.5 | 成本拆解块 | replay 指标区 | 佣金 / 印花税 / 滑点（或 simple_cn 分项）可看见；`zero` 对照标注教学 |
| W1.6 | 归因表 UI | 接 `attribution` API | 选股超额 + 行业/个股贡献表；脚注「非完整 Brinson」 |
| W1.7 | 成交样本增强 | 已有样本表 | 信号日 vs 成交价/是否涨跌停跳过 列齐全 |
| W1.8 | 报告导出 | MD/HTML 或打印友好 | 一键导出与页内块序一致 |

### 4.3 后端配合（可并行）

- 冲击成本写入回测引擎统计 → 解锁 W1.5 真数（见 roadmap 模块 5）  
- 归因 API 字段稳定 → W1.6  

缺数时 UI 显示「模型未启用」而非空表误导。

### 4.4 验收清单（W1 出门）

- [x] 观察页 K 线具备十字线与 MA  
- [x] 回测页可见成本拆解 + 归因表 + 双线净值  
- [x] `ASSET_V` bump；说明书 [quant-ui.md](quant-ui.md) 补「怎么读回测报告」一小节  
- [x] standard 组件白名单增加 `.quant-chart` / `.quant-attr-table`（或等价）

---

## 5. W2 · 数据质量与风控/审计台

**目标**：把散落在五问/待办里的「质量与风险」收成可巡检的一屏；**不加第 7 侧栏**。

### 5.1 信息架构落点（裁决）

| 能力 | 落点 | 理由 |
|------|------|------|
| 数据质量看板 | **数据中心 `/watching`** 折叠「数据质量」+ 平台面板摘要 | 对齐「数据中心」命名；不改六格 |
| 敞口 / 限额 / 拦截流水 | **策略中心 `/strategy`** 折叠「风控与敞口」+ 仪表盘待办跳转 | 限额已在策略；审计只读 |
| promote / Decision 时间线 | **系统设置** 平台面板强化「审计」区 | 已有 feedback / 决策碎片 |

若后续证明必须独立主入口 → **先改** [quant-ui-standard IA 表](quant-ui-standard.md)，再加导航。

### 5.2 交付包

| ID | 项 | 验收 |
|----|----|------|
| W2.1 | 质量看板：最新拉取缺失率、`fallback_count`、源、复权标记 | 观察池维度可列表；点行看详情 |
| W2.2 | 质量进仪表盘待办规则 | 严重质量问题进 issues；手动模式 sources 空保持 warning（已修） |
| W2.3 | 敞口摘要：行业权重、单票上限、预算 scale | 与最近一次调仓/日更一致 |
| W2.4 | 拦截流水：近 N 次 risk_blocks | 时间、标的、原因；可跳模拟 |
| W2.5 | 审计时间线：promote / 配置建议 / 日更结果 | 平台面板一页可翻 |
| W2.6 | 只读限额 UI 与文案统一 | 禁止在此页静默写 `signal_config` |

### 5.3 验收清单（W2 出门）

- [x] 不打开五问也能答：「数据这周烂不烂」「风控拦过谁」  
- [x] 待办 → 对应折叠区深链可用  
- [x] 文档：standard 页面契约更新 watching/strategy/platform  

---

## 6. W3 · 研究工作台加深

**目标**：缩小与 Quant IDE / 参数研究的差距；仍非 Jupyter 全集。

### 6.1 交付包（按优先级）

| ID | 项 | 说明 | 验收 |
|----|----|------|------|
| W3.1 | **Command Palette**（⌘⇧K 或扩 ⌘K 模式） | 跳转六模块 + 常用命令；与 AI 抽屉模式切换 | 键盘可达各主页 |
| W3.2 | 策略规格只读「结构化编辑预览」 | ~~Monaco 草稿~~ → **已退役**；限额走策略卡，选股权走研究枢纽 | 不静默写 signal_config |
| W3.3 | 参数扫描 MVP | Top-K / 窗口网格；结果表 + **热力简图**（可用 ECharts 或 canvas） | 至少 2 维参数；最优标出 |
| W3.4 | 因子字典侧栏 | 因子名 → 定义（复用 factor-tip 数据）可插入到 AI 提问 | 悬停/点击一致 |
| W3.5 | Notebook | **不做内嵌 Jupyter**；改为「导出研究包」（参数+结果 JSON）供本地 Notebook | 文档说明工作流 |
| W3.6 | 画线工具（可选） | 基于 Lightweight Charts 插件或降级跳过 | 不挡 W3 主验收 |

### 6.2 与后端

参数扫描需稳定回测 job API（已有 jobs 则复用）；避免同步长请求卡死 UI（busy + 进度）。

### 6.3 验收清单（W3 出门）

- [x] 键盘可完成「打开回溯 → 跑网格 → 看热力」  
- [x] Monaco 规格草稿路径已退役（选股权 → 研究枢纽）  

---

## 7. W4 · 可选现代化（契约闸门）

**状态（2026-07-29）**：闸门已开并落地最小包——非全站 React、非交易终端。

| 议题 | 落地 | 边界 |
|------|------|------|
| React 岛 | `#dash-equity-react-root` + CDN React 18 + Lightweight Charts | 禁止 Ant Design Pro / 全站 SPA |
| Dock | `/watching` `#research-dock` 表\|图可拖拽；`localStorage` | 仅研究分屏；无盘口 |
| WebSocket | `GET /ws/live` · `js/live_ws.js` · ~3s 推送 | 纸面摘要/健康/告警；不断线假 Tick |
| 密度 | 顶栏 `#btn-density` · `html[data-density=compact]` | 默认仍舒适/浅色报告感 |

**W4 不做**：OMS、Level2、多账户、真券商文案。

闸门检查表：

- [x] `quant-ui-standard` W4 条款已写  
- [x] 验收：仪表盘净值走 React 岛（CDN 失败回退原生图）；观察页可分屏；WS 断线亮 Banner  
- [x] 性能预算：首屏、表 500 行、图 5y 日线（持续观测）

---

## 8. 与后端 N 轨的咬合（避免 UI 空转）

| UI 工作流 | 需要后端加深时 | 文档 |
|-----------|----------------|------|
| W1.5 成本真数 | 冲击成本进 engine stats | roadmap 模块 5 / matching |
| W1.6 归因加深 | 完整 Brinson / 因子暴露（可选） | attribution |
| W2.1 质量 | 财务 PIT、源级质量指标 | data-layer |
| W2.3 敞口 | QP / 风险预算已有 lite；完整 QP 增强条 | risk-layer · portfolio_optimize |
| W3.3 网格 | 回测 job 队列稳定 | jobs API |

UI 阶段可先用现有字段；后端到位后加列，不阻塞 W1/W2 出门。

---

## 9. 里程碑与粗排期

| 里程碑 | 内容 | 建议顺序 |
|--------|------|----------|
| **M0** | W0 完成 | 立即 |
| **M1** | W1 图表+报告 | M0 后 |
| **M2** | W2 质量+风控审计 | 可与 W1 后半并行（不同页） |
| **M3** | W3 研究加深 | M1 后 |
| **M4** | W4 闸门评审 | M2 后；通过才开工 |

并行建议：W1 前端图表 ∥ 后端冲击成本；W2 UI ∥ 财务 PIT（不挡 W2.1 日线质量）。

粗日历（单人全职约）：**M0～M2 ≈ 6～8 周** → 研究台「专业报告感」达标；M3 再 +3～4 周；M4 另议。

---

## 10. 每阶段通用工程规范

1. 改 UI 先更新 [quant-ui-standard](quant-ui-standard.md) 页面契约 / 白名单。  
2. 只 bump `web/asset_version.py` 的 `ASSET_V`。  
3. 危险动作：busy / disabled；错误进 meta。  
4. 测试：相关 `tests/test_*web*` + 关键路径手工清单。  
5. 说明书 [quant-ui.md](quant-ui.md) 同步「怎么用新块」。  
6. 禁止在文案/按钮暗示真实下单。

---

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 图表库体积拖慢首屏 | vendor + 按页动态 `import()`；观察/回溯才加载 |
| `paper.js` 拆分回归 | 先抽纯函数/渲染，行为单测 + 手工脚本 |
| 质量/风控页变「第二仪表盘」 | 严守折叠 + 深链；主叙事仍在 `/` |
| 过早上 React/Dock | W4 闸门；W0–W2 规则写死 vanilla |
| 范围膨胀到 OMS | 每周对照 gap §5.A；PR 描述禁止出现实盘词除非 N6 |

---

## 12. 文档与职责分工

| 文档 | 职责 |
|------|------|
| [quant-ui-gap.md](quant-ui-gap.md) | **为什么缺**（对照专业终端） |
| **本文** | **怎么补**（阶段、交付、验收、闸门） |
| [quant-ui-refactor-plan.md](archive/quant-ui-refactor-plan.md) | 壳/AI 已完成简史；指向本文 |
| [quant-ui-standard.md](quant-ui-standard.md) | 现行改法契约（W 阶段中增量修改） |
| [roadmap.md](roadmap.md) | 后端 N1–N6 / P0–P3；与本文 §8 咬合 |
| [upgrade-refactor-plan.md](archive/upgrade-refactor-plan.md) | 路径内下一程 R0–R5（KPI / PIT / 研究吞吐）；UI 跟随后端字段 |
| [design-spine.md](design-spine.md) | [产品北极星](design-spine.md#产品北极星)（三项乘积）· 能力地图 |

---

## 13. 立即开工建议（下一刀）

**W0–W5 主路径已落地。** 下一刀按「体感 × 工程债」：

1. **W0.1 续** — ✅ `paper.js` 已拆至 7 个子模块（holdings_ui · holdings_sort · logs_ui · rules_ui · t0_ui · ops_ui · chart）；`renderPaperAccountDetail` 仅做编排与 DOM 写入  
2. **回测–纸面拟合指标** — ✅ 仪表盘 4 指标卡（滚动纸面夏普/卡玛 · TTM · 回测–纸面 Corr/TE）；回测成功缓存 curve 至 localStorage  
3. **表格虚拟列表** — 观察/持仓 ≥200 行时真虚拟化（现为分页）  
4. **归因加深** — 完整 Brinson 仅后端就绪后加列  

勿回退到 OMS / Level2；性能预算（首屏 · 表 500 行 · 图 5y）持续观测。

### W5 深化包（已落地 2026-07-29）

| 项 | 落点 |
|----|------|
| 实况 → 净值曲线 | `dashboard.__investmentOnLivePaper` 合并 `snapshot_tail` |
| 实况 → 净值元数据 | 同步 `dash-chart-meta`（快照数 + 较上次涨跌） |
| 实况 → 交易执行顶栏 | `live_ws` 更新 `#follow-stats` |
| 实况 → 曲线防重复 | WebSocket 同一帧跳过重复重绘（更轻量） |
| 持仓分页 | `paper.js` ≥40 行用 `paginateItems` |
| T0 UI 拆分 | `paper/t0_ui.js` |
| 命令面板 | 密度 / 分屏 / 实况刷新 |
| 密度 CSS | 持仓 · 操作条 · Dock · 策略卡 |
