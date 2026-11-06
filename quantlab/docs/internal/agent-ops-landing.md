# Agent × 量化调仓 · 完整落地方案（AO 轨）

[← 文档索引](../README.md) · 产品边界 [design-spine.md](../design-spine.md) · 工程分层 [architecture.md](../architecture.md) · 运维 [quant-ops.md](../quant-ops.md) · 角色辨析见会话 Canvas「量化系统里 Agent 能做什么」

**定位**：双轨落地——**A 可解释/人机闸**（主交付）+ **B 可验收地提升调仓效果**（配置/因子/事件/视图进规则，禁止 LLM 直接改仓）。  
**一句话**：主轴继续算数；Agent 解释与预演；若要抬效果，只经「结构化建议 → 人审/回测 promote → 规则消费」，不经对话当场改 ŷ/权重。

**状态**：已落地首版 · 2026-11-06 · 统一 Skill `ops`（`services/agent_ops.py`）；AO5 规则接线 / AO7 融合开关默认关；AO2.5 confirm 未开

---

## 0. 目标与非目标

### 双轨一览

| 轨 | 主张 | 是否改生产数字 | 阶段 |
|----|------|----------------|------|
| **A · 可解释** | 五问 / 预演 / 排障对话化 | 否（只读 + dry_run） | AO0–AO4 |
| **B · 可抬效果** | 减误操作；事件跳过；因子/配置 promote；远期视图融合 | 仅经人审 promote 或规则 flag | AO3 加强 · AO5–AO7 |

**预期管理**：A 轨 alone **不保证** IC/净值变好；B 轨每条路径必须带 **前后对照验收**（见 §0.3、§8）。

### 目标

**A · 可解释 / 效率**

1. 自然语言完成：解读最近调仓/日更、预演下一步、排查 `risk_block` / IC / 数据质量。
2. Agent 默认只暴露 **只读快照** 与 **dry_run**；写盘须二次确认（UI 或 `AGENT_ALLOW_CONFIRM`，默认关）。
3. 复用 `ops_report`、三路预演、`feedback/suggest`、DecisionRecord；少造平行领域逻辑。
4. 验收可回放：golden + mock LLM 环路进 CI。

**B · 提升调仓效果（可验收）**

5. **间接**：解释 + 预演降低误确认/漏看拦截（以人为介质，用操作日志与拦仓有效率衡量）。
6. **配置闭环**：`feedback_draft` → 人审 promote；强制附带回测/纸面前后对照包。
7. **事件旁路**：结构化 `event_flags` → 规则跳过加仓等；不改 ŷ；用「本可避免的亏损/误加」类指标验收。
8. **Alpha 矿工**：Agent 起草因子/假设 → IC/OOS → 草稿 → 人审进 `signal_config`；生产排序仍是可解释 ŷ。
9. **视图融合（远期）**：Agent 只产 `{symbol, view, confidence, evidence}`，经校验进入 BL/现有 blend 旁路输入；权重仍由确定性优化器算。

### 非目标（两轨共同）

- LLM **直接**改写 `score` / `stance_label` / 目标权重 / 静默 `promote`
- 以「多智能体 Trader 辩论下单」作生产主路径（可沙盒对照，不进默认 paper）
- OMS / 实盘下单工具
- 向量长期记忆库（除非有明确召回用例）
- 空 stub Skill（`macro` / `announcement`）无数据上架

### 锁定原则

| 原则 | 落地约束 |
|------|----------|
| 事实与结论分离 | 解释必须引用 `ops_report` / Skill JSON；禁止编造买卖手数 |
| 工具即边界 | 新能力 = `tool_config` + 薄 handler → Application Service / 已有 API |
| 预演→确认 | 默认 dry_run；成交在 UI/规则；confirm 双闸 |
| 两条调仓语义不混 | `holding_cycle` ≠ `watching_matrix`；工具 `path` 枚举 |
| **效果须可验收** | B 轨变更不得宣称「上了 Agent 就赚钱」；必须有对照指标与 promote 留痕 |
| **建议 ≠ 生效** | Agent 只产草稿/flag/views；生效只走既有 promote / 规则引擎 |

### 0.3 功能 → 效果目录（写入本方案的全部能力）

