# skills/screen

A 股条件选股 Skill（AkShare + 本地过滤）。

## 能力

- 行业关键词、PE/PB、涨跌幅过滤；排除 ST

## 示例

「找市盈率低于 15 的银行股」

## 依赖

- 完整功能需安装 `akshare`
- 现货拉取：进程内短缓存（约 2 分钟）+ 失败重试 + 本地 `data/store/spot_a_em.json` 回退（24h）

## 相关文档

- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
