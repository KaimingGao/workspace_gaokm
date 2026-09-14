# 底仓做 T 产品文档（v6 收盘带宽）

> A 股 T+1 底仓日内往返策略。非实盘、不代客下单，服务于策略验证与纸面增强。

---

## 1. 产品定位与本质

### 1.1 一句话定义

在**已持有的底仓**上，利用日内 5 分钟 K 线的价格波动，条件性地做一笔 **先卖后买（反 T）** 或 **先买后卖（正 T）** 的往返，赚取腿间价差。

### 1.2 与策略调仓的边界

| 维度 | 策略调仓 | 底仓做 T |
|------|----------|----------|
| 决策问题 | 持有什么、每次加几手 | 既有底仓上今天能否用日内波动做往返 |
| 信号头 | y_fuse / y_on ranking · 200/500 股 | v6：ŷ_oc clip 估 C_τ，收价相对 C_τ×(1±δ) 选向；leg2 冻结 C_τ |
| 决策频率 | 每个交易日 09:30 | 每 5 分钟扫至 11:00 |
| 收益类型 | 持有期相对收益 | 已实现 round-trip 价差 |
| 仓位出处 | `origin=strategy` | 做 T leg（`t0_batch`） |
| 分组分档 | 观察池按 `fit_tier` 限制新开/加 | **不看**：做 T 只在已持底仓上 overlay，不吃 ŷ_EOD / 拟合档 |

**关键区别**：做 T 不是独立选股 Alpha，而是在调仓给定的底仓上做 **timing overlay**。归因必须分层（调仓 PnL vs 做 T leg）。v6 估 C_τ / 选腿用 ŷ_oc；**ŷ_EOD 不参与**，因此也不套分组 A/B/C 宇宙。

### 1.3 A 股 T+1 硬约束

- **反 T（先卖后买）**：只能卖 **昨日及以前** 买入的可卖旧仓，不能卖当日新买入的仓位。
- **正 T（先买后卖）**：当日买入的新股 T+1 锁定，第二腿只能卖 **旧底仓**，不能卖当日加仓部分。
- 交易必须 **整手（100 股）**，不足 1 手跳过。

---

## 2. 核心概念

### 2.1 方向（direction）

| 方向 | 内部枚举 | 中文 | 操作 | 适用场景 |
|------|----------|------|------|----------|
| 正 T | `buy_then_sell` | 先买后卖 | 现价加仓 → 冲高卖旧仓 | C 低于 C_τ×(1−δ) |
| 反 T | `sell_then_buy` | 先卖后买 | 现价卖旧仓 → 回落买回 | C 高于 C_τ×(1+δ) |

### 2.2 两腿（leg）

- **Leg1（第一腿 / 触发腿）**：确认根的 **5m 收盘价** 成交。反 T = 卖出；正 T = 买入。
- **Leg2（第二腿 / 平仓腿）**：盘中分钟第一触达目标价成交。反 T = 买回；正 T = 卖出旧仓。

### 2.3 关键价格锚

| 符号 | 含义 | 来源 |
|------|------|------|
| `ref` | 参考价（默认开盘价） | 日线 open / 分钟首根 open |
| `C_τ`（`close_px`） | 目标交易价 | `C_τ = O × (1 + clip(ŷ_oc × scale, y_oc_l, y_oc_u) / 100)`（默认 scale=10，clip ±3；ŷ_τc 不参与） |
| `δ`（`delta_pct`） | 超额带宽 | `upper = C_τ×(1+δ/100)`，`lower = C_τ×(1−δ/100)`；默认 3 |
| `leg2_target` | 第二腿冻结目标价 | **C_τ** |

---

## 3. 多层 ŷ 信号体系（dual_y）

做 T 的方向与准入由多层预测分数联合决定。所有 ŷ 均为 **百分点**（ratio × 100），但角色不同：