| ID | 功能 | 用户感知 | 效果类型 | 如何证明变好 | 阶段 |
|----|------|----------|----------|--------------|------|
| F1 | 五问对话解释 | 「今天五问怎么说？」 | A 可解释 | 回复字段 ⊆ JSON；空报告失败 | AO1 |
| F2 | 三路预演 + 深链 | 对话看将买卖/拦截 | A + 间接 B | dry_run 后持仓不变；误确认率 ↓（日志） | AO2 |
| F3 | 拦仓 / 异常问答 | 「为什么没买成？」 | A + 间接 B | 引用 reason code；排障时间 ↓ | AO3 |
| F4 | feedback 草稿 | 「给参数建议」 | B 配置 | promote 前后 IC/纸面/五问对照包 | AO3+ |
| F5 | 意图裁 tools / 降本 | （后台） | A 成本 | token/延迟基线 | AO4 |
| F6 | 事件 flag → 规则 | 「因公告跳过加仓」 | B 规则 | flag 开/关回放；误加次数 | AO5 |
| F7 | 可选对话确认 | env 打开 | A 效率 | DecisionRecord `agent_confirm` | AO2.5 |
| F8 | 调仓后研究对照 | 「这次调仓与 ŷ/OOS 是否一致」 | A→B 桥梁 | 只读报告；不改仓 | AO3.5 |
| F9 | Alpha 起草→OOS→promote | 「试这个因子假设」 | B Alpha | holdout IC / 组合回测增量 | AO6 |
| F10 | 结构化 views→优化器 | 「纳入事件观点再算仓」 | B 组合 | 同窗对照：无 views vs 有 views 的净值/换手/TE | AO7 |
| — | LLM 直接定买卖 | — | **不做** | — | 禁 |

---

## 1. 目标架构

```text
用户 / AI Drawer
    │
    ▼
Agent（编排旁路）
    │  Function Calling
    │
    │ ── A 可解释 ──────────────────────────────────────
    ├─ ops_snapshot         → last_ops_report + risk_blocks + Decision
    ├─ rebalance_preview    → 三路 dry_run 统一信封
    ├─ ops_anomaly          → health + risk-blocks + timeline
    │
    │ ── B 效果（建议层，不直写生产）──────────────────
    ├─ feedback_draft       → /api/feedback/suggest（不写盘）
    ├─ post_rebalance_review→ 调仓结果 vs ŷ/OOS 只读对照（AO3.5）
    ├─ event_flags          → 结构化事件；规则引擎消费（AO5）
    ├─ alpha_draft          → 因子假设 + 触发 IC/OOS 草稿（AO6）
    ├─ view_bundle          → 校验后的 BL/blend 视图输入（AO7）
    ├─（既有）quant / position / advise / news …
    │
    ▼
领域主轴（算数与成交）
    score_bars → ŷ → stance → rank_lots / cycle → risk → paper
         ▲                      ▲
         │ promote（人审）       │ event_flags / views（规则·优化器）
         └──── 仅经既有晋升/门禁，Agent 无直连写权 ────┘
```

**禁止暴露给 LLM 的工具**：任意改 `signal_config`、无预演的写盘 `rebalance`/`run`、`promote_strategy`、直接提交目标权重。  
**B 轨生效闸**：草稿 →（可选）回测 Job → **人点 promote** 或 **规则读 flag**；Agent 会话内无「一键生效生产」。

---

## 2. 现状基线（复用清单）

| 能力 | 已有落点 | Agent 现状 |
|------|----------|------------|
| 五问结构 | `build_ops_report` · `paper.last_ops_report` · UI `formatOpsReportHtml` | **无** Skill 读取 |
| 建仓预演→确认 | `POST /api/watching/sync-paper/preview` + confirm | **未**挂 Agent |
| 日循环 dry_run | `POST /api/paper/run` `{dry_run:true}` | **未**挂 |
| 策略调仓 dry_run | `POST /api/paper/rebalance` `{dry_run:true\|open_replay}` | **未**挂 |
| risk_block | `GET /api/paper/risk-blocks` · annotate | **未**挂 |
| 监控健康 | `quant(task=health)` · `assess_strategy_health` | 部分可用 |
| 持仓桥 | `quant(task=portfolio_bridge)` | **缺** ops_report |
| 反馈草稿 | `POST /api/feedback/suggest` | **未**挂 |
| 决策留痕 | DecisionRecord · `paper_daily` / advise | 解释链可引用，无统一拉取工具 |
| 新闻 | `news` 标题摘要 | 无事件 flag schema |

研究助理（Role3：回测 / IC / preset / config_diff）**已较强**：AO3.5 / AO6 **复用**这些能力做对照与 Alpha 草稿，不重写研究台引擎。

