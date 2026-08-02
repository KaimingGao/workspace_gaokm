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
| 1 | 策略中心 | `/strategy` | 策略规格 + 只读 IC/权重 / 人审 promote |
| 2 | 数据中心 | `/watching` | 长期名单 + **开仓入口** |
| 3 | 交易执行 | `/follow` | 已有仓位账本（加减仓 · 清仓 · 调仓） |
| 4 | 历史回测 | `/replay` | 组合历史回测 |
| 5 | 研究枢纽 | `/quant` | **股票分组**（一组一表）· 探针 · 进阶 live；全局对照/日报按需 |
| 6 | 平台 | `/platform` | 态势收口 · 可生效偏好 · 调度/审计 |

| 控制面 | 路径 / 入口 | 唯一任务 |
|--------|-------------|----------|
| AI 命令抽屉 | 顶栏「AI 助手」· ⌘K | 自然语言驱动系统；结果回执在抽屉，重活落到业务页 |

### 进阶（有路由，不进左侧主链）

`/paper` · evals · usage。**AI 不占侧栏格**。全屏对话 `/chat` **已下线**（302 → `/watching`）；命令入口仅 AI 抽屉。

见 [quant-ui-refactor-plan.md](quant-ui-refactor-plan.md)。

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
| **自动** | **次日预判**（主表「次日」列；名单加载后后台填充；不写 config） |
| **禁止** | 策略调仓确认流、净值大图、回测指标卡 |
| **忙/空** | 拉行情时 meta 提示；空名单引导搜索 |
| **决策归属** | 建仓金额/股数由**人**拍板 |

### `/follow` 模拟

| | 约定 |
|--|------|
| **唯一任务** | 管理已有仓位 |
| **主区必有** | 净值摘要 · 持仓表 · 操作区（交易/资金/策略调仓）· 调仓时可显「五问」ops |
| **按需折叠** | 做 T · 对比 |
| **禁止** | 开新仓（引导去观察）；把研究枢纽整页塞进来 |
| **忙/空** | 调仓/日更按钮 busy；空持仓 →「去观察建仓」 |
| **决策归属** | 手动加减仓 = 人；预演→确认调仓 = 策略规则（标出处） |

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
| **唯一任务** | 看/改规格草稿 + 只读因子建议 + 人审晋升 |
| **主区必有** | 策略卡 · 限额只读 · IC/权重分析区 · Monaco 草稿 · promote 入口 |
| **禁止** | 静默写 `signal_config`；把完整 quant 枢纽嵌进来 |
| **因子名** | 悬停显示定义（`factor-tip`） |

#### 规格草稿白名单与人审边界（R2）

- **可编辑顶层键**：`version` · `weights` · `hard_reject` · `rank` · `stance_thresholds` · `invalidation` · `relative_strength` · `regime` · `fundamentals` · `cross_section`（见 `core/signal_config_draft.ALLOWED_TOP_KEYS`）
- **草稿落盘**：`data/signal_config_draft.json`（非生产）；API：`/api/signal/config/draft/{validate,save,diff,promote}`
- **生产写入唯一路径**：人审「晋升」→ 先备份到 `data/config_backups/` 再写 `signal_config.json`
- **IC / feedback**：只读建议或补丁进编辑器；**不得**自动 promote

### `/platform` 平台

| | 约定 |
|--|------|
| **唯一任务** | 验证闭环态势收口 + 可生效偏好 + 调度/审计 |
| **主区必有** | 偏好档案（风险风格/备注；**研究 horizon 只读**，编辑在研究枢纽）· 北极星 KPI · 审计时间线 · 纸面日更调度 |
| **按需折叠** | 反馈建议 · 非交易预填 · 决策记录可次要展示 |
| **禁止** | 静默写 `signal_config`；假装未接线字段为全局生产参数；开仓/调仓 |
| **决策归属** | 偏好写 `memory.json`；生产权重仍只经策略页人审 promote |

<a id="仪表盘"></a>

### `/` 仪表盘

| | 约定 |
|--|------|
| **唯一任务** | 资产与系统态势概览（开盘先看这一屏） |
| **主区必有** | **策略指纹** · 核心指标（权益/现金/持仓/盈亏/仓位占用/回撤）· **净值曲线**（纸面 `snapshots`）· **调仓五问**（`ops_report`）· **待办条**（健康/告警/空仓引导）· **持仓切片**（Top 盈亏 + 评分）· **来源暴露**（`origin_summary`）· **今日动态** |
| **次要** | 健康明细 · 快捷入口 · AI 芯片（页底折叠，不与主态势同级） |
| **数据来源** | 前端汇聚 `/api/paper` · `/api/daily/health` · `/api/schedule/last`；五问 HTML 与模拟页共用 `formatOpsReportHtml`；不新增第三套首页专用账本 |
| **禁止** | 把全屏对话嵌成唯一主区；静默成交；观察名单编辑；完整回测报告 |
| **AI** | 芯片打开命令抽屉；全屏见 `/chat` |
| **刷新** | 进页拉取；交易时段轻量轮询（约 90s） |
| **视觉** | A 股红涨绿跌（`--color-up` / `--color-down`）；浅色默认 |

