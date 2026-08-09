# evals

黄金用例、离线校验与信号可复现 evals。

## 主要文件

| 文件 | 说明 |
|------|------|
| `golden_cases.json` | 20 个 Agent/Skill 黄金用例 |
| `core_golden_paths.py` | **R5.6** 北极星 KPI · PIT · 调仓硬拦 · **CostPort 对齐** |
| `run_core_paths.py` | 仅跑核心黄金路径 CLI |
| `run_checklist.py` | mock Skills + 可选 LLM 对照（默认同跑 core paths） |
| `run_repro.py` | 信号指纹双跑可复现 |
| `mock_context.py` | mock patch 注册 |
| `preset_check.py` | daily preset 标志位校验 |
| `readme_check.py` | 子目录 README 覆盖 + 架构回链 |
| `run_readme_check.py` | 仅跑 README 校验 CLI |

## 常用命令

```bash
python3 evals/run_checklist.py --mock --presets      # CI 同款（cases + preset + README + core paths）
python3 evals/run_core_paths.py                      # 仅 R5.6 三条核心路径
python3 evals/run_checklist.py --mock --quant-only   # 11 个 quant_* case
python3 evals/run_checklist.py --core-paths          # 显式跑核心路径
python3 evals/run_readme_check.py                    # 仅 README
python3 evals/run_repro.py
bash scripts/agent_regression_quant.sh               # 需 DOUBAO_API_KEY
```

## Agent 周末回归（量化子集）

`quant_interpret_neutral` 校验 Agent 在「解读 + 中性化对照」场景下须含 **中性化**、**对照** 及免责声明短语；与 Skill 侧 `offline: true` 规则解读（`source=rule_based`）对齐。跑法：

```bash
bash scripts/agent_regression_quant.sh    # 11 个 quant_* case（含 factor_ols / model_policy）
bash scripts/agent_regression.sh          # 全量 19 case
```

## 相关文档

- [development.md](../docs/development.md)
- [upgrade-refactor-plan.md · R5](../docs/archive/upgrade-refactor-plan.md)
- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
