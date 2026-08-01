# tests

离线单元测试与量化阶段验收（网络依赖均 mock）。

## 运行

```bash
python3 -m unittest discover -s tests -v
bash scripts/ci_quant.sh
```

## 命名（按域优先）

| 模式 | 说明 |
|------|------|
| `test_*.py` | 功能单测（域命名） |
| `test_neutralize.py` / `test_neutral_compare.py` / `test_neutral_export_interpret.py` | 截面中性化与对照 |
| `test_daily_ops.py` | 日报 preset / health / 信号配置 |
| `test_export_report.py` | 报告导出 / TOC / 摘要 |
| `test_eval_routing.py` | Eval / golden / quant 路由 |
| `test_quant_package_guards.py` | 包结构 / shim / README / CI 守卫 |
| `test_docs_guards.py` · `test_web_quant_js_guards.py` | 文档与前端字符串守卫 |
| `test_evals_quant_cases.py` · `test_agent_quant_hints.py` | golden 用例 / Agent 提示 |
| `test_p*_quant.py` | 尚未收口的历史阶段验收（逐步迁入域测） |
| `test_web_api.py` | FastAPI 路由 mock 测 |
| `test_p95_p96_web.py` | 多页壳渲染验收 |
| `test_p102_frontend_js.py` | 前端 JS 语法门禁 |

```bash
python3 scripts/check_frontend_js.py   # 也可单独跑
```

## 约定

- 新 quant 测试 import `quant.*`（见 `test_quant_package_guards.py` 内 AST 守卫，扫全部 `test_*.py`）
- UI 字符串断言指向 `web/static/js/quant.js` 与 `partials/*`，勿再读已拆空的 `index.html` / 薄壳 `app.js`
- Agent 全量回归不进 PR CI（需 `DOUBAO_API_KEY`）

## 相关文档

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
