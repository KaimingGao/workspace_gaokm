"""兼容 shim：实现位于 ``adapters.sentiment.spot_breadth``（同模块对象，便于 mock）。"""
from __future__ import annotations

import importlib
import sys

_impl = importlib.import_module("adapters.sentiment.spot_breadth")
sys.modules[__name__] = _impl
_parent_name, _, _leaf = __name__.rpartition(".")
if _parent_name and _leaf:
    _parent = sys.modules.get(_parent_name)
    if _parent is not None:
        setattr(_parent, _leaf, _impl)
