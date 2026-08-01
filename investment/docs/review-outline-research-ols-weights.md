# Review 大纲：研究 OLS / 权重建议 / 平台默认（v1 · d8220bb + 9e3603d）

便于按模块 review 近期改动。范围：相对 `8949f01` 之后至 `9e3603d`。  
**不写盘生产权重**仍是硬边界；本批强化的是研究探针数值、建议证据链与默认策略。

---

## 0. 建议 Review 顺序（约 60–90 分钟）

| 序 | 主题 | 优先级 | 建议时长 |
|----|------|--------|----------|
| 1 | 因子 OLS：QR + Ridge | P0 | 20 min |
| 2 | 权重建议 + OOS 门禁 | P0 | 25 min |
| 3 | 研究 horizon / 平台偏好 | P1 | 10 min |
| 4 | 纸面调仓默认保守短线 | P1 | 5 min |
| 5 | Web 文案 / API / ASSET_V | P1 | 10 min |
| 6 | 本地缓存 gitignore | P2 | 5 min |
| 7 | 测试覆盖与手工验收 | P0 | 15 min |

对比命令：

```bash
git show d8220bb --stat
git diff 8949f01..d8220bb -- investment/quant/research/factor_ols.py
git diff 8949f01..d8220bb -- investment/core/signal/
```

---

## 1. 模块 A — 因子 OLS（QR / Ridge）

### 目标

- 用 **QR 最小二乘**替换「正规方程 + 高斯消元」
- 可选 **Ridge（L2）**：`ridge_lambda>0` 收缩斜率、截距不惩罚
- API / CLI / 研究页控件贯通；默认 `λ=0` 行为兼容

### 必读文件

| 文件 | 看什么 |
|------|--------|
| [`quant/research/factor_ols.py`](../quant/research/factor_ols.py) | `_qr_lstsq` · `_ridge_lstsq` · `_fit_ols_once` · `clamp_ridge_lambda` · 报告字段 `solver` / `ridge_lambda` |
| [`quant/services/quant_service_factors.py`](../quant/services/quant_service_factors.py) | `run_factor_ols*` / `suggest_weights` 传 `ridge_lambda` |
| [`web/schemas.py`](../web/schemas.py) | `FactorExperimentRequest` / `FactorOlsPoolRequest` / `WeightSuggestRequest.ridge_lambda` |
| [`web/routers/quant.py`](../web/routers/quant.py) | 路由参数下传 |
| [`research/factor_ols_run.py`](../research/factor_ols_run.py) | CLI `--ridge-lambda` |
| [`web/static/partials/quant_panel.html`](../web/static/partials/quant_panel.html) | `ridge λ` 控件 + 帮助里 QR/Ridge 公式（勿再写「无 L1/L2」） |
| [`web/static/js/quant.js`](../web/static/js/quant.js) | `readRidgeLambda` · OLS / 池 OLS / weight-suggest payload |

### Review 检查点

- [ ] `λ=0` 仍走 QR；共线时剔列重试逻辑是否合理
- [ ] Ridge 增广矩阵是否**不惩罚截距**；秩亏时是否误剔列
- [ ] 响应字段：`solver` ∈ `{qr,ridge}`，`ridge_lambda` 有界 `[0,100]`
- [ ] β 仍是 z-score 偏效应，**不是**生产权重
- [ ] 依赖：`pyproject.toml` 显式 `numpy`

### 相关测试

- `tests/test_p86_quant.py`：已知 β 还原、精确共线拒拟合、Ridge 保留共线列
- `tests/test_factor_ols_pool.py`：页内 `quant-ridge-lambda`

### 手工验收

1. `/quant` 硬刷新 → 见 `ridge λ`
2. `λ=0` 跑 OLS → 摘要含 `QR`
3. `λ=5` 再跑 → `solver=ridge`，系数幅度通常更小、共线少剔列

---

## 2. 模块 B — 权重建议深化 + OOS 门禁

### 目标链路

```text
截面 IC/ICIR →（弱）OLS 符号 → 约束小步 Δ → OOS Top-K 门禁 → 人审 promote
```

说明文档：[`docs/weight-suggest-deepen.md`](weight-suggest-deepen.md)

### 必读文件

