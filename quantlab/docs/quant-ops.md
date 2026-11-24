# 量化运维

[← 文档索引](README.md) · ŷ 契约见 [quant.md](quant.md) · Web 面板见 [quant-ui.md](quant-ui.md)

定时任务、preset 组合、报告归档与任务状态查询。运维改 β 否见下表；**不**在本文展开 ŷ 公式。

## 可交易研究宇宙

| 步骤 | API / 动作 |
|------|------------|
| 重建并对齐（**Web 唯一按钮**，数据中心概览） | `POST /api/quant/research-universe/rebuild-and-reconcile` |
| 只重建研究宇宙（运维/脚本分步） | `POST /api/quant/research-universe/rebuild-tradable` |
| 只对齐观察池（运维/脚本分步） | `POST /api/quant/research-universe/reconcile-watching` |
| 门槛配置 | `data/tradable_universe_policy.json` |
| 子集强制开关 | `QUANTLAB_WATCHING_SUBSET_OF_RESEARCH`（默认 `1`） |

面板只暴露合并入口；分步 API 供运维/自动化，不是双按钮。完成后重跑 ŷ 拟合（宇宙指纹会变）。

---

## 会话日开盘 L2 补齐（盘中）

live / 手动出分只读会话日 open L2（meta：`phase` provisional|final · `open_source` quote|daily）。缺覆盖返回 `materialize_required`，`request` 带 `do_session_open_l2`；消费侧**不**临时拼截面、不回退昨日立方。

| 动作 | API / 命令 |
|------|------------|
| 补齐会话日 open L2 | `POST /api/quant/materialize`，body：`{"do_session_open_l2": true, "do_l1": false, "do_l2": true, "clocks": ["open"], "families": ["oo", "tc"], "cycle_day": "YYYY-MM-DD"}`；`cycle_day` 缺省 `oo_cycle_date()` |
| 查进度 | `GET /api/jobs/materialize-l1l2?progress=1`（Job 槽 `materialize-l1l2`） |
| 命令行同步补齐 | `python3 -c "from core.research.materialize import run_materialize; print(run_materialize(do_session_open_l2=True, do_l1=False, do_l2=True, clocks=['open']))"` |

Web `/follow` 补 ŷ、策略调仓「09:30 复现」与自动调仓 worker 遇到该缺口会自动调用物化；编排走 `ensure_session_open_l2`（日线齐→lake 终态，否则行情临时立方），成功后重试本轮出分。见 `core/research/pit_session.py` · `session_open.py`。

---

## 每日 preset

| preset | 说明 | 包含步骤 |
|--------|------|----------|
| `advisor` | 投顾日常（工作日收盘后） | 离线 golden checklist（mock）；持仓规则日循环已下线 |
| `quant` | 量化研究 | watching 刷新 · sync watchlist · 横截面 · quant 日报 · 导出 md/html |
| `quant_paper` | 量化 + 纸面调仓 | quant 全流程 + 观察池 rank_lots 纸面调仓（显式 opt-in，非实盘） |
| `full` | 全量 | advisor + quant（不含纸面调仓与 Agent 回归） |

**CLI**：

```bash
cd quantlab
python3 research/daily_run.py --preset advisor --json
python3 research/daily_run.py --preset quant --json
python3 research/daily_run.py --preset quant_paper --json
python3 research/daily_run.py --preset full --json
```

**Shell 封装**（供 cron / launchd）：

```bash
bash scripts/daily_advisor.sh
bash scripts/daily_quant.sh
bash scripts/daily_quant_paper.sh
bash scripts/daily_full.sh
```

收盘 `paper_daily` / `scripts/daily_paper.sh` 已下线。开盘调仓在交易执行（自动调仓 worker）。

**Web / API**：

- `GET /api/daily/presets` — 列出 preset 与 flags
- `POST /api/daily/run` — body 可传 `{ "preset": "quant" }`；显式 flag 会覆盖 preset 对应项
- `GET /api/daily/last` — 上次任务摘要（`data/daily_last_run.json`）
- `GET /api/schedule/last` — 上次调度摘要（`data/schedule_last_run.json`）。`kind=paper_daily` 已拒绝。

`POST /api/daily/run` 且 `preset: "quant"` 即每日量化（研究日报，不是开盘调仓）。

