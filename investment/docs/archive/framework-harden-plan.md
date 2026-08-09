# 框架深度补强规划（FH 轨）

[← 文档索引](../README.md) · 复审结论见 Canvas「量化框架专业复审」· 既有台账 [framework-review.md](../framework-review.md) · ŷ 轨 [yhat-strengthen.md](yhat-strengthen.md) · N6 [n6-live-gate.md](../n6-live-gate.md)

**规划日期**：2026-08-05  
**定位**：在 O/Y/F 拆分与 ŷ 生产硬化之后，把「文档承诺的研究/生产隔离」从**纪律**升为**机制**。对照专业研究台 + 模拟盘惯例，专治 live 语义、晋升事务、异步研究任务、分层纯度与契约测试。  
**命名**：**FH** = Framework Harden。  
**终点**：mode 硬门禁 ŷ · 原子晋升 · 长研究 Job 化 · core 无 services 依赖 · 关键 live 语义有契约测。  
**不做**：OMS / Tick / 全市场数仓 / 静默写 `signal_config.weights` / 多机横向扩展。

---

## 0. 一句话目标

```text
成功画像（FH 终点）
  · mode=off → primary_score 绝不吃组 β；shadow 只对照；active 才驱动纸面分池
  · promote = staging 校验 → 原子切换指针；半晋升不可静默存活
  · 分组 / 重 OLS 走 Job 槽（对齐 paper）；UI 可取消、可轮询
  · core 不 import services；公式 DTO 在 core/view 或 services 边界
  · 契约测钉死上述语义；OOS/active 启用默认门槛可辩护（可豁免但留痕）
```

---

## 1. 背景：复审短板 → 本轨条目

| 复审 ID | 严重度 | 本轨阶段 | 一句话 |
|---------|--------|----------|--------|
| P0-1 | Critical | **FH0** | mode 硬门禁 ŷ |
| P0-2 | Critical | **FH1** | 原子晋升 / 指针切换 |
| P1-3 | High | **FH0/FH1** | OOS/active 门槛收紧 + 豁免审计 |
| P2-4 | Medium | **贯穿** | 契约测与各阶段同交付 |
| P1-2 | High | **FH2** | 研究长任务 Job 化 |
| P1-1 | High | **FH3** | 切断 core→services |
| P1-4 | High | **FH3** | legacy 0–100 只读迁移层 |
| P2-3 | Medium | **FH4** | 静默 except → 结构化失败 |
| P2-1 | Medium | **FH4** | 巨石按用例再切（非为拆而拆） |
| P2-2 | Medium | **FH5** | 研究默认 PIT 基本面 + look-ahead 标注 |
| P3-1/2 | Low | 顺带 / 不做 | UI view-model · 单进程假设保留 |

与已收口轨的关系：Y 轨抬高了 ŷ **可信度**；FH 抬高 ŷ **隔离与工程机制**。二者正交，可并行但 **FH0 优先于一切**（语义错误优先于体验）。

---

## 2. 总览

```text
FH0  Live 语义硬化（mode × ŷ × 契约测）
        │
        ▼
FH1  晋升事务化（staging → 指针 → manifest）
        │
        ▼
FH2  研究 Job 化（factor-ols-clusters 首批）
        │
        ▼
FH3  分层纯度 + legacy 标尺隔离
        │
        ▼
FH4  可观测性 + 巨石用例再切
        │
        ▼
FH5  研究数据诚实度（PIT 默认 / look-ahead 旗标）
```

---

## 3. 分阶段交付

### FH0 · Live 语义硬化（P0，建议 1–2 日）

**问题**：`score_stock` 在 `mode=off/shadow` 时仍可能经 `lookup_code_return_model` 写 `predicted_score`；mode 多半只改 `weight_source` 标签。

