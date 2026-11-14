# 模型工厂 × 研究枢纽注册 — 开发规划

[← 文档索引](../README.md) · 产品主轴 [design-spine.md](../design-spine.md) · UI 契约 [quant-ui.md](../quant-ui.md) · Arm·T 实现 [arm-t.md](arm-t.md) · ŷ 契约 [quant.md](../quant.md)

**定位**：把「拟合/评测/管版本」与「数据准备 + 回测/live 绑定」拆开，作为后续落地的单一规划真源。  
**阶段**：策略验证（研究台 + 纸面）；工厂与注册均 **不静默写生产 score**；live 变更仍须人审。

---

## 1. 一句话目标

```text
模型工厂（按后端分页）  →  拟合 · 评测 · 版本管理  →  产出可复现制品 + 评测报告
数据中心（/data）       →  日线/分钟/物化
研究枢纽（/quant）      →  按报告注册 · 观察池分档  →  回测指针 / research / live
打分链路                →  只认枢纽注册表中的 model_id（不认工厂「当前选中」）
```

| 角色 | 负责 | 不负责 |
|--|--|--|
| **Transformer 工厂**（`/yhat-t`；旧 `/yhat-tf` 重定向） | 四头 **Arm·T** 拟合、Holdout 评测、跑次版本 | 拉数主入口、物化主入口、live promote、回测落账；**不再同页训 Arm·L** |
| **LightGBM 工厂**（`/yhat-l`） | 四头 **Arm·L**（LGB/LambdaRank）拟合、评测、跑次版本 | 同上；**不再同页训 Arm·T** |
| **数据中心**（`/data`） | 日线/分钟/L1·L2 物化 | 观察池分档、拟合、live promote |
| **研究枢纽**（`/quant`） | 观察池分档、**按报告绑定**回测与 live；跨工厂对照 | 拉数与物化（在数据中心）；超参网格拟合台（长期迁出） |
| **历史回测 / 交易执行** | 消费已注册模型打分 | 选模型、改工厂版本 |

### 0. 页面拆分（A0，版本管理之前）

改造前必须先拆掉 Scheme LT「同页双臂」：

| | `/yhat-l` · Arm·L | `/yhat-t` · Arm·T |
|--|--|--|
| 模型 | LightGBM / LambdaRank | `SeqFactorTransformer` |
| Holdout | `--factory L`（不建序列面板） | `--factory T`（序列+因子） |
| 落盘 | `live/yhat_l/` | `live/yhat_t/`（旧 `yhat_tf/` 迁此或别名） |
| 页内 Gate | 绝对指标（无 vs T） | 绝对指标（无 vs L）；跨厂对照留给枢纽 |
| 对照 | 不做页内硬闸相对臂 | 同上 |

**不变量**：一页只跑一臂；`hard_gate_vs_arm_L` 仅历史 LT 双臂跑次保留；新跑次不再写相对闸（或写 `gate=null`）。  
**旧 `/yhat-tf`**：302 → `/yhat-t`。命令面板两入口。

---

## 2. 为何这样拆

与 [design-spine](../design-spine.md) 一致：

- **已发生 → 估计 → 验证 → 动作**；配置迭代须人审。
- live / 纸面 / 回测 **同源打分** 的前提，是「用哪份模型」有单一注册真源。
- 工厂可并行迭代算法；枢纽只做 **选用**，避免「又训又上线」混在一页。

**现行错位（债）**

| | 现行 | 目标 |
|--|--|--|
| `/yhat-tf` | TF Holdout；draft→晋级 research；禁止 live | TF **工厂**：拟合/评测/版本 |
| `/quant` | LGB 四头 **拟合 + research/live 槽 + 物化 + promote** | **数据 + 注册**；拟合迁出 LGB 工厂 |
| 版本库 | `model_versions` 仅 suite（LGB 槽文件） | 工厂各有跑次/制品版本；枢纽另有 **注册指针** |

---

## 3. 制品与评测契约（跨工厂）

### 3.1 统一制品（Conceptual Artifact）

每个可被枢纽引用的版本至少包含：

| 字段 | 含义 |
|--|--|
| `model_id` | 稳定 id（建议：后端 + head + fitted_at/run_id） |
| `head` | `oo` \| `tc` \| `oo_rank` \| `tc_rank`（API 勿与 suite 的 `tau` 混用时要有显式映射） |
| `backend` | `lgb` \| `transformer`（可扩展） |
| `fit_params` | 可复现超参与因子方案 |
| `fingerprints` | 特征包、universe、lookback、CS-z / 物化指纹 |
| `eval_report` | Holdout 指标、相对基线、正式对照标记、评测宇宙 |
| `weights_ref` | 可加载权重路径或内嵌（TF 研究期可先只存评测包） |
| `live_eligible` | **仅**表示评测够格候选；**不等于**已绑定 live |