- **主轴**：`ŷ_oc` 估 C_τ（clip 后），超额 r=(C/C_τ−1)% 相对 ±δ 选向；调仓 ranking 仍是 `y_trade` / `y_eod`（做 T **不吃**）。
- **形状旁路**：`y_hl` · `y_cx` · `y_tpd` 拟合 **分钟 K 的形状**（极值序 High–Low / 曲折度 / 转折密度），**不进 ranking、不进 `C_τ`**，只做准入与风险闸。旧键 `y_path` / `y_complexity` 仍可读。

### 3.1 各 ŷ 角色

| ŷ | 标签 | 含义 | 在做 T 中的作用 |
|----|------|------|-----------------|
| `y_trade` | 日频交易分数 | 横截面排序主信号 | **不参与**估 C_τ 与选腿 |
| `y_eod` | 预估日收收益 | open→close（昨收口径） | **不参与**估 C_τ 与选腿 |
| **`y_τ`** | 盘中 τ 收益 | open→close（开盘口径，OC 拟合） | **估 C_τ**；门槛 \|y_τ\| 入场 |
| `y_hl` | 分钟K形状旁路 | 极值序 signed range%（旧键 `y_path`） | 缺测闸 + 强异号 + 门槛 \|y_hl\| 入场 |
| `y_nowcast` | 即时对照 | 盘中剩余收益 | **不参与**估 C_τ 与选腿 |
| `y_on` | 尾盘回补 | 尾盘是否强制回补 | 控制 EOD 强平 |
| `y_cx` | 分钟K形状旁路 | 曲折度 \(1-D/L\in[0,1]\)（旧键 `y_complexity`） | **风险**：太折（`> y_complexity_max` / `y_cx_max`）跳过 |
| `y_tpd` | 分钟K形状旁路 | 转折点密度 TPD∈[0,1] | **风险**：反转过密（`> y_tpd_max`）跳过 |
| **`y_τc`** | τ→收 | `close[T]/price(τ)−1` | **旁路验证**：不进 C_τ / 选腿 / 目标价；破带后 |ŷ_τc| 超 `y_tc_strong` 且逆带则跳过；0=任意有符号须同号；1=关 |
| **`y_τ30`** | τ→τ⊕30m | `price(τ⊕30m)/price(τ)−1`（交易时钟，跳过午休） | **旁路 + 门槛入场**：不进 C_τ / 选腿 / 目标价。破带后须与方向同号（`y_t30_strong`，默认 0=有符号须同号；1=关）。\|ŷ_τ30\| 入场在门槛1/2（`y_t30_enter` / `_alt`；缺分不拦）。14:35 后不足 30 交易分钟不出分 |
| **`y_τ60`** | τ→τ⊕60m | `price(τ⊕60m)/price(τ)−1`（交易时钟，跳过午休） | **旁路 + 门槛入场**：不进 C_τ / 选腿 / 目标价。破带后须与方向同号（`y_t60_strong`，默认 0=有符号须同号；1=关）。\|ŷ_τ60\| 入场在门槛1/2（`y_t60_enter` / `_alt`；缺分不拦）。14:05 后不足 60 交易分钟不出分 |

### 3.2 方向映射（`y_tau_map`，已下线）

v6 选正/反 T **只看** C 相对 C_τ 的 ±δ 破带（§4）。`y_tau_map` 在 `load_t0_rules` 时丢弃，不改破带结果。`resolve_dual_y_direction` 单测若直接传入该键，仍按旧表解析：

| 模式 | 规则 |
|------|------|
| `trend`（默认） | `ŷ_τ > 0` → 正 T；`ŷ_τ < 0` → 反 T |
| `fixed_sell_then_buy` | 忽略 ŷ_τ 符号，固定反 T（仍过门槛） |
| `fixed_buy_then_sell` | 忽略 ŷ_τ 符号，固定正 T（仍过门槛） |

### 3.3 准入链（`resolve_dual_y_direction`，库函数）

v6 开腿不走本链。下列闸仅当 cfg **直接**传入时生效；`load_t0_rules` / 纸面 overlay 会丢弃 `y_trade_strong` / `y_eod_*` / nowcast 闸。

顺序固定，任一不满足则跳过：

