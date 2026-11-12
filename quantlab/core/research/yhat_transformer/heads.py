"""四头 ŷ_* HeadSpec：公共 yhat_transformer 的唯一分叉点。

一次 run 只服务一个 head；不写生产 score。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class HeadSpec:
    id: str
    label: str
    task_type: str  # regression | rank
    target_desc: str
    factor_pack: str
    seq_label: str
    group_key: Optional[str]
    loss: str
    gate: str
    arm0: str
    status: str  # holdout_ready | in_progress | planned
    default_cli: Optional[str]
    role: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_HEADS: Dict[str, HeadSpec] = {
    "oo": HeadSpec(
        id="oo",
        label="ŷ_oo",
        task_type="regression",
        target_desc="open[T]→open[T+1]",
        factor_pack="aoo_core",
        seq_label="y_oo",
        group_key=None,
        loss="smooth_l1",
        gate="cs_rank_ic",
        arm0="lgb_oo",
        status="holdout_ready",
        default_cli=(
            "scripts/run_yhat_transformer_holdout.py --head oo --arms 0,A,C"
        ),
        role="fuse 左端回归头",
    ),
    "tc": HeadSpec(
        id="tc",
        label="ŷ_τc",
        task_type="regression",
        target_desc="price[τ]→close[T]",
        factor_pack="atc_core",
        seq_label="y_tc",
        group_key=None,
        loss="smooth_l1",
        gate="cs_rank_ic",
        arm0="lgb_tc",
        status="holdout_ready",
        default_cli=(
            "scripts/run_yhat_transformer_holdout.py --head tc --arms 0,A,C "
            "--tau-end 09:30"
        ),
        role="fuse 右端回归头（本期 τ=开盘 close/open）",
    ),
    "oo_rank": HeadSpec(
        id="oo_rank",
        label="ŷ_oo_rank",
        task_type="rank",
        target_desc="rank(open[T]→open[T+1])",
        factor_pack="aoo_core",
        seq_label="y_oo_rank",
        group_key="date",
        loss="listwise",
        gate="gate_lift",
        arm0="lgb_oo_rank",
        status="holdout_ready",
        default_cli=(
            "scripts/run_yhat_transformer_holdout.py --head oo_rank --arms 0,C"
        ),
        role="rank_score 左端排序头",
    ),
    "tc_rank": HeadSpec(
        id="tc_rank",
        label="ŷ_τc_rank",
        task_type="rank",
        target_desc="rank(price[τ]→close[T])",
        factor_pack="atc_core",
        seq_label="y_tc_rank",
        group_key="date_tau",
        loss="listwise",
        gate="gate_lift",
        arm0="lgb_tc_rank",
        status="holdout_ready",
        default_cli=(
            "scripts/run_yhat_transformer_holdout.py --head tc_rank --arms 0,C "
            "--tau-end 09:30"
        ),
        role="rank_score 右端排序头",
    ),
}

_HEAD_ORDER: List[str] = ["oo", "tc", "oo_rank", "tc_rank"]


def get_head(head_id: str) -> HeadSpec:
    h = str(head_id or "").strip()
    if h not in _HEADS:
        raise ValueError(f"unknown head: {head_id!r}; expect {_HEAD_ORDER}")
    return _HEADS[h]


def iter_heads() -> Iterable[HeadSpec]:
    for hid in _HEAD_ORDER:
        yield _HEADS[hid]


def head_ids() -> List[str]:
    return list(_HEAD_ORDER)


def assert_trainable(spec: HeadSpec) -> None:
    if spec.status == "planned":
        raise NotImplementedError(f"head={spec.id} status=planned")
    if spec.task_type not in ("regression", "rank"):
        raise NotImplementedError(f"head={spec.id} task_type={spec.task_type!r}")
    if spec.task_type == "rank" and spec.loss != "listwise":
        raise NotImplementedError(
            f"head={spec.id} rank loss={spec.loss!r}；期望 listwise"
        )


__all__ = [
    "HeadSpec",
    "get_head",
    "iter_heads",
    "head_ids",
    "assert_trainable",
]
