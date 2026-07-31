# 量化升级总览（P6～P22）

[← 文档索引](README.md) · 详细路线图见 [quant-upgrade.md](quant-upgrade.md) · 运维见 [quant-ops.md](quant-ops.md)

本文档为 **P6～P22 全部落地** 后的一页速查：阶段成果、常用命令、API 与验收方式。（P23+ 见 [quant-upgrade.md](quant-upgrade.md)）

---

## 阶段一览

| 阶段 | 主题 | 关键交付 |
|------|------|----------|
| P6 | 因子与数据一致性 | `signal_config.json`、真 RS、`factor_contrib`、`regime` |
| P7 | 验证体系 | OOS 切分、IC/IR、组合回测、成本模型 |
| P8 | 纸面闭环 | 纸面卖出、`quant_daily`、Web 量化入口 |
| P9 | Watching / 横截面 | `watching.json`、cross-section、factor registry |
| P10 | Web 量化面板 | 组合回测、IC 权重建议、daily 集成 |
| P11 | 净值与调仓 | equity curve、weight diff、纸面 rebalance |
| P12 | TopK 摘要 | daily 组合回测摘要（纸面 vs TopK 对照已下线） |
| P13 | 解读与阈值 | LLM 解读、stance 阈值 OOS（纸面双曲线已下线） |
| P14 | quant Skill | Markdown 导出、watching 阈值聚合 |
| P15 | Evals 闭环 | golden quant、HTML 导出、路由 hint |
| P16 | 运维 preset | `advisor` / `quant` / `full` / cron 模板 |
| P17 | 可观测性 | health API、报告索引、运维面板 |
| P18 | Watching 编辑 | JSON 编辑器、`quant_health` case、daily_check |
| P19 | 信号只读 | signal config 预览、`quant_paper` preset |
| P20 | Diff 预览 | diff-preview API、Web 量化+调仓 |
| P21 | 上手整合 | diff 导出包、setup_quant、preset 校验 |
| P22 | CI 收尾 | ci_quant.sh、quant_paper 集成测、preset CLI |
| P23 | 运维增强 | Web preset 下拉、quant config_diff/daily_presets |
| P24 | 持仓联动 | portfolio_bridge、Web 持仓↔量化、Agent 周末回归 |
| P25 | Evals 增强 | Web CI 同款、preset API、golden portfolio_bridge |
| P26 | 路由与分享 | eval routing API、报告 share_url、development 同步 |
| P27 | 报告摘要 | executive summary、eval 路由跳转、README 整合 |
| P28 | `quant/` 包 | services + ops + research + skill 独立目录 |
| P29 | 包结构 API | `GET /api/quant/package` · Web 运维模块树 |
| P30 | canonical import | CLI / daily / preset_check 改用 `quant.*` |
| P31 | 测试 canonical | `test_p*_quant` 改 `quant.*` · AST 守卫 |
| P32 | Agent package_info | `quant(task=package_info)` · 包结构路由 |
| P33 | Evals golden | `quant_package_info` case · 22 cases total |
| P34 | Agent quant 子集 | `--quant-only` · `agent_regression_quant.sh`（8 cases） |
| P35 | Web 量化 CI | 校验面板「量化 CI 同款」· `quant_only` API |
| P36 | 量化面板 CI | 运维区「量化 CI」· 无需打开校验弹窗 |
| P38 | GitHub CI 对齐 | Actions 跑 import audit · 与 `ci_quant.sh` 一致 |
| P39 | Shim 移除 | P41 删除 14 个 compat shim · 仅 `quant.*` |
| P40 | 子目录 README | 各目录 README.md · `GET /api/readme-index` |
| P41 | Web 浏览 | `GET /api/readme?dir=` · 运维区弹窗 · 架构双向链接 |
| P42 | Evals README | `GET /api/evals/readme` · CI `--presets` 附带覆盖校验 |
| P53 | 中性化 golden | `quant_portfolio_neutral_compare` · routing |
| P54 | daily 中性化摘要 | 报告导出 · Web Δ/winner |
| P55 | Agent 回归 | 22 cases · prompts · agent_must_contain |
| P56 | preset 中性化 | `portfolio_neutral_compare` · quant/full/quant_paper |
| P57 | Agent task 目录 | `QUANT_TASK_ROUTES` · 13 task 对齐 |
| P58 | 导出专节 | 中性化对照 MD/HTML · `#neutral-compare` |
| P59 | Web preset flags | 运维区 chip · 中性化对照高亮 |
| P60 | golden daily 专节 | `quant_daily_neutral_section` · export 校验 |
| P61 | interpret 中性化 | `QUANT_INTERPRET_SYSTEM` · compact 胜率 |
| P62 | Web 对照表 | `#quant-neutral-compare-table` · 上次报告 |
| P63 | golden interpret | `quant_interpret_neutral` · offline 规则解读 |
| P64 | Web 解读摘要 | `neutral_compare_brief` · 解读区对照表 |
| P65 | 导出 TOC | `#neutral-compare` 目录锚点 |
| P66 | Agent interpret golden | `quant_interpret_neutral` · agent_must_contain |
| P67 | Web 导出预览 | TOC 导航 · HTML/Markdown 预览 |
| P68 | tool_config | `offline` · `include_portfolio_neutral_compare` |
| P69 | daily 自动预览 | quant preset 完成后 Markdown + TOC |
| P70 | API offline | `POST /api/quant/interpret` · `offline: true` |
| P71 | Web 回退解读 | 无 LLM 时规则解读 · `【规则解读】` |
| P72 | 文档同步 | quant.md · evals README · quant-summary |
| P73 | score 用途 | quant.md 专节 · 六大用途表 |
| P74 | Web 规则解读 | 显式 offline 按钮 |
| P75 | 日报 score 摘要 | 横截面最高/中位 · ranking 键 |
| P76 | Agent score/stance | `SCORE_STANCE_HINT` · routing |
| P77 | 解读 score | offline/compact 横截面摘要 |
| P78 | golden cross_section | `quant_cross_section_score` · 22 cases |
| P79 | Web 横截面表 | score / score_raw 列 |
| P80 | 导出 cross-section | TOC `#cross-section` · MD/HTML |
| P81 | 拟合模型策略 | 为何不用 LR/GBDT · 何时启用 |
| P82 | 原则交叉链接 | quant-upgrade ↔ quant.md |
| P83 | Agent ML hint | `MODEL_POLICY_HINT` · routing |
| P84 | 预览 TOC 跳转 | Markdown `#cross-section` scroll |
| P85 | 工业因子分类 | quant.md 对照表 · alpha/risk |
| P86 | 因子 OLS 研究 | `factor_ols.py` · CLI · quant task |
| P87 | golden OLS/policy | `quant_factor_ols` · `quant_model_policy` |
| P88 | OLS 路由 | infer_quant_task · tool_config |
| P89 | Web OLS 面板 | `/api/quant/factor-ols` · 系数表格 |
| P90 | OLS 解读 | daily · compact · 规则解读 |
| P91 | 导出 OLS | `#factor-ols` · TOC 跳转 |
| P92 | 文档同步 | quant-upgrade · quant-summary |
| P93 | 底仓做 T | `core/t0` · 日线代理回测 · 纸面模拟 |
| P94 | 演进拆分 | QuantService Mixin · `web/routers` · 前端 js 模块 · 文档归档 |
| P95 | 回测展示 | Web 指标卡 · 成交/做T明细 · 日报摘要预填 |
| P96 | 多页壳 | `/quant` `/paper` `/portfolio` · `page_html` 组装 |
| P97 | 对话工作台 | 左 Agent · 右 Tab（量化/持仓/纸面/回复） |
| P98 | 对话驱动结果 | chat `artifacts` · 自动切 Tab 并渲染 |

