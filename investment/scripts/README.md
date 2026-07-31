# scripts

Shell 封装：cron / launchd / 本地 CI / Agent 回归。

## 常用脚本

| 脚本 | 说明 |
|------|------|
| `ci_quant.sh` | 本地 CI 镜像（单测 · import 审计 · evals · daily mock） |
| `check_quant_imports.sh` | quant legacy import 守卫 |
| `daily_advisor.sh` | 投顾 daily preset |
| `daily_quant.sh` | 量化 daily preset |
| `daily_quant_paper.sh` | 量化 + 纸面调仓 |
| `daily_full.sh` | advisor + quant |
| `daily_check.sh` | cron 失败检查 |
| `setup_quant.sh` | 初始化 watching + 纸面 |
| `agent_regression.sh` | 全量 Agent 黄金回归 |
| `agent_regression_quant.sh` | 9 个 quant_* case |

## 子目录

- [launchd/](launchd/README.md) — macOS 定时任务 plist 模板

## 相关文档

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