1. `|y_trade| ≥ y_trade_enter`（入场下限）
2. 存在 `y_τ`（方向锚）
3. 若 `y_use_path` 且有 `y_hl`：`y_τ · y_hl` 同号 且 各过**侧向** enter；否则 `|y_τ| ≥ 侧向 y_tau_enter`
4. 若 `y_path_required` 但缺 `y_hl` → 跳过
5. `|y_trade| > y_trade_strong` 须与定方向 `y_τ`（OC）同号
6. `|y_eod| ≥ y_eod_enter`；`|y_eod| > y_eod_strong` 须与 `y_τ` 同号
7. 可选 `y_nowcast`：`|nc| ≥ y_nc_enter`；`|nc| > y_nc_strong` 须同号

### 3.4 因果信息集（防前视）

- **开盘选向**：仅用昨收因子 + 今开缺口（开盘 Z）。
- **确认根重算**：每根 5m 用 **截至该根的前缀分钟** 因果重算 `ŷ_τ` / `ŷ_hl`，再定方向。
- **禁止**用全日或未发生分钟做开盘选向。
- 盘后（`eod_next`）决策排除 τ 侧分量，避免 look-ahead。

---

## 4. v6 收盘带宽选腿（核心入场机制）

### 4.1 整体流程

```
每根 5m K（09:35 ~ 11:00）
   │
   ├─ 解析价空间 S = O_d / O_m
   ├─ 用前缀 ŷ_oc 估 C_τ = O×(1+clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)
   ├─ 超额 r = (C/C_τ−1)×100 相对对称 ±δ：
   │     r > +δ  → 反 T（现价卖；leg2=C_τ）
   │     r < −δ  → 正 T（现价买；leg2=C_τ）
   │     带内      → 跳过
   ├─ 入场门槛校验（门槛1 ∪ 门槛2：τ / path / τ30 / τ60 入场 · complexity / tpd 风险）
   ├─ 强 path 同号校验
   ├─ ŷ_τc 旁路：|ŷ_τc| 超 `y_tc_strong` 且逆带 → 跳过（0=任意有符号须同号；1=关）
   ├─ ŷ_τ30 旁路：|ŷ_τ30| 超 `y_t30_strong` 且逆带 → 跳过（默认 0=有符号须同号；1=关）
   ├─ ŷ_τ60 旁路：|ŷ_τ60| 超 `y_t60_strong` 且逆带 → 跳过（默认 0=有符号须同号；1=关）
   └─ 通过 → 开一轮（现价成交；freeze 目标价 = C_τ）
```

### 4.2 价空间对齐（price space）

模型在 **日线空间** 训练（open→close），执行在 **分钟价空间** 触价。用缩放因子 `S = O_d / O_m` 对齐：

- 估 C_τ 用日线 open（与训标签同空间）
- 破带比较本根 5m 收价 C
- `|S − 1| > t0_price_space_max_dev_pct`（默认 5%）→ 跳过（`price_space_mismatch`）

### 4.3 方向判定

```python
# r、δ、ŷ_oc、clip 沿均为百分点；price = 开盘 O
C_τ = O * (1 + clip(y_oc * scale, y_oc_l, y_oc_u) / 100)
r = (C / C_τ - 1) * 100
if r > +δ:   → 反 T，现价卖，leg2 = C_τ
if r < −δ:   → 正 T，现价买，leg2 = C_τ
```

ŷ_τc **不参与**估 C_τ / 选向 / 目标价。默认 scale=10、y_oc_l=−3、y_oc_u=+3、δ=3。可选旁路闸见 4.6。

### 4.4 入场门槛（`close_band_enter_skip_reason`）

选向后过 **已启用的门槛1 或 门槛2** 任一即可开 leg1。每档都是 **|y_τ|、|ŷ_τc|、|y_hl|、|ŷ_τ30|、|ŷ_τ60| 过入场 AND cx/tpd 过上限**：