使用说明书（怎么点、怎么看一屏）见 [quant-ui.md · 仪表盘](quant-ui.md#仪表盘)。

#### 设计逻辑

**唯一任务**：开盘态势感知，不是聊天台，也不是交易台。  
一屏回答：「账怎样 → 有没有事要处理 → 组合结构如何 → 上次动作是否可审计」。

**信息因果链**（与产品主轴一致）：

```text
待办（阻断） → 总账指标 → 净值轨迹 → 五问审计 → 持仓/来源切片 → 时间线
```

先处理风险与异常，再读数字与结构；动作落在侧栏业务页，仪表盘只做摘要与跳转。

**数据策略**：不建第二套账本。前端汇聚现有 API；五问与模拟页共用同一套 HTML，避免「首页一套、交易页另一套」。

**主次分工**：
- **主区**：策略指纹、指标、曲线、五问、持仓、来源、动态  
- **次要**（折叠）：健康明细、快捷入口、AI 芯片  

AI 是控制面（顶栏/⌘K），不占侧栏、不占首页主叙事。

**边界**：不下单、不改名单、不嵌全屏对话、不堆完整回测报告——专业感来自「可决策摘要」，不是信息密度。

#### 设计语言

**块序语言**（对标聚宽/米筐报告，不借 Bloomberg）：

`指标 → 曲线 → 表/条`

读起来像研究报告，不像运营仪表墙。

**组件白名单**（不发明第三种容器）：

`.quant-metrics` · `.quant-section` · `.paper-ops-report` · `.quant-weight-table` · `.quant-fold` · `.quant-fingerprint`  
仪表盘五问容器：`#dash-ops-report`（与 `#paper-ops-report` 共用 `formatOpsReportHtml`）。

**视觉约定**：
- **主列铺满**：`.page-main` 吃满侧栏右侧剩余宽度（工作台，非博客居中栏）  
- **A 股红涨绿跌**（`--color-up` / `--color-down`）  
- **浅色默认**；深色可选  
- 数字用等宽/tabular；指纹用小号次要色  
- 告警用边框色阶（info 虚线 / warn / block），可点击跳转  

**密度克制**：六指标一排够用；持仓只 Top 切片；来源用细条权重，不用饼图墙；快捷与 AI 下沉，避免与资产态势抢层级。

**一句话**：仪表盘是「纸面准实盘的态势封面页」——报告块序 + 白名单组件 + A 股语义色，把决策前置信息聚到一屏，把动手留给业务模块。

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
| `/quant` | 研究枢纽（股票分组主路径·探针·全局对照折叠·日报） |
| 平台面板 | 偏好档案（研究 horizon 只读）· 北极星态势 · 决策 · 调度 · feedback · 预填 |

---

## 3. 组件白名单

改 UI **优先复用**；禁止为单页发明第三种同类容器。

| 组件 | 用途 | 典型 class / 落点 |
|------|------|-------------------|
| **页壳** | 主列铺满 · 四页+仪表盘同壳 | `.page-main`（`width:100%`）· `.watching-page` / `.follow-page` / `.strategy-page` / `.replay-page` / `.dashboard-page` |
| **AI 抽屉** | 全局命令 | `#ai-drawer` · 顶栏 `#btn-ai-open` · ⌘K |
| **主表** | 名单/持仓/回测表 | `.quant-weight-table` + wrap；遵守四页表规范 |
| **图表 / 归因表** | 观察/回测曲线与归因表 | `.quant-chart-host` / `.quant-chart-wrap` + `.quant-attr-table` |
| **指标卡** | 回测/净值数字 | `.quant-metrics` / metric 行 |
| **五问条** | 调仓/日更可审计摘要 | `#paper-ops-report` · `#dash-ops-report` · `.paper-ops-report-grid` |
| **策略卡** | 策略列表 | `.strategy-card` |
| **区段** | 一块一事 | `.quant-section` / `.platform-section` / `.follow-ops-*`（锚点：`#platform-audit-section`） |
| **主按钮 / 次按钮** | 动作 | `.dialog-btn` · `.dialog-btn.secondary` |
| **折叠** | 按需 | `<details class="quant-fold">`（折叠深链：`#watching-data-quality-fold` / `#strategy-risk-audit-fold` / `#quant-daily-fold`） |
| **状态行** | meta / 指纹 | `.quant-fingerprint` · 页顶 meta |
| **因子悬停** | 定义注释 | `.factor-tip` + `title` |
| **确认流** | 预演→确认 | 报告区 + 显式确认按钮（禁止一键静默成交） |
| **研究 Dock** | 观察名单 ↔ 图/详情分屏 | `#research-dock` · `.dock-pane` · `.dock-splitter`（仅 `/watching`） |
| **React 岛** | 仪表盘净值等可挂载岛 | `#dash-equity-react-root` · `js/react_islands.js`（CDN；非全站 React） |
| **实况通道** | 纸面/健康/告警推送 | `GET /ws/live` · `js/live_ws.js`；断线复用 `#api-degrade-banner` |
| **密度** | 紧凑/舒适 | `html[data-density=compact]` · 顶栏 `#btn-density` |

**不要**：新圆角卡片体系（白名单外）、顶栏恢复横排五链主导航、页面级横向滚动表、盘口/Level2 模块。侧栏仅六模块（含研究枢纽）。
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
