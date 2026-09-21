# skills

Agent 工具层：自然语言意图 → `handler` →（shim）→ `adapters.*` / `core`。

取数与快照实现在 [`adapters/`](../adapters/README.md)，经 [`adapters.bind`](../adapters/bind.py) 注入 `core.ports`。  
本目录下的 `engine.py` 等为**兼容 shim**（与 adapters 同模块对象，便于旧 mock 路径），不再承载 I/O 实现。

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
- [adapters README](../adapters/README.md)
