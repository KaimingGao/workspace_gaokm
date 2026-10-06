# data

本地运行时数据与配置（含观察/纸面账本 · 行情缓存 · 日报）

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
- [分钟线采集与缓存](../docs/architecture.md#分钟线采集架构akshare--baostock)

**分钟线落盘**（默认 SQLite）：`store/bars.db` 表 `minute_bars` / `minute_cache_meta`；JSON 回退：`store/minute/{period}/CN/{code}.json`。
