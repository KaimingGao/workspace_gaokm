"""QuantLab 量化研究台（P28+ 独立包）。

共享领域层仍在 `core/signal`、`core/backtest`（投顾与量化共用 score/回测）。
本包聚合：services · ops · research CLI · Agent skill 适配。
"""

from quant.services.quant_service import QuantService

__all__ = ["QuantService"]
