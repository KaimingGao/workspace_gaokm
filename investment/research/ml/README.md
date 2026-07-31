# ML 研究轨（N2）

离线训练与评估产物目录。**禁止**把推理结果直接写入生产 `score` / `stance_label`。

## 约定

1. 训练脚本产出 `artifact.json`（特征清单、样本区间、指标、模型路径）。
2. 若要对齐生产：只允许导出「权重/阈值 diff」或旁路 alpha 列，经人审 `promote_strategy` / 合并 `signal_config`。
3. 生产主轴仍是 `core/signal/scorer.score_bars` 线性加权。

## 快速生成占位 artifact

```bash
python -m research.ml.write_artifact --note "placeholder"
```

见 `write_artifact.py`。
