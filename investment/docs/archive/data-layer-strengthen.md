# 数据层深化补强规划（D 轨）

[← 文档索引](../README.md) · 域文档 [data-layer.md](../data-layer.md) · 产品主轴 [design-spine.md](../design-spine.md) · 验证 S 轨 [validation-strengthen.md](validation-strengthen.md) · N6 [n6-live-gate.md](../n6-live-gate.md)

**规划日期**：2026-07-31  
**定位**：相对专业量化**数据层**的路径内缺口补强——覆盖面可信、PIT/源一致、复权可辩护、日历对齐、DQ 可巡检。  
**命名**：**D** = Data-layer Strengthen；与 **S**（验证）、**V/R**（已收口）区分。  
**不做**：全市场 Tick 仓、整体迁库、券商 Level2、多供应商 SLA 合同级对账。

---

## 0. 一句话目标

```text
成功画像（D 轨终点）
  · 验证宇宙财务多期带可用日（公告日优先）；ingest 写入 ann_date
  · 回测结果默认挂 source_audit（core 引擎级，不依赖 QuantService）
  · 日线可显式请求 qfq / raw（hfq 可选），缓存带 adjust 标签防混用
  · A 股交易日历 lite：过滤周末/节假日；停牌仅最小标记口
  · GET /api/ops/data-quality 一页聚合 coverage + 空财务 + 审计 + 样本纪律
```

差距依据见对话分析与 [data-layer.md](../data-layer.md)「成熟模型五模块」对照（收集/清洗 ~62%）。

---

## 1. 相对专业：路径内缺陷 → 排期

| 优先级 | 缺陷 | 污染什么 | D 阶段 |
|--------|------|----------|--------|
| **P0** | 财务 ann 未入库 / 覆盖运营债 | 前视 → 虚假 IC/回测美 | **D0** |
| **P1** | source_audit 仅 Quant 路径 | live≠回测不可见 | **D1** |
| **P2** | 复权仅声明 qfq、缓存易混 | 长回测不可辩护 | **D2** |
| **P3** | 无交易所日历 / 停牌主数据 | 截面日对齐弱 | **D3** |
| **P4** | DQ 信号散落、无中心 API | 运维不可巡检 | **D4** |

**边界外（不进 D 轨）**：全市场数仓、Tick、Bloomberg 级主数据、多源字段级对账引擎。

---

## 2. 锁定原则

| # | 原则 |
|---|------|
| 1 | 仍经 **DataService / ports**；禁止业务散落扫盘 |
| 2 | 生产分继续 quality 门禁；thin/empty/fallback 不硬塞分 |
| 3 | demo / synthetic 不得计入「已验证」覆盖 |
| 4 | 存储继续 JSON，直至观察池触顶再立项 Parquet（见 data-layer 选型） |
| 5 | 每阶段 ≥1 API/落盘 + ≥1 可见入口 + 测试 |
| 6 | 与 S 轨 PIT（可用日）兼容，不回退 |

---

## 3. 阶段总览（D0–D4）

```text
D0  财务可用日入库 + 覆盖聚合
        │
        ▼
D1  源审计默认进 core 回测
        │
        ▼
D2  复权 qfq|raw 可切换
        │
        ▼
D3  交易日历 lite
        │
        ▼
D4  DQ 中心 API + 文档收口
```

| 阶段 | 主题 | 状态 |
|------|------|------|
| **D0** | ann_date 写入 ingest · 覆盖汇总 | **已落地** |
| **D1** | `attach_source_audit` → engine / topk | **已落地** |
| **D2** | `get_bars(adjust=)` · 缓存标签 | **已落地** |
| **D3** | `market_calendar` CN lite | **已落地** |
| **D4** | `/api/ops/data-quality` · 测 · 文档 | **已落地** |

---

## 4. D0 · 财务可用日与覆盖

| ID | 交付 | 落点 |
|----|------|------|
| D0.1 | `merge_history_point(..., ann_date=)` | `fundamentals_pit.py` |
| D0.2 | 财务行解析 `ann_date`（有则写；无则 `ann_missing`） | `skills/fundamentals/engine.py` |
| D0.3 | ingest 写入 ann / available_as_of | `sample_ops.ingest_real_fundamentals_history` |
| D0.4 | `build_data_layer_snapshot` 合并 bars coverage + fund sample | `core/data_quality_center.py`（D4 共用） |

---

## 5. D1 · 源审计默认

| ID | 交付 | 落点 |
|----|------|------|
| D1.1 | 单票 `run_signal_backtest` 返回挂 `source_audit` | `core/backtest/engine.py` |
| D1.2 | TopK 回测返回挂 `source_audit` | `core/backtest/topk_backtest.py` |
| D1.3 | 独立 `GET /api/ops/source-audit` | `platform` router |

---

## 6. D2 · 复权可切换

| ID | 交付 | 落点 |
|----|------|------|
| D2.1 | `normalize_adjust(policy)` ∈ `{qfq,raw,hfq}` | `data_service` |
| D2.2 | `get_bars(..., adjust=)`；缓存 `adjust_policy` 标签；混用则降级重拉提示 | `data_service` · `history` · `store` |
| D2.3 | 回测/manifest 记录实际 `adjust` | 已有字段加深 |

`hfq`：源支持则拉，否则 `unavailable` 回退 qfq 并标注。

---

## 7. D3 · 交易日历 lite

| ID | 交付 | 落点 |
|----|------|------|
| D3.1 | `is_trading_day` / `filter_trading_dates`（周末 + 节假日表） | `core/market_calendar.py` |
| D3.2 | 可选磁盘 `data/store/cn_holidays.json`；无则仅周末 | store |
| D3.3 | 停牌：`halt_hint` 仅关键词/状态口（不伪造全日停牌库） | calendar 模块 note |

---

## 8. D4 · DQ 中心

| ID | 交付 | 落点 |
|----|------|------|
| D4.1 | `GET /api/ops/data-quality` | platform |
| D4.2 | 平台折叠「数据质量」刷新 | `platform_panel` / `platform.js` |
| D4.3 | `tests/test_d_track.py` | tests |
| D4.4 | data-layer / README / design-spine 链接 | docs |

---

## 9. 代码索引

| 模块 | 路径 |
|------|------|
| 规划（本文） | `docs/data-layer-strengthen.md` |
| 财务可用日 | `fundamentals_pit` · `fundamentals/engine` · `sample_ops` |
| 源审计 | `data_consistency` · `backtest/engine` · `topk_backtest` |
| 复权 | `data_service.get_bars` · `history` · `store` |
| 日历 | `core/market_calendar.py` |
| DQ 中心 | `core/data_quality_center.py` · `/api/ops/data-quality` |
| 测 | `tests/test_d_track.py` |

---

## 10. 验收

- [x] D0–D4 代码与 API 可演示  
- [x] `python -m unittest tests.test_d_track` 通过  
- [x] ingest 新点可带 `ann_date` 或明确 `ann_missing`  
- [x] core 回测含 `source_audit`  
- [x] `get_bars(adjust="raw")` 与 qfq 标签可区分  
- [ ] （运营）`real_multi_coverage≥0.5` — 持续跑 warmup，非一次 commit  

**现行不冲全市场数仓 / N6 OMS。**