| 档 | 启用 | `|y_τ|` | `|ŷ_τc|` | `|y_hl|` | `|ŷ_τ30|` | `|ŷ_τ60|` | `ŷ_cx` | `ŷ_tpd` |
|----|------|---------|----------|----------|-----------|-----------|---------|---------|
| 门槛1 | `y_enter_enabled` 默认开 | `y_tau_enter` 默认 0（关） | `y_tc_enter` 默认 0（关） | `y_path_enter` 默认 0（关幅度） | `y_t30_enter` 默认 0（关；缺分不拦） | `y_t60_enter` 默认 0（关；缺分不拦） | `y_complexity_max` 默认 1.0≈关 | `y_tpd_max` 默认 1.0≈关 |
| 门槛2 | `y_enter_alt_enabled` 默认开 | `y_tau_enter_alt` 默认 0（关） | `y_tc_enter_alt` 默认 0（关） | `y_path_enter_alt` 默认 0（关幅度） | `y_t30_enter_alt` 默认 0（关；缺分不拦） | `y_t60_enter_alt` 默认 0（关；缺分不拦） | `y_complexity_max_alt` 默认 1.0≈关 | `y_tpd_max_alt` 默认 1.0≈关 |

关启用则该档不参与 OR；两档都关则不开腿。缺键时门槛2 跟随门槛1。缺 path / 分钟缺失共用，不能被门槛2 绕过。缺 ŷ_τ30 / ŷ_τ60 **不拦**（与 TC 不同）。两档都未过时跳过文案写「门槛1 …；门槛2 …」。

### 4.5 强信号同号闸（`close_band_sign_skip_reason`）

`|ŷ_hl| > y_path_strong`（默认 5%）时，`ŷ_hl` 须与 `ŷ_τ` **同号**，异号跳过。trade/eod 强闸已下线，仅保留 HL。

### 4.6 ŷ_τc 旁路验证（`close_band_y_tc_skip_reason`）

破带选向之后、开腿之前：ŷ_τc 只校验「剩余窗是否支持向 C_τ 回归」，**不改 C_τ**。

| 破带 | 期望 ŷ_τc | 含义 |
|------|-----------|------|
| 反 T（C > C_τ×(1+δ)） | remaining < 0 | 价在 C_τ 上，应收口向下 |
| 正 T（C < C_τ×(1−δ)） | remaining > 0 | 价在 C_τ 下，应收口向上 |

- `y_tc_strong` 默认 **1=关**（只把顺/逆带写入扫描与分槽位「旁路」列）。
- `=0`：任意有符号 ŷ_τc 须与破带方向同号（逆带即跳过）。
- `(0, 1)`：`|ŷ_τc|` 超此值且逆带 → 跳过；弱 ŷ_τc / 缺测不拦。
- 与 y_oc 符号命中无关：旁路问的是 **τ→收**，不是已被早上垫高的全日 OC。

---

## 5. Leg1 执行

### 5.1 成交价

Leg1 以 **确认根的 5m 收盘价** 成交（`fill_sell = close` / `fill_buy = close`）。不再使用相对开盘的触发百分比。

### 5.2 多轮开仓（slots）

- 每轮仓位 `ratio = t0_round_ratio`（默认 0.4，即 40% 可卖量）
- 最多 `t0_slots_max_rounds` 轮（默认 5），累计不超过 `t0_max_position_pct`（默认 1.0）
- 每轮独立冻结 leg2 目标，各轮 leg1 / leg2 独立配对
- **11:00 后不开新 leg1**（`T0_LAST_LEG1_HM = "11:00"`），午后只处理已开仓的 leg2

### 5.3 现金与可卖约束

- 反 T leg1 卖出受剩余 **可卖旧仓** 约束
- 正 T leg1 买入受 **账户现金** 约束（含手续费）；第二腿仍卖旧仓，开新轮前预扣已承诺回补额度
- 不足整手时本轮跳过
- 日终按墙钟重放：现金/可卖不够时丢掉 **最晚开** 的一轮，不撤已成交的第一笔

### 5.4 冻结第二腿目标（`freeze_round`）

Leg1 成交瞬间冻结本轮参数：

| 方向 | leg2_target |
|------|-------------|
| 正 T | C_τ |
| 反 T | C_τ |

冻结后 leg2 目标价不再随后续 ŷ 变化。

---

## 6. Leg2 执行

