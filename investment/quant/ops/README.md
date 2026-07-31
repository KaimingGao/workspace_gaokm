# quant/ops

量化运维与 introspection（preset、健康、CI 路由、包结构）。

## 主要模块

| 模块 | 说明 |
|------|------|
| `daily_presets.py` | `advisor` / `quant` / `quant_paper` / `full` preset |
| `daily_health.py` | watching + daily 上次运行 + 报告索引聚合 |
| `eval_routing_map.py` | golden case 路由预期对照表 |
| `package_info.py` | 包模块树、`removed_shim_paths` |
| `shim_audit.py` | legacy import 守卫 |

README 覆盖索引见 [`core/readme_index.py`](../../core/readme_index.py)；内容 API：`GET /api/readme?dir=`（P43）。

## 脚本

```bash
bash scripts/check_quant_imports.sh
python3 evals/run_preset_check.py
```

## 相关文档

- [quant-ops.md](../../docs/quant-ops.md)
- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