**P23 日更预设**：`advisor` / `quant` / `full` / `quant_paper` 走 `POST /api/daily/run` 或 `research/daily_run.py --preset`。

---

## cron（Linux / macOS）

工作日 16:30 跑投顾日常：

```cron
30 16 * * 1-5 cd /path/to/quantlab && bash scripts/daily_advisor.sh >> data/logs/daily-advisor.log 2>&1
```

工作日 17:00 跑量化：

```cron
0 17 * * 1-5 cd /path/to/quantlab && bash scripts/daily_quant.sh >> data/logs/daily-quant.log 2>&1
```

可选周末 Agent 回归（需 `DASHSCOPE_API_KEY`）：

```cron
0 10 * * 6 cd /path/to/quantlab && python3 research/daily_run.py --eval-agent --json
```

---

## launchd（macOS）

1. 复制并编辑 `scripts/launchd/*.plist.example`，将 `CHANGE_ME` 替换为项目绝对路径
2. `mkdir -p data/logs`
3. `launchctl load ~/Library/LaunchAgents/com.quantlab.daily-advisor.plist`

---

## 报告归档

| 文件 | 说明 |
|------|------|
| `data/quant_daily.json` | 最新量化日报 JSON（API `/api/quant/last`） |
| `data/last_portfolio_backtest.json` | 最近一次 `/replay` 产品回测（刷新恢复，不重跑） |
| `data/last_t0_backtest.json` | 最近一次 `/replay` 做 T 回测（刷新恢复，不重跑） |
| `data/reports/quant_daily_YYYYMMDD.md` | preset quant/full 自动导出 Markdown |
| `data/reports/quant_daily_YYYYMMDD.html` | 同上 HTML |
| `data/daily_last_run.json` | 最近一次 daily 任务状态 |

手动导出：

```bash
python3 research/daily_run.py --quant-report --export-quant-report
```

---

## 前置条件

| 任务 | 需要先 |
|------|--------|
| `quant_paper` 纸面调仓 | `python3 research/paper_run.py --init` 或 Web「纸面」初始化 |
| `quant` / `full` watching | `python3 research/watching_run.py --init`（或 `bash scripts/setup_quant.sh`）：`watching.example.json` → `watching.json`。模板 `sources` 为空即手动名单；要按规则重算再写 `sources` 后 `--refresh` |

失败时 CLI 退出码为 `1`，JSON 中 `failures` 列出原因；Web 返回 HTTP 422 与同样结构。

---

## 健康检查与报告索引（P17）

| API | 说明 |
|-----|------|
| `GET /api/daily/health` | 聚合 daily 上次运行 + watching 健康 + 最近归档报告 |
| `GET /api/watching/health` | watchlist 数量、stale、issues/warnings |
| `GET /api/quant/reports` | 列出 `data/reports/quant_daily_*.md/html` |
| `GET /api/quant/reports/{filename}` | 读取单个归档报告 |

Agent：`quant(task=health)` · `quant(task=package_info)` 查看包模块树。量化 preset 跑完后会追加 `watching_health` 步骤（watchlist 为空等会记为失败）。

### AO 轨 · Agent × 调仓运维（`ops` Skill）

完整方案见 [internal/agent-ops-landing.md](internal/agent-ops-landing.md)。门面：`services/agent_ops.py` · 工具名 **`ops`**。

| task | 作用 | 写盘 |
|------|------|------|
| `ops_snapshot` | 读 `last_ops_report` 五问 | 否 |
| `rebalance_preview` | `path=holding_cycle\|watching_matrix\|sync_paper` dry_run | 否 |
| `ops_anomaly` | 告警 / risk_block / 审计尾 | 否 |
| `feedback_draft` | 配置建议 + `suggested_checks`；`scope=market_prior` 仅建议 M 层 prior 键 | 否（人审 promote / `POST /api/signal/config/market-prior`） |
| `prior_brief` | 盘前市场先验结构化简报（freshness · flags · checklist · regime） | 否 |
| `post_rebalance_review` | 调仓后只读对照 | 否 |
| `event_flags` | 新闻启发式旗标 | 否（规则消费另接） |
| `alpha_draft` | 因子假设草稿 | 否 |
| `view_bundle` | 观点包（默认不融合） | 否 |