---

## 3. 阶段规划（AO0–AO7）

建议工期按 **单人兼职** 估算；可并行处已标注。  
**必达**：AO0–AO3（A 轨 + feedback 入口）。**强烈建议**：AO3.5、AO4。**效果增强**：AO5–AO7 按数据与带宽立项。

### AO0 · 契约冻结（0.5–1 日）

**交付**

1. 本文档入库；`architecture.md` / `quant-ops.md` 各加一节「AO 轨」指针（短链即可）。
2. 冻结三个 JSON 契约（放 `docs/internal/schemas/` 或 `tests/fixtures/agent_ops/`）：
   - `OpsSnapshot`：五问核心键 + `source`（cycle|rebalance|paper_daily）+ `as_of`
   - `RebalancePreview`：`path` ∈ `{holding_cycle, watching_matrix, sync_paper}` · `dry_run: true` · `trades[]` 摘要 · `risk_blocks` · `buys_blocked` · `ops_report?`
   - `AnomalyDossier`：`monitor_alerts` · `risk_block_summary` · `data_quality` · `timeline_tail[]`
3. Prompt 增补：**禁止**把预演当成已成交；两路调仓语义说明。

**验收**

- 契约有 JSON Schema 或 dataclass + `tests/test_agent_ops_contracts.py` 最小单测
- 文档声明：无 `execute` 工具直至 AO2.5

---

### AO1 · 只读解释（Role2）· 2–3 日 · **优先**

**交付**

1. 新 Skill **`ops_snapshot`**（或 `quant` 新 task `ops_snapshot`——推荐 **独立 Skill**，避免 quant task 继续膨胀）：
   - 读 `paper.last_ops_report`；若空则返回明确 `success:false` + 提示先跑日更/调仓
   - 附最近 N 条 `risk_block` 操作日志摘要、最近 1 条 `source=paper_daily|advise` Decision
2. `portfolio_bridge` 可选附带 `last_ops_report` 精简键（或废弃重复，统一走 ops_snapshot）
3. `artifacts.py`：新 artifact 类型 → 模拟页 / 跟单 tab；`ai_drawer` 深链「打开五问条」
4. prompts：五问固定问答模板（策略 / 成本 / 数据质量 / 风控 / 监控）——**数字来自 JSON**

**验收**

- 单测：mock paper 含 `last_ops_report` → handler 键齐全
- golden：`agent_must_contain` 引用 `strategy_id` 或 `risk_blocks` 片段（mock LLM 或 checklist）
- 手动：⌘K「解释最近一次调仓五问」→ 侧栏有结构化卡

---

### AO2 · 人机闸预演（Role5）· 3–5 日

**交付**

1. Skill **`rebalance_preview`**，参数：
   - `path`: `holding_cycle` | `watching_matrix` | `sync_paper`
   - 其余透传已有 dry_run / preview 安全子集（禁止 `dry_run=false`）
2. Application Service 薄封装（建议 `services/agent_ops.py`）：内部调既有 `paper_jobs` / `paper_trades.rebalance` / `plan_watching_to_paper`，**统一收成 `RebalancePreview` 信封**
3. Artifact：预演成交表摘要 + `buys_blocked` + 风险点 checklist（供 LLM 叙述，UI 可渲染）
4. Drawer：**不**自动确认；文案引导用户到模拟页已有「确认」按钮，或展示「请到 UI 确认」深链

**AO2.5（可选、默认关闭）· 确认写盘**

- 环境变量 `AGENT_ALLOW_CONFIRM=0`（默认）
- 仅当 =1 时注册工具 `rebalance_confirm`，参数必须带 preview 指纹 / job_id；成功写 DecisionRecord `source=agent_confirm`
- 未开则文档与 registry 均不出现该工具

**验收**

- 三路 preview 各至少 1 个单元/集成测（mock 领域返回）
- Agent 调用无法传 `dry_run=false`（参数 schema + handler 硬拒）
- UI：预演 artifact 可点回模拟页对应预演面板

---

### AO3 · 异常侦探 + 配置草稿（Role4 · F3/F4）· 2–4 日（可与 AO2 后半并行）

**交付**

1. Skill **`ops_anomaly`**：聚合
   - `quant` health / `assess_strategy_health` 摘要
   - `GET` risk-blocks 摘要（`summarize_risk_blocks`）
   - `GET /api/audit/timeline` 尾部（只读）