| 项 | 内容 |
|----|------|
| **改** | `predicted_score` 选组模型仅当 `mode=="active"`（且 `cluster_scoring.enabled`）；`shadow` 可算 `score_cluster` 对照字段但不进 `primary_score`；`off` 不 lookup 组模型（或 lookup 但忽略） |
| **交叉** | `cross_section_batch` / 纸面调仓路径同口径 |
| **测** | `tests/test_cluster_mode_yhat_gate.py`：off→无组 ŷ；shadow→primary 非组、字段可有对照；active→组 ŷ |
| **文档** | `cluster_live` docstring · design-spine 一句对齐 |
| **验收** | 单测绿；手动：mode=off 时观察/交易评分 `return_model_source`≠`cluster_group_beta` |
| **状态** | **已落地**（2026-08-05）：helpers + `score_stock`/`cross_section`/`topk` live 注入门禁；`max_oos_fail_rate`→0.5；`tests/test_cluster_mode_yhat_gate.py` |

**顺带**：`max_oos_fail_rate` 默认 0.5 已收；`mode=active` 强制 health 过线 + force 审计见 **FH1**。

---

### FH1 · 晋升事务化（P0/P1，建议 2–3 日）

**问题**：weights → mode → book → manifest 分步写盘；半晋升只告警。

| 项 | 内容 |
|----|------|
| **模型** | `data/live/cluster_pointer.json`（或等价）指向 `cluster_weights_v{n}.json`；promote 写 staging → 校验 → `os.replace` 切换指针 |
| **API** | 现有 promote/mode/refresh-book 收敛为「一次提交」或内部事务函数；失败回滚 staging |
| **Manifest** | 半晋升从 alert 升级为 **block active**（可 `force=true` 豁免并记 `data/live/promote_audit.jsonl`） |
| **测** | 中断模拟：校验失败不改指针；force 有审计行 |
| **验收** | 杀进程半写入后重启，active 不指向残缺 artifact |
| **状态** | **已落地**：`cluster_pointer` + `publish_cluster_weights_doc`；apply 先刷簿再 active；`force`+`promote_audit.jsonl`；`tests/test_cluster_pointer_fh1.py` |

---

### FH2 · 研究 Job 化（P1，建议 2–4 日）

**问题**：`POST /api/quant/factor-ols-clusters` 同步 ~20s 占 worker；前端曾冻在「分组中…」。

| 项 | 内容 |
|----|------|
| **后端** | 对齐 `paper` Job：`POST` 入队 → `GET /api/jobs/quant-ols-clusters`（或通用 `/api/jobs/{name}`）轮询；结果落 `data/jobs/` 或短 TTL 报告路径 |
| **前端** | `domain_suggest.runFactorOlsClustersSuggest` 改轮询；可取消（abort job）；保留现同步 API 作 `?sync=1` 兼容单测 |
| **范围** | 首批只做 clusters；pool OLS / 大回测列 FH2.1 |
| **测** | Job 完成态 / 失败态 / 取消态 |
| **验收** | 跑分组时其它 API（health/watching）不饿死；刷新页可恢复进度 |
| **状态** | **已落地**：默认入队 `quant-ols-clusters`；`sync=true` 兼容；`POST /api/jobs/{name}/cancel`；UI 轮询；`tests/test_fh2_ols_clusters_job.py` |

---

### FH3 · 分层纯度 + legacy 标尺（P1，建议 2–3 日）

**问题**：`core` → `services.paper_helpers`；`rank.min_score:55` 与 ŷ 门槛并存。

| 项 | 内容 |
|----|------|
| **DTO** | `_build_score_formula` / `_active_return_model_payload` 迁至 `core/signal/score_view.py`（或 `core/view/`）；services 再导出 |
| **守卫** | `test_framework_hardening` 增加：core 不得 import services |
| **Legacy** | `rank.min_score` 标 deprecated；读路径只走 `scoring_floors`；UI/API 拒绝写入 0–100 选股门（Y-S 已部分做，本阶段清残留键与文档） |
| **验收** | `rg "from services" core` 为空（测试白名单外）；新配置无 `rank.min_score` 依赖 |
| **状态** | **已落地**：`score_view.py`；core 四处改 import；守卫测；`rank.min_score` 标注 deprecated |

---

### FH4 · 可观测性 + 巨石用例再切（P2，建议持续）