### 3.2 跨工厂可比评测

同一 `head` 对齐：

- 同一 Holdout 交易日窗与评测宇宙（`eval_shared`）
- 同一标签与 CS-z 口径
- 相对基线规则可复用 Scheme LT 硬闸思想（见 [arm-t.md §8](arm-t.md)），升格为 **工厂间通用评测契约**，而非 TF 私有流程

探索跑（如 T 采样 10%、core 因子）可归档，但须打标；**正式对照**（full 因子 + 全样本 + 共享评测）才建议 `live_eligible`。

### 3.3 枢纽注册表（Binding Registry）

按 **流水线角色** 存指针，不按工厂页：

| 角色 | 用途 | 门禁 |
|--|--|--|
| `backtest` | `/replay` 等历史验证 | 可宽；允许多套对照 |
| `research` | 研究槽 / 预启用 | 须有评测报告；可低于 live |
| `live` | 纸面与生产打分 | 人审 + 相对 active 硬闸；禁止静默覆盖 |

默认策略建议：**同套后端成套注册**（四头同一 `backend`）；混绑（oo=LGB、τc=TF）仅高级开关，并写明 fuse/rank 语义。

**硬约束**：`score_bars` / 回测 / 纸面 **只读注册表 `model_id`**；工厂 UI「当前选中版本」不得直接改 live。

---

## 4. UI 目标形态

### 4.1 Transformer 工厂（现模型中心）

| 做 | 不做 |
|--|--|
| 四头拟合、双臂对照、Holdout 日志/console | 存草稿 → 晋级研究整套心智 |
| **跑次版本下拉**：选中 → 表单回填该版 `fit_params`；console 显示该版拟合信息 | 复用 `/quant` 的 `model_versions` suite API（schema 不对） |
| 工作配置（可隐式，无「存草稿」按钮）+ 每次成功/失败 ingest 归档 | 产物栏 / 检查栏 / 摘要栏（信息迁入 console + 版本 meta） |
| 版本徽章：过闸 / 正式对照 / 采样% / failed | promote-live、写 active |

**交互不变量**

1. 选历史版 = **只读回看**；改参再拟合 = **新版本**（不覆盖旧档）。
2. 拟合进行中：console **优先当前 job**，不被历史选中版盖住。
3. 可选「钉选研究指针」替代旧晋级（写入工厂侧 pin 或仅标记）；**live 仍只经枢纽**。

### 4.2 LightGBM 工厂（规划页）

与 TF 工厂同壳不同后端：拟合、评测、版本；从 `/quant` 迁出现有四头拟合 UI；落盘仍可先写现有 research 槽文件，但 **启用 live 的动作迁到枢纽注册**。

### 4.3 研究枢纽

分区建议：

1. **数据准备** — 日线 / 分钟 / L1·L2 物化（工厂只声明依赖指纹）
2. **模型注册** — 读各工厂评测报告，绑定 backtest / research / live
3. **探针 / 日报 / 分档** — 保持现有运维能力

长期 **不再** 作为超参拟合主入口。

---

## 5. 阶段落地（A → D）

### 阶段 A — 工厂收口（优先）

**目标**：双页工厂独立拟合/评测，仍 `live_allowed=False`；再上版本管理。

| # | 交付 | 验收要点 |
|--|--|--|
| **A0** | **拆页**：`/yhat-l`（Arm·L）+ `/yhat-t`（Arm·T）；Holdout `--factory`；分目录落盘 | 一页只训一臂；L 不建 seq；无页内 L↔T 硬闸；`/yhat-tf`→`/yhat-t` |
| A1 | 去掉「存草稿 / 晋级研究 / 清草稿 / 清研究」主路径；隐式工作配置即可 | HTTP draft/promote/clear 已下线；`save_fit_draft` 仅内部；UI 无晋级按钮 · **已落地** |
| A2 | 去掉产物 / 检查 / 摘要栏与概览草稿/研究 KPI；硬门进 console / 头卡 Gate | 页面更短；跑中/失败态仍可读 · **已落地** |
| A3 | 各工厂 **跑次版本库**（勿接 suite `model_versions`） | ingest 归档；列表/预览/选中回填；KEEP 上限 |
| A4 | 版本 meta：指标、正式对照标记、采样%/因子方案 | 下拉可辨探索 vs 正式 |
| A5 | 更新 [quant-ui.md](../quant-ui.md) 双页契约 + [arm-t.md](arm-t.md) | 文档与 UI 一致 |

**存储草图**（实现时可微调）