2. **`feedback_draft`（B 轨入口）**：包装 `feedback/suggest`，返回建议 patch **不写盘**；prompts 要求「仅展示，人去策略中心 promote」
3. **对照包钩子（效果验收）**：草稿响应附带机器可读字段建议：
   - `baseline_ref`（当前 strategy_version / config hash）
   - `suggested_checks[]`（须跑的回测/纸面 dry 项，如 `portfolio_backtest`、滚动 IC）
   - 人 promote 后由既有流程写 DecisionRecord；本阶段不自动跑全套回测也可，但 UI/文案必须提示「未对照不得宣称变好」
4. Drawer 深链：risk-block 列表 / 平台审计 / 策略 promote 入口

**验收**

- 构造含 `monitor_alerts` + risk_block 的 fixture → dossier 字段稳定
- 对话「为什么没买成」必须能引用 `risk_block` 原因码（mock）
- `feedback_draft` 含 `suggested_checks`；无 promote 工具暴露给 LLM

---

### AO3.5 · 调仓后研究对照（F8）· 1–2 日 · **A→B 桥梁**

**交付**

1. Skill 或 `quant` task **`post_rebalance_review`**（只读）：
   - 输入：最近一次 `ops_report` / rebalance 结果摘要
   - 对照：当期横截面 ŷ 分布、持仓重叠、滚动 IC、`buys_blocked` 是否与闸值一致
2. 输出固定章节：一致点 / 疑点 / **不建议自动改参的项**（避免模型乱开药方）
3. 可被 AO3 的 `suggested_checks` 引用为默认检查之一

**验收**

- fixture：有 ops_report 时键齐全；无则明确失败
- 文档写明：本工具 **不**产生 promote patch，只服务人判断「要不要去改配置」

---

### AO4 · 编排与成本（Role1 + 前期 Agent 优化 · F5）· 2–4 日

**交付**

1. **安全编排**：`quant(daily_presets)` 已有；新增只读 `list_runnable_presets`；**禁止** Agent 直接 `POST /api/daily/run` 含写盘 preset，除非走 AO2 预演指纹
2. 消费 `intent.required_tools`：ops / 效果类意图分别裁子集（解释 vs feedback vs alpha_draft）
3. 短句跳过意图 LLM；tool 结果进 messages 截断；search 策略收紧
4. session 级 chat 锁替代全局锁（若多 Tab 已成为痛点）

**验收**

- 意图「解释五问」时 tools 列表不含无关 screen/peer 等（单测路由）
- token / 延迟基线：同问对比 AO0（记录在 evals 备注即可）

---

### AO5 · 事件旁路抬效果（Role6 · F6）· 按需 · 3–5 日+

**前提**：有稳定公告/新闻结构化源，不只是标题爬取。

**交付**

1. 扩展 `news`（或新 **`event_flags`**）：输出 `{symbol, event_type, severity, expires, evidence}`  
2. **规则消费**（非 LLM 改分）：例如 `risk_budget_skips` / 暂停加仓；写入 `ops_report` 可引用字段  
3. 与现有 sentiment desk（执行 prior）对齐文档，避免与 ŷ 混淆  
4. **效果验收夹具**：同一历史窗「flag 开 vs 关」纸面或 dry 回放，记录误加/少亏代理指标（文档约定，不要求一次完美）

**验收**

- flag 可单测；无 flag 时行为与现网一致  
- Agent 解释「因事件跳过」必须引用 flag JSON  
- 至少 1 个对照脚本/测试说明「如何证明 flag 有用」

---

### AO6 · Alpha 矿工（F9）· 效果轨 · 5–10 日 · 独立可排期

**定位**：LLM as Alpha Miner——起草假设与特征，**不**直接改线上 β。

**交付**

1. Skill **`alpha_draft`**（或 quant 研究 task 族）：
   - 输入：自然语言因子想法 / 对照现有 `factor_registry`
   - 输出：结构化草稿（因子定义摘要、依赖字段、预期方向、建议检验窗）
2. 串联既有：`factor_*` / IC / OOS / `config_diff` → 生成 **不可写盘** 的 patch 预览
3. 人审路径：与现网 `promote` / 策略草稿晋升对齐；DecisionRecord `source=agent_alpha_draft`
4. prompts：禁止「未过 OOS 就建议上线」；必须列出失败条件

**验收**

- 端到端 fixture：草稿 → mock IC 报告 → diff 预览；无自动 promote
- 至少 1 条「OOS 不达标则不得建议 promote」的 golden / 单测
- 生产 ŷ 路径无新依赖（grep：agent 不 import 写 config）

