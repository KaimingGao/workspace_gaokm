"""Compat shim — prefer ``core.platform.job_progress``.

Re-export existing public names; ensure newer slots exist even if an older
in-memory ``platform.job_progress`` lacked them (branch switch without restart).
"""

from core.platform import job_progress as _jp

globals().update(
    {name: getattr(_jp, name) for name in dir(_jp) if not name.startswith("_")}
)

_reg = globals()["job_registry"]
for _attr, _slot in (
    ("oo_lgb_job", "oo-lgb"),
    ("oo_rank_job", "oo-rank"),
    ("tc_rank_job", "tc-rank"),
    ("roll_gate_job", "roll-gate"),
):
    if globals().get(_attr) is None:
        globals()[_attr] = _reg.slot(_slot)

del _jp, _reg, _attr, _slot
