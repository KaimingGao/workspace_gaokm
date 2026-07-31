# data

本地运行时数据与配置（部分入 git，部分运行时生成）。

## 主要文件

| 文件 | 说明 |
|------|------|
| `paper.example.json` | 纸面账户模板 → 复制为 `paper.json`（**假钱模拟账户**，非券商实盘；说明见 [quant.md · 纸面是什么](../docs/quant.md#纸面是什么给小白)） |
| `position_rules.json` | 持仓规则阈值（对话 position 默认读 **paper**） |
| `signal_config.json` | 因子权重与 stance 阈值 |
| `watching.example.json` | 观察池 watching 模板 |

## 子目录

| 目录 | 说明 |
|------|------|
| [store/](store/README.md) | 日线本地缓存 |
| [reports/](reports/README.md) | 量化日报 MD/HTML 归档 |

## 注意

- 勿提交真实 `.env` 或含隐私的 `paper.json`
- `store/` 与部分报告为运行时生成
- 现行以本地 JSON/JSONL 为主，**不上 SQLite**；何时换库见 [数据层 · 存储选型](../docs/data-layer.md#存储选型为何是-json何时才上数据库)

## 相关文档

- [数据层说明](../docs/data-layer.md)（含 JSON vs 数据库）
- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