| 文件 | 看什么 |
|------|--------|
| [`core/signal/weight_suggest.py`](../core/signal/weight_suggest.py) | ICIR 门槛、OLS 回退、零权冻结、组内 cap、警告 |
| [`core/signal/weight_oos_gate.py`](../core/signal/weight_oos_gate.py) | **新文件**：建议权 vs 当前权 OOS 对照 |
| [`core/signal/config.py`](../core/signal/config.py) | `signal_config_overlay`（研究对照不写盘） |
| [`quant/services/quant_service_factors.py`](../quant/services/quant_service_factors.py) | `suggest_weights` 拼 `oos_gate` / `promote_ready` |
| [`web/static/js/quant.js`](../web/static/js/quant.js) | 表列 ICIR / Δ 证据 / OOS 状态 / 导出二次确认 |

### Review 检查点

- [ ] 强证据条件：`|IC|≥0.03` 且 `|ICIR|≥0.25`；步长是否被 ICIR scale
- [ ] OLS 仅符号 ±0.02，**没有** raw β→权重
- [ ] `signal_config_overlay` 是否真的不落盘、可嵌套复位
- [ ] OOS：后 30% 权益、容差 `oos_tol_pp`、`promote_ready` 语义
- [ ] 未过门时导出 / UI 是否阻断或二次确认

### 相关测试

- `tests/test_p10_quant.py`：建议规则与约束
- `tests/test_weight_oos_gate.py`：门禁 mock

---

## 3. 模块 C — 研究 horizon / 平台偏好

### 目标

- 平台页 **horizon 只读**；研究页可改并「存为默认」→ `memory.json`
- `effective_preferences` / `clamp_horizon_days`（1–10）统一钳制
- **不**覆盖数据中心 / 交易 live 评分的 horizon

### 必读文件

| 文件 | 看什么 |
|------|--------|
| [`core/memory_store.py`](../core/memory_store.py) | `clamp_horizon_days` · `effective_preferences` · write 钳制 |
| [`services/platform_service.py`](../services/platform_service.py) | prefs API 暴露 effective |
| [`web/static/partials/platform_panel.html`](../web/static/partials/platform_panel.html) | horizon 只读展示 |
| [`web/static/partials/quant_panel.html`](../web/static/partials/quant_panel.html) | `#quant-horizon` + 存为默认 |
| [`web/static/js/quant.js`](../web/static/js/quant.js) · [`platform.js`](../web/static/js/platform.js) | 读写 memory |
| [`docs/quant-ui-standard.md`](quant-ui-standard.md) | `/platform` 约定 |

### Review 检查点

- [ ] 平台无法直接改 horizon；研究页「存为默认」才写 memory
- [ ] live `score_stock` / 数据中心评分是否仍独立（不被 memory 劫持）
- [ ] 非法 horizon / risk_style 写入口是否钳制

### 相关测试

- `tests/test_d1_d6_platform.py`

---

## 4. 模块 D — 纸面「策略调仓」默认保守

### 目标

默认策略：`short_conservative`（保守短线），可选手动切回 `short`。

### 必读文件

| 文件 | 看什么 |
|------|--------|
| [`web/static/partials/follow_panel.html`](../web/static/partials/follow_panel.html) | `#paper-strategy` selected |
| [`web/static/partials/platform_panel.html`](../web/static/partials/platform_panel.html) | 调度下拉默认 |
| [`web/static/js/paper.js`](../web/static/js/paper.js) · [`platform.js`](../web/static/js/platform.js) | fallback |
| [`web/schemas.py`](../web/schemas.py) `PaperRunRequest` | API 默认 |
| [`web/routers/platform.py`](../web/routers/platform.py) `ScheduleBody` | 调度默认 |

### Review 检查点

- [ ] UI / API / 调度默认一致
- [ ] CLI / `schedule_jobs` 深层默认是否仍为 `short`（有意保留或遗漏？）
- [ ] 已有纸面账户 `strategy_id` 是否被静默覆盖（应仅影响未指定时的默认）

---

## 5. 模块 E — Web 说明与版本

| 项 | 位置 |
|----|------|
| OLS/Ridge 计算说明 | `quant_panel.html` 帮助折叠 |
| `ASSET_V` | `web/asset_version.py`（当前应 ≥ p453） |
| 摘要导出 Ridge 标签 | `quant_report_export.py` |

检查：帮助文案与实现一致；硬刷新后控件与文案同步。

---

## 6. 模块 F — 本地缓存出库（9e3603d）

| 项 | 内容 |
|----|------|
| `.gitignore` | `data/store/fundamentals/**/*.json` · `minute/**/*.json` |
| 仓库 | 仅留 `.gitkeep`；本地文件仍可用 |