| 项 | 内容 |
|----|------|
| **except** | 关键路径（sentiment / promote history / strategy apply）改为记 `warnings[]` 或 `run_manifest` 计数，禁止裸 `pass` |
| **切分原则** | 按用例：`cluster_partition`→距离/切分/平衡；`topk_backtest`→撮合/组合循环；`paper.js`→持仓岛已有，继续拆执行条 |
| **不做** | 为凑行数再拆空文件 |
| **验收** | 分组失败/舆情失败在 meta 或报告可见；单测覆盖至少 2 条「曾静默」路径 |
| **状态** | **首批已落地**：`score_stock.warnings`（舆情）；promote/mode manifest 写失败进 `warnings`；`tests/test_fh4_score_warnings.py`。巨石再切：`cluster_panels`（分组面板）· `cluster_live_audit`（双分审计）已按用例拆出 |

---

### FH5 · 研究数据诚实度（P2，建议 1–2 日）

| 项 | 内容 |
|----|------|
| **默认** | 研究台 OLS/分组请求 `pit_fundamentals=true`（或 UI 明示「非 PIT」红旗） |
| **报告** | 结果带 `lookahead_flags`（财务快照 / 无 PIT） |
| **边界** | 不建完整财务数仓（与「明确不做」一致） |
| **验收** | 默认跑分组报告含 PIT 旗标；关 PIT 时 UI 有醒目提示 |
| **状态** | **已落地**：schema/service/UI 默认 PIT；报告 `lookahead_flags`；ols_ui 红旗条 |
| **深化（2026-08-05）** | `quant/research/cluster_panels.py` 末日 as_of 探针注入 panel；逐日仍走 `collect_subscore_forward_panel` PIT；旗标 `pit_as_of` / `pit_as_of_missing` + `fundamentals_pit_summary`；审计用例拆至 `cluster_live_audit.py` |

---

## 4. 优先级与依赖

| 顺序 | 阶段 | 依赖 | 风险若推迟 |
|------|------|------|------------|
| 1 | FH0 | 无 | 纸面/观察在 off/shadow 仍吃组 ŷ → 验证失真 |
| 2 | FH1 | FH0 语义稳定后 | 半晋升难排障 |
| 3 | FH2 | 可与 FH1 并行 | 长任务拖垮 desk |
| 4 | FH3 | FH0 测稳定后 | 分层继续腐蚀 |
| 5 | FH4/FH5 | 前序稳定后 | 债利息；非阻断 |

**建议迭代切片（每周可验收）**

1. **W1**：FH0 + 契约测 + OOS 默认门槛草案  
2. **W2**：FH1 指针晋升 + 审计  
3. **W3**：FH2 clusters Job + UI 轮询  
4. **W4**：FH3 分层 + legacy 清扫；FH4/5 择要

---

## 5. 验收总表（FH 终点）

- [x] `mode=off`：组 β 不进 `primary_score`（契约测）  
- [x] `mode=shadow`：对照字段可有，主分仍全局  
- [x] `mode=active`：组 ŷ + 健康/簿/指针门禁；`force`+审计豁免  
- [x] promote 失败不残留半指针；manifest 与指针一致  
- [x] 分组走 Job；同步路径仅 `sync=true`  
- [x] `core` 无 `services` import  
- [x] 关键路径失败可见（sentiment / manifest warnings）  
- [x] 研究默认 PIT 或显式非 PIT 旗标  

---

## 6. 与 framework-review 台账

| 关系 | 说明 |
|------|------|
| O/Y/F「已收口」 | 文件拆分与 ŷ 主轴落地仍成立 |
| 本轨增量 | **语义硬化 + 事务 + Job**，不是再拆一轮空壳文件 |
| 更新方式 | 每完成一阶段在 [framework-review.md](../framework-review.md) §5.1 增 `FH*` 行；本文勾验收表 |

---

## 7. 明确不做

- 券商 OMS / 实盘（N6）  
- 全市场 Tick / 完整 Barra / 财务数仓迁库  
- 多 worker 无共享存储的横向扩展（单进程 desk 假设保留）  
- 自动静默改 `signal_config.weights`  
- 为行数而拆分无用例边界的模块  
