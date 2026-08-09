# Web UI 标准（研究台）

[← 文档索引](README.md) · 使用说明书见 [quant-ui.md](quant-ui.md) · 产品主轴见 [design-spine.md](design-spine.md)

本文是 **改 Web UI 的契约**：信息架构、页面契约、组件白名单、验收清单。  
说明书（怎么用）在 `quant-ui.md`；本文（怎么改才不算破坏）在此。

**产品边界**：UI 上一切「交易 / 持仓 / 调仓」均指 **模拟账户**；禁止暗示或接入真实券商下单。定位见 [design-spine · 产品边界](design-spine.md#产品边界现行)。

**视觉参考（只借信息层级，不借皮肤）**：聚宽 / 米筐「回测报告」块序（指标 → 曲线 → 表）；**默认仍不学 Bloomberg 盯盘密度**。  
**W4 闸门已开（2026-07-29）**：允许研究页 **Dock 分屏**、**React 岛**（图表/指标）、**WebSocket 实况**、**紧凑密度开关**；仍禁止 OMS / Level2 / 盘口 / 真券商。差距见 [quant-ui-gap.md](quant-ui-gap.md)；排期见 [quant-ui-upgrade.md](quant-ui-upgrade.md)。

**组件增量（W0–W4）**：既有 W0–W3 组件 + `#research-dock` · `#btn-density` · `live_ws` · `react_islands`（CDN React）。

---

## 1. 信息架构

**业务面（左侧六模块）** + **状态面（顶栏）** + **AI 控制面（抽屉，不占侧栏）**。

| 顺序 | 模块 | 路径 | 唯一任务 |
|------|------|------|----------|
| 1 | 策略中心 | `/strategy` | 策略卡限额 · 人审 promote；选股权在研究枢纽 |
| 2 | 数据中心 | `/watching` | 长期名单 + **开仓入口** |
| 3 | 交易执行 | `/follow` | 已有仓位账本（加减仓 · 清仓 · 调仓） |
| 4 | 历史回测 | `/replay` | 组合历史回测 |
| 5 | 研究枢纽 | `/quant` | **股票分组**（一组一表）· **落地向导**（对照→启用）· 探针；**不调仓** |
| 6 | 平台 | `/platform` | 态势收口 · 可生效偏好 · 调度/审计 |

| 控制面 | 路径 / 入口 | 唯一任务 |
|--------|-------------|----------|
| AI 命令抽屉 | 顶栏「AI 助手」· ⌘K | 自然语言驱动系统；结果回执在抽屉，重活落到业务页 |

### 进阶（有路由，不进左侧主链）

`/paper` · evals · usage。**AI 不占侧栏格**。全屏对话 `/chat` **已下线**（302 → `/watching`）；命令入口仅 AI 抽屉。

见 [quant-ui-refactor-plan.md](archive/quant-ui-refactor-plan.md)。

### 落点裁决（加功能前先答）

1. 落在哪一页的「唯一任务」里？  
2. 主路径正文，还是折叠「按需」？  
3. 是否已有白名单组件可复用？  
4. 是否需要人审（promote）还是只读？  
5. 若是自然语言命令 → AI 抽屉发起，结果落哪一业务页？

答不上来 → **先改文档契约，再改代码**。

---

## 2. 页面契约

每页只允许下列主区块；新增块须改本表。

### `/watching` 观察

| | 约定 |
|--|------|
| **唯一任务** | 维护名单；从这里建仓进模拟 |
| **主区必有** | 搜索加入 · 主表（行情/评分/倾向等）· 批量「加入纸面 / 移除」· **建仓记录**（流水，与模拟页交易记录同构） |
| **自动** | 名单加载后拉行情 / 评分摘要 / 情绪（不写 config） |
| **禁止** | 策略调仓确认流、净值大图、回测指标卡 |
| **忙/空** | 拉行情时 meta 提示；空名单引导搜索 |
| **决策归属** | 建仓金额/股数由**人**拍板 |

### `/follow` 模拟

| | 约定 |
|--|------|
| **唯一任务** | 管理已有仓位（**唯一**买卖/调仓写盘入口） |
| **主区必有** | 净值摘要 · 持仓表（含情绪徽章）· 操作区（交易/资金/策略调仓）· 分组打分状态 · 调仓时可显「五问」ops |
| **自动** | 加载后刷持仓并显示加载状态；情绪同源 `/api/watching/sentiment`（不写 config） |
| **按需** | 持仓区「刷新」重拉账户 / 评分 / 情绪 |
| **按需折叠** | 做 T · 对比 |
| **禁止** | 开新仓（引导去观察）；把研究枢纽整页塞进来；在研究页写 paper |
| **忙/空** | 调仓/日更按钮 busy；空持仓 →「去观察建仓」 |
| **决策归属** | 手动加减仓 = 人；`mode=active` 时预演调仓吃 live 组权簿；否则策略规则调仓 |

### `/replay` 历史回测

| | 约定 |
|--|------|
| **唯一任务** | 对观察名单做历史 Top-K 回测 |
| **主区必有** | 运行控件 · 指标卡 · 曲线 · 成交/折表（含 WF 若开启） |
| **禁止** | 纸面买卖按钮、观察名单编辑 |
| **忙/空** | 回测进度；无结果时说明「先跑回测」 |

### `/strategy` 策略

| | 约定 |
|--|------|
| **唯一任务** | 策略卡限额与人审晋升 · 风控审计；选股 α 在研究枢纽 |
| **主区必有** | 策略卡 · 限额只读 · promote 入口 · 因子字典 |
| **禁止** | 静默写 `signal_config`；整稿编辑 weights；把完整 quant 枢纽嵌进来 |
| **因子名** | 悬停显示定义（`factor-tip`） |

#### 配置写盘边界

- **选股真源**：研究枢纽 `ReturnScoreModel` / 分组 β → live promote
- **策略限额**：策略卡「晋升 / 晋升并应用到纸面」
- **ŷ 门槛**：页脚展示 `scoring` 滞回（买入/卖出）；**不得**在本页改 weights 或静默 promote

### `/quant` 研究枢纽

| | 约定 |
|--|------|
| **唯一任务** | 观察池按 OLS β 分组 → 一组一表建议权 → **人审落地**（不写全局 weights） |
| **主区必有** | 股票分组（跑分组 · 状态条 · 一组一表）· **落地条**（mode/version/覆盖率 · 对照/启用 · 双分样本）· 探针 |
| **按需折叠** | 全局对照（阈值/横截面）· 日报 · 回滚/关闭等次操作 |
| **禁止** | 把各组权合并进 `signal_config.weights`；跨组统一总榜冒充分组；无健康门禁直接 active；**本页写 paper / 调仓** |
| **落地两步** | ① `cluster-live/apply`（shadow）→ ② `mode=active`（门禁）；调仓经侧栏 `/follow` |
| **日更** | `mode=active` 时交易执行/日更默认吃 `cluster_book`；陈旧可自动降级 |
| **决策归属** | 晋升/启用 = 研究页人审；**买卖/调仓确认 = 交易执行页**；自动 OLS 重聚类不自动 promote |

### `/platform` 平台

| | 约定 |
|--|------|
| **唯一任务** | 验证闭环态势收口 + 可生效偏好 + 调度/审计 |
| **主区必有** | 偏好档案（风险风格/备注；**研究 horizon 只读**，编辑在研究枢纽）· 北极星 KPI · 审计时间线 · 纸面日更调度 |
| **按需折叠** | 反馈建议 · 非交易预填 · 决策记录可次要展示 |
| **禁止** | 静默写 `signal_config`；假装未接线字段为全局生产参数；开仓/调仓 |
| **决策归属** | 偏好写 `memory.json`；选股 β 经研究枢纽 live；策略限额经策略卡 promote |

<a id="仪表盘"></a>

### `/dashboard` 仪表盘

| | 约定 |
|--|------|
| **唯一任务** | 纸面组合报告墙：市场 · KPI · 风险 · 净值 · 仓位/信号（开盘先看这一屏） |
| **主区必有** | **概览**（指数/广度 + 纸面 KPI）· **风险**（摘要条 + 收益/波动卡）· **净值路径**（累计净值 · 可选沪深300 · 区间 tab）· **风险分解**（回撤 · 历史 VaR · 板块热力）· **仓位与信号**（配置条带 · 板块暴露 · 最新信号 · 策略卡） |
| **次要** | 无强制折叠次要带；动作落在侧栏业务页 |
| **数据来源** | 前端汇聚 `/api/dashboard/*`（market-overview / kpis / risk-metrics / nav-curve / sector-heatmap / signals / allocation / factor-exposure / drawdown / var-historical） |
| **禁止** | 下单 · 改观察名单 · 嵌全屏对话 · 堆完整回测报告 |
| **刷新** | 进页拉取；约 **90s** 轻量轮询；页头「刷新」 |
| **视觉** | 收益类用 A 股红涨绿跌；**质量类**（Sharpe/Sortino/Calmar/胜率等）用 `--ok` / `--danger`；配置用**横向条带**（不用饼图）；浅色默认 |

路径：`/dashboard`（`/` **302 → `/dashboard`**）。使用说明书见 [quant-ui.md · 仪表盘](quant-ui.md#仪表盘)。

#### 设计逻辑

**唯一任务**：开盘报告式态势感知，不是聊天台，也不是交易台。  
一屏回答：「市场怎样 → 账与风险怎样 → 净值轨迹 → 仓位/信号结构」。

**信息块序**（对标聚宽/米筐报告，不借 Bloomberg）：

```text
概览（行情+KPI） → 风险摘要 → 净值路径 → 风险分解 → 仓位与信号
```

先读数字与风险，再看结构；动作落在侧栏业务页，仪表盘只做摘要。

**主次分工**：
- **主区**：上述五段报告轨  
- **未挂载（可选/另开）**：调仓五问、待办条、策略指纹、React 净值岛（`#dash-equity-react-root`）——现行报告墙不强制

**边界**：不下单、不改名单、不嵌全屏对话、不堆完整回测报告。

#### 设计语言

**块序语言**：`指标 → 曲线 → 表/条`

**组件**：复用 `.dashboard-head` / `.dashboard-section*` / `.dashboard-card` / `.dashboard-kpi-*` / `.dashboard-chart-host`；图表 Lightweight Charts + DOM 条带/直方图。

**视觉约定**：
- **主列铺满 · 同壳 gutter**：`.page-main` 吃满侧栏右侧；左右仅 `--page-gutter-x`（默认 16px）  
- **收益** → `--color-up` / `--color-down`；**质量** → `--d-quality-pos` / `--d-quality-neg`（绑 `--ok` / `--danger`）  
- 数字等宽/tabular；配置与暴露用细条权重，**不用饼图墙**  
- 字号阶梯：Micro11 / Caption12 / Body13 / Section14 / Title18 / KPI20  

**一句话**：仪表盘是「纸面组合的报告封面」——报告块序 + 收益/质量分色 + 条带结构，把决策前置信息聚到一屏。

### `/chat` 全屏对话（已下线）

| | 约定 |
|--|------|
| **状态** | 路由 **302 → `/watching`**；入口已从侧栏/命令面板/AI 抽屉移除 |
| **替代** | 顶栏「AI 助手」· ⌘K 抽屉；`POST /api/chat` 仍服务抽屉 |
| **禁止** | AI 改写 `score` / `stance_label` |

### 进阶页（摘要）

| 页 | 唯一任务 |
|----|----------|
| `/paper` | 假账初始化 / 高级配置（日常用模拟） |
| `/quant` | 研究枢纽（分组·落地向导·探针·全局对照/日报） |
| 平台面板 | 偏好档案（研究 horizon 只读）· 北极星态势 · 决策 · 调度 · feedback · 预填 |

---

## 2.1 全站 Desk Shell（设计语义 · 必守）

各业务页（含进阶 `/paper` · evals · usage）共用同一套**报告台**语义，以仪表盘 / 研究枢纽为范本。禁止再发明平行页头 / 区段 / 卡片族。

| 层级 | 唯一 class 族 | 语义 |
|------|----------------|------|
| **页头** | `.dashboard-head`（可叠 `.quant-head` 粘性）· `.dashboard-eyebrow` · `h2` · `.dashboard-meta` · 可选 `.dashboard-head-actions` | Eyebrow=Desk 英文名 · 标题=中文页名 · meta=唯一任务一句 |
| **区段** | `.dashboard-section` · `.dashboard-section-head` · `.dashboard-section-title` · `.dashboard-section-desc` | 一块一事；页内可叠 `platform-section` / `strategy-section` / `quant-section` 作锚点，**标题必须用 dashboard-*** |
| **卡片** | `.dashboard-card` · `.dashboard-card-head` · 卡体 padding 复用 `.quant-pro-card-body` | 交互/表/图的容器；勿为单页新建 `*-card-v2` |
| **按钮** | `.dialog-btn` · `.dialog-btn.secondary` | 主/次动作 |
| **色义** | 收益 `--color-up/--color-down`；质量/闸门 `--ok/--danger`（或 `--d-quality-*`） | 研究质量指标**禁止**误用涨跌色 |
| **gutter** | 仅 `.page-main` 的 `--page-gutter-x/y` | `*-page` 左右 `padding:0`，勿按页覆写主列边距 |

**Eyebrow 对照**（勿自造第三种命名）：Portfolio / Research / Data / Execution / Backtest / Strategy / Platform / Paper / Evals / Usage + `Desk`。

---

## 3. 组件白名单

改 UI **优先复用**；禁止为单页发明第三种同类容器。

| 组件 | 用途 | 典型 class / 落点 |
|------|------|-------------------|
| **页壳** | 主列铺满 · 六页+仪表盘同壳同 gutter | `.page-main`（`--page-gutter-x/y`，默认 16/12）· `*-page` 页体左右 `padding:0` · 勿按页覆写主列边距 |
| **页头** | Desk 标识 · 唯一任务 meta | `.dashboard-head` · `.dashboard-eyebrow` · `.dashboard-meta`（§2.1） |
| **AI 抽屉** | 全局命令 | `#ai-drawer` · 顶栏 `#btn-ai-open` · ⌘K |
| **主表** | 名单/持仓/回测表 | `.quant-weight-table` + wrap；遵守四页表规范 |
| **图表 / 归因表** | 观察/回测曲线与归因表 | `.dashboard-chart-host` 优先；兼容 `.quant-chart-host` / `.quant-chart-wrap` + `.quant-attr-table` |
| **指标卡** | 回测/净值数字 | `.quant-metrics` / metric 行 |
| **五问条** | 调仓/日更可审计摘要（模拟页） | `#paper-ops-report` · `.paper-ops-report-grid`（`#dash-ops-report` 现行未挂载） |
| **策略卡** | 策略列表 | `.strategy-card` |
| **区段** | 一块一事 | `.dashboard-section*`（必用）· 页锚点可叠 `.quant-section` / `.platform-section` / `.follow-ops-card` |
| **卡片** | 区段内容器 | `.dashboard-card`（必用） |
| **主按钮 / 次按钮** | 动作 | `.dialog-btn` · `.dialog-btn.secondary` |
| **折叠** | 按需 | `<details class="quant-fold">`（观察/策略仍可折叠；研究枢纽主/次要块已直出，`#quant-daily-fold` 等为卡片锚点） |
| **状态行** | meta / 指纹 | `.quant-fingerprint` · 页顶 `.dashboard-meta` |
| **因子悬停** | 定义注释 | `.factor-tip` + `title` |
| **确认流** | 预演→确认 | 报告区 + 显式确认按钮（禁止一键静默成交） |
| **研究 Dock** | 观察名单 ↔ 图/详情分屏 | `#research-dock` · `.dock-pane` · `.dock-splitter`（仅 `/watching`） |
| **React 岛** | 可选挂载（现行仪表盘未用） | `#dash-equity-react-root` · `js/react_islands.js`（CDN；非全站 React） |
| **实况通道** | 纸面/健康/告警推送 | `GET /ws/live` · `js/live_ws.js`；断线复用 `#api-degrade-banner` |
| **密度** | 紧凑/舒适 | `html[data-density=compact]` · 顶栏 `#btn-density` |

**不要**：新圆角卡片体系（白名单外）、顶栏恢复横排五链主导航、页面级横向滚动表、盘口/Level2 模块、平行 `.quant-eyebrow` / `.strategy-section-title` / 裸 `platform-section > h3` 页头族。侧栏仅六模块（含研究枢纽）。
**W4 边界**：React **仅岛**，不替换六页壳；Dock **仅研究分屏**，不引入交易终端盘口。

---

## 3.1 W4 闸门纪要（已确认）

| 项 | 决议 |
|----|------|
| 取向 | 研究台 + 可选终端化能力；默认浅色报告感保留 |
| React | CDN 岛挂载；禁止引入 Ant Design Pro / 全站 CRA |
| Dock | 观察页表+图可拖拽分栏；布局存 `localStorage` |
| WebSocket | `/ws/live` 推送 paper 摘要 / health / alerts；≥2s 节流 |
| 仍禁止 | OMS、Level2、Time&Sales、多账户、真券商文案 |

---

## 4. 静态资源版本（单一来源）

| 文件 | 作用 |
|------|------|
| `web/asset_version.py` → `ASSET_V` | **唯一 bump 点** |
| `page_html.py` | 注入 `{{ASSET_V}}` 与 `window.__ASSET_V__` |
| `app.js` | 所有 `import(...?v=)` 使用 `window.__ASSET_V__` |

改 `styles.css` / 任一 `static/js/*` / partial 行为后：**只改 `ASSET_V`**，重启或刷新即可。  
禁止再手写分散的 `?v=p114` / `p248` 等。

## 4.1 性能预算（R5.4）

| 项 | 预算 | 说明 |
|----|------|------|
| 首屏可交互 | ≤ 3s（本机冷启） | 六页壳 + 关键 API；CDN 失败须有回退 |
| 主表行数 | **500 行**虚拟滚动不掉帧 | `virtual_table.js` · 观察/持仓岛；勿回退为全量 DOM |
| 净值/回测图 | **5 年**日线量级 | Lightweight Charts；超长序列先降采样再画 |
| 静态缓存 | bump `ASSET_V` | 禁止手写散落 `?v=` |

超预算须在 PR 说明原因与缓解（分页回退不算达标）。验收勾选见 §5。

---

## 5. PR / 改 UI 验收清单（必过）

改 `web/static/**` 或 `page_html.py` 时自检：

- [ ] 新块落在某页「唯一任务」内，或已更新本文页面契约  
- [ ] 未新增主导航项（除非文档同步改 IA 表）  
- [ ] 复用白名单组件；无新横滚主表  
- [ ] Desk Shell：页头 / 区段 / 卡片用 `.dashboard-*`（§2.1）；无平行 eyebrow/section-title 族  
- [ ] 四页主表：无横向滚动 · 股票名 ≥6 字规则 · 同壳 padding  
- [ ] 主路径危险动作有 busy / disabled；错误进 meta，不静默失败  
- [ ] 空态有一句引导（空名单 / 空持仓 / 未回测）  
- [ ] 已 bump `web/asset_version.py` 的 `ASSET_V`  
- [ ] 硬刷新验证：观察 / 模拟 / 策略 / 回溯至少扫一眼  
- [ ] 性能：主表未引入全量 DOM 大表；图窗口未无说明地超过 5y 预算  

因果与产品边界见 [design-spine · 因果链](design-spine.md#因果链已发生--影响估计--动作)。

---

## 6. 与说明书的分工

| 文档 | 读者 | 内容 |
|------|------|------|
| [quant-ui.md](quant-ui.md) | 使用者 | 怎么点、流程、[仪表盘怎么看](quant-ui.md#仪表盘)、主表规范摘要 |
| **本文** | 改 UI 的人 / Agent | 契约、白名单、验收；仪表盘设计逻辑/语言见 [§仪表盘](#仪表盘) |
| [quant-ui-gap.md](quant-ui-gap.md) | 产品 / 规划 | 相对专业交易终端的 UI 差距与分级（A/B/C） |
| [quant-ui-upgrade.md](quant-ui-upgrade.md) | 产品 / 执行 | W0–W4 升级方案、验收与闸门 |
| [design-spine.md](design-spine.md) | 产品 | 本质与模块因果 |

Cursor 规则：`.cursor/rules/investment-web-ui.mdc`（改 static 时自动带上）。
