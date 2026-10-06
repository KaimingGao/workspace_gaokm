"""因子表达式 DSL（吸收 Qlib 表达式引擎思想）。

用类 Qlib 语法手写因子公式。研究枢纽对单票求时序值，并在观察池上算截面 Rank IC。

语法示例::

    ($close - Ref($close, 5)) / Ref($close, 5)   # 5 日收益率
    Mean($close, 5) / $close                       # MA5 / 收盘
    Ts_Rank($volume, 20)                           # 成交量 20 日时序排名
    Corr($close, $volume, 10)                      # 价量 10 日相关
    CS_Rank(ROC($close, 5))                        # 横截面排名（需横截面模式）

字段（对齐 Qlib）::

    $close $open $high $low $volume $vwap

时序函数::

    Ref(x, n)        x 的 n 天前值
    Mean(x,n)/MA     n 日均值
    Std(x,n)         n 日标准差（总体，ddof=0）
    Var(x,n)         n 日方差
    Max(x,n)/Ts_Max  n 日最大值
    Min(x,n)/Ts_Min  n 日最小值
    Delta(x,n)       x - Ref(x,n)
    ROC(x,n)         x/Ref(x,n) - 1
    Ts_Rank(x,n)     x 在最近 n 天中的时序排名（0~1）
    RSV(x,n)         (x-Min)/(Max-Min) 随机指标
    Corr(x,y,n)      n 日相关系数
    Cov(x,y,n)       n 日协方差

横截面函数（单票求值返回 NaN；横截面模式下计算）::

    CS_Rank(x)       横截面排名（0~1）
    CS_ZScore(x)     横截面 z-score

时序求值按算子向量化，窗口只扫一遍。``cross_section_rank_ic`` 在多票上
计算日度 Spearman，再取平均，供研究枢纽判断因子是否有截面区分度。

本模块纯 numpy 实现，无第三方依赖。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

_EPS = 1e-12

# bars 中支持的字段
_FIELD_KEYS = ("close", "open", "high", "low", "volume", "vwap")

# --------------------------------------------------------------------------- #
# 1. 词法分析
# --------------------------------------------------------------------------- #
_TT_FIELD = "FIELD"   # $close
_TT_IDENT = "IDENT"   # Ref, Mean
_TT_NUM = "NUM"       # 123 / 1.5
_TT_OP = "OP"         # + - * / ( ) ,


def _tokenize(src: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c == "$":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            tokens.append((_TT_FIELD, src[i + 1 : j].lower()))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            tokens.append((_TT_IDENT, src[i:j]))
            i = j
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            has_dot = False
            while j < n and (src[j].isdigit() or (src[j] == "." and not has_dot)):
                if src[j] == ".":
                    has_dot = True
                j += 1
            tokens.append((_TT_NUM, src[i:j]))
            i = j
            continue
        if c in "+-*/(),":
            tokens.append((_TT_OP, c))
            i += 1
            continue
        raise ValueError(f"表达式含非法字符: {c!r} (位置 {i})")
    return tokens

# --------------------------------------------------------------------------- #
# 2. 语法分析（递归下降）
# --------------------------------------------------------------------------- #

class _Node:
    pass


class _Num(_Node):
    def __init__(self, v: float):
        self.v = v


class _Field(_Node):
    def __init__(self, name: str):
        self.name = name


class _BinOp(_Node):
    def __init__(self, op: str, left: _Node, right: _Node):
        self.op = op
        self.left = left
        self.right = right


class _UnaryMinus(_Node):
    def __init__(self, operand: _Node):
        self.operand = operand


class _Call(_Node):
    def __init__(self, name: str, args: List[_Node]):
        self.name = name.upper()
        self.args = args


class _Parser:
    def __init__(self, tokens: List[Tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> Optional[Tuple[str, str]]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> Tuple[str, str]:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def parse(self) -> _Node:
        node = self._expr()
        if self.pos != len(self.tokens):
            raise ValueError(f"表达式末尾有多余 token: {self.tokens[self.pos:]}")
        return node

    def _expr(self) -> _Node:
        left = self._term()
        while True:
            t = self._peek()
            if t and t[0] == _TT_OP and t[1] in ("+", "-"):
                self._next()
                right = self._term()
                left = _BinOp(t[1], left, right)
            else:
                break
        return left

    def _term(self) -> _Node:
        left = self._factor()
        while True:
            t = self._peek()
            if t and t[0] == _TT_OP and t[1] in ("*", "/"):
                self._next()
                right = self._factor()
                left = _BinOp(t[1], left, right)
            else:
                break
        return left

    def _factor(self) -> _Node:
        t = self._peek()
        if t is None:
            raise ValueError("表达式意外结束")
        if t[0] == _TT_OP and t[1] == "-":
            self._next()
            return _UnaryMinus(self._factor())
        if t[0] == _TT_OP and t[1] == "(":
            self._next()
            node = self._expr()
            close = self._peek()
            if not close or close[0] != _TT_OP or close[1] != ")":
                raise ValueError("缺少右括号")
            self._next()
            return node
        if t[0] == _TT_NUM:
            self._next()
            return _Num(float(t[1]))
        if t[0] == _TT_FIELD:
            self._next()
            return _Field(t[1])
        if t[0] == _TT_IDENT:
            name = t[1]
            self._next()
            lp = self._peek()
            if not lp or lp[0] != _TT_OP or lp[1] != "(":
                raise ValueError(f"函数 {name} 后缺少左括号")
            self._next()
            args: List[_Node] = []
            if not (self._peek() and self._peek()[0] == _TT_OP and self._peek()[1] == ")"):
                args.append(self._expr())
                while self._peek() and self._peek()[0] == _TT_OP and self._peek()[1] == ",":
                    self._next()
                    args.append(self._expr())
            rp = self._peek()
            if not rp or rp[0] != _TT_OP or rp[1] != ")":
                raise ValueError(f"函数 {name} 缺少右括号")
            self._next()
            return _Call(name, args)
        raise ValueError(f"意外 token: {t}")


def parse_expr(src: str) -> _Node:
    tokens = _tokenize(src)
    if not tokens:
        raise ValueError("空表达式")
    return _Parser(tokens).parse()

# --------------------------------------------------------------------------- #
# 3. 求值
# --------------------------------------------------------------------------- #

class _BarsCtx:
    """单票 bars 上下文：把 bars 转成各字段的 numpy 数组。

    缺字段的单根记 NaN，不把整段序列废掉。``$vwap`` 缺失时用 (高+低+收)/3。
    """

    def __init__(self, bars: Sequence[dict]):
        self.bars = [b for b in bars if isinstance(b, dict)]
        self.n = len(self.bars)
        self._arrays: Dict[str, np.ndarray] = {}
        for key in _FIELD_KEYS:
            arr = np.full(self.n, np.nan, dtype=np.float64)
            for i, b in enumerate(self.bars):
                v = b.get(key)
                if v is None or v == "":
                    continue
                try:
                    arr[i] = float(v)
                except (TypeError, ValueError):
                    continue
            self._arrays[key] = arr

    def field(self, name: str) -> np.ndarray:
        key = name.lower()
        if key == "vwap":
            vw = self._arrays.get("vwap")
            typical = (
                self._arrays.get("high", np.full(self.n, np.nan))
                + self._arrays.get("low", np.full(self.n, np.nan))
                + self._arrays.get("close", np.full(self.n, np.nan))
            ) / 3.0
            if vw is None or not np.any(np.isfinite(vw)):
                return typical
            if np.any(~np.isfinite(vw)):
                out = vw.copy()
                miss = ~np.isfinite(out)
                out[miss] = typical[miss]
                return out
            return vw
        return self._arrays.get(key, np.full(self.n, np.nan))


def _eval(node: _Node, ctx: _BarsCtx, pos: int) -> float:
    if isinstance(node, _Num):
        return node.v
    if isinstance(node, _Field):
        arr = ctx.field(node.name)
        if pos < 0 or pos >= len(arr):
            return math.nan
        return float(arr[pos])
    if isinstance(node, _UnaryMinus):
        return -_eval(node.operand, ctx, pos)
    if isinstance(node, _BinOp):
        lv = _eval(node.left, ctx, pos)
        rv = _eval(node.right, ctx, pos)
        if math.isnan(lv) or math.isnan(rv):
            return math.nan
        if node.op == "+":
            return lv + rv
        if node.op == "-":
            return lv - rv
        if node.op == "*":
            return lv * rv
        if node.op == "/":
            return lv / rv if abs(rv) > _EPS else math.nan
    if isinstance(node, _Call):
        return _eval_call(node, ctx, pos)
    raise ValueError(f"未知节点类型: {type(node)}")


def _window(node: _Node, ctx: _BarsCtx, pos: int, n: int) -> Optional[np.ndarray]:
    if n <= 0 or pos - n + 1 < 0:
        return None
    out = np.empty(n, dtype=np.float64)
    for k in range(n):
        out[k] = _eval(node, ctx, pos - n + 1 + k)
    if np.any(np.isnan(out)):
        return None
    return out


def _eval_call(node: _Call, ctx: _BarsCtx, pos: int) -> float:
    name = node.name
    args = node.args

    if name in ("CS_RANK", "CS_ZSCORE", "RANK"):
        return math.nan

    if name == "REF":
        x, n = args[0], int(_eval(args[1], ctx, pos))
        return _eval(x, ctx, pos - n)

    if name in ("MEAN", "MA", "AVG"):
        x, n = args[0], int(_eval(args[1], ctx, pos))
        w = _window(x, ctx, pos, n)
        return float(np.mean(w)) if w is not None else math.nan

    if name == "STD":
        x, n = args[0], int(_eval(args[1], ctx, pos))
        w = _window(x, ctx, pos, n)
        return float(np.std(w)) if w is not None else math.nan

    if name == "VAR":
        x, n = args[0], int(_eval(args[1], ctx, pos))
        w = _window(x, ctx, pos, n)
        return float(np.var(w)) if w is not None else math.nan

    if name in ("MAX", "TS_MAX"):
        x, n = args[0], int(_eval(args[1], ctx, pos))
        w = _window(x, ctx, pos, n)
        return float(np.max(w)) if w is not None else math.nan

    if name in ("MIN", "TS_MIN"):
        x, n = args[0], int(_eval(args[1], ctx, pos))
        w = _window(x, ctx, pos, n)
        return float(np.min(w)) if w is not None else math.nan

    if name == "DELTA":
        x, n = args[0], int(_eval(args[1], ctx, pos))
        cur = _eval(x, ctx, pos)
        prev = _eval(x, ctx, pos - n)
        return cur - prev if not (math.isnan(cur) or math.isnan(prev)) else math.nan

    if name == "ROC":
        x, n = args[0], int(_eval(args[1], ctx, pos))
        cur = _eval(x, ctx, pos)
        prev = _eval(x, ctx, pos - n)
        if math.isnan(cur) or math.isnan(prev) or abs(prev) < _EPS:
            return math.nan
        return cur / prev - 1.0

    if name == "TS_RANK":
        x, n = args[0], int(_eval(args[1], ctx, pos))
        w = _window(x, ctx, pos, n)
        if w is None:
            return math.nan
        cur = w[-1]
        less = np.sum(w < cur)
        equal = np.sum(w == cur)
        return float((less + equal / 2.0) / n)

    if name == "RSV":
        x, n = args[0], int(_eval(args[1], ctx, pos))
        w = _window(x, ctx, pos, n)
        if w is None:
            return math.nan
        lo, hi = float(np.min(w)), float(np.max(w))
        if hi - lo < _EPS:
            return 0.5
        return float((w[-1] - lo) / (hi - lo))

    if name in ("CORR", "CORRELATION"):
        x, y, n = args[0], args[1], int(_eval(args[2], ctx, pos))
        wx = _window(x, ctx, pos, n)
        wy = _window(y, ctx, pos, n)
        if wx is None or wy is None:
            return math.nan
        sx, sy = np.std(wx), np.std(wy)
        if sx < _EPS or sy < _EPS:
            return math.nan
        return float(np.corrcoef(wx, wy)[0, 1])

    if name == "COV":
        x, y, n = args[0], args[1], int(_eval(args[2], ctx, pos))
        wx = _window(x, ctx, pos, n)
        wy = _window(y, ctx, pos, n)
        if wx is None or wy is None:
            return math.nan
        return float(np.cov(wx, wy)[0, 1])

    raise ValueError(f"未知函数: {name}")

# --------------------------------------------------------------------------- #
# 4. 向量化求值（窗口常数时一次扫完；非常数窗口回退逐点）
# --------------------------------------------------------------------------- #
_CS_FUNCS = ("CS_RANK", "CS_ZSCORE", "RANK")

_VEC_FUNCS = _CS_FUNCS + (
    "REF", "MEAN", "MA", "AVG", "STD", "VAR", "MAX", "TS_MAX", "MIN", "TS_MIN",
    "DELTA", "ROC", "TS_RANK", "RSV", "CORR", "CORRELATION", "COV",
)


def _window_n(node: _Node, ctx: "_BarsCtx") -> Optional[int]:
    """窗口长度。非常数（各 bar 不一致）时返回 None，调用方回退逐点求值。"""
    if isinstance(node, _Num) and math.isfinite(node.v):
        return int(node.v)
    if isinstance(node, _UnaryMinus) and isinstance(node.operand, _Num) and math.isfinite(node.operand.v):
        return int(-node.operand.v)
    arr = _eval_vec(node, ctx)
    fin = arr[np.isfinite(arr)]
    if fin.size == 0:
        return None
    iv = int(fin[0])
    if np.all(np.abs(fin - float(iv)) < 1e-8):
        return iv
    return None


def _scalar_series(node: _Node, ctx: "_BarsCtx") -> np.ndarray:
    out = np.empty(ctx.n, dtype=np.float64)
    for i in range(ctx.n):
        out[i] = _eval(node, ctx, i)
    return out


def _binop_vec(op: str, lv: np.ndarray, rv: np.ndarray) -> np.ndarray:
    lv = np.asarray(lv, dtype=np.float64)
    rv = np.asarray(rv, dtype=np.float64)
    if op == "+":
        out = lv + rv
    elif op == "-":
        out = lv - rv
    elif op == "*":
        out = lv * rv
    elif op == "/":
        out = np.full(lv.shape, np.nan, dtype=np.float64)
        np.divide(lv, rv, out=out, where=np.abs(rv) > _EPS)
    else:
        raise ValueError(f"未知运算符: {op}")
    out = np.array(out, dtype=np.float64, copy=True)
    out[np.isnan(lv) | np.isnan(rv)] = np.nan
    return out


def _roll(arr: np.ndarray, n: int) -> Tuple[np.ndarray, np.ndarray]:
    """完整窗口。返回 (windows, 窗口末端下标)。n 非法或不够长时为空。"""
    base = np.ascontiguousarray(arr, dtype=np.float64)
    n_bars = int(base.shape[0])
    if n <= 0 or n_bars < n:
        return np.zeros((0, max(n, 0)), dtype=np.float64), np.zeros(0, dtype=np.int64)
    windows = np.lib.stride_tricks.sliding_window_view(base, n)
    idx = np.arange(n - 1, n_bars, dtype=np.int64)
    return windows, idx


def _ref_series(node: _Node, n: int, ctx: "_BarsCtx") -> np.ndarray:
    """对齐标量 Ref：区间内平移，越界的少数点逐点求值。"""
    src = _eval_vec(node, ctx)
    n_bars = ctx.n
    if n == 0 or n_bars == 0:
        return src
    out = np.full(n_bars, np.nan, dtype=np.float64)
    if n > 0:
        if n < n_bars:
            out[n:] = src[:-n]
        for i in range(min(n, n_bars)):
            out[i] = _eval(node, ctx, i - n)
        return out
    k = -n
    if k < n_bars:
        out[:-k] = src[k:]
        for i in range(n_bars - k, n_bars):
            out[i] = _eval(node, ctx, i + k)
        return out
    for i in range(n_bars):
        out[i] = _eval(node, ctx, i + k)
    return out


def _reduce_roll(arr: np.ndarray, n: int, fn) -> np.ndarray:
    out = np.full(arr.shape[0], np.nan, dtype=np.float64)
    windows, idx = _roll(arr, n)
    if idx.size == 0:
        return out
    bad = np.any(~np.isfinite(windows), axis=1)
    vals = np.asarray(fn(windows), dtype=np.float64)
    vals[bad] = np.nan
    out[idx] = vals
    return out


def _eval_call_vec(node: "_Call", ctx: "_BarsCtx") -> np.ndarray:
    name = node.name
    args = node.args
    if name not in _VEC_FUNCS:
        raise ValueError(f"未知函数: {name}")
    if name in _CS_FUNCS:
        return np.full(ctx.n, np.nan, dtype=np.float64)

    if name == "REF":
        n = _window_n(args[1], ctx)
        if n is None:
            return _scalar_series(node, ctx)
        return _ref_series(args[0], n, ctx)

    if name in ("MEAN", "MA", "AVG", "STD", "VAR", "MAX", "TS_MAX", "MIN", "TS_MIN", "TS_RANK", "RSV"):
        n = _window_n(args[1], ctx)
        if n is None:
            return _scalar_series(node, ctx)
        src = _eval_vec(args[0], ctx)
        if name in ("MEAN", "MA", "AVG"):
            return _reduce_roll(src, n, lambda w: np.mean(w, axis=1))
        if name == "STD":
            return _reduce_roll(src, n, lambda w: np.std(w, axis=1))
        if name == "VAR":
            return _reduce_roll(src, n, lambda w: np.var(w, axis=1))
        if name in ("MAX", "TS_MAX"):
            return _reduce_roll(src, n, lambda w: np.max(w, axis=1))
        if name in ("MIN", "TS_MIN"):
            return _reduce_roll(src, n, lambda w: np.min(w, axis=1))
        if name == "TS_RANK":
            def _ts_rank(w: np.ndarray) -> np.ndarray:
                cur = w[:, -1:]
                less = np.sum(w < cur, axis=1)
                equal = np.sum(w == cur, axis=1)
                return (less + equal / 2.0) / n
            return _reduce_roll(src, n, _ts_rank)

        def _rsv(w: np.ndarray) -> np.ndarray:
            lo = np.min(w, axis=1)
            hi = np.max(w, axis=1)
            span = hi - lo
            val = np.full(w.shape[0], 0.5, dtype=np.float64)
            ok = span >= _EPS
            val[ok] = (w[ok, -1] - lo[ok]) / span[ok]
            return val
        return _reduce_roll(src, n, _rsv)

    if name in ("DELTA", "ROC"):
        n = _window_n(args[1], ctx)
        if n is None:
            return _scalar_series(node, ctx)
        cur = _eval_vec(args[0], ctx)
        prev = _ref_series(args[0], n, ctx)
        if name == "DELTA":
            out = cur - prev
            out[np.isnan(cur) | np.isnan(prev)] = np.nan
            return out
        out = np.full(ctx.n, np.nan, dtype=np.float64)
        ok = np.isfinite(cur) & np.isfinite(prev) & (np.abs(prev) > _EPS)
        out[ok] = cur[ok] / prev[ok] - 1.0
        return out

    if name in ("CORR", "CORRELATION", "COV"):
        n = _window_n(args[2], ctx)
        if n is None:
            return _scalar_series(node, ctx)
        wx, idx = _roll(_eval_vec(args[0], ctx), n)
        wy, _idx = _roll(_eval_vec(args[1], ctx), n)
        out = np.full(ctx.n, np.nan, dtype=np.float64)
        if idx.size == 0:
            return out
        bad = np.any(~np.isfinite(wx), axis=1) | np.any(~np.isfinite(wy), axis=1)
        if name == "COV":
            if n < 2:
                return out
            mx = np.mean(wx, axis=1, keepdims=True)
            my = np.mean(wy, axis=1, keepdims=True)
            vals = np.sum((wx - mx) * (wy - my), axis=1) / (n - 1)
        else:
            mx = np.mean(wx, axis=1, keepdims=True)
            my = np.mean(wy, axis=1, keepdims=True)
            cov = np.mean((wx - mx) * (wy - my), axis=1)
            sx = np.std(wx, axis=1)
            sy = np.std(wy, axis=1)
            vals = np.full(idx.shape[0], np.nan, dtype=np.float64)
            ok = (sx >= _EPS) & (sy >= _EPS)
            vals[ok] = cov[ok] / (sx[ok] * sy[ok])
        vals = np.asarray(vals, dtype=np.float64)
        vals[bad] = np.nan
        out[idx] = vals
        return out

    return _scalar_series(node, ctx)


def _eval_vec(node: _Node, ctx: "_BarsCtx") -> np.ndarray:
    if ctx.n == 0:
        return np.zeros(0, dtype=np.float64)
    if isinstance(node, _Num):
        return np.full(ctx.n, node.v, dtype=np.float64)
    if isinstance(node, _Field):
        return np.array(ctx.field(node.name), dtype=np.float64, copy=True)
    if isinstance(node, _UnaryMinus):
        return -_eval_vec(node.operand, ctx)
    if isinstance(node, _BinOp):
        return _binop_vec(node.op, _eval_vec(node.left, ctx), _eval_vec(node.right, ctx))
    if isinstance(node, _Call):
        return _eval_call_vec(node, ctx)
    raise ValueError(f"未知节点类型: {type(node)}")


def _average_ranks(vals: np.ndarray) -> np.ndarray:
    order = np.argsort(vals, kind="mergesort")
    ranks = np.empty(len(vals), dtype=np.float64)
    ranks[order] = np.arange(1, len(vals) + 1, dtype=np.float64)
    sorted_vals = vals[order]
    i = 0
    n = len(sorted_vals)
    while i < n:
        j = i
        while j + 1 < n and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = float(np.mean(ranks[order[i:j + 1]]))
        i = j + 1
    return ranks


def _cs_apply(name: str, values: Dict[str, float]) -> Dict[str, float]:
    codes = list(values.keys())
    vals = np.array([values[c] for c in codes], dtype=np.float64)
    valid_mask = ~np.isnan(vals)
    if int(valid_mask.sum()) < 2:
        return {c: math.nan for c in codes}
    valid_vals = vals[valid_mask]
    if name in ("CS_RANK", "RANK"):
        cs_vals = _average_ranks(valid_vals) / len(valid_vals)
    else:
        mu = float(np.mean(valid_vals))
        sd = float(np.std(valid_vals))
        cs_vals = (valid_vals - mu) / sd if sd > _EPS else np.zeros_like(valid_vals)
    out: Dict[str, float] = {}
    idx = 0
    for code, v in zip(codes, vals):
        if math.isnan(float(v)):
            out[code] = math.nan
        else:
            out[code] = float(cs_vals[idx])
            idx += 1
    return out


def _bar_date(bar: dict, _i: int) -> Optional[str]:
    raw = str((bar or {}).get("date") or (bar or {}).get("trade_date") or "")[:10]
    return raw or None

# --------------------------------------------------------------------------- #
# 5. 对外接口
# --------------------------------------------------------------------------- #

def eval_expr_series(expr: str, bars: Sequence[dict]) -> np.ndarray:
    """求值表达式在每根 bar 上的值，返回与 bars 等长的 ndarray。"""
    ctx = _BarsCtx(bars)
    if ctx.n == 0:
        return np.zeros(0, dtype=np.float64)
    return _eval_vec(parse_expr(expr), ctx)


def eval_expr(expr: str, bars: Sequence[dict], pos: Optional[int] = None) -> float:
    """在单票 bars 上求值表达式，返回最后一根（或指定 pos）的标量值。"""
    series = eval_expr_series(expr, bars)
    if series.size == 0:
        return math.nan
    p = series.size - 1 if pos is None else pos
    if p < 0 or p >= series.size:
        return math.nan
    return float(series[p])


def eval_expr_cross_section(
    expr: str,
    bars_by_code: Dict[str, Sequence[dict]],
    pos: Optional[int] = None,
) -> Dict[str, float]:
    """横截面求值。

    支持两种形式：
    - 最外层为 CS_Rank(...) / CS_ZScore(...)：先算内部时序值，再横截面变换
    - 纯时序表达式：每票独立 eval_expr
    """
    if not bars_by_code:
        return {}

    ast = parse_expr(expr)
    if isinstance(ast, _Call) and ast.name in _CS_FUNCS:
        values: Dict[str, float] = {}
        for code, bars in bars_by_code.items():
            ctx = _BarsCtx(bars)
            p = (ctx.n - 1) if pos is None else pos
            if p < 0 or p >= ctx.n:
                values[code] = math.nan
                continue
            values[code] = float(_eval_vec(ast.args[0], ctx)[p])
        return _cs_apply(ast.name, values)

    return {code: eval_expr(expr, bars, pos=pos) for code, bars in bars_by_code.items()}


def cross_section_rank_ic(
    expr: str,
    bars_by_code: Dict[str, Sequence[dict]],
    horizon_days: int = 5,
    *,
    min_names: int = 5,
) -> Dict[str, Any]:
    """观察池截面 Rank IC。

    每个交易日，在当日有因子值和前瞻收益的股票上算 Spearman，再对交易日取平均。
    最外层 ``CS_Rank`` / ``CS_ZScore`` 会先在当日截面上变换，再和前瞻收益做相关。
    """
    empty: Dict[str, Any] = {
        "cs_rank_ic": None,
        "cs_ir": None,
        "cs_days": 0,
        "cs_names": 0,
        "cs_positive_rate": None,
        "cs_ic_path": [],
    }
    if not bars_by_code or horizon_days < 1:
        return empty

    ast = parse_expr(expr)
    # code -> date -> factor
    panel: Dict[str, Dict[str, float]] = {}
    ordered: Dict[str, List[Tuple[str, float]]] = {}
    if isinstance(ast, _Call) and ast.name in _CS_FUNCS:
        raw: Dict[str, List[Tuple[str, float]]] = {}
        for code, bars in bars_by_code.items():
            ctx = _BarsCtx(bars)
            if ctx.n == 0:
                continue
            vals = _eval_vec(ast.args[0], ctx)
            rows: List[Tuple[str, float]] = []
            for i, bar in enumerate(ctx.bars):
                d = _bar_date(bar, i)
                if not d:
                    continue
                rows.append((d, float(vals[i])))
            raw[code] = rows
        by_date: Dict[str, Dict[str, float]] = {}
        for code, rows in raw.items():
            for d, v in rows:
                by_date.setdefault(d, {})[code] = v
        panel = {code: {} for code in raw}
        for d, pairs in by_date.items():
            for code, v in _cs_apply(ast.name, pairs).items():
                panel[code][d] = v
        for code, rows in raw.items():
            ordered[code] = [(d, panel[code].get(d, math.nan)) for d, _v in rows]
    else:
        for code, bars in bars_by_code.items():
            ctx = _BarsCtx(bars)
            if ctx.n == 0:
                continue
            vals = _eval_vec(ast, ctx)
            rows = []
            fmap: Dict[str, float] = {}
            for i, bar in enumerate(ctx.bars):
                d = _bar_date(bar, i)
                if not d:
                    continue
                fv = float(vals[i])
                rows.append((d, fv))
                fmap[d] = fv
            ordered[code] = rows
            panel[code] = fmap

    samples: Dict[str, List[Tuple[float, float]]] = {}
    for code, bars in bars_by_code.items():
        rows = ordered.get(code) or []
        if not rows:
            continue
        ctx = _BarsCtx([b for b in bars if isinstance(b, dict)])
        # 前瞻收益按该票自己的交易日序，键用信号日
        dated: List[Tuple[str, float]] = []
        for i, bar in enumerate(ctx.bars):
            d = _bar_date(bar, i)
            if not d:
                continue
            close = bar.get("close")
            try:
                c = float(close) if close is not None else math.nan
            except (TypeError, ValueError):
                c = math.nan
            dated.append((d, c))
        fmap = {d: v for d, v in rows}
        for i in range(len(dated) - horizon_days):
            d, c0 = dated[i]
            c1 = dated[i + horizon_days][1]
            fv = fmap.get(d, math.nan)
            if not (math.isfinite(fv) and math.isfinite(c0) and math.isfinite(c1)) or abs(c0) <= _EPS:
                continue
            samples.setdefault(d, []).append((fv, c1 / c0 - 1.0))

    ics: List[float] = []
    ic_dates: List[str] = []
    name_counts: List[int] = []
    for d in sorted(samples):
        pairs = samples[d]
        if len(pairs) < min_names:
            continue
        xs = np.array([p[0] for p in pairs], dtype=np.float64)
        ys = np.array([p[1] for p in pairs], dtype=np.float64)
        if float(np.std(xs)) <= _EPS or float(np.std(ys)) <= _EPS:
            continue
        rx = _average_ranks(xs)
        ry = _average_ranks(ys)
        ic = float(np.corrcoef(rx, ry)[0, 1])
        if not math.isfinite(ic):
            continue
        ics.append(ic)
        ic_dates.append(d)
        name_counts.append(len(pairs))

    if not ics:
        return empty
    arr = np.array(ics, dtype=np.float64)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0
    ir = mean / std if std > _EPS else None
    return {
        "cs_rank_ic": mean,
        "cs_ir": ir,
        "cs_days": int(arr.size),
        "cs_names": int(max(name_counts) if name_counts else 0),
        "cs_positive_rate": float(np.mean(arr > 0)),
        "cs_ic_path": [
            {"date": d, "value": float(v)} for d, v in zip(ic_dates, ics)
        ],
    }

# --------------------------------------------------------------------------- #
# 6. 因子元信息（语法说明）
# --------------------------------------------------------------------------- #
DSL_FIELDS = ["$close", "$open", "$high", "$low", "$volume", "$vwap"]

DSL_FUNCTIONS = [
    ("Ref(x, n)", "n 天前的值"),
    ("Mean(x, n) / MA(x, n)", "n 日均值"),
    ("Std(x, n)", "n 日标准差"),
    ("Var(x, n)", "n 日方差"),
    ("Max(x, n) / Ts_Max(x, n)", "n 日最大值"),
    ("Min(x, n) / Ts_Min(x, n)", "n 日最小值"),
    ("Delta(x, n)", "x - Ref(x, n)"),
    ("ROC(x, n)", "x / Ref(x, n) - 1"),
    ("Ts_Rank(x, n)", "x 在最近 n 天的时序排名(0~1)"),
    ("RSV(x, n)", "(x-Min)/(Max-Min)"),
    ("Corr(x, y, n)", "n 日相关系数"),
    ("Cov(x, y, n)", "n 日协方差"),
    ("CS_Rank(x)", "横截面排名(0~1)，需多票"),
    ("CS_ZScore(x)", "横截面 z-score，需多票"),
]

DSL_EXAMPLES = [
    ("ROC($close, 5)", "5 日收益率"),
    ("Mean($close, 5) / $close", "MA5 相对收盘"),
    ("Ts_Rank($volume, 20)", "成交量 20 日时序排名"),
    ("Corr($close, $volume, 10)", "价量 10 日相关"),
    ("($high - $low) / $close", "当日振幅"),
    ("RSV($close, 9)", "9 日 RSV"),
    ("Delta($close, 1) / Ref($close, 1)", "单日涨跌幅"),
]


def dsl_cheatsheet() -> str:
    """返回 DSL 语法速查。"""
    lines = ["# 因子表达式 DSL 语法", ""]
    lines.append("字段: " + ", ".join(DSL_FIELDS))
    lines.append("")
    lines.append("时序函数:")
    for sig, desc in DSL_FUNCTIONS:
        lines.append(f"  {sig}  — {desc}")
    lines.append("")
    lines.append("运算符: + - * / ( )")
    lines.append("")
    lines.append("示例:")
    for ex, desc in DSL_EXAMPLES:
        lines.append(f"  {ex}  # {desc}")
    return "\n".join(lines)