### 6.1 目标价优先级

Leg2 的触发目标价按以下优先级确定：

1. **冻结目标交易价**（`leg2_target_px`）：leg1 成交时冻结的 `C_τ`
2. **τ 出场价闸**：`bound = open × (1 + ŷ_τ × mult / 100 + bias)`，无冻结目标时回退
3. **平盘线**：以 leg1 成交价为目标（缺 ŷ_τ 时）

### 6.2 τ 出场价闸（`tau_exit_bound_px`）

公式：

```
move_pct = clamp(ŷ_τ × mult, move_min, move_max) + bias
bound = ref × (1 + move_pct / 100)
```

| 参数 | 默认（正 T / 反 T） | 说明 |
|------|---------------------|------|
| `mult` | 1.0 / 1.0 | 裕度乘数 |
| `bias` | +1.0 / −1.0 | 百分点偏移 |
| `move_min/move_max` | ±100 | clamp 范围 |
| `skip` | True | 闸开关 |

**方向条件**：

- 正 T（卖旧仓）：`fill_px > bound`（卖价须高于 bound）
- 反 T（买回）：`fill_px < bound`（买价须低于 bound）

### 6.3 午后追价（PM chase）

到 `t0_pm_degrade`（默认 13:00）后：

- **禁开新 leg1**
- 已开未平的 leg2 启用 **中点追价**：`新目标 = (旧目标 + 现价) / 2`
- 每 `t0_pm_chase_interval_min`（默认 5 分钟）调整一次
- 追价有上下限保护：
  - 反 T 追买上限 `≤ leg1 卖价`（`t0_pm_chase_cap_leg1_sell_then_buy`）
  - 正 T 追卖下限 `≥ leg1 买价`（`t0_pm_chase_cap_leg1_buy_then_sell`）
  - `must_cover_same_day=True` 时强制生效

### 6.4 止损

| 方向 | 止损条件 | 配置 |
|------|----------|------|
| 正 T | 跌破 `买价 × (1 − stop_pct%)` | `t0_stop_pct_buy_then_sell`（默认 1.2%） |
| 反 T | 涨破 `卖价 × (1 + stop_pct%)` | `t0_stop_pct_sell_then_buy`（默认 1.2%） |

- 延迟 `t0_stop_arm_bars`（默认 1 根）后生效
- 止损固定收盘破线确认（`t0_stop_on_close=True`，表单不再提供开关）
- 止损 leg2 不受 τ 出场价闸约束

### 6.5 收盘强平 / 敞口

收盘窗（末根 ≥ 14:55）时 leg2 未平：

| `must_cover_same_day` | 行为 |
|------------------------|------|
| `True`（默认） | 用末根 5m 收盘价强制买回/卖回；现金不足则记 `abandon_cover_cash` 敞口 |
| `False` | 记 `abandon_cover` 敞口（`exposure_pnl`） |

- 强平价用 **末根 5m 收盘价**，不用日线收盘价
- 盘中前缀（`defer_eod=True`）未完记 `defer_eod_pending`，不计敞口

---

## 7. 成交模式（fill_mode）

| 模式 | 卖出成交价 | 买入成交价 |
|------|-----------|-----------|
| `trigger`（默认） | 触发价（目标价） | 触发价（目标价） |
| `mid` | `(high + sell_level) / 2` | `(low + buy_level) / 2` |
| `optimistic` | 当根 high | 当根 low |

可分侧配置 `fill_mode_sell_then_buy` / `fill_mode_buy_then_sell`。

---

## 8. 成本模型

与纸面调仓同源（`CostPort`），默认 A 股简单费率：

| 项 | 默认值 | 说明 |
|----|--------|------|
| 佣金 | 2.5 bps | 最低 5 元 |
| 印花税 | 5 bps（仅卖出） | |
| 滑点 | 3 bps | 研究回测默认 |

每腿成交写入 `net_cash_delta`（含手续费与滑点）。已实现 PnL = 各腿净现金之和。

---

## 9. 回测框架

### 9.1 数据要求

