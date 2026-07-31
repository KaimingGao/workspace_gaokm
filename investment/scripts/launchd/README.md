# scripts/launchd

macOS `launchd` 定时任务 plist 模板。

## 使用

1. 复制 `*.plist.example` 到 `~/Library/LaunchAgents/`
2. 将 `CHANGE_ME` 替换为项目绝对路径
3. `mkdir -p data/logs`
4. `launchctl load ~/Library/LaunchAgents/com.investment.*.plist`

## 示例

- `com.investment.daily-advisor.plist.example` — 工作日投顾 daily
- `com.investment.daily-quant.plist.example` — 工作日量化 daily
- `com.investment.paper-daily.plist.example` — 工作日纸面日更（P2 / N5）

详见 [quant-ops.md](../../docs/quant-ops.md)。

## 相关文档

- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
