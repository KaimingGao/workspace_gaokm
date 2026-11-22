"""四头 ŷ_* HeadSpec：公共 yhat_transformer 的唯一分叉点。

一次 run 只服务一个 head；不写生产 score。
CLI 真源在 ``yhat_tf_desk.build_holdout_cli``。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

# 训练 batch。desk 校验、训练入口、推理分块和页面 max 用同一对边界。
ARM_T_BATCH_MIN = 16
ARM_T_BATCH_MAX = 4096
# 拟合缺省。desk、CLI、训练入口和页面 value 用这一套。
ARM_T_SEQ_MIN = 5
ARM_T_SEQ_LEN = 60
ARM_T_D_MODEL = 128
ARM_T_NLAYERS = 2
ARM_T_EPOCHS = 20
ARM_T_BATCH = 2048
ARM_T_LOOKBACK = 1000
ARM_T_HOLDOUT = 60


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
    status: str  # holdout_ready | in_progress | planned
    role: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_HEADS: Dict[str, HeadSpec] = {
    "oo": HeadSpec(
        id="oo",
        label="ŷ_oo",
        task_type="regression",
        target_desc="open[T]→open[T+1]",
        factor_pack="aoo_full",
        seq_label="y_oo",
        group_key=None,
        loss="mse",
        gate="cs_rank_ic",
        status="holdout_ready",
        role="fuse 左端回归头",
    ),
    "tc": HeadSpec(
        id="tc",
        label="ŷ_τc",
        task_type="regression",
        target_desc="price[τ]→close[T]",
        factor_pack="atc_full",
        seq_label="y_tc",
        group_key=None,
        loss="mse",
        gate="cs_rank_ic",
        status="holdout_ready",
        role="fuse 右端回归头（本期 τ=开盘 close/open）",
    ),
    "oo_rank": HeadSpec(
        id="oo_rank",
        label="ŷ_oo_rank",
        task_type="rank",
        target_desc="rank(open[T]→open[T+1])",
        factor_pack="aoo_full",
        seq_label="y_oo_rank",
        group_key="date",
        loss="lambdarank",
        gate="gate_lift",
        status="holdout_ready",
        role="rank_score 左端排序头",
    ),
    "tc_rank": HeadSpec(
        id="tc_rank",
        label="ŷ_τc_rank",
        task_type="rank",
        target_desc="rank(price[τ]→close[T])",
        factor_pack="atc_full",
        seq_label="y_tc_rank",
        group_key="date_tau",
        loss="lambdarank",
        gate="gate_lift",
        status="holdout_ready",
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
    if spec.task_type == "rank" and spec.loss != "lambdarank":
        raise NotImplementedError(
            f"head={spec.id} rank loss={spec.loss!r}；期望 lambdarank"
        )


__all__ = [
    "ARM_T_BATCH_MIN",
    "ARM_T_BATCH_MAX",
    "ARM_T_SEQ_MIN",
    "ARM_T_SEQ_LEN",
    "ARM_T_D_MODEL",
    "ARM_T_NLAYERS",
    "ARM_T_EPOCHS",
    "ARM_T_BATCH",
    "ARM_T_LOOKBACK",
    "ARM_T_HOLDOUT",
    "HeadSpec",
    "get_head",
    "iter_heads",
    "head_ids",
    "assert_trainable",
]