---

### AO7 · 结构化视图 → 优化器（F10）· 远期 · 独立立项

**定位**：Agent 产 views，Black–Litterman / 现有 blend **旁路输入**；权重仍确定性计算。

**交付（草案）**

1. 契约 **`ViewBundle`**：`{as_of, views:[{symbol, direction|mu_tilt, confidence, evidence_refs[]}]}`  
2. Skill **`view_bundle`**：只产出经 schema 校验的 JSON；非法 view 丢弃并计 warning  
3. 适配器：把 ViewBundle 译成现有优化器/BL 可消费的输入（**新代码在 quant/portfolio，不在 agent 内算仓**）  
4. 开关：`fusion_mode` / feature flag；默认 off；开时 ops_report 记录 `views_hash`  
5. 对照实验：同窗「无 views vs 有 views」净值、换手、TE、五问字段

**验收**

- 默认 off 时与现网字节级一致（或契约测：无 views 键）  
- 开启后可回放；views 来源可审计  
- **禁止** Agent 工具直接返回目标权重当作成交指令  

**不做**：TradingAgents 式多角色自由文本下单进 paper。

---

## 4. 工作分解（WBS）

| ID | 工作包 | 阶段 | 依赖 | 主要文件（预期） |
|----|--------|------|------|------------------|
| W1 | 契约 + 测试夹具 | AO0 | — | `tests/test_agent_ops_contracts.py` · fixtures |
| W2 | `ops_snapshot` Skill | AO1 | W1 | `skills/ops_snapshot/` · `services/agent_ops.py` · registry |
| W3 | artifacts + drawer 深链 | AO1 | W2 | `agent/artifacts.py` · `ai_drawer.js` |
| W4 | `RebalancePreview` 信封服务 | AO2 | W1 | `services/agent_ops.py` |
| W5 | `rebalance_preview` Skill | AO2 | W4 | `skills/rebalance_preview/` |
| W6 | prompts / routing ops 意图 | AO1–2 | W2 | `prompts.py` · `routing.py` · `intent.py` |
| W7 | `ops_anomaly` + feedback_draft + suggested_checks | AO3 | W2 | skills + platform 只读包装 |
| W7b | `post_rebalance_review` | AO3.5 | W2 | `quant` task 或独立 Skill |
| W8 | tool 子集 + 截断 + search | AO4 | W6 | `agent.py` · `intent` |
| W9 | 可选 confirm 工具 | AO2.5 | W5 · 显式开关 | registry 条件注册 |
| W10 | event_flags + 规则消费 + 对照夹具 | AO5 | 数据源 | news · risk/paper skip · ops_report |
| W11 | 文档与 quant-ops 任务表 | 贯穿 | — | `quant-ops.md` · `architecture.md` |
| W12 | evals golden（ops + 效果话术） | AO1–3 | W2/W5/W7 | `evals/golden_cases.json` |
| W13 | `alpha_draft` + OOS 门禁文案/测 | AO6 | quant 研究 API | skills + config_diff + promote 人审 |
| W14 | `ViewBundle` + 优化器适配 + flag | AO7 | 组合/BL 接口 | quant/portfolio · agent schema |

---

## 5. 安全与合规清单

- [ ] 所有新工具默认 `dry_run` / 只读；写盘工具双闸（env + 不在默认 registry）
- [ ] Handler 层拒绝 `dry_run=false` / 未知 path
- [ ] 回复强制：预演 ≠ 已成交；附 DISCLAIMER
- [ ] 不暴露 `promote` / 任意 config 写
- [ ] DecisionRecord：Agent 确认（若开启）必须 `source=agent_*` 可审计
- [ ] 单测：恶意参数（execute、改权重）被拒

---

## 6. 测试与验收矩阵

| 层级 | 内容 | 门禁 |
|------|------|------|
| 契约单测 | OpsSnapshot / Preview / Dossier 键 | PR 必过 |
| Handler 测 | mock paper / mock preview 返回 | PR 必过 |
| Mock Agent 环路 | 假 LLM 固定 tool_calls → 断言 messages 含截断后 JSON | PR 必过（AO4 起） |
| Golden routing | ops 类问句 → 期望工具名 | PR 必过 |
| Live Agent | `scripts/agent_regression.sh` 子集 ops | 可选 / 夜间 |
| 手动 UAT | 五问解释 · 三路预演 · 「为什么没买」 | AO1/2/3 各阶段演示 |
| 效果对照 | feedback/flag/alpha/views 开闭或 promote 前后 | AO3+/AO5–7 各轨门禁 |
| 话术回归 | 禁止「Agent 已提升收益」类无对照宣称 | golden / prompt 测 |

