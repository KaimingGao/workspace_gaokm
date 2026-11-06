"""ST / 退市名称判定（入池与回测共用）。"""


def is_st_name(name: str) -> bool:
    """名称含 ST（含 *ST）或「退」视为不可交易。"""
    n = str(name or "").upper().replace(" ", "")
    return "ST" in n or "退" in str(name or "")
