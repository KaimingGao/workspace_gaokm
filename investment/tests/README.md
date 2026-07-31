# tests

离线单元测试与量化阶段验收（网络依赖均 mock）。

## 运行

```bash
python3 -m unittest discover -s tests -v
bash scripts/ci_quant.sh
```

## 命名

| 模式 | 说明 |
|------|------|
| `test_*.py` | 功能单测 |
| `test_p*_quant.py` | 量化升级阶段验收（P6～P42） |
| `test_web_api.py` | FastAPI 路由 mock 测 |
| `test_p102_frontend_js.py` | 前端 JS 语法门禁（`??`/`||` 混用等） |

```bash
python3 scripts/check_frontend_js.py   # 也可单独跑
```

## 约定

- 新 quant 测试 import `quant.*`（见 `test_p34_quant.py` AST 守卫）
- Agent 全量回归不进 PR CI（需 `DOUBAO_API_KEY`）

## 相关文档

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
