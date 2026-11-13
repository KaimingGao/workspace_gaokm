"""模型中心 desk：四头槽位 IO + Holdout 任务（不写生产 score）。

HeadSpec 真源在 ``yhat_transformer.heads``；CLI 真源 ``build_holdout_cli``。
"""

from __future__ import annotations

import fcntl
import functools
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from core.paths import ROOT_DIR, YHAT_TF_DIR, YHAT_TF_LAST_PATH
from core.research.yhat_transformer.heads import get_head, head_ids, iter_heads


def _ensure_dir() -> None:
    os.makedirs(YHAT_TF_DIR, exist_ok=True)


def _path(head: str, slot: str) -> str:
    return os.path.join(YHAT_TF_DIR, f"{head}_{slot}.json")


def _job_path(head: str) -> str:
    return os.path.join(YHAT_TF_DIR, f"{head}_job.json")


def _log_path(head: str) -> str:
    return os.path.join(YHAT_TF_DIR, f"{head}_fit.log")


def _log_tail(head: str, *, n_lines: int = 80) -> str:
    """读日志末尾若干行（退出原因推断用）。"""
    path = _log_path(head)
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-max(1, int(n_lines)) :]).rstrip()
    except OSError:
        return ""


def _log_full(head: str, *, max_bytes: int = 512_000) -> str:
    """本次拟合完整日志（信息窗用；过大时只留末尾，避免撑爆 status）。"""
    path = _log_path(head)
    if not path or not os.path.isfile(path):
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            if size > int(max_bytes):
                f.seek(max(0, size - int(max_bytes)))
                f.readline()  # 丢半行
            return f.read().rstrip()
    except OSError:
        return ""


def _log_for_status(head: str, *, tail_lines: int = 200) -> str:
    """状态接口用日志尾部。长跑时 Arm·L 终态可能滑出尾部，那种行单独留下。"""
    raw = _log_full(head, max_bytes=131_072)
    if not raw:
        return ""
    lines = raw.splitlines()
    if len(lines) <= int(tail_lines):
        return raw
    tail = lines[-int(tail_lines) :]
    blob = "\n".join(tail).lower()
    markers = ("arml rankic", "l_lightgbm", "l_lambdarank", "arml 失败", "arml begin")
    kept: List[str] = []
    if not any(m in blob for m in markers):
        for line in lines[: -int(tail_lines)]:
            low = line.lower()
            if any(m in low for m in markers):
                kept.append(line)
        kept = kept[-6:]
    return "\n".join([*kept, *tail]).rstrip()


def _reap_pid(pid: int) -> Optional[int]:
    """若为本进程子进程且已退出，waitpid 收割并返回 exit status；否则 None。"""
    pid = int(pid or 0)
    if pid <= 0:
        return None
    try:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return int(status)
    except ChildProcessError:
        return None
    except OSError:
        return None
    return None


_HOLDOUT_CMD_MARK = "run_yhat_transformer_holdout.py"