- **必须有 5 分钟 K 线**，缺分钟线的交易日 **整段跳过**（不回退日线模拟）
- 分钟线 ≥ 2 根才进入路径（slots 模式 ≥ 1 根）
- 5m 数据拉取有 18 秒超时保护，失败回退无分钟 + 跳过

### 9.2 路径模式

统一 `path_mode=first_touch`：按分钟时间序 **第一触达** 成交，不再使用日线 high/low 代理。

### 9.3 跳过类别

| 类别 | 原因 |
|------|------|
| `missing_minute` | 缺分钟线 |
| `signal_skip` | 未过 ŷ 入场门槛 / 破带未成立 |
| `coupling_skip` | stance 耦合不允许做 T |
| `t_plus_one` | 可卖旧仓不足 1 手 |
| `intraday_legs_open` | 盘中已有成交腿，整单回放防重复 |
| `price_space_mismatch` | 日/分钟开盘偏差超阈 |

### 9.4 高/低 ≈ 低（涨停/跌停无波动）的票跳过

`simulate_t0_day` 中 `high ≈ low` 的票不做 T。

### 9.5 虚拟仓与本金

`/replay` 做 T 回测只用纸面股票名单；仓位/本金走表单「共用」：

- **股数/票** 默认 1000（`initial_shares`）
- **本金** 默认 20 万（`initial_cash`），也是累计收益比例的分母
- 不写账本；保存规则不改这两项

---

## 10. 纸面与实盘执行

### 10.1 纸面持仓做 T

`simulate_t0_on_holdings` 对纸面持仓逐票跑单日做 T：

- 按股票代码排序，共享现金池，执行序确定可复现
- T+1 按「当前交易会话日」解冻可卖量
- `dry_run=True` 只返回预演结果，不改 paper
- 成交腿写入 `paper.trades`，记 `origin=t0`
- 操作日志记 `t0_batch`

### 10.2 盘中 Worker

- `auto_worker.py` / `intraday.py`：每 5 分钟（`T0_INTRADAY_TICK_SEC = 300s`）轮询
- 分钟缓存 TTL 5 小时，lookback 5 天
- 盘中前缀 `defer_eod=True`，避免半日分钟误强平
- 已落账腿的代码在整单回放时跳过（防重复）

### 10.3 stance 耦合

`coupling.t0_vs_stance` 控制做 T 与调仓 stance 的关系：

- `independent`（默认）：互不影响
- `avoid`：该 stance 下跳过做 T

---

## 11. 关键参数速查