**刻意不做**（全阶段）：实盘下单、保证收益、自动写 `signal_config.json`、PDF 导出、远端 webhook。

---

## 快速上手

```bash
cd investment
bash scripts/setup_quant.sh
bash scripts/daily_quant.sh              # preset quant
bash scripts/daily_quant_paper.sh        # preset quant_paper（含纸面调仓）
bash scripts/daily_check.sh              # cron 失败检查
bash scripts/ci_quant.sh                 # 本地 CI 同款
```

---

## Daily preset

| preset | 含 paper_rebalance |
|--------|-------------------|
| `advisor` | 否 |
| `quant` | 否 |
| `full` | 否 |
| `quant_paper` | **是** |

---

## 核心 API（量化）

| 路径 | 说明 |
|------|------|
| `GET /api/quant/config` | 因子权重/阈值摘要 |
| `GET /api/quant/last` | quant_daily.json |
| `GET /api/quant/package` | quant 包结构（subpackages / modules / removed_shim_paths） |
| `GET /api/quant/export/summary` | quant_daily 一页摘要 bullets |
| `GET /api/quant/reports` | 归档报告列表（含 `share_url`） |
| `GET /api/evals/routing` | golden routing 预期 vs 推断 |
| `GET /api/daily/health` | daily + watching + reports |
| `GET /api/signal/config/file` | signal_config 只读 |
| `GET /api/signal/config/diff-preview` | diff 预览 |
| `GET /api/signal/config/diff-export` | diff 合并包下载 |
| `POST /api/daily/run` | `{ "preset": "quant" \| "quant_paper" }` |
| `GET /api/portfolio/quant-bridge` | 模拟持仓 / watching / quant 联动摘要 |
| Web 运维区 | preset 下拉 +「运行 daily」（P23） |
| Web 持仓联动 | 持仓「打开量化/量化对照」· 量化「持仓联动」（P24） |