---

## 7. UI / 产品触点

| 触点 | 行为 |
|------|------|
| ⌘K AI Drawer | 新 artifact：五问卡、预演表、异常 dossier、对照摘要、草稿 diff；深链模拟页 / 风控 / 策略 |
| 模拟页五问条 | 仍为 SSOT；Agent 只引用同一 `last_ops_report` |
| 观察页建仓 | 预演仍以现有 preview UI 为准；Agent 给摘要 |
| 策略 / promote | 展示 Agent 草稿 + `suggested_checks`；**人点 promote** |
| 平台 / 审计 | 有页则深链；DecisionRecord 区分 `agent_*` 来源 |

文案统一：

- A：**「AI 解释与预演；成交以页面确认或定时规则为准」**  
- B：**「AI 只给可检验建议；是否变好以回测/纸面对照为准，不以对话语气为准」**

---

## 8. 里程碑与退出标准

| 里程碑 | 退出标准 | 轨 |
|--------|----------|----|
| **M1 = AO0+AO1** | 能对话解释最近五问；无幻觉数字；契约测绿 | A |
| **M2 = AO2** | 三路 dry_run 统一信封；无法写盘 | A |
| **M3 = AO3** | 引用 risk_block；feedback 草稿含 `suggested_checks` | A+B 入口 |
| **M3.5 = AO3.5** | `post_rebalance_review` 只读对照可用 | 桥梁 |
| **M4 = AO4** | tool 子集 + 成本可测下降 | A |
| **M5 = AO5** | event_flags → 规则跳过 + 开闭对照说明 | B |
| **M6 = AO6** | alpha_draft → OOS 门禁；无自动 promote | B |
| **M7 = AO7** | ViewBundle 默认 off；开启可回放对照 | B 远期 |

**整轨完成定义**：

- **产品可用（解释优先）**：M1–M3 必达；M3.5、M4 强烈建议。  
- **效果增强**：M5–M7 各自独立立项，每项带对照验收，不捆绑进 M1。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 两路调仓语义被 LLM 说混 | 工具 `path` 枚举 + prompts 对照表 + golden |
| 预演 JSON 过大撑爆上下文 | AO4 截断；artifact 给 UI 全量、messages 精简 |
| 用户以为对话已成交 | UI 强提示 + confirm 默认关闭 + DISCLAIMER |
| **用户以为「有 Agent = 收益更好」** | §0.3 预期管理；B 轨强制对照；禁止无基线宣称 |
| **草稿被当成已 promote** | 无 promote 工具；文案 + Decision 来源字段 |
| quant Skill 继续膨胀 | ops_* / alpha_draft / view_bundle 独立；quant 保持研究台 |
| AO6/7 拖垮 A 轨进度 | 排期隔离；A 轨先合主分支 |
| 与 FH/工程轨抢带宽 | AO1 可单独合并；AO2 依赖 paper API 稳定即可 |

---

## 10. 建议排期（示例）

```text
── A 可解释（先做）────────────────────────────────
W1  AO0–1     契约 + ops_snapshot + drawer          → M1
W2  AO2       RebalancePreview + preview Skill      → M2
W3  AO3(+4)   anomaly + feedback_draft + 降本       → M3/M4
W3b AO3.5     post_rebalance_review（可并行）         → M3.5

── B 效果（按需立项，不挡 A）──────────────────────
之后  AO2.5   对话确认（默认关）
      AO5     event_flags + 规则 + 对照夹具           → M5
      AO6     alpha_draft + OOS 门禁                  → M6
      AO7     ViewBundle + 优化器旁路（远期）         → M7
```

---

## 11. 下一步（开工第一刀）

1. 合并本文档指针到 `quant-ops.md` / `architecture.md`（短链；注明双轨 A/B）。  
2. 实现 `OpsSnapshot` 契约测 + `ops_snapshot` Skill（**A 轨**）。  
3. 加 2–3 条 evals golden（解释五问 / 空 ops_report / 禁止编造成交）。  
4. 排期会上单独登记 **B 轨候选**（AO5 vs AO6 二选一优先），避免与 A 混成大爆炸。

无需先上多智能体、confirm 写盘、或 views→BL。
