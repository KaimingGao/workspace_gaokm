# 量化运维（P16）

定时任务、preset 组合、报告归档与任务状态查询。

---

## 每日 preset

| preset | 说明 | 包含步骤 |
|--------|------|----------|
| `advisor` | 投顾日常（工作日收盘后） | 纸面观察池 · eval mock |
| `quant` | 量化研究 | watching 刷新 · sync watchlist · 横截面 · quant 日报 · 导出 md/html |
| `quant_paper` | 量化 + 纸面调仓 | 同上 + `paper_rebalance`（显式 opt-in，非实盘） |
| `full` | 全量 | advisor + quant（不含纸面调仓与 Agent 回归） |

**CLI**：

```bash
cd investment
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
bash scripts/daily_paper.sh   # P2 / N5：paper_daily（五问 + DecisionRecord + 衰减告警）
```

环境变量（`daily_paper.sh`）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `PAPER_DAILY_SIMULATE_BUY` | `0` | `1` 时日更模拟买入 |
| `PAPER_DAILY_STRATEGY` | `short` | 策略 ID |

**Web / API**：

- `GET /api/daily/presets` — 列出 preset 与 flags
- `POST /api/daily/run` — body 可传 `{ "preset": "quant" }`；显式 flag 会覆盖 preset 对应项
- `GET /api/daily/last` — 上次任务摘要（`data/daily_last_run.json`）
- `POST /api/schedule/run` — `{ "kind": "paper_daily", "strategy": "short", "simulate_buy": false }`
- `GET /api/schedule/last` — 上次调度摘要（`data/schedule_last_run.json`，含五问字段）

量化面板 **「每日量化」** 按钮等价于 `preset: "quant"`。平台页 **「运行纸面日更」** / 模拟页 **「纸面日更」** 等价于 `kind: "paper_daily"`。

**P2 演示闭环**：纸面日更告警 → 平台/模拟「从告警生成建议」→ 策略页人审 **promote** → 再回测/纸面。

**P23 运维区**：打开量化面板 →「运维状态」→ 下拉选择 `advisor` / `quant` / `full` / `quant_paper` → **「运行 daily」**（与底部快捷按钮共用同一 API）。

---

## cron（Linux / macOS）

工作日 16:30 跑投顾日常：

```cron
30 16 * * 1-5 cd /path/to/investment && bash scripts/daily_advisor.sh >> data/logs/daily-advisor.log 2>&1
```

工作日 17:00 跑量化：

```cron
0 17 * * 1-5 cd /path/to/investment && bash scripts/daily_quant.sh >> data/logs/daily-quant.log 2>&1
```

工作日 16:35 跑纸面日更（P2）：

```cron
35 16 * * 1-5 cd /path/to/investment && bash scripts/daily_paper.sh >> data/logs/paper-daily.log 2>&1
```

可选周末 Agent 回归（需 `DOUBAO_API_KEY`）：

```cron
0 10 * * 6 cd /path/to/investment && python3 research/daily_run.py --eval-agent --json
```

---

## launchd（macOS）

1. 复制并编辑 `scripts/launchd/*.plist.example`，将 `CHANGE_ME` 替换为项目绝对路径
2. `mkdir -p data/logs`
3. `launchctl load ~/Library/LaunchAgents/com.investment.daily-advisor.plist`

---

## 报告归档

| 文件 | 说明 |
|------|------|
| `data/quant_daily.json` | 最新量化日报 JSON（API `/api/quant/last`） |
| `data/reports/quant_daily_YYYYMMDD.md` | preset quant/full 自动导出 Markdown |
| `data/reports/quant_daily_YYYYMMDD.html` | 同上 HTML |
| `data/daily_last_run.json` | 最近一次 daily 任务状态 |

手动导出：

```bash
python3 research/quant_export_run.py --format html -o data/reports/manual.html
```

---

## 前置条件

| 任务 | 需要先 |
|------|--------|
| `advisor` / `full` 纸面步骤 | `python3 research/paper_run.py --init` 或 Web「纸面」初始化 |
| `quant` / `full` watching | 复制 `data/watching.example.json` → `data/watching.json` 并配置 sources |

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

Web 量化面板 **「运维状态」** 区可 **「量化 CI」**（11 quant_* + preset）、**运行 daily**、复制报告链接；包模块树见 `GET /api/quant/package`。

Agent：`quant(task=daily_presets)` 列出 preset；`quant(task=config_diff)` 预览 signal_config diff；`quant(task=portfolio_bridge)` 汇总持仓/纸面/量化联动。

归档报告含 `share_url`（如 `/api/quant/reports/quant_daily_YYYYMMDD.html`）；Web 运维区可 **复制链接**。导出 MD/HTML 顶部含 **一页摘要**（`GET /api/quant/export/summary`）。

### 持仓 ↔ 量化联动（P24）

| 入口 | 说明 |
|------|------|
| Web 持仓 ·「打开量化」 | 关闭持仓弹窗，打开量化面板并加载联动摘要 |
| Web · 持仓联动 | `GET /api/portfolio/quant-bridge`（只读：模拟持仓 ↔ 观察重叠） |
| Agent | `quant(task=portfolio_bridge)` |

### Agent 周末回归（P24，可选）

需 `DOUBAO_API_KEY`，**不**加入 `ci_quant.sh` / PR CI：

```bash
bash scripts/agent_regression.sh
bash scripts/agent_regression_quant.sh
python3 evals/run_agent_check.py --case quant_portfolio_backtest
python3 evals/run_agent_check.py --quant-only --presets
python3 evals/run_checklist.py --mock --quant-only --presets
```

---

## Watching 编辑（P18）

Web 量化面板 **「编辑 watching.json」** 可修改 `sources` / `max_size` / `watchlist`（保存后不会自动 refresh，需点「刷新」）。

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

Web 量化面板 **「信号配置（只读）」** 展示当前 `signal_config.json` 合并后的权重、stance 阈值与 rank 参数。

| API | 说明 |
|-----|------|
| `GET /api/signal/config` | 完整元信息 + merged config |
| `GET /api/signal/config/file` | 同上（Web 编辑器只读预览） |

**不提供 PUT**：权重/阈值调整仍通过 IC/OOS 建议 → 导出 diff → 手动合并 `signal_config.json`。

### Diff 预览（P20）

| API | 说明 |
|-----|------|
| `GET /api/signal/config/diff-preview` | 合并 quant_daily 中权重/阈值 diff（或即时计算） |

Web **「预览 diff」** 展示待手动合并的 patch 摘要；**「导出 diff 包」** 下载 `signal_config_diff_bundle.json`；**「量化+调仓」** 等价于 `preset: quant_paper`。

### 一键初始化（P21）

```bash
bash scripts/setup_quant.sh
```

### Diff 导出包

```bash
python3 research/signal_diff_export_run.py --fresh -o data/reports/signal_config_diff_bundle.json
curl -s 'localhost:8000/api/signal/config/diff-export?use_saved=true'
```

`merged_patch` 须手动合并到 `signal_config.json`，系统不会自动写盘。

---

## 相关文档

- [quant.md](quant.md) — 量化原理；[机器学习视角](quant.md#机器学习视角如何理解量化)
- [quant-upgrade.md](archive/quant-upgrade.md) — P6～P33 升级与落地状态
- [roadmap.md](roadmap.md) — 投顾层 cron 说明