Agent：`quant(task=...|portfolio_bridge|config_diff|daily_presets|health|...)` — 13 工具之一。

**Agent 周末回归**（不进 PR CI）：

```bash
bash scripts/agent_regression.sh              # 全量 22 cases
bash scripts/agent_regression_quant.sh        # 量化专项 11 quant_* cases
python3 evals/run_checklist.py --mock --quant-only --presets
python3 evals/run_agent_check.py --case quant_package_info
```

需 `DOUBAO_API_KEY`。

---

## 验收命令

```bash
python3 -m unittest discover -s tests -v     # ~270+ 项
bash scripts/check_quant_imports.sh          # shim import 守卫
python3 evals/run_checklist.py --mock --presets   # 16 golden + preset
python3 evals/run_repro.py
python3 evals/preset_check.py                  # 仅 preset（或通过 checklist）
```

GitHub Actions：`.github/workflows/investment-ci.yml`（push/PR：单测 · import 审计 · repro · `--mock --presets` · preset · daily eval-mock）。

---

## Golden cases（22）

含 `quant_daily_neutral_section`、`quant_interpret_neutral`、`quant_factor_ols`、`quant_model_policy` 等；定义见 `evals/golden_cases.json`。

Web **「校验」** 面板支持「CI 同款」（mock + presets）、**「量化 CI 同款」**（11 quant_* + preset）、单独 preset 校验；摘要见 `GET /api/evals/summary`。

---

## 相关文档

| 文档 | 用途 |
|------|------|
| [quant.md](quant.md) | 原理：因子 → stance → 回测 → 纸面；[纸面是什么](quant.md#纸面是什么给小白)；[ML 视角](quant.md#机器学习视角如何理解量化) |
| [quant-upgrade.md](quant-upgrade.md) | 分阶段详细交付与验收 |
| [quant-ops.md](quant-ops.md) | cron、preset、报告路径 |
| [development.md](development.md) | 单测与 CI 说明 |
