"""将 PaperService 注入 ``core.ports.paper_cycle``（打破 core→services 硬依赖）。"""

from __future__ import annotations


def bind_paper_cycle(*, force: bool = False) -> None:
    from core.paths import PAPER_PATH
    from core.ports.paper_cycle import is_paper_cycle_bound, set_paper_cycle_factory
    from services.paper_service import PaperService

    if not force and is_paper_cycle_bound():
        return

    path = PAPER_PATH

    def _factory() -> PaperService:
        return PaperService(path)

    set_paper_cycle_factory(_factory)
