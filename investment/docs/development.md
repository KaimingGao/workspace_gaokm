# 开发与测试

[← 文档索引](README.md)

技术栈与依赖边界见 **[architecture · 技术栈](architecture.md#技术栈)**；安装见 [getting-started.md](getting-started.md)。

## 运行测试

```bash
cd investment
python3 -m unittest discover -s tests -v
```

单测覆盖：符号解析与缓存、对比、选股过滤（fixture）、短线打分/硬拒绝、K 线形态、持仓规则（mock 行情）。网络依赖接口在测试中 mock，避免 CI 强依赖外网。

## 黄金用例 / 能力校验（evals）

改 prompts 或 Skill 后，用 golden checklist 校验 Skills 与路由（推荐离线 mock）：

```bash
cd investment

# 离线 mock + daily preset 校验（CI / PR 同款，22 cases）
python3 evals/run_checklist.py --mock --presets

# 只跑 Skills（需外网时可去掉 --mock）
python3 evals/run_checklist.py

# 只跑某一案
python3 evals/run_checklist.py --mock --case quant_portfolio_bridge

# 只跑量化 golden（11 quant_* cases）
python3 evals/run_checklist.py --mock --quant-only --presets

# preset 单独校验
python3 evals/run_preset_check.py

# Agent 周末回归（需 .env 配置 DASHSCOPE_API_KEY，不进 PR CI）
bash scripts/agent_regression.sh
bash scripts/agent_regression_quant.sh
python3 evals/run_agent_check.py --case quant_package_info

# 信号可复现性（fixture 双跑）
python3 evals/run_repro.py
```

**CI**（`.github/workflows/investment-ci.yml`）：push/PR 自动跑单测 · **import 审计** · repro · `--mock --presets` checklist · preset 校验 · daily eval-mock。

本地量化 CI 同款：`bash scripts/ci_quant.sh`（单测 · import 审计 · repro · checklist · daily eval-mock）。

**Web**：顶栏 **「校验」** → 离线 mock · preset 校验 · **「CI 同款」** · **「量化 CI 同款」** · 可选 Agent · **路由对照表**（`GET /api/evals/routing`）。

**Eval API**：

| 路径 | 说明 |
|------|------|
| `GET /api/evals/summary` | case 数、preset、上次结果、CI 命令 |
| `GET /api/evals/routing` | golden routing 预期 vs 推断 |
| `GET /api/evals/presets` | daily preset 标志位校验 |
| `GET /api/evals/readme` | 子目录 README 覆盖 + 架构回链 |
| `POST /api/evals/run` | body 可含 `with_presets`（含 readme 校验） |

用例定义在 `evals/golden_cases.json`（**22 cases**，含量化 `quant_*`、`quant_factor_ols`、`quant_model_policy` 等）。校验原则：**数字以 Skills 为准；Agent 回复不得出现工具结果里没有的关键数字；买入类须含免责声明。**

Web 量化面板：quant preset daily 完成后自动加载 **Markdown 导出预览**（含 `#neutral-compare` / `#cross-section` TOC）；无 LLM 时「AI 解读」自动走 `POST /api/quant/interpret` · `{ "offline": true }` 规则解读。

生产决策为 **规则 score + stance**，未默认线性回归/复杂拟合模型；说明见 [quant.md · 为何不用拟合模型](quant.md#为何不用拟合模型线性回归--复杂模型)。

**量化报告分享**：归档报告列表含 `share_url`（如 `/api/quant/reports/quant_daily_YYYYMMDD.html`）；Web 量化面板「运维状态」可 **复制链接**（需 Web 服务运行中）。

顶栏 **「纸面」** → 净值曲线、初始化、跑观察池、**每日任务**。  
顶栏 **「持仓」** → 规则 action / stance；**「打开量化」** / **「量化对照」** 跳转量化联动面板。  
顶栏 **「量化」** → watching / 横截面 / 组合回测 / diff / daily preset 运维等，详见 [quant-ops.md](quant-ops.md)。

## 扩展新 Skill（约定）

接口形状与注册步骤见 [接口设计](architecture.md#接口设计)。摘要：

1. 在 `skills/<name>/` 下增加 `tool_config.json`、`handler.py`（`BaseSkillHandler`）、`engine.py`。
2. 在 `agent/registry.py` 的 `SKILL_SPECS` 注册（只改一处）。
3. 在 `prompts.py` 补充工具说明与路由偏好。
4. 在 `tests/` 增加离线可跑的单测（网络一律 mock）。

量化类 Skill 优先放在 `research/` 放纯函数引擎，`skills/<name>/` 只做参数解析与拉数。

## 限制与风险

- 免费行情/AkShare 可能延迟或变更；失败时应报错，不得由 LLM 补造数字。
- `quote_fallback` 短线评分信息量弱于完整日线，用 `data_source` 区分。
- 名称映射表有限；未知中文名请改用代码。
- `fundamentals` / `news` 以 A 股接口为主；港美覆盖弱于 A 股。
- `peer` 为预设同业组，非自动行业聚类。
- 1～3 天信号胜率不稳定；策略结论仍须提示风险，不保证收益。
- 完整自然语言路径强依赖通义千问（`DASHSCOPE_MODEL=qwen-plus`，可开联网搜索）。

## 合规与风险说明

系统定位为 **策略验证**（量化研究与模拟，融合 AI 编排，非持牌投顾问诊）：策略结论与模拟盈亏**不保证收益、不代客下单**；待验证成熟后再评估实盘。禁止内幕/违法交易方案。回复末尾附带风险声明。

---

## 数据源一览

| 数据 | 来源 | 主要消费者 |
|------|------|------------|
| 实时报价 | 腾讯 `qt.gtimg.cn` | `quote`（及依赖它的 Skill） |
| A 股现货全表 | AkShare | `screen` |
| A/港/美日线 | AkShare（经 `skills/common/history.py`） | `signal` `kline` `index` |
| 日线失败降级 | 当日 `quote` 拼近似 bar | `history.quote_fallback` |
| 财务要点 | AkShare | `fundamentals` |
| 指数历史 | AkShare | `index` |
| 资讯标题 | AkShare | `news` |
| 模拟持仓与规则 | `data/paper.json` / `position_rules.json` | `position` |

---