检查：新 clone 后 fundamentals/minute 可重新拉取；不误 ignore `a_code_name.json` / `README.md`。

---

## 7. 非目标 / 已知边界（Review 时勿当 bug）

| 项 | 说明 |
|----|------|
| OLS β ≠ 生产权重 | 只做探针 / 弱证据符号 |
| 无 CV 选 λ | Ridge λ 人手调，无交叉验证 |
| 无 Huber / PCA | 稳健回归、正交化未做 |
| Top-K 累计偏负 | 常见成本拖累 + 弱边缘；见回测页「零成本 / 含成本」对照，非本批 OLS 引入 |
| `schedule_jobs` 等 CLI 默认 | 可能仍为 `short`，与 Web 默认可不一致 |

---

## 8. 验收清单（合入前）

### 8.1 本批单测索引（目标 / 预期不变量 / 执行）

预期写成**不变量**（与断言对齐）；具体数值以测试代码为准。

| 测试模块 | 目标 | 预期不变量 | 执行 |
|----------|------|------------|------|
| `tests.test_p86_quant` | 单票 OLS：QR / Ridge 数值与边界 | λ=0 走 QR、已知 β 可还原；精确共线拒拟合；Ridge 保留共线列并收缩；常量列在 complete panel 被剔除；报告 `task=factor_ols` | `python -m unittest tests.test_p86_quant -v` |
| `tests.test_factor_ols_pool` | 池/单票 OLS API、堆叠与排除原因 | ≥2 票才池化；单票 API `task=factor_ols`；exclusion / standardized；页内 ridge / pool 与 JS | `python -m unittest tests.test_factor_ols_pool -v` |
| `tests.test_weight_oos_gate` | 权重 overlay + OOS 门禁 | overlay 临时改权重、退出后复位；建议 OOS 更好 → gate pass / `promote_ready`；diff `apply_note` 反映门禁 | `python -m unittest tests.test_weight_oos_gate -v` |
| `tests.test_p10_quant` | IC→权重建议与约束 | 正 IC 增大权重；弱 IC 可走 OLS 符号再 decay；`cs_ic` 要 ICIR 并缩放；group cap / freeze-zero | `python -m unittest tests.test_p10_quant -v` |
| `tests.test_d1_d6_platform` | 平台偏好 / horizon 钳制 | `clamp_horizon_days`（1–10）；`effective_preferences` 一致；memory roundtrip | `python -m unittest tests.test_d1_d6_platform -v` |
| `tests.test_web_quant_js_guards` | 量化前端关键控件 | `quant.js` / partials 含 OLS、规则解读、中性化对照 | `python -m unittest tests.test_web_quant_js_guards -v` |
| `tests.test_p95_p96_web` | Web 壳层路由与主路径页 | 入口重定向、工具页可加载（防本批 UI 改坏导航） | `python -m unittest tests.test_p95_p96_web -v` |

一次跑齐（合入前）：

```bash
cd investment
python -m unittest \
  tests.test_p86_quant \
  tests.test_factor_ols_pool \
  tests.test_weight_oos_gate \
  tests.test_p10_quant \
  tests.test_d1_d6_platform \
  tests.test_web_quant_js_guards \
  tests.test_p95_p96_web -v
```

通用说明见 [`development.md`](development.md#运行测试)。

### 8.2 页面手工验收

- [ ] `/quant`：ridge λ · 存 horizon 默认 · 权重建议含 OOS
- [ ] `/platform`：horizon 只读 · 调度默认 conservative
- [ ] `/follow`：策略调仓默认「保守短线」
- [ ] 改 Python 后需**重启** `run_web.py`（`WEB_RELOAD=0` 时静态可热更、API 不会）

---

## 9. 文件速查（按 diff 体量）

**核心逻辑（先看）**

1. `quant/research/factor_ols.py`
2. `core/signal/weight_suggest.py`
3. `core/signal/weight_oos_gate.py`
4. `core/signal/config.py`（overlay）
5. `core/memory_store.py`

**服务 / API**

6. `quant/services/quant_service_factors.py`
7. `web/schemas.py` · `web/routers/quant.py` · `web/routers/platform.py`

**UI**

8. `web/static/js/quant.js`
9. `web/static/partials/quant_panel.html`
10. `web/static/partials/platform_panel.html` · `follow_panel.html`

**文档 / 测试**

11. `docs/weight-suggest-deepen.md`
12. `tests/test_p86_quant.py` · `test_weight_oos_gate.py` · `test_p10_quant.py`
