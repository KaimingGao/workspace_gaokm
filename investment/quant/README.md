# quant

量化研究台 canonical 包（P28+）：服务、运维、研究与 Agent Skill。

## 子目录

| 目录 | 说明 |
|------|------|
| [services/](services/README.md) | `QuantService`、报告导出、持仓联动 |
| [ops/](ops/README.md) | daily preset、健康检查、eval 路由、包信息 |
| [research/](research/README.md) | 因子 IC、TopK 摘要、中性化对照 |
| [skill/](skill/README.md) | Agent `quant(task=...)` 引擎与 Handler |

## 导入约定

```python
from quant import QuantService
from quant.ops.daily_presets import resolve_daily_preset
```

P41 起 legacy shim 已删除，仅使用 `quant.*` 路径。

## 相关文档

- [quant.md](../docs/quant.md) — 原理与实现；[ML 视角](../docs/quant.md#机器学习视角如何理解量化)
- [quant-ops.md](../docs/quant-ops.md)
- [quant-upgrade.md](../docs/archive/quant-upgrade.md)
- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
