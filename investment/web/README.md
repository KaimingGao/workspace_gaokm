# web

FastAPI Web UI：对话、量化面板、持仓、校验与 evals。

## 主要文件

| 文件 | 说明 |
|------|------|
| `app.py` | 组装 FastAPI：挂载 `routers/*` + 静态页 |
| `deps.py` | 共享服务实例（`quant` / `paper` / …） |
| `schemas.py` | 请求体 Pydantic 模型 |
| `routers/` | 按域拆分：`chat` · `paper` · `quant` · `daily` · `watching` · `platform` · `portfolio` · `evals` · `meta` |
| [static/](static/README.md) | 单页前端（HTML/CSS/JS） |

## 启动

```bash
python3 run_web.py
# → http://127.0.0.1:8000
```

## 顶栏

**对话** · **观察** · **策略** · **模拟** · **回溯** · **研究枢纽**（`/quant`）；`/paper` → `/follow`

## 建仓链路

观察页是唯一的开仓入口，且成交前必须过一次预览：

| 端点 | 作用 |
|------|------|
| `POST /api/watching/sync-paper/preview` | 试算每只买多少、合计花费、建仓后现金；默认按金额，可传 `amount_per_code` / `position_pct` / `shares` / 按只覆盖；**不落盘** |
| `POST /api/watching/sync-paper` | 按确认过的定量参数真买入，持仓标出处 `manual` |
| `POST /api/paper/run` | `dry_run=true` 时预演策略调仓（含 `cash_impact`）；确认后再 `dry_run=false` 成交 |
| `POST /api/paper/cost-model` | 切换 `zero` / `simple_cn` 成交成本（建仓与调仓共用） |

两者共用 `core.paper.plan_buy_codes`，预览与实际成交不会漂移。默认每只 2 万元；股数模式须为 100 的整数倍。

**命名**：产品页 `/follow`（模拟）；内部账本与 API 仍为 `paper`；`/paper` 302 到 `/follow`。

## 相关文档

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