def _pid_command(pid: int) -> str:
    """读进程命令行；``-ww`` 避免 macOS/窄 COLUMNS 截断导致误判非 Holdout。"""
    pid = int(pid or 0)
    if pid <= 0:
        return ""
    try:
        return subprocess.check_output(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _pid_holdout_check(pid: int) -> Optional[bool]:
    """True=仍是 Holdout；False=命令行可见但已非 Holdout；None=无法判定（ps 失败）。"""
    cmd = _pid_command(pid)
    if not cmd:
        return None
    return _HOLDOUT_CMD_MARK in cmd


def _pid_alive(pid: int) -> bool:
    """本机 pid 是否仍在跑。会收割我们拉起的僵尸子进程，避免「假运行中」。"""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    if _reap_pid(pid) is not None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # 非我方子进程：用 ps 排除 Z（僵尸）
    try:
        out = subprocess.check_output(
            ["ps", "-o", "stat=", "-p", str(pid)],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out.upper().startswith("Z"):
            return False
    except (OSError, subprocess.CalledProcessError):
        pass
    return True


def _pid_alive_confirmed(pid: int, *, retries: int = 3, pause_sec: float = 0.08) -> bool:
    """瞬时 kill/ps 抖动时多探几次，避免日线静默期误杀。"""
    n = max(1, int(retries))
    for i in range(n):
        if _pid_alive(pid):
            return True
        if i + 1 < n:
            time.sleep(max(0.0, float(pause_sec)))
    return False


# 日志无新行时的宽限：load_bars / 立方 CS-z 可数分钟无输出。
# 宽限只看日志修改时间或启动瞬间，不看日志里有没有「日线 / load_bars」字样。
_FIT_LOG_STALE_SEC = 180.0
_FIT_START_GRACE_SEC = 45.0
_FIT_ALIVE_MISS_MAX = 3


def _job_started_age_sec(job: Dict[str, Any]) -> float:
    raw = str(job.get("started_at") or "").strip()
    if not raw:
        return 1e9
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            started = time.mktime(time.strptime(raw[:19], fmt))
            return max(0.0, time.time() - started)
        except ValueError:
            continue
    return 1e9


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    _ensure_dir()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _validate_head(head: str) -> str:
    return get_head(head).id


class _HeadLock:
    """同一线程可重入；其它进程用 flock 排队。路径在加锁时取，跟随测试里的目录补丁。"""

    def __init__(self) -> None:
        self._tlock = threading.RLock()
        self._fd: Optional[int] = None
        self._depth = 0

    def acquire(self, directory: str, name: str) -> None:
        self._tlock.acquire()
        try:
            if self._depth == 0:
                os.makedirs(directory, exist_ok=True)
                fd = os.open(
                    os.path.join(directory, f"{name}.lock"),
                    os.O_CREAT | os.O_RDWR,
                    0o644,
                )
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(fd)
                    raise
                self._fd = fd
            self._depth += 1
        except BaseException:
            self._tlock.release()
            raise

    def release(self) -> None:
        try:
            self._depth -= 1
            if self._depth <= 0:
                self._depth = 0
                fd = self._fd
                self._fd = None
                if fd is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)
        finally:
            self._tlock.release()


_HEAD_LOCKS: Dict[str, _HeadLock] = {}
_HEAD_LOCKS_GUARD = threading.Lock()


def _lock_for(head: str) -> _HeadLock:
    with _HEAD_LOCKS_GUARD:
        lk = _HEAD_LOCKS.get(head)
        if lk is None:
            lk = _HeadLock()
            _HEAD_LOCKS[head] = lk
        return lk


def _with_head_lock(fn):
    """串行化本头 job / last 的读改写。嵌套调用（fit → refresh）只加一次 flock。"""

    @functools.wraps(fn)
    def wrapper(head: str, *args: Any, **kwargs: Any):
        h = _validate_head(head)
        lk = _lock_for(h)
        lk.acquire(YHAT_TF_DIR, h)
        try:
            return fn(h, *args, **kwargs)
        finally:
            lk.release()

    return wrapper


def _scheme_blob() -> Dict[str, Any]:
    per_head: Dict[str, Any] = {}
    for spec in iter_heads():
        gate_label = (
            "Gate lift"
            if spec.gate == "gate_lift"
            else "Arm·L"
            if spec.gate == "cs_rank_ic"
            else spec.gate
        )
        per_head[spec.id] = {
            "status": spec.status,
            "loss": spec.loss,
            "script": build_holdout_cli(spec.id),
            "task_type": spec.task_type,
            "gate": spec.gate,
            "gate_label": gate_label,
            "factor_pack": spec.factor_pack,
            "target": spec.target_desc,
            "note": spec.role,
        }
    return {
        "name": "Scheme LT · LightGBM vs Transformer",
        "layer": "L4 research-only",
        "package": "yhat_transformer",
        "arms": [
            {"id": "L", "title": "Arm·L", "desc": "现网 LightGBM · 全量（硬闸对照）"},
            {
                "id": "T",
                "title": "Arm·T",
                "alias": "双支路晚融合",
                "desc": "双支路晚融合（序列塔 ∥ 因子支；须过 Holdout 硬闸）",
            },
        ],
        "gate": "Holdout CS rank IC / ICIR vs Arm·L；不直连生产 score",
        "promote": (
            "draft → research 仅研究槽；须 gate_pass + 正式对照协议"
            "（T full 因子 + T采样 100% + 共享评测）；禁止 live / active"
        ),
        "per_head": per_head,
    }


_BOOL_ON = frozenset({"1", "true", "yes", "on", "full"})
_BOOL_OFF = frozenset({"0", "false", "no", "off", "core"})


def _as_bool(value: Any, *, default: bool) -> bool:
    """桌面遗留字符串与布尔共用一张真值表。空值用 default。"""
    if value is None or value == "":
        return bool(default)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _BOOL_ON:
            return True
        if s in _BOOL_OFF:
            return False
        return bool(default)
    return bool(value)


# 晋级时 draft 与 last.fit_params 必须一致的协议键
_PROMOTE_PROTOCOL_KEYS = (
    "seq_feat_set",
    "full_aoo",
    "full_atc",
    "t_sample_pct",
    "holdout_trading_days",
    "seq_len",
    "lookback",
    "d_model",
    "nlayers",
    "nhead",
    "mlp_hidden",
    "dropout",
    "patience",
    "batch_size",
    "epochs",
    "lr",
    "loss",
)


def compute_parity(
    head: str,
    params: Optional[Dict[str, Any]],
    *,
    eval_shared: bool = False,
) -> Dict[str, Any]:
    """正式对照协议：T 全量因子 + 100% 采样 + 共享评测集。"""
    hid = str(head or "").strip().lower()
    p = params if isinstance(params, dict) else {}
    if hid in ("tc", "tc_rank"):
        factor_width_match = _as_bool(p.get("full_atc", True), default=True)
    else:
        factor_width_match = _as_bool(p.get("full_aoo", False), default=False)
    try:
        pct = float(p.get("t_sample_pct", 10.0))
    except (TypeError, ValueError):
        pct = 10.0
    if pct != pct:
        pct = 10.0
    train_sample_full = pct >= 99.999
    formal = bool(factor_width_match and train_sample_full and eval_shared)
    return {
        "factor_width_match": bool(factor_width_match),
        "train_sample_full": bool(train_sample_full),
        "eval_shared": bool(eval_shared),
        "formal": formal,
    }


def _protocol_values(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    p = params if isinstance(params, dict) else {}
    out: Dict[str, Any] = {}
    for k in _PROMOTE_PROTOCOL_KEYS:
        if k not in p:
            continue
        v = p.get(k)
        if k == "t_sample_pct":
            try:
                out[k] = float(f"{float(v):.6g}")
            except (TypeError, ValueError):
                out[k] = v
        else:
            out[k] = v
    return out


def _protocol_matches(
    a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]
) -> tuple[bool, List[str]]:
    """比较晋级协议键；返回 (ok, drift_keys)。"""
    pa, pb = _protocol_values(a), _protocol_values(b)
    keys = sorted(set(pa) | set(pb))
    drift: List[str] = []
    for k in keys:
        va, vb = pa.get(k), pb.get(k)
        if k == "t_sample_pct":
            try:
                if abs(float(va) - float(vb)) > 1e-6:
                    drift.append(k)
            except (TypeError, ValueError):
                if va != vb:
                    drift.append(k)
        elif k in ("full_aoo", "full_atc"):
            default_on = k == "full_atc"
            if _as_bool(va, default=default_on) != _as_bool(vb, default=default_on):
                drift.append(k)
        elif va != vb:
            drift.append(k)
    return (not drift), drift


def _gate_pass_from_last(last: Optional[Dict[str, Any]]) -> Any:
    """metrics.gate_pass；旧盘缺省时回退 hard_gate_vs_arm_L.T.pass。"""
    if not isinstance(last, dict):
        return None
    metrics = last.get("metrics") if isinstance(last.get("metrics"), dict) else {}
    gate_pass = metrics.get("gate_pass")
    if gate_pass is not None:
        return gate_pass
    hg = last.get("hard_gate_vs_arm_L")
    tg = hg.get("T") if isinstance(hg, dict) else None
    if isinstance(tg, dict) and tg.get("pass") is not None:
        return tg.get("pass")
    return None


def promote_eligibility(
    head: str,
    *,
    last: Optional[Dict[str, Any]] = None,
    draft: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """晋级前置条件（服务端与 UI 共用语义）。"""
    h = str(head or "").strip().lower()
    reasons: List[str] = []
    if not isinstance(draft, dict):
        reasons.append("无草稿")
    if not isinstance(last, dict):
        reasons.append("无最近 Holdout 结果")
        return {
            "ok": False,
            "promotable": False,
            "reasons": reasons,
            "parity": compute_parity(h, None, eval_shared=False),
        }
    if str(last.get("action") or "") != "holdout_result":
        reasons.append("最近记录不是 holdout_result")
    if str(last.get("head") or "") != h:
        reasons.append("last.head 与当前头不一致")
    if _gate_pass_from_last(last) is not True:
        reasons.append("硬门未过（gate_pass≠true）")
    parity = last.get("parity") if isinstance(last.get("parity"), dict) else None
    if not isinstance(parity, dict):
        fit_p = last.get("fit_params") or last.get("params")
        eu = last.get("eval_universe") if isinstance(last.get("eval_universe"), dict) else {}
        parity = compute_parity(
            h,
            fit_p if isinstance(fit_p, dict) else None,
            eval_shared=bool(eu.get("eval_shared")),
        )
    if not parity.get("formal"):
        bits = []
        if not parity.get("factor_width_match"):
            bits.append("T须full因子")
        if not parity.get("train_sample_full"):
            bits.append("T采样须100%")
        if not parity.get("eval_shared"):
            bits.append("须共享评测集")
        reasons.append("非正式对照（" + "；".join(bits or ["parity"]) + "）")
    draft_params = (draft or {}).get("params") if isinstance(draft, dict) else None
    fit_params = (
        last.get("fit_params")
        if isinstance(last.get("fit_params"), dict)
        else last.get("params")
    )
    # 两侧都 normalize，避免旧盘缺 patience/dropout/lr 等键被误判成协议漂移
    if isinstance(draft_params, dict):
        draft_params = _normalize_fit_params(h, draft_params)
    if isinstance(fit_params, dict):
        fit_params = _normalize_fit_params(h, fit_params)
    ok_proto, drift = _protocol_matches(
        draft_params if isinstance(draft_params, dict) else None,
        fit_params if isinstance(fit_params, dict) else None,
    )
    if not ok_proto:
        reasons.append("草稿协议与过闸 run 不一致：" + ",".join(drift[:6]))
    promotable = not reasons
    return {
        "ok": promotable,
        "promotable": promotable,
        "reasons": reasons,
        "parity": parity,
    }


def heads_meta() -> List[Dict[str, str]]:
    return [
        {
            "id": s.id,
            "label": s.label,
            "target": s.target_desc,
            "role": s.role,
        }
        for s in iter_heads()
    ]


def default_fit_params(head: str) -> Dict[str, Any]:
    spec = get_head(head)
    base: Dict[str, Any] = {
        "head": spec.id,
        "pool_n": 800,
        "lookback": 900,
        "holdout_trading_days": 60,
        "d_model": 64,
        "nlayers": 3,
        "nhead": 4,
        # mlp_hidden 故意缺省：harden 按当前 d_model 对齐（避免改 d_model 仍锁旧 hidden）
        "dropout": 0.1,
        "patience": 6,
        "batch_size": 32,
        "epochs": 12,
        "lr": 1e-3,
        # Arm·T 相对训练段 subsample（%）；默认 10，最大 100=不截断。Arm·L 仍全样本。
        "t_sample_pct": 10.0,
        "arms": "L,T",
        "device": "cpu",
        "factor_pack": spec.factor_pack,
        "loss": spec.loss,
        "seq_feat_set": "seq16",
        "seq_len": 20,
        "full_aoo": False,
    }
    if spec.id in ("tc", "tc_rank"):
        base["tau_end"] = "09:30"
        base["full_atc"] = True
        # 与 oo 同窗；不改 lookback / Holdout（曾误把 900/60 盖成 200/40）
    if spec.id == "oo_rank":
        # 排序头略缩 lookback 省内存；Holdout 天数与其它头统一 60
        base["lookback"] = 700
    return base


_DESK_PARAM_KEYS = frozenset(
    {
        "head",
        "pool_n",
        "watching_limit",
        "lookback",
        "holdout_trading_days",
        "d_model",
        "nlayers",
        "nhead",
        "mlp_hidden",
        "dropout",
        "patience",
        "batch_size",
        "epochs",
        "lr",
        "t_sample_pct",
        "arms",
        "device",
        "factor_pack",
        "loss",
        "tau_end",
        "seq_feat_set",
        "seq_len",
        "full_aoo",
        "full_atc",
    }
)


def _as_bounded_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    return max(int(lo), min(int(hi), n))


def _as_bounded_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        x = float(default)
    if x != x:  # NaN
        x = float(default)
    return max(float(lo), min(float(hi), x))


def harden_fit_params(head: str, params: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    """数值边界钳制 + 告警；返回 ``(params, warnings)``。τc 长窗与 Holdout 不改数、不告警。"""
    spec = get_head(head)
    out = dict(params or {})
    warns: List[str] = []

    def _clamp(key: str, default: int, lo: int, hi: int, *, label: str = "") -> None:
        raw = out.get(key, default)
        cur = _as_bounded_int(raw, default, lo, hi)
        try:
            orig = int(raw)
        except (TypeError, ValueError):
            orig = default
        if orig != cur:
            warns.append(f"{label or key}: {orig}→{cur}")
        out[key] = cur

    def _floor_int(key: str, default: int, lo: int, *, label: str = "") -> None:
        """只保下限，不硬钳上限（用户显式加大的配置原样保留）。"""
        raw = out.get(key, default)
        try:
            orig = int(raw)
        except (TypeError, ValueError):
            orig = int(default)
        cur = max(int(lo), orig)
        if orig != cur:
            warns.append(f"{label or key}: {orig}→{cur}")
        out[key] = cur

    _clamp("pool_n", 800, 50, 2000, label="池")
    out["watching_limit"] = int(out["pool_n"])
    _clamp("lookback", int(out.get("lookback") or 900), 60, 1200, label="lookback")
    _clamp(
        "holdout_trading_days",
        int(out.get("holdout_trading_days") or 20),
        5,
        90,
        label="Holdout",
    )
    # d_model：不硬钳上限（曾把 256→128，表现为「点拟合就变回」）
    _floor_int("d_model", 64, 16, label="d_model")
    _clamp("nlayers", 3, 1, 6, label="nlayers")
    # nhead / mlp / dropout / lr / patience：可配置；缺省用默认（不进页面）
    _clamp("nhead", 4, 1, 8, label="nhead")
    d_model = int(out["d_model"])
    nhead = int(out["nhead"])
    if d_model % nhead != 0:
        nh = nhead
        while nh > 1 and d_model % nh != 0:
            nh -= 1
        warns.append(f"nhead: {nhead}→{nh}（需整除 d_model={d_model}）")
        out["nhead"] = nh
        nhead = nh
    if "mlp_hidden" not in out or out.get("mlp_hidden") is None or out.get("mlp_hidden") == "":
        out["mlp_hidden"] = d_model
    else:
        _clamp("mlp_hidden", d_model, 16, 512, label="mlp_hidden")
    raw_drop = out.get("dropout", 0.1)
    drop = _as_bounded_float(raw_drop, 0.1, 0.0, 0.5)
    try:
        drop_orig = float(raw_drop)
    except (TypeError, ValueError):
        drop_orig = 0.1
    if abs(drop_orig - drop) > 1e-9 or drop_orig != drop_orig:
        warns.append(f"dropout: {raw_drop}→{drop:g}")
    out["dropout"] = float(f"{drop:.6g}")
    _clamp("patience", 6, 1, 20, label="patience")
    # 与 train._batches 地板 16 对齐（UI/CLI 写 8 会静默变成 16）
    _clamp("batch_size", 32, 16, 256, label="batch")
    _clamp("epochs", 12, 1, 40, label="epochs")
    out["lr"] = _as_bounded_float(out.get("lr"), 1e-3, 1e-5, 1e-1)
    # Arm·T subsample %：(0,100]；默认 10；100=不截断。旧键 max_train_rows 丢弃。
    out.pop("max_train_rows", None)
    try:
        pct = float(out.get("t_sample_pct", 10.0))
    except (TypeError, ValueError):
        pct = 10.0
        warns.append("t_sample_pct: 无效→10")
    if pct != pct:  # NaN
        pct = 10.0
        warns.append("t_sample_pct: NaN→10")
    capped_pct = max(0.1, min(100.0, pct))
    if abs(capped_pct - pct) > 1e-9:
        warns.append(f"t_sample_pct: {pct}→{capped_pct:g}")
    out["t_sample_pct"] = float(f"{capped_pct:.6g}")
    if capped_pct >= 100.0 and int(out.get("d_model") or 0) >= 256:
        warns.append("t_sample_pct=100 且 d_model≥256：Arm·T 易 OOM，建议降低采样%或 d_model")

    # τc*：只校 tau_end，不改用户的长窗 / Holdout
    if spec.id in ("tc", "tc_rank"):
        out.setdefault("tau_end", "09:30")
        te = str(out.get("tau_end") or "09:30").strip()
        if te not in ("09:30", "0930", "open"):
            warns.append(f"tau_end: {te}→09:30")
            te = "09:30"
        out["tau_end"] = "09:30" if te in ("0930", "open", "09:30") else te
    else:
        out.pop("tau_end", None)

    raw_sfs = str(out.get("seq_feat_set") or "seq16").strip().lower()
    sfs_alias = {
        "8": "seq8",
        "base": "seq8",
        "seq8": "seq8",
        "16": "seq16",
        "seq16": "seq16",
        "32": "seq32",
        "seq32": "seq32",
        "64": "seq64",
        "seq64": "seq64",
    }
    if raw_sfs not in sfs_alias:
        warns.append(f"seq_feat_set: {out.get('seq_feat_set')}→seq16")
        sfs = "seq16"
    else:
        sfs = sfs_alias[raw_sfs]
    out["seq_feat_set"] = sfs
    _clamp("seq_len", int(out.get("seq_len") or 20), 5, 60, label="seq_len")

    fa = _as_bool(out.get("full_aoo", False), default=False)
    # full_aoo 只属于 oo；τc 的全量/精简走 full_atc
    if spec.id in ("tc", "tc_rank") and fa:
        warns.append(f"{spec.id} 忽略 full_aoo（仅 oo/oo_rank）")
        fa = False
    out["full_aoo"] = bool(fa)

    if spec.id in ("tc", "tc_rank"):
        # Holdout summary.args 用 atc_core；desk 用 full_atc。仅有 atc_core 时要映射，
        # 否则 ingest 会落到默认 full_atc=True，草稿/CLI 丢掉 --atc-core。
        if "atc_core" in out and (
            "full_atc" not in out or out.get("full_atc") is None or out.get("full_atc") == ""
        ):
            ac = out.get("atc_core")
            if isinstance(ac, str):
                ac = ac.strip().lower() in ("1", "true", "yes", "on")
            out["full_atc"] = not bool(ac)
        out["full_atc"] = _as_bool(out.get("full_atc", True), default=True)
    else:
        out.pop("full_atc", None)
    out.pop("atc_core", None)

    out.pop("protocol_preset", None)

    # Scheme LT：固定双臂；历史 seeds 字段丢弃（seed 固定 42）
    out["arms"] = "L,T"
    out.pop("seeds", None)
    device = str(out.get("device") or "cpu").strip().lower() or "cpu"
    if device not in ("cpu", "mps", "cuda"):
        warns.append(f"device: {device}→cpu")
        device = "cpu"
    out["device"] = device
    out["head"] = spec.id
    want_loss = str(out.get("loss") or "").strip()
    if want_loss and want_loss != spec.loss:
        warns.append(f"loss: {want_loss}→{spec.loss}（本头固定）")
    out["loss"] = spec.loss
    # 与 Arm·T 实际列宽一致（L 仍始终全量）；勿写死 HeadSpec 的 *_full
    if spec.id in ("tc", "tc_rank"):
        out["factor_pack"] = "atc_full" if bool(out.get("full_atc", True)) else "atc_core"
    else:
        out["factor_pack"] = "aoo_full" if bool(out.get("full_aoo")) else "aoo_core"
    out["pool_n"] = int(out["pool_n"])
    out["watching_limit"] = int(out["pool_n"])
    return out, warns


def _normalize_fit_params(head: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """合并默认值；``pool_n`` 同步为 ``watching_limit``；loss 锁定 HeadSpec。

    只吸收 desk 表单键，忽略脚本 ``args`` 里的 out / update_desk 等杂质。
    脚本 ``args`` 常只有 ``watching_limit`` 无 ``pool_n``：先映射再 harden，
    避免 ingest 后把池子盖回默认 800。
    Holdout ``args`` 用 ``atc_core``（不在 desk 键集）：合并默认前先映成 ``full_atc``，
    否则会被默认 ``full_atc=True`` 盖掉。
    """
    spec = get_head(head)
    incoming: Dict[str, Any] = dict(params) if isinstance(params, dict) else {}
    if "atc_core" in incoming and "full_atc" not in incoming:
        ac = incoming.pop("atc_core")
        if isinstance(ac, str):
            ac = ac.strip().lower() in ("1", "true", "yes", "on")
        incoming["full_atc"] = not bool(ac)
    else:
        incoming.pop("atc_core", None)
    # Holdout args 用 t_sample_frac；desk 用 t_sample_pct。合并默认前先映射。
    if "t_sample_frac" in incoming and "t_sample_pct" not in incoming:
        try:
            frac = float(incoming.pop("t_sample_frac"))
        except (TypeError, ValueError):
            incoming.pop("t_sample_frac", None)
            frac = None
        if frac is not None and frac == frac:
            incoming["t_sample_pct"] = max(0.1, min(100.0, float(frac) * 100.0))
    else:
        incoming.pop("t_sample_frac", None)
    merged = default_fit_params(spec.id)
    if incoming:
        for k, v in incoming.items():
            if v is None or k not in _DESK_PARAM_KEYS:
                continue
            merged[k] = v
        # 表单用 pool_n；CLI/args 用 watching_limit。两者都给时以 pool_n 为准。
        if "pool_n" not in incoming and incoming.get("watching_limit") is not None:
            merged["pool_n"] = incoming.get("watching_limit")
        elif incoming.get("pool_n") is not None:
            merged["watching_limit"] = incoming.get("pool_n")
    merged["head"] = spec.id
    merged["loss"] = spec.loss
    hardened, _warns = harden_fit_params(spec.id, merged)
    return hardened


def build_holdout_argv(
    head: str, params: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Holdout 脚本 argv（相对 ``ROOT_DIR`` 的脚本路径段除外，含绝对脚本路径）。"""
    spec = get_head(head)
    p = _normalize_fit_params(spec.id, params)
    script = os.path.join(ROOT_DIR, "scripts", "run_yhat_transformer_holdout.py")
    argv = [
        script,
        "--head",
        spec.id,
        "--watching-limit",
        str(int(p["watching_limit"])),
        "--lookback",
        str(int(p["lookback"])),
        "--holdout-trading-days",
        str(int(p["holdout_trading_days"])),
        "--d-model",
        str(int(p["d_model"])),
        "--nhead",
        str(int(p["nhead"])),
        "--nlayers",
        str(int(p["nlayers"])),
        "--mlp-hidden",
        str(int(p.get("mlp_hidden") if p.get("mlp_hidden") is not None else p["d_model"])),
        "--dropout",
        f"{float(p.get('dropout') if p.get('dropout') is not None else 0.1):.6g}",
        "--patience",
        str(int(p.get("patience") or 6)),
        "--batch-size",
        str(int(p["batch_size"])),
        "--epochs",
        str(int(p["epochs"])),
        "--device",
        str(p.get("device") or "cpu").strip(),
        "--seq-feat-set",
        str(p.get("seq_feat_set") or "seq16").strip() or "seq16",
        "--seq-len",
        str(int(p.get("seq_len") or 20)),
        "--update-desk",
    ]
    lr = float(p.get("lr") or 1e-3)
    argv.extend(["--lr", f"{lr:.6g}"])
    try:
        pct = float(p.get("t_sample_pct") if p.get("t_sample_pct") is not None else 10.0)
    except (TypeError, ValueError):
        pct = 10.0
    pct = max(0.1, min(100.0, pct))
    argv.extend(["--t-sample-frac", f"{(pct / 100.0):.6g}"])
    if bool(p.get("full_aoo")) and spec.id in ("oo", "oo_rank"):
        argv.append("--full-aoo")
    if spec.id in ("tc", "tc_rank") and not bool(p.get("full_atc", True)):
        argv.append("--atc-core")
    if spec.id in ("tc", "tc_rank"):
        argv.extend(["--tau-end", str(p.get("tau_end") or "09:30").strip()])
    return argv


def build_holdout_cli(head: str, params: Optional[Dict[str, Any]] = None) -> str:
    """按 desk 参数拼可复制 CLI（显式传参，覆盖脚本默认）。"""
    argv = build_holdout_argv(head, params)
    # 展示用相对路径
    rel = os.path.relpath(argv[0], ROOT_DIR).replace(os.sep, "/")
    shown = [rel, *argv[1:]]
    return " ".join(shown)


def _arm_t_log_has(low: str, token: str) -> bool:
    """Arm·T 训练日志前缀（transformer …）。"""
    t = str(token or "").strip().lower()
    if not t:
        return False
    return f"transformer {t}" in low


def _fit_exit_error_from_log(head: str) -> str:
    """进程无 metrics 退出时，据日志末尾推断更可读原因。"""
    tail = _log_tail(head, n_lines=24)
    low = (tail or "").lower()
    if "holdout 收到" in low and "中途退出" in low:
        return "拟合进程被终止（收到 SIGTERM/SIGINT）；未回写 metrics，请重新拟合"
    hint_8g = "8GB 机建议 d_model≤64；τc 长窗 OOM 时可自行下调 lookback / Holdout / 池"
    if _arm_t_log_has(low, "build") and not _arm_t_log_has(low, "model ready"):
        return (
            "拟合进程被系统终止（疑似内存不足，停在 Arm·T 模型构建）。"
            f"请降低 lookback / d_model / 池大小后重试（{hint_8g}）"
        )
    # 已进 fit start 但无 step：优先判训练起步崩溃（勿被「有 model ready 无 buffers」盖住）
    if (
        _arm_t_log_has(low, "fit start")
        and not _arm_t_log_has(low, "step=")
        and not _arm_t_log_has(low, "ep=")
    ):
        return (
            "拟合进程在 Arm·T 训练起步时崩溃（常见于 libtorch OpenMP 大张量索引 SIGSEGV）。"
            "已改为 numpy 按 batch 取数；请再试一次拟合"
        )
    if (
        _arm_t_log_has(low, "model ready")
        and not _arm_t_log_has(low, "buffers ready")
        and not _arm_t_log_has(low, "tensors ready")
        and not _arm_t_log_has(low, "fit start")
    ):
        return (
            "拟合进程被系统终止（疑似内存不足，停在 Arm·T 缓冲准备）。"
            f"请降低 lookback / d_model / 池大小后重试（{hint_8g}）"
        )
    arm_l_split = "arml 切分" in low
    arm_l_begin = "arml begin" in low
    arm_l_done = ("arml rankic" in low) or ("arml 失败" in low)
    if arm_l_split and not arm_l_begin and not arm_l_done:
        return (
            "拟合进程在 Arm·L 切分后退出（未进入 LGB）。"
            f"请降低 lookback / 池大小后重试（{hint_8g}）"
        )
    if arm_l_begin and not arm_l_done:
        return (
            "拟合进程在 Arm·L LightGBM 训练中被系统终止（疑似内存不足；τc 因子维更高）。"
            f"请用默认窗或缩小池后重试（{hint_8g}）"
        )
    if "memoryerror" in low or "killed" in low:
        return f"拟合进程因内存不足退出；请降低 lookback / d_model / 池大小后重试（{hint_8g}）"
    if "traceback" in low:
        return "拟合进程异常退出（见下方日志 Traceback）；未回写 metrics"
    # 训练中途突然没了：常见于 web 重启 / 手动杀进程，而非代码抛错
    if (
        (
            _arm_t_log_has(low, "step=")
            or "arml begin" in low
        )
        and "traceback" not in low
        and "写入 " not in (tail or "")
        and "desk last 已回写" not in (tail or "")
    ):
        return (
            "拟合进程被中断（训练中途退出，常见于服务重启或进程被杀）；"
            "未回写 metrics，请重新拟合"
        )
    return "拟合进程已结束，但未回写 metrics；请查看下方日志"


@_with_head_lock
def refresh_fit_job(head: str) -> Optional[Dict[str, Any]]:
    """读/刷新本头拟合任务；进程退出则标 done/error。"""
    h = _validate_head(head)
    job = _read_json(_job_path(h))
    if not isinstance(job, dict):
        return None
    status = str(job.get("status") or "")
    pid = int(job.get("pid") or 0)
    # starting 占位：尚无 pid 时宽限，避免被当成死任务；有 pid 后按 running 走
    if status == "starting" and job.get("running"):
        if pid <= 0:
            if _job_started_age_sec(job) < 8:
                return job
            job["status"] = "error"
            job["running"] = False
            job["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            job["error"] = "拟合启动超时（未拉起进程）"
            job["exit_hint"] = "start_timeout"
            _write_json(_job_path(h), job)
            return job
        status = "running"
        job["status"] = "running"
    # 无论 done/error/running：先尝试收割子进程，避免成功后僵尸残留
    reaped = _reap_pid(pid)
    last = _read_json(_path(h, "last")) or {}
    metrics = last.get("metrics") if isinstance(last.get("metrics"), dict) else None
    # 任意状态：已有非空 metrics 回写则视为完成（空 dict 不算成功）
    if last.get("action") == "holdout_result" and metrics:
        dirty = False
        if status != "done" or job.get("running"):
            job["status"] = "done"
            job["running"] = False
            job["finished_at"] = last.get("updated_at") or time.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            # 部分成功的失败原因留在 job.error，不要被「有 metrics」清掉
            if str(job.get("exit_hint") or "") == "partial" and str(job.get("error") or "").strip():
                job["exit_hint"] = "partial"
            else:
                job.pop("error", None)
                job["exit_hint"] = "holdout_result"
            dirty = True
        else:
            job["running"] = False
        if reaped is not None and job.get("exit_status") is None:
            job["exit_status"] = reaped
            dirty = True
        if dirty:
            _write_json(_job_path(h), job)
        return job
    if status == "running":
        alive = _pid_alive_confirmed(pid) if pid > 0 else False
        holdout = _pid_holdout_check(pid) if alive else False
        if alive and holdout is True:
            if job.get("alive_miss") or job.get("holdout_miss"):
                job.pop("alive_miss", None)
                job.pop("holdout_miss", None)
                _write_json(_job_path(h), job)
            job["running"] = True
            return job
        # pid 仍在且命令行可见但不是 Holdout：连续确认后再钉死（防 ps 截断/瞬时错读）
        if alive and holdout is False:
            miss = int(job.get("holdout_miss") or 0) + 1
            job["holdout_miss"] = miss
            if miss < _FIT_ALIVE_MISS_MAX:
                job["running"] = True
                job["status"] = "running"
                _write_json(_job_path(h), job)
                return job
            finished = time.strftime("%Y-%m-%dT%H:%M:%S")
            err = (
                f"拟合 pid={pid} 已不再是 Holdout 进程（可能被复用）；"
                "未回写 metrics，请重新拟合"
            )
            job["status"] = "error"
            job["running"] = False
            job["finished_at"] = finished
            job["error"] = err
            job["exit_hint"] = "pid_reused"
            job.pop("alive_miss", None)
            if reaped is not None:
                job["exit_status"] = reaped
            _write_json(_job_path(h), job)
            prev_last = last if isinstance(last, dict) else {}
            if prev_last.get("action") != "holdout_result":
                failed = {
                    **prev_last,
                    "role": "yhat_tf_last",
                    "head": h,
                    "action": "holdout_failed",
                    "metrics": None,
                    "message": err,
                    "updated_at": finished,
                    "live_allowed": False,
                }
                if job.get("params") and not failed.get("params"):
                    failed["params"] = job.get("params")
                _write_json(_path(h, "last"), failed)
            return job
        # 进程短暂不可见 / ps 暂不可读 / 刚启动 / 日线静默：宽限，避免误标失败
        if alive and holdout is None:
            job.pop("alive_miss", None)
            job["running"] = True
            job["status"] = "running"
            _write_json(_job_path(h), job)
            return job
        log_path = _log_path(h)
        try:
            log_age = (
                time.time() - os.path.getmtime(log_path) if os.path.isfile(log_path) else 1e9
            )
        except OSError:
            log_age = 1e9
        started_age = _job_started_age_sec(job)
        # 进程已死时：日志 180 秒没更新，或启动已超过 45 秒，就不再靠日志原文续命
        soft_grace = (
            log_age < _FIT_LOG_STALE_SEC
            or started_age < _FIT_START_GRACE_SEC
        )
        if not alive:
            miss = int(job.get("alive_miss") or 0) + 1
            job["alive_miss"] = miss
            if soft_grace or miss < _FIT_ALIVE_MISS_MAX:
                job["running"] = True
                job["status"] = "running"
                _write_json(_job_path(h), job)
                return job
        job["status"] = "error"
        job["running"] = False
        finished = time.strftime("%Y-%m-%dT%H:%M:%S")
        job["finished_at"] = finished
        err = _fit_exit_error_from_log(h)
        job["error"] = err
        job.pop("alive_miss", None)
        job.pop("holdout_miss", None)
        if reaped is not None:
            job["exit_status"] = reaped
        _write_json(_job_path(h), job)
        # 同步 last，避免一直停在 holdout_intent、信息窗/晋级语义不一致
        prev_last = last if isinstance(last, dict) else {}
        if prev_last.get("action") != "holdout_result":
            failed = {
                **prev_last,
                "role": "yhat_tf_last",
                "head": h,
                "action": "holdout_failed",
                "metrics": None,
                "message": err,
                "updated_at": finished,
                "live_allowed": False,
            }
            if job.get("params") and not failed.get("params"):
                failed["params"] = job.get("params")
            _write_json(_path(h, "last"), failed)
    else:
        # error/done 等：Holdout 进程其实还在则纠回 running（避免宽限误杀后假失败钉死）
        if status == "error" and pid > 0:
            alive = _pid_alive_confirmed(pid)
            holdout = _pid_holdout_check(pid) if alive else False
            if alive and holdout is not False:
                job["status"] = "running"
                job["running"] = True
                job.pop("error", None)
                job.pop("finished_at", None)
                job.pop("exit_hint", None)
                job.pop("exit_status", None)
                job.pop("alive_miss", None)
                job.pop("holdout_miss", None)
                _write_json(_job_path(h), job)
                return job
        job["running"] = False
        if reaped is not None and job.get("exit_status") is None:
            job["exit_status"] = reaped
            _write_json(_job_path(h), job)
        # 已是 error 但 last 仍停在 intent：补写失败态（例如上次只改了 job）
        if (
            status == "error"
            and isinstance(last, dict)
            and last.get("action") in (None, "holdout_intent", "fit_start")
        ):
            hinted = _fit_exit_error_from_log(h)
            err = hinted or str(job.get("error") or "").strip() or "拟合失败"
            # 旧泛化文案被更具体的日志推断替换
            if (
                "未回写 metrics" in str(job.get("error") or "")
                and hinted
                and hinted != job.get("error")
            ):
                err = hinted
            finished = (
                job.get("finished_at")
                or time.strftime("%Y-%m-%dT%H:%M:%S")
            )
            failed = {
                **last,
                "role": "yhat_tf_last",
                "head": h,
                "action": "holdout_failed",
                "metrics": None,
                "message": err,
                "updated_at": finished,
                "live_allowed": False,
            }
            if job.get("params") and not failed.get("params"):
                failed["params"] = job.get("params")
            _write_json(_path(h, "last"), failed)
            if err != job.get("error"):
                job["error"] = err
                _write_json(_job_path(h), job)
    return job


@_with_head_lock
def start_holdout_fit(
    head: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """存草稿并后台启动 Holdout 拟合（不写生产 score）。"""
    spec = get_head(head)
    prev = refresh_fit_job(spec.id)
    prev_status = str((prev or {}).get("status") or "") if isinstance(prev, dict) else ""
    if (
        isinstance(prev, dict)
        and prev.get("running")
        and prev_status in ("running", "starting")
    ):
        return {
            "ok": False,
            "success": False,
            "error": f"{spec.label} 正在拟合中（pid={prev.get('pid')}）",
            "job": prev,
        }

    _ensure_dir()
    log_path = _log_path(spec.id)
    job_path = _job_path(spec.id)

    intent = record_holdout_intent(spec.id, params)
    p = (intent.get("last") or {}).get("params") or _normalize_fit_params(spec.id, params)
    _, clamp_warns = harden_fit_params(spec.id, dict(p))
    cli = intent.get("cli") or build_holdout_cli(spec.id, p)
    argv = build_holdout_argv(spec.id, p)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    run_id = f"{spec.id}-{started_at.replace(':', '').replace('-', '')}"
    cmd = [sys.executable, "-u", *argv]
    # 先落 starting 占位，避免删 job→写 job 空窗内二次 /fit
    starting_job = {
        "role": "yhat_tf_job",
        "head": spec.id,
        "run_id": run_id,
        "status": "starting",
        "running": True,
        "pid": 0,
        "cli": cli,
        "log_path": log_path,
        "params": p,
        "param_warnings": clamp_warns,
        "started_at": started_at,
        "live_allowed": False,
    }
    _write_json(job_path, starting_job)
    # 子进程强制单线程 BLAS/OpenMP，避开 libtorch / LGB 线程风暴。
    # 用赋值覆盖：父进程里已有的 OMP_NUM_THREADS 也要收成 1。
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONUNBUFFERED", "1")
    for k in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "TORCH_NUM_THREADS",
        "HORIZON_LGB_NUM_THREADS",
    ):
        child_env[k] = "1"
    # 用裸 fd 交接 stdout：避免 TextIOWrapper 在 with 退出时关 fd 导致子进程日志静默
    log_fd: Optional[int] = None
    try:
        log_fd = os.open(
            log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644
        )
        banner = (
            f"==== fit start {started_at} head={spec.id} run={run_id} ====\n"
            f"{' '.join(cmd)}\n"
        )
        if clamp_warns:
            banner += f"param notes: {'; '.join(clamp_warns)}\n"
        os.write(log_fd, banner.encode("utf-8", errors="replace"))
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT_DIR,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=child_env,
        )
    except OSError as exc:
        if log_fd is not None:
            try:
                os.close(log_fd)
            except OSError:
                pass
        failed_start = {
            **starting_job,
            "status": "error",
            "running": False,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "error": f"无法启动拟合进程：{exc}",
            "exit_hint": "spawn_failed",
        }
        _write_json(job_path, failed_start)
        return {
            "ok": False,
            "success": False,
            "error": f"无法启动拟合进程：{exc}",
            "cli": cli,
        }
    else:
        # 父进程关掉自己的副本；子进程在 fork 后已持有独立 fd
        try:
            os.close(log_fd)
        except OSError:
            pass
        log_fd = None

    # 立刻写成 running，再做启动即崩探测
    job = {
        **starting_job,
        "status": "running",
        "pid": int(proc.pid),
    }
    _write_json(job_path, job)
    time.sleep(0.2)
    early_reap = _reap_pid(int(proc.pid))
    if early_reap is not None or not _pid_alive(int(proc.pid)):
        err = _fit_exit_error_from_log(spec.id) or "拟合进程启动后立即退出"
        finished = time.strftime("%Y-%m-%dT%H:%M:%S")
        failed_job = {
            "role": "yhat_tf_job",
            "head": spec.id,
            "run_id": run_id,
            "status": "error",
            "running": False,
            "pid": int(proc.pid),
            "cli": cli,
            "log_path": log_path,
            "params": p,
            "param_warnings": clamp_warns,
            "started_at": started_at,
            "finished_at": finished,
            "error": err,
            "exit_hint": "early_exit",
            "exit_status": early_reap,
            "live_allowed": False,
        }
        _write_json(_job_path(spec.id), failed_job)
        prev_last = _read_json(_path(spec.id, "last")) or {}
        _write_json(
            _path(spec.id, "last"),
            {
                **prev_last,
                "role": "yhat_tf_last",
                "head": spec.id,
                "action": "holdout_failed",
                "metrics": None,
                "message": err,
                "params": prev_last.get("params") or p,
                "updated_at": finished,
                "live_allowed": False,
            },
        )
        return {
            "ok": False,
            "success": False,
            "error": err,
            "job": failed_job,
            "cli": cli,
            "param_warnings": clamp_warns,
        }

    job = {
        "role": "yhat_tf_job",
        "head": spec.id,
        "run_id": run_id,
        "status": "running",
        "running": True,
        "pid": int(proc.pid),
        "cli": cli,
        "log_path": log_path,
        "params": p,
        "param_warnings": clamp_warns,
        "started_at": started_at,
        "live_allowed": False,
    }
    _write_json(_job_path(spec.id), job)
    _write_json(
        YHAT_TF_LAST_PATH,
        {
            "action": "fit_start",
            "head": spec.id,
            "pid": job["pid"],
            "updated_at": job["started_at"],
        },
    )
    msg = f"已启动拟合 pid={job['pid']}（后台 Holdout；不写 live）"
    if clamp_warns:
        # 含边界钳制与其它告警；勿一律写成「已钳制」
        msg += " · 提示：" + "；".join(clamp_warns[:4])
    return {
        "ok": True,
        "success": True,
        "job": job,
        "cli": cli,
        "last": intent.get("last"),
        "param_warnings": clamp_warns,
        "message": msg,
    }


@_with_head_lock
def mark_fit_failed(head: str, error: str) -> Dict[str, Any]:
    """子进程异常退出时回写 job/last，避免 UI 只见「未回写 metrics」。"""
    h = _validate_head(head)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    err = str(error or "拟合失败").strip() or "拟合失败"
    job = _read_json(_job_path(h)) or {
        "role": "yhat_tf_job",
        "head": h,
        "status": "error",
    }
    job.update(
        {
            "status": "error",
            "running": False,
            "finished_at": now,
            "error": err,
            "exit_hint": "mark_fit_failed",
        }
    )
    _write_json(_job_path(h), job)
    prev = _read_json(_path(h, "last")) or {}
    last = {
        **prev,
        "role": "yhat_tf_last",
        "head": h,
        "action": "holdout_failed",
        "metrics": None,
        "message": err,
        "updated_at": now,
        "live_allowed": False,
    }
    _write_json(_path(h, "last"), last)
    return {"ok": True, "success": True, "job": job, "last": last}


def _coerce_slot_params(
    head: str, blob: Optional[Dict[str, Any]], *, persist_path: str = ""
) -> Optional[Dict[str, Any]]:
    """读盘槽位时规范化 params；变更时懒回写。

    进行中的 job（running/starting）不回写、不改写 cli：避免用默认值
    （如 t_sample_pct=10）覆盖「进程实际启动参数」，造成 UI 假回退。
    """
    if not isinstance(blob, dict):
        return blob
    out = dict(blob)
    status = str(out.get("status") or "")
    in_flight = bool(out.get("running")) and status in ("running", "starting")
    raw = out.get("params")
    dirty = False
    if isinstance(raw, dict):
        fixed = _normalize_fit_params(head, raw)
        # 进行中：内存里仍给规范化视图，但不把缺省键写回盘、不改 cli
        if in_flight:
            out["params"] = fixed
            if isinstance(out.get("fit_params"), dict):
                out["fit_params"] = _normalize_fit_params(head, out["fit_params"])
            return out
        out["params"] = fixed
        if isinstance(out.get("fit_params"), dict):
            fp_fixed = _normalize_fit_params(head, out["fit_params"])
            if fp_fixed != out.get("fit_params"):
                out["fit_params"] = fp_fixed
                dirty = True
        try:
            synced_cli = build_holdout_cli(head, fixed)
        except Exception:
            synced_cli = out.get("cli")
        cli_changed = bool(synced_cli) and str(out.get("cli") or "") != str(synced_cli)
        if synced_cli:
            out["cli"] = synced_cli
        # 旧 message 里常嵌着过期 CLI；与 params 不一致时改成短提示
        msg = str(out.get("message") or "")
        if msg and "CLI：" in msg:
            lb = fixed.get("lookback")
            if lb is not None and f"--lookback {lb}" not in msg:
                out["message"] = (
                    f"参数已同步 lookback={lb} "
                    f"Holdout={fixed.get('holdout_trading_days')}"
                )
        scrubbed = any(
            k in raw for k in ("protocol_preset", "atc_core", "seeds", "max_train_rows")
        )
        # 非 τc 头残留 tau_end、或其它被 pop 的键 → 回写 scrub（τc 合法 tau_end 在 fixed 内）
        extra_keys = set(raw) - set(fixed)
        # full_atc 等被映射/钳制后也要回写，避免盘上残留错档
        key_drift = any(
            fixed.get(k) != raw.get(k)
            for k in (
                "full_atc",
                "full_aoo",
                "seq_feat_set",
                "lookback",
                "d_model",
                "arms",
                "loss",
                "tau_end",
                "t_sample_pct",
            )
        )
        dirty = (
            dirty
            or scrubbed
            or bool(extra_keys)
            or key_drift
            or cli_changed
            or out.get("message") != blob.get("message")
        )
    if persist_path and dirty:
        _write_json(persist_path, out)
    return out


_ARM_SUMMARY_KEYS = {"L": "arm_L", "T": "arm_T"}


def _summary_arms(last: Any) -> Dict[str, Dict[str, Any]]:
    """两臂指标：优先本 run 的 experiment_out；否则回退 last.metrics.arms。"""
    if not isinstance(last, dict):
        return {}

    def _pack_from_row(row: Any) -> Dict[str, Any]:
        if not isinstance(row, dict):
            return {}
        # 失败臂只保留失败标记，勿展示旧指标残渣；UI 依 success=false 显示「失败」
        if row.get("success") is False:
            return {
                k: row[k]
                for k in ("success", "error", "elapsed_sec")
                if row.get(k) is not None
            }
        return {
            k: row[k]
            for k in (
                "cs_rank_ic",
                "cs_rank_icir",
                "sign_hit",
                "cs_top_mean_y",
                "success",
                "elapsed_sec",
                "n",
                "n_features",
                "n_train",
                "n_test",
            )
            if row.get(k) is not None
        }

    out: Dict[str, Dict[str, Any]] = {}
    # holdout_failed 且无 experiment_out：不要读到历史路径
    path = last.get("experiment_out")
    if (
        isinstance(path, str)
        and path.strip()
        and os.path.isfile(path)
        and last.get("action") != "holdout_intent"
    ):
        try:
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
        except (OSError, ValueError):
            blob = None
        if isinstance(blob, dict):
            for tag, key in _ARM_SUMMARY_KEYS.items():
                pack = _pack_from_row(blob.get(key))
                if pack:
                    out[tag] = pack
    if out:
        return out
    # 旧 last / experiment 丢失：用 ingest 已写入的 metrics.arms
    arms = (last.get("metrics") or {}).get("arms") if isinstance(last.get("metrics"), dict) else None
    if isinstance(arms, dict):
        for tag in ("L", "T"):
            pack = _pack_from_row(arms.get(tag))
            if pack:
                out[tag] = pack
    return out


def head_status(head: str) -> Dict[str, Any]:
    spec = get_head(head)
    # 先收割/结算 job，再读 last（refresh 可能把 intent 改成 holdout_failed）
    job = refresh_fit_job(spec.id)
    # job 启动时已冻结 params/cli：进行中仅内存规范化；done/error 不改写落盘
    if isinstance(job, dict):
        st = str(job.get("status") or "")
        if st in ("running", "starting"):
            job = _coerce_slot_params(spec.id, job, persist_path="") or job
        # done/error/其它：原样返回，避免 status 刷新改写已结束 job
    # 读状态只在内存里规范化，不回写。清洗发生在存草稿 / 拟合意图 / ingest。
    draft = _coerce_slot_params(
        spec.id, _read_json(_path(spec.id, "draft")), persist_path=""
    )
    research = _coerce_slot_params(
        spec.id,
        _read_json(_path(spec.id, "research")),
        persist_path="",
    )
    last = _coerce_slot_params(
        spec.id, _read_json(_path(spec.id, "last")), persist_path=""
    )
    scheme = _scheme_blob()["per_head"][spec.id]
    running = bool(
        isinstance(job, dict)
        and job.get("running")
        and str(job.get("status") or "") in ("running", "starting")
    )
    last_action = (last or {}).get("action") if isinstance(last, dict) else None
    log_tail = (
        _log_for_status(spec.id)
        if (
            running
            or job
            or last_action in ("holdout_result", "holdout_failed")
        )
        else ""
    )
    elig = promote_eligibility(spec.id, last=last, draft=draft)
    return {
        "head": spec.id,
        "label": spec.label,
        "scheme": scheme,
        "draft": draft,
        "research": research,
        "last": last,
        "job": job,
        "fit_running": running,
        "log_tail": log_tail,
        "arms_metrics": _summary_arms(last),
        "has_draft": draft is not None,
        "has_research": research is not None,
        "promotable": bool(elig.get("promotable")),
        "promote_reasons": list(elig.get("reasons") or []),
        "parity": elig.get("parity"),
        "live_allowed": False,
    }


def desk_status() -> Dict[str, Any]:
    _ensure_dir()
    scheme = _scheme_blob()
    heads: List[Dict[str, Any]] = []
    for hid in head_ids():
        try:
            heads.append(head_status(hid))
        except Exception as exc:  # noqa: BLE001
            # 单头损坏不拖垮整页
            heads.append(
                {
                    "head": hid,
                    "label": hid,
                    "scheme": (scheme.get("per_head") or {}).get(hid) or {},
                    "draft": None,
                    "research": None,
                    "last": None,
                    "job": None,
                    "fit_running": False,
                    "log_tail": "",
                    "has_draft": False,
                    "has_research": False,
                    "promotable": False,
                    "promote_reasons": [f"{type(exc).__name__}: {exc}"],
                    "parity": None,
                    "live_allowed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    last_global = _read_json(YHAT_TF_LAST_PATH)
    return {
        "ok": True,
        "success": True,
        "layer": scheme["layer"],
        "package": "yhat_transformer",
        "scheme": scheme,
        "heads_meta": heads_meta(),
        "heads": heads,
        "last_global": last_global,
        "live_allowed": False,
        "store_dir": YHAT_TF_DIR,
    }


@_with_head_lock
def save_fit_draft(head: str, params: Dict[str, Any], *, note: str = "") -> Dict[str, Any]:
    h = _validate_head(head)
    merged = _normalize_fit_params(h, params if isinstance(params, dict) else None)
    payload = {
        "role": "yhat_tf_draft",
        "head": h,
        "params": merged,
        "note": str(note or ""),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "live_allowed": False,
    }
    _write_json(_path(h, "draft"), payload)
    _write_json(
        YHAT_TF_LAST_PATH,
        {"action": "save_draft", "head": h, "updated_at": payload["updated_at"]},
    )
    return {"ok": True, "success": True, "draft": payload}


def _merge_request_over_draft(
    base: Dict[str, Any], params: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """页面/请求参数盖草稿；页面不传的自动键按规则重对齐。

    ``mlp_hidden``：页面不暴露。若草稿仍是「对齐旧 d_model」的自动值，
    随新 d_model 重对齐；显式覆盖（≠旧 d_model）保留。
    """
    if not isinstance(params, dict):
        return dict(base)
    merged = dict(base)
    try:
        old_d = int(base.get("d_model") or 64)
    except (TypeError, ValueError):
        old_d = 64
    old_mlp = base.get("mlp_hidden")
    merged.update({k: v for k, v in params.items() if v is not None})
    if "mlp_hidden" not in params:
        auto = False
        if old_mlp is None or old_mlp == "":
            auto = True
        else:
            try:
                auto = int(old_mlp) == old_d
            except (TypeError, ValueError):
                auto = True
        if auto:
            merged.pop("mlp_hidden", None)
    return merged


@_with_head_lock
def record_holdout_intent(head: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """记录 Holdout 意图；不在 Web 内跑重训练。"""
    spec = get_head(head)
    draft = _read_json(_path(spec.id, "draft")) or {}
    base = (
        (draft.get("params") if isinstance(draft, dict) else None)
        or default_fit_params(spec.id)
    )
    merged_in = _merge_request_over_draft(
        dict(base) if isinstance(base, dict) else default_fit_params(spec.id),
        params,
    )
    p = _normalize_fit_params(spec.id, merged_in)
    cli = build_holdout_cli(spec.id, p)
    msg = f"已记录拟合意图。CLI：{cli}（不写生产 score；live_allowed=False）。"
    last = {
        "role": "yhat_tf_last",
        "head": spec.id,
        "action": "holdout_intent",
        "params": p,
        "scheme_status": spec.status,
        "cli": cli,
        "message": msg,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "live_allowed": False,
        # 新意图覆盖旧结果，信息窗不残留上一次 metrics
        "metrics": None,
        "hard_gate_vs_arm_L": None,
        "gate": None,
        "experiment_out": None,
    }
    _write_json(_path(spec.id, "last"), last)
    _write_json(
        YHAT_TF_LAST_PATH,
        {
            "action": "holdout_intent",
            "head": spec.id,
            "updated_at": last["updated_at"],
        },
    )
    # 每次拟合意图都同步草稿，避免 UI 刷新用旧 draft 盖回表单
    prev_note = ""
    if isinstance(draft, dict):
        prev_note = str(draft.get("note") or "")
    save_fit_draft(
        spec.id,
        p,
        note=prev_note or "auto from holdout intent",
    )
    return {"ok": True, "success": True, "last": last, "cli": cli, "draft_params": p}


@_with_head_lock
def promote_draft_to_research(head: str) -> Dict[str, Any]:
    h = _validate_head(head)
    draft = _read_json(_path(h, "draft"))
    last = _read_json(_path(h, "last"))
    elig = promote_eligibility(h, last=last, draft=draft)
    if not elig.get("promotable"):
        reason = "；".join(elig.get("reasons") or []) or "不满足晋级条件"
        return {
            "ok": False,
            "success": False,
            "error": f"不可晋级：{reason}",
            "eligibility": elig,
        }
    assert isinstance(draft, dict) and isinstance(last, dict)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    fit_params = (
        last.get("fit_params")
        if isinstance(last.get("fit_params"), dict)
        else last.get("params")
    )
    payload = {
        **draft,
        "role": "yhat_tf_research",
        "params": dict(fit_params) if isinstance(fit_params, dict) else draft.get("params"),
        "promoted_at": now,
        "live_allowed": False,
        "promoted_from_last": {
            "action": last.get("action"),
            "updated_at": last.get("updated_at"),
            "gate_pass": _gate_pass_from_last(last),
            "hard_gate_vs_arm_L": last.get("hard_gate_vs_arm_L"),
            "parity": last.get("parity") or elig.get("parity"),
            "eval_universe": last.get("eval_universe"),
            "experiment_out": last.get("experiment_out"),
            "fit_params": fit_params,
        },
    }
    _write_json(_path(h, "research"), payload)
    _write_json(
        YHAT_TF_LAST_PATH,
        {
            "action": "promote_research",
            "head": h,
            "updated_at": now,
        },
    )
    return {
        "ok": True,
        "success": True,
        "research": payload,
        "eligibility": elig,
    }


@_with_head_lock
def clear_slot(head: str, slot: str) -> Dict[str, Any]:
    h = _validate_head(head)
    s = str(slot or "").strip()
    if s not in ("draft", "research", "last"):
        raise ValueError("slot must be draft|research|last")
    path = _path(h, s)
    if os.path.isfile(path):
        os.remove(path)
    return {"ok": True, "success": True, "cleared": s, "head": h}


def _arm_metrics_slice(pack: Any) -> Optional[Dict[str, Any]]:
    """单臂 success pack → UI 对照切片。"""
    if not isinstance(pack, dict) or not pack.get("success"):
        return None
    out: Dict[str, Any] = {}
    for k in ("cs_rank_ic", "cs_rank_icir", "sign_hit", "cs_top_mean_y", "n", "n_test", "n_train", "n_features", "n_eval"):
        if pack.get(k) is not None:
            out[k] = pack.get(k)
    note = pack.get("note")
    if note:
        out["note"] = note
    return out or None


def _metrics_from_holdout_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """从 Holdout summary 抽 desk 扁平 metrics（供 UI OOS + L/T 对照）。"""
    gates = None
    if isinstance(summary, dict):
        gates = summary.get("hard_gate_vs_arm_L")
    if not isinstance(gates, dict):
        gates = {}

    arms: Dict[str, Any] = {}
    for key, tag in (("arm_L", "L"), ("arm_T", "T")):
        sliced = _arm_metrics_slice((summary or {}).get(key) if isinstance(summary, dict) else None)
        if sliced:
            arms[tag] = sliced
    splits: Dict[str, Any] = {}
    if isinstance(summary, dict):
        for sk, tag in (("split_L", "L"), ("split_T", "T")):
            sp = summary.get(sk)
            if not isinstance(sp, dict):
                continue
            splits[tag] = {
                k: sp.get(k)
                for k in ("n_train", "n_test", "fit_end", "eval_start")
                if sp.get(k) is not None
            }

    # 顶层指标优先 Arm·T；Gate 始终取 hard_gate.T（勿绑在「当前顶层臂」上）
    out: Dict[str, Any] = {}
    for key, tag in (("arm_T", "T"), ("arm_L", "L")):
        pack = (summary or {}).get(key) if isinstance(summary, dict) else None
        sliced = _arm_metrics_slice(pack)
        if not sliced:
            continue
        out.update(sliced)
        out["arm"] = tag
        break
    g_t = gates.get("T") if isinstance(gates.get("T"), dict) else {}
    if g_t.get("lift") is not None:
        out["gate_lift"] = g_t.get("lift")
    if g_t.get("pass") is not None:
        out["gate_pass"] = g_t.get("pass")
    # 双臂皆败时仍回写 gate，避免 last 变成空 metrics / 丢掉硬门结论
    if not out and not arms:
        if g_t.get("pass") is None and not splits:
            return {}
        out = {"arm": "T"}
        if g_t.get("pass") is not None:
            out["gate_pass"] = g_t.get("pass")
        if g_t.get("lift") is not None:
            out["gate_lift"] = g_t.get("lift")
    if arms:
        out["arms"] = arms
    if splits:
        out["splits"] = splits
    if "arm" not in out:
        out["arm"] = "T" if "T" in arms else next(iter(arms), "T")
    return out


def _failed_arm_notes(summary: Dict[str, Any]) -> List[str]:
    """记下失败臂的原因。成功臂不在这里。"""
    notes: List[str] = []
    if not isinstance(summary, dict):
        return notes
    for key in ("arm_L", "arm_T"):
        pack = summary.get(key)
        if not isinstance(pack, dict) or pack.get("success") is not False:
            continue
        err = str(pack.get("error") or "").strip()
        if err and err not in notes:
            notes.append(err)
    return notes


@_with_head_lock
def ingest_holdout_summary(
    head: str,
    summary: Dict[str, Any],
    *,
    out_path: str = "",
) -> Dict[str, Any]:
    """Holdout 跑完回写 ``{head}_last.json`` metrics（不写 live score）。"""
    h = _validate_head(head)
    if not isinstance(summary, dict):
        return {"ok": False, "success": False, "error": "summary must be dict"}
    # 防踩踏：desk job 已换新 pid（用户重跑）时，旧进程不得改 job/last/draft
    job_blob = _read_json(_job_path(h))
    my_pid = int(os.getpid())
    if isinstance(job_blob, dict):
        try:
            job_pid = int(job_blob.get("pid") or 0)
        except (TypeError, ValueError):
            job_pid = 0
        # 只拦进行中的另一轮。已结束的旧 job 留着 pid，不能挡住下一次回写。
        in_flight = bool(job_blob.get("running")) and str(
            job_blob.get("status") or ""
        ) in ("running", "starting")
        if in_flight and job_pid != my_pid:
            return {
                "ok": True,
                "success": False,
                "skipped": True,
                "error": (
                    f"skip ingest: desk job pid={job_pid} ≠ self={my_pid}"
                    "（可能已启动新拟合）"
                ),
            }
    metrics = _metrics_from_holdout_summary(summary)
    has_metrics = bool(metrics)
    arms_blob = metrics.get("arms") if isinstance(metrics, dict) else None
    has_arm_success = bool(isinstance(arms_blob, dict) and arms_blob)
    # 双臂皆败：可保留 gate metrics，但 action=holdout_failed
    result_ok = bool(has_metrics and has_arm_success)
    arm_errors = _failed_arm_notes(summary)
    prev = _read_json(_path(h, "last")) or {}
    # 优先 summary.params；否则 job（desk 形）；再 args（需映射 atc_core）；再 last
    raw_params = summary.get("params")
    if not isinstance(raw_params, dict):
        if isinstance(job_blob, dict) and isinstance(job_blob.get("params"), dict):
            raw_params = job_blob.get("params")
    if not isinstance(raw_params, dict):
        raw_params = summary.get("args") if isinstance(summary.get("args"), dict) else None
    if not isinstance(raw_params, dict):
        raw_params = prev.get("params") if isinstance(prev.get("params"), dict) else None
    params = _normalize_fit_params(h, raw_params) if raw_params is not None else None
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    eu = summary.get("eval_universe") if isinstance(summary.get("eval_universe"), dict) else {}
    # 以 desk 参数 + eval_universe 重算 parity，不盲信 summary 快照
    parity_in = compute_parity(
        h,
        params if isinstance(params, dict) else None,
        eval_shared=bool(eu.get("eval_shared")),
    )
    last = {
        **prev,
        "role": "yhat_tf_last",
        "head": h,
        "action": "holdout_result" if result_ok else "holdout_failed",
        "metrics": metrics if has_metrics else None,
        "hard_gate_vs_arm_L": summary.get("hard_gate_vs_arm_L"),
        "gate": summary.get("gate"),
        # 勿继承上次 experiment_out：失败/空 out 时否则 UI 会继续展示旧 L/T 指标
        "experiment_out": (str(out_path).strip() if out_path else None) or None,
        "eval_universe": eu or None,
        "parity": parity_in,
        "message": (
            "；".join(arm_errors)
            if arm_errors
            else (
                "Holdout 完成 · metrics 已回写（不写 live）"
                if result_ok
                else "Holdout 结束但无可用 arm metrics"
            )
        ),
        "updated_at": now,
        "live_allowed": False,
    }
    if params is not None:
        last["params"] = params
        last["fit_params"] = dict(params)
    _write_json(_path(h, "last"), last)
    # 子进程内同步 job 状态；父进程 refresh 再 waitpid 收割僵尸
    job = job_blob if isinstance(job_blob, dict) else _read_json(_job_path(h))
    if isinstance(job, dict):
        job["running"] = False
        job["finished_at"] = now
        if result_ok and not arm_errors:
            job["status"] = "done"
            job.pop("error", None)
            job["exit_hint"] = "holdout_result"
        elif result_ok and arm_errors:
            # 一臂成功一臂失败：partial，job 仍 done
            job["status"] = "done"
            job["error"] = "；".join(arm_errors)
            job["exit_hint"] = "partial"
        else:
            job["status"] = "error"
            job["error"] = (
                "；".join(arm_errors) or "Holdout 结束但无可用 arm metrics"
            )
            job["exit_hint"] = "holdout_failed"
        _write_json(_job_path(h), job)
    # 有产出时同步草稿，避免下次进页仍用旧 lookback/d_model
    if has_metrics and params is not None:
        gate_ok = _gate_pass_from_last(
            {"metrics": metrics, "hard_gate_vs_arm_L": summary.get("hard_gate_vs_arm_L")}
        ) is True
        draft = {
            "role": "yhat_tf_draft",
            "head": h,
            "params": params,
            "note": ((_read_json(_path(h, "draft")) or {}).get("note") or ""),
            "updated_at": now,
            "live_allowed": False,
            "promotable": bool(gate_ok and parity_in.get("formal") and result_ok),
            "parity": parity_in,
        }
        _write_json(_path(h, "draft"), draft)
    _write_json(
        YHAT_TF_LAST_PATH,
        {
            "action": last["action"],
            "head": h,
            "updated_at": now,
        },
    )
    return {
        "ok": True,
        "success": result_ok,
        "last": last,
        "metrics": metrics if has_metrics else {},
    }
