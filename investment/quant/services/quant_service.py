"""量化研究 Application Service（P8.4 Web / daily 共用；P94 Mixin；①模拟·②回溯·③联动）。

文档称 Application Service，不是 Domain Facade；向下调 DS/SS/BS 与 PaperService。
"""


import logging

logger = logging.getLogger(__name__)
from quant.services.quant_service_compare import QuantCompareMixin
from quant.services.quant_service_config import QuantConfigMixin
from quant.services.quant_service_factors import QuantFactorMixin
from quant.services.quant_service_follow import QuantFollowMixin
from quant.services.quant_service_ops import QuantOpsMixin
from quant.services.quant_service_replay import QuantReplayMixin


class QuantService(
    QuantConfigMixin,
    QuantFollowMixin,
    QuantReplayMixin,
    QuantCompareMixin,
    QuantFactorMixin,
    QuantOpsMixin,
):
    """稳定门面：方法名与 patch 路径保持 ``quant.services.quant_service.QuantService.*``。

    产品动作归属见 ``quant.services.action_map`` / ``GET /api/quant/actions``：

    - ① 模拟：``QuantFollowMixin`` + ``PaperService``
    - ② 回溯：``QuantReplayMixin`` + watching / cross-section
    - ③ 联动：``QuantCompareMixin``
    - 进阶：config / factors(IC·OLS) / ops(日报·导出)
    """

    def action_map(self):
        from quant.services.action_map import action_map

        return action_map()

    def run_research_task(self, head: str, **params):
        """按注册表分发研究头。记录步骤在各 run_*_experiment 返回前执行。"""
        from core.research.task import dispatch_research_task

        return dispatch_research_task(self, head, **params)
