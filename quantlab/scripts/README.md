# scripts

Shell 封装：launchd 定时 · 本地 CI · 环境引导

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
- 日线观察池 topup：`python3 scripts/run_bars_topup_once.py`（须用脚本文件启动，勿 heredoc，否则 spawn 进程池会找 `<stdin>`）