| 参数 | 默认 | 说明 |
|------|------|------|
| `t0_ratio` | 1.0 | 动仓比例（纸面/回测强制 1.0） |
| `t0_round_ratio` | 0.4 | 每轮开仓比例 |
| `t0_max_position_pct` | 1.0 | 累计最大动仓比例 |
| `t0_slots_max_rounds` | 5 | 最大轮数 |
| `t0_close_band_delta_pct` | 3.0 | 超额带宽 δ%：upper/lower = C_τ×(1±δ/100)；leg2 = C_τ |
| `t0_y_oc_target_scale` | 10.0 | y_oc 放大：clip(ŷ_oc×scale, y_oc_l, y_oc_u) 的倍数 |
| `t0_y_oc_l` | −3.0 | C_τ clip 下界（百分点） |
| `t0_y_oc_u` | +3.0 | C_τ clip 上界（百分点） |
| `must_cover_same_day` | True | 收盘强制回补 |
| `t0_pm_degrade` | 13:00 | 午后禁新开 leg1 |
| `t0_stop_pct_*` | 1.2 | 止损百分比 |
| `t0_stop_arm_bars` | 1 | 止损延迟根数 |
| `t0_stop_on_close` | True | 止损固定收盘破线确认（表单无开关） |
| `y_enter_enabled` | True | 门槛1 启用（关则本档不参与 OR） |
| `y_enter_alt_enabled` | True | 门槛2 启用（关则本档不参与 OR） |
| `y_tau_enter` | 0 | 门槛1 \|ŷ_oc\| 入场下限（%；0=关；页面 oc入场%） |
| `y_tau_enter_alt` | 0 | 门槛2 \|ŷ_oc\| 入场下限（%；0=关；页面 oc入场%） |
| `y_tc_enter` | 0 | 门槛1 \|ŷ_τc\| 入场下限（%；0=关；页面 TC入场%） |
| `y_tc_enter_alt` | 0 | 门槛2 \|ŷ_τc\| 入场下限（%；0=关；页面 TC入场%） |
| `y_t30_enter` | 0 | 门槛1 \|ŷ_τ30\| 入场下限（%；0=关；缺分不拦；页面 τ30入场%） |
| `y_t30_enter_alt` | 0 | 门槛2 \|ŷ_τ30\| 入场下限（%；0=关；缺分不拦；页面 τ30入场%） |
| `y_t60_enter` | 0 | 门槛1 \|ŷ_τ60\| 入场下限（%；0=关；缺分不拦；页面 τ60入场%） |
| `y_t60_enter_alt` | 0 | 门槛2 \|ŷ_τ60\| 入场下限（%；0=关；缺分不拦；页面 τ60入场%） |
| `y_path_enter` | 0 | 门槛1 \|y_hl\| 入场下限（%；0=关幅度） |
| `y_path_enter_alt` | 0 | 门槛2 \|y_hl\| 入场下限（%；0=关幅度） |
| `y_path_strong` | 5 | \|y_hl\| 超此值须与 ŷ_oc 同号 |
| `y_tc_strong` | 1 | ŷ_τc 旁路：0=任意有符号须同号；1=关 |
| `y_use_path` | True | 须有 y_hl（缺则跳过；表单已下线，恒为开） |
| `y_complexity_max` | 1.0 | 门槛1 复杂度上限（0–1；≈关） |
| `y_tpd_max` | 1.0 | 门槛1 拐点密度上限（0–1；≈关） |
| `y_complexity_max_alt` | 1.0 | 门槛2 复杂度上限（0–1；≈关） |
| `y_tpd_max_alt` | 1.0 | 门槛2 拐点密度上限（≈关） |
| `t0_price_space_max_dev_pct` | 5.0 | 价空间偏差上限（%） |
| `fill_mode` | trigger | 成交假设 |
| `direction` | dual_y | 方向模式 |

---

## 12. 代码结构

```
core/t0/
├── config.py          # 规则配置（DEFAULT_T0_RULES、load_t0_rules）
├── rules.py           # 单日做 T 入口、整手/参考价/ATR
├── close_band.py      # v6 收盘带宽选腿（ĉ 估计、破带、冻结）
├── slots.py           # 多轮开仓（逐根扫描、合并）
├── minute_path.py     # 分钟第一触达路径（leg1/leg2/止损/追价/EOD）
├── score_policy.py    # dual_y 多层 ŷ 准入链与方向解析
├── costs.py           # 成交成本（佣金/印花税/滑点）
├── backtest.py        # 回测入口与质量指标
├── intraday.py        # 盘中 Worker
└── auto_worker.py     # 自动做 T 调度
```

---

## 13. 已下线设计（避免误用）

以下旧机制已在 v6 下线，配置中残留会被 `drop_dead_t0_keys` 丢弃：

- 相对开盘的 leg1 触发百分比（`buy_trigger_pct_*` / `sell_trigger_pct_*`）
- τ 入场价闸（`y_tau_entry_price_*`）—— leg1 改为确认根收盘
- 前缀阴阳占比确认（`y_prefix_*`）
- 复合确认 / 环境闸选腿（`t0_confirm_*` / `t0_env_*`）
- y_path 放弃（`y_path_abandon_*`）
- score 先验平移带宽（`y_tau_leg1_prior_*`）—— 改为 clip 估 C_τ + 对称 ±δ
- `y_tau_map` 符号选向、缺口档 `y_gap_tier_*`、振幅下限 `min_range_pct*`、ATR 触发、trade/eod 强闸、nowcast 异号闸 —— v6 开腿只看收盘带宽 + 入场门槛 / 旁路

---

*文档版本：v6 收盘带宽 · 生成日期 2026-09-06*
