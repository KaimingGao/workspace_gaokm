# web/static

Web 前端静态资源（无构建链，原生 ES modules）。

HTML 由 [`web/page_html.py`](../page_html.py) 从 `templates/` + `partials/` 组装；勿再把整页写进 `index.html`。  
静态缓存版本：**只改** [`web/asset_version.py`](../asset_version.py) 的 `ASSET_V`（注入 `{{ASSET_V}}` / `window.__ASSET_V__`）。UI 契约见 [docs/quant-ui-standard.md](../../docs/quant-ui-standard.md)。

## 文件

| 文件 | 说明 |
|------|------|
| `templates/chat.html` | 对话工作台壳（左对话 / 右结果 Tab） |
| `templates/tool.html` | 工具全页壳（`/watching` `/strategy` `/follow` `/replay` `/quant` `/paper`） |
| `partials/*_panel.html` | 观察 / 模拟 / 量化 / 纸面 / 持仓面板（工作台嵌入与全页共用） |
| `partials/shared_dialogs.html` | 校验 / 用量 / README 弹窗 |
| `app.js` | 入口：按 `data-page` 调用各 `init*` |
| `js/shared.js` | 跨面板 helpers |
| `js/chat.js` | 左侧对话 |
| `js/results.js` | 右侧 Tab 切换与懒加载 |
| `js/paper.js` | 模拟页编排（轮询 `/api/jobs/paper`） |
| `js/paper/fmt.js` · `chart.js` | 模拟页格式化 / 净值图画布 |
| `js/evals.js` | 黄金用例校验 |
| `js/quant.js` | 量化研究台编排壳：`initQuant(ctx)` · 共享 `q` bag · 域 `install*` |
| `js/quant/*.js` | 研究台拆分：**域** `domain_watching` · `domain_backtest` · `domain_cluster` · `domain_suggest` · `domain_strategy` · `domain_export`；工厂 names · params · research_grid · bt_* · scoring · promote_* · cluster_* · watching_* · neutral_compare · factor_meta · factor_ic_ui · ols_ui · export_preview · strategy_* · bt_tables · universe_ui · watching_dq_ui · probe_ui · watching_insights_ui · watching_quotes_ui · watching_build_ui · watching_panel_ui · param_grid_ui · suggest_status_ui |
| `js/quant_scoring.js` | 兼容再导出 → `quant/scoring.js` |
| `js/platform.js` | 平台面板（Memory / Decision / 反馈 / 调度 / 预填） |
| `partials/platform_panel.html` | 平台面板（`/?tab=platform`） |
| `styles.css` | 工作台分栏 · 嵌入面板 |

## 路由

| 路径 | 页面 |
|------|------|
| `/` | 左 Agent 对话 · 右结果 Tab（默认观察；`?tab=portfolio` 等可指定） |
| `/watching` | 观察全页：名单 + 舆情 + **建仓入口** |
| `/strategy` | 策略全页 |
| `/follow` | 模拟全页：持仓加减仓 + 资金/成本模型 + 进阶调仓 |
| `/replay` | 回溯全页 |
| `/quant` | 研究枢纽（侧栏主入口：因子 · 运维 · 日报） |
| `/paper` | 302 → `/follow`（API 仍为 `/api/paper`） |

## 面板约定

- **观察页开仓、模拟页不开仓**：`watching_panel.html` 里的 `#watching-build-layer` 是唯一建仓弹层，行内「建仓」与「批量建仓」共用它，先 `preview` 再 `sync-paper`
- **模拟页操作区**：持仓下方为策略调仓 → 资金调整 → 交易记录 → 做 T（可选折叠）；人做加减仓在持仓表，规则调仓紧随其后，流水落在动作下方
- 面板内容是动态渲染的，跨 Tab 跳转链接用 `data-results-tab`（`results.js` 里委托绑定，后插入的也生效）

## 相关文档

- [架构总览 · 子目录 README 索引](../../docs/architecture.md#子目录-readme-索引)
- [quant-upgrade · P95–P97](../../docs/quant-upgrade.md)