```text
live/yhat_tf/
  {head}_job.json / {head}_last.json / {head}_fit.log   # 当前运行
  {head}_work.json                                      # 可选：工作配置
  versions/{head}/index.json
  versions/{head}/{run_id}.json                         # 完整跑次快照
```

**明确不做（A）**：TF 进 live；与 LGB suite 共用 archive API；自动 promote。

### 阶段 B — LGB 工厂迁出

| # | 交付 | 验收要点 |
|--|--|--|
| B1 | 新页或同壳「LightGBM 工厂」；迁走 `/quant` 四头拟合面板 | 枢纽不再改超参开训（可留只读当前注册） |
| B2 | 复用/对齐现有 `model_versions` 为工厂版本 UX | 与 TF 工厂交互同构（列表/预览/再训） |
| B3 | live **写入**仍暂时走旧 promote API，但 UI 入口标记为「将迁注册」 | 不双入口乱写 |

**明确不做（B）**：枢纽与工厂两处都能 promote live。

### 阶段 C — 枢纽注册表

| # | 交付 | 验收要点 |
|--|--|--|
| C1 | `BindingRegistry`：按 head（或 suite）存 `backtest` / `research` / `live` → `model_id` | 落盘可审计；有历史 |
| C2 | 枢纽「模型注册」区：展示工厂报告摘要 → 人审绑定 | 回测绑定与 live 绑定门禁分档 |
| C3 | 打分路径改为解析注册表 | 工厂切版本不影响 live，直至枢纽改绑 |
| C4 | 废弃工厂页内「一键 live」；平台审计能看到绑定事件 | 与 design-spine 人审一致 |

### 阶段 D — TF 可消费制品

| # | 交付 | 验收要点 |
|--|--|--|
| D1 | TF 权重 export + Scorer 适配（至少回测可读） | 指纹与评测报告齐全 |
| D2 | 枢纽允许将 `live_eligible` 的 TF 版绑定到 **backtest** | 回测–报告可复现 |
| D3 | （可选）TF → research → live，门禁不低于 LGB | 默认仍建议成套后端 |

**前置**：阶段 A 版本库与评测契约稳定；跨工厂 Holdout 协议可比。

---

## 6. 与近期 UI 讨论的对应关系

此前结论直接落入 **阶段 A**：

| 讨论项 | 规划处置 |
|--|--|
| 下掉草稿 → 晋级 | A1；认证改版本徽章 / 可选钉选 |
| 下掉产物/检查/摘要 | A2 |
| 模型版本下拉 | A3–A4；**新建** yhat-tf 版本，不接 suite |
| 选版回填配置 + console | A3；只读历史；再拟合出新版 |

详见对话分析要点：进行中 job 优先于历史选中；探索跑与正式跑必须可区分。

---

## 7. 风险与约束

1. **枢纽膨胀** — 注册 + 数据已够重；禁止再塞拟合网格。
2. **组合爆炸** — 四头 × 两后端；默认成套绑定。
3. **评测不可比** — 无统一 Holdout 契约则枢纽「选模型」无依据。
4. **命名** — 对外逐步称「Transformer / LightGBM 工厂」，避免「模型中心 = live 控制台」误解。
5. **物化** — 属数据平面，归属枢纽；工厂只读指纹、缺则 `materialize_required`。
6. **head 命名** — suite 历史 `tau` vs desk `tc`；注册表与 API 层做映射表，禁止静默混用。

---

## 8. 验收总清单（跨阶段）

- [ ] 工厂页无法直接改 live 打分
- [ ] 任意 live/回测所用模型可追溯到 `model_id` + 评测报告
- [ ] TF 与 LGB 同 head 的正式对照指标可并排比较
- [ ] 文档：`quant-ui` 页面唯一任务、本规划、arm-t 无矛盾冲突
- [ ] 单测：版本归档/预览/绑定门禁；回归 desk Holdout

---

## 9. 相关代码锚点（现行）

| 区域 | 路径 |
|--|--|
| TF desk | `core/research/yhat_tf_desk.py` · `scripts/run_yhat_transformer_holdout.py` |
| 工厂 UI | `web/static/partials/yhat_factory_panel.html` · `web/static/js/yhat_tf.js`（`/yhat-l` · `/yhat-t`） |
| Suite 版本（LGB） | `core/research/model_versions.py` · `/api/quant/model-versions*` |
| 枢纽拟合 UI | `web/static/js/quant.js` · `web/static/partials/quant_panel.html` |
| ŷ 落盘 / promote | `core/research/oo_model.py` · `tc_model.py` · `oo_rank_lambdarank.py` · `tc_rank.py` |

---

## 修订

| 日期 | 说明 |
|--|--|
| 2026-11-14 | 初稿：双工厂 + 枢纽注册；阶段 A–D；纳入 TF 版本下拉与下掉草稿晋级 |