CLI 抽查：`python3 -c "from services.agent_ops import AgentOpsService; print(AgentOpsService().run({'task':'prior_brief'}))"`

市场先验 × AI：Prior Desk「盘前简报 / 建议 prior 草稿」→ AI 抽屉 prefill；观察台行内 `AI` → `advise` + `market_prior`。数字不进 ŷ；Agent 不写盘。

量化 CI（9 quant_* + preset）与 daily 走 CLI / Eval API；包模块树见 `GET /api/quant/package`。归档报告的 `share_url` 由 API 取出。

Agent：`quant(task=daily_presets)` 列出 preset；`quant(task=config_diff)` 预览 signal_config diff；`quant(task=portfolio_bridge)` 汇总持仓/纸面/量化联动。

归档报告含 `share_url`（如 `/api/quant/reports/quant_daily_YYYYMMDD.html`）。导出 MD/HTML 顶部含 **一页摘要**（`GET /api/quant/export/summary`）。

### 持仓 ↔ 量化联动（P24）

| 入口 | 说明 |
|------|------|
| 交易执行 | 持仓在本页管理；联动不另开弹窗 |
| API | `GET /api/portfolio/quant-bridge`（只读：模拟持仓 ↔ 观察池重叠） |
| Agent | `quant(task=portfolio_bridge)` |

### Agent 周末回归（P24，可选）

需 `DASHSCOPE_API_KEY`，**不**加入 `ci_quant.sh` / PR CI：

```bash
bash scripts/agent_regression.sh
bash scripts/agent_regression_quant.sh
python3 evals/run_agent_check.py --case quant_portfolio_backtest
python3 evals/run_agent_check.py --quant-only --presets
python3 evals/run_checklist.py --mock --quant-only --presets
```

---

## Watching 编辑（P18）

观察台用搜索加入或移除名单。数据中心概览的观察池滑条改只数上限，「重建并对齐」严筛研究宇宙并裁观察池。没有「编辑 watching.json」按钮。

| API | 说明 |
|-----|------|
| `GET /api/watching/file` | 读取完整 watching JSON |
| `PUT /api/watching/file` | 保存并校验（与 portfolio 编辑同类） |

---

## Cron 失败检查（P18）

任务跑完后可用本地脚本判断是否应告警（非远端 webhook）：

```bash
# 量化 daily 后检查
bash scripts/daily_quant.sh && bash scripts/daily_check.sh

# 严格模式：尚无 daily 记录也视为失败
python3 research/daily_check.py --require-run --json
```

配合 crontab `MAILTO=you@example.com` 时，`daily_check.sh` exit 1 会触发邮件。

---

## 信号配置（P19）

没有「信号配置（只读）」面板。`signal_config.json` 用下面的接口读取。市场先验人审写入 M prior；ŷ 权重不从这里改。

| API | 说明 |
|-----|------|
| `GET /api/signal/config` | 完整元信息 + merged config |
| `GET /api/signal/config/file` | 同上（Web 编辑器只读预览） |

**不提供 PUT**：权重/阈值调整仍通过 IC 建议 → 导出 diff → 手动合并 `signal_config.json`（ŷ_oo 已切 LightGBM 后 weights 不进打分；无 TopK ŷ OOS 审批权重补丁）。

### Diff 预览（P20）

| API | 说明 |
|-----|------|
| `GET /api/signal/config/diff-preview` | 合并 quant_daily 中权重/阈值 diff（或即时计算） |

Web 研究枢纽不挂「预览 diff」。API 仍可用：`GET /api/signal/config/diff-preview`、`GET /api/signal/config/diff-export`。日报 preset `quant_paper` 仍走 `POST /api/daily/run`。

### 一键初始化（P21）

```bash
bash scripts/setup_quant.sh
```

### Diff 导出包

```bash
curl -s 'localhost:8000/api/signal/config/diff-export?use_saved=true'
```

`merged_patch` 须手动合并到 `signal_config.json`，系统不会自动写盘。

---

## 相关文档

- [quant.md](quant.md) — ŷ / stance / 回测–纸面契约
- [design-spine.md · 路线图](design-spine.md#能力评估与升级规划路线图视角) — 产品节奏
- [quant-ui.md](quant-ui.md) — 研究枢纽与侧栏页面
