"""出站适配器层：外部数据源 / I/O，经 ports 注入 core。

- ``adapters.market`` — 行情 · 日线 · 分钟线 · 指数
- ``adapters.news`` / ``fundamentals`` / ``macro`` / ``sentiment`` / ``announcement`` 等 — 非行情外部源
- ``adapters.bind`` — 默认实现登记到 ``core.ports.registry``

Skills（``skills/*/``）只服务 Agent（handler + 兼容 re-export）。
"""
