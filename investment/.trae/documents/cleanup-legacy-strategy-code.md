# 清理旧策略代码

## Context

策略中心页当前只展示 2 个 canonical 策略（`short` / `short_conservative`），但代码中仍保留两套遗留兼容层：

1. **旧策略 ID 别名**（`STRATEGY_ALIASES`）：`signal_v1` / `short_v1` / `signal_v1_conservative` 三个旧名经 `resolve_strategy_id()` 映射到 canonical。运行时代码无任何调用方主动传入旧 ID（仅 `tool_config.json` 宣传"旧名仍可用"），属纯兼容层。
2. **遗留纸面评分键**（`_LEGACY_PAPER_SCORE_KEYS`）：0–100 时代 `min_score` / `add_score` / `min_hold_score` / `reduce_score` 作为 `paper_rules` 键的产物。`apply_strategy_to_paper` / `promote_strategy` 主动从 `paper_rules` 剥离这些键；选股门槛真源已迁至 `signal_config.scoring.min_predicted_score`。

存量数据中仍残留 legacy 键：`data/strategy_promoted.json`（paper_rules 含 4 个 legacy 键）、`data/paper.example.json`（paper_rules 含 `min_score` / `min_hold_score`）。`data/paper.json` 真实账户已无 legacy 键。

**目标**：彻底删除两套兼容层，清理存量数据，同步文档与测试。

## 变更清单

### 1. `core/backtest/strategies.py` — 删除别名映射

- 删除 `STRATEGY_ALIASES` 字典（L13-L17）
- 简化 `resolve_strategy_id()`：直接返回 `key`，不再查表（L127-L129）
  ```python
  def resolve_strategy_id(name: Optional[str] = None) -> str:
      return (name or DEFAULT_STRATEGY).strip() or DEFAULT_STRATEGY
  ```
- `get_strategy()` 中 `KeyError` 提示保持原样（仅列 canonical 可选名）
- 头部注释 `# 旧 ID → 新 canonical` 删除

### 2. `core/strategy.py` — 删除 legacy 键剥离逻辑

- 删除 `_LEGACY_PAPER_SCORE_KEYS` 元组（L132-L137）
- `apply_strategy_to_paper()`（L140-L193）：
  - 删除 L148-L150 的 `for k in _LEGACY_PAPER_SCORE_KEYS: pr.pop(k, None); rules.pop(k, None)` 循环
  - 删除函数 docstring 中"不合并 0–100 的 min_score/add_score 等"句
- `promote_strategy()`（L204-L260）：
  - 删除 L235-L237 的 `if k in _LEGACY_PAPER_SCORE_KEYS: continue` 分支
  - 删除 L247-L248 的 `for k in _LEGACY_PAPER_SCORE_KEYS: (spec.get("paper_rules") or {}).pop(k, None)` 防御
- `__all__` 无需改动（未导出 `_LEGACY_PAPER_SCORE_KEYS`）

### 3. `skills/backtest/tool_config.json` — 清理旧名宣传

L14 `strategy` 字段 description：
```
"策略名：short（短线评分）或 short_conservative（保守短线）；旧名 signal_v1 仍可用"
```
改为：
```
"策略名：short（短线评分）或 short_conservative（保守短线）"
```

### 4. `data/strategy_promoted.json` — 清理存量 paper_rules

L16-L25 的 `paper_rules` 删除 4 个 legacy 键：
```json
"paper_rules": {
  "min_score": 60.0,        ← 删
  "add_score": 65.0,       ← 删
  "min_hold_score": 50.0,  ← 删
  "reduce_score": 55.0,    ← 删
  "max_positions": 15,
  "position_pct": 0.12,
  "horizon_days": 3,
  "signal_limit": 5
}
```
保留 `params.min_score: 72.0`（这是合法的回测参数，非 paper_rules 键）。

### 5. `data/paper.example.json` — 清理示例 paper_rules

L10-L26 的 `rules` 删除 2 个 legacy 键：
- `"min_score": 55` ← 删
- `"min_hold_score": 45` ← 删

### 6. `tests/test_q2_q5_strategy.py` — 删除 legacy 测试

删除 3 个测试方法：
- `test_apply_strategy_strips_legacy_score_keys`（L30-L51）
- `test_promote_ignores_legacy_score_overrides`（L53-L66）
- `test_legacy_aliases_resolve`（L68-L74）

保留：
- `test_short_lifecycle`：其中 `assertNotIn("min_score", spec["paper_rules"])` 仍成立（spec 从未含此键，与剥离逻辑无关）
- `test_promote_writes_file`：不涉及 legacy
- `TestRunManifest.test_write_manifest`：`rules={"min_score": 55}` 是测试入参，与 paper_rules 剥离无关，可保留（也可顺手清理为 `rules={"max_positions": 20}`，非必须）

## 不在范围内

以下不属本次清理范围，避免范围蔓延：

- `data/signal_config.json` 的 `rank.min_score: 55`：这是 `signal_config.rank` 段的字段，被 `skills/signal/engine.py` 的 `rank_candidates(min_score=50.0)` 读取，属独立子系统。architecture.md L2755 已标 `rank.min_score deprecated`，但清理需先确认 `rank_candidates` 的迁移路径，不在本次范围。
- `data/paper.json` 的 `last_north_star.something.limits.min_score: 0.4`：这是运行时计算的 `predicted_score` 门槛快照，非 paper_rules 键，不属 legacy。
- `quant/services/quant_service_ops.py` 的 `"deprecated": True` 标记：与策略中心无关，是 quant 服务侧的废弃 ops。
- `quant/ops/shim_audit.py` 的 `LEGACY_MODULES`：模块导入审计，与策略 ID 无关。

## 验证

1. **运行策略相关测试**：
   ```bash
   cd /Users/gaokaiming/Workspace/workspace_gaokm/investment
   python -m pytest tests/test_q2_q5_strategy.py -v
   ```
   预期：3 个 legacy 测试已删除，剩余测试（test_short_lifecycle / test_promote_writes_file / TestRiskGate / TestRunManifest / TestDefaultCost / TestDeepAnalysisRouting）全绿。

2. **运行回测引擎测试**（覆盖 strategies.py）：
   ```bash
   python -m pytest tests/test_backtest.py -v
   ```
   预期：`test_*` 列举 `list_strategies()` 返回的 canonical 名，全绿。

3. **JSON 合法性校验**（清理后的数据文件）：
   ```bash
   python -c "import json; json.load(open('data/strategy_promoted.json')); json.load(open('data/paper.example.json')); print('ok')"
   ```

4. **API 冒烟**（可选）：启动 web 后访问 `/api/strategy`，确认返回 2 个策略卡，无 legacy 键。
