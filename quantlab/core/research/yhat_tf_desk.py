"""模型工厂 desk：四头槽位 IO + Holdout 任务（不写生产 score）。

``factory=L`` → /yhat-l · Arm·L；``factory=T`` → /yhat-t · Arm·T；
``factory=R`` → /yhat-r · Arm·R。
HeadSpec 真源在 ``yhat_transformer.heads``；CLI 真源 ``build_holdout_cli``。
"""

from __future__ import annotations

import fcntl
import functools
import json
import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from core.paths import ROOT_DIR
from core.paths import YHAT_L_DIR as _DEFAULT_YHAT_L_DIR
from core.paths import YHAT_L_LAST_PATH as _DEFAULT_YHAT_L_LAST
from core.paths import YHAT_R_DIR as _DEFAULT_YHAT_R_DIR
from core.paths import YHAT_R_LAST_PATH as _DEFAULT_YHAT_R_LAST
from core.paths import YHAT_T_DIR as _DEFAULT_YHAT_T_DIR
from core.paths import YHAT_T_LAST_PATH as _DEFAULT_YHAT_T_LAST
from core.research.yhat_transformer.heads import get_head, head_ids, iter_heads

logger = logging.getLogger(__name__)

# 可被单测 patch；T 侧保留 YHAT_TF_* 别名
YHAT_L_DIR = _DEFAULT_YHAT_L_DIR
YHAT_L_LAST_PATH = _DEFAULT_YHAT_L_LAST
YHAT_R_DIR = _DEFAULT_YHAT_R_DIR
YHAT_R_LAST_PATH = _DEFAULT_YHAT_R_LAST
YHAT_T_DIR = _DEFAULT_YHAT_T_DIR
YHAT_T_LAST_PATH = _DEFAULT_YHAT_T_LAST
YHAT_TF_DIR = YHAT_T_DIR
YHAT_TF_LAST_PATH = YHAT_T_LAST_PATH

_FACTORY_CTX = threading.local()


def loss_for_factory(spec: Any, factory: Optional[str] = None) -> str:
    """Arm·T 用 HeadSpec。Arm·L 回归为 l2、排序为 lambdarank。Arm·R 为 ridge。"""
    fac = normalize_factory(factory)
    if fac == "R":
        return "ridge"
    if fac != "L":
        return str(spec.loss)
    if str(getattr(spec, "task_type", "") or "") == "rank":
        return "lambdarank"
    return "l2"


def normalize_factory(factory: Optional[str] = None) -> str:
    raw = factory if factory is not None else getattr(_FACTORY_CTX, "factory", None)
    s = str(raw or "T").strip().upper()
    if s in ("L", "ARM-L", "ARM_L", "ARM·L", "LGB", "LIGHTGBM"):
        return "L"
    if s in ("R", "ARM-R", "ARM_R", "ARM·R", "RIDGE"):
        return "R"
    if s in ("T", "ARM-T", "ARM_T", "ARM·T", "TF", "TRANSFORMER"):
        return "T"
    if s in ("LT", "L+T", "BOTH"):
        return "LT"
    return "T"


def _store_dir(factory: Optional[str] = None) -> str:
    """L → YHAT_L_DIR；R → YHAT_R_DIR；T/默认 → YHAT_TF_DIR（可被 patch）。"""
    fac = normalize_factory(factory)
    if fac == "L":
        return YHAT_L_DIR
    if fac == "R":
        return YHAT_R_DIR
    return YHAT_TF_DIR


def _last_global_path(factory: Optional[str] = None) -> str:
    fac = normalize_factory(factory)
    if fac == "L":
        return YHAT_L_LAST_PATH
    if fac == "R":
        return YHAT_R_LAST_PATH
    return YHAT_TF_LAST_PATH


@contextmanager
def factory_scope(factory: str) -> Iterator[str]:
    prev = getattr(_FACTORY_CTX, "factory", None)
    cur = normalize_factory(factory)
    _FACTORY_CTX.factory = cur
    try:
        yield cur
    finally:
        _FACTORY_CTX.factory = prev


def _ensure_dir(factory: Optional[str] = None) -> None:
    os.makedirs(_store_dir(factory), exist_ok=True)


def _path(head: str, slot: str, factory: Optional[str] = None) -> str:
    return os.path.join(_store_dir(factory), f"{head}_{slot}.json")


def _job_path(head: str, factory: Optional[str] = None) -> str:
    return os.path.join(_store_dir(factory), f"{head}_job.json")


def _log_path(head: str, factory: Optional[str] = None) -> str:
    return os.path.join(_store_dir(factory), f"{head}_fit.log")


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
    markers = (
        "arml rankic",
        "l_lightgbm",
        "l_lambdarank",
        "arml 失败",
        "arml begin",
        "armr rankic",
        "r_ridge",
        "armr 失败",
        "armr begin",
    )
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


def _lock_for(key: str) -> _HeadLock:
    with _HEAD_LOCKS_GUARD:
        lk = _HEAD_LOCKS.get(key)
        if lk is None:
            lk = _HeadLock()
            _HEAD_LOCKS[key] = lk
        return lk


def _factory_from_call(*args: Any, **kwargs: Any) -> str:
    """显式 ``factory=`` 优先；否则沿用外层 ``factory_scope``，避免嵌套默认盖成 T。"""
    fac = kwargs.get("factory")
    if fac is None and args and isinstance(args[0], dict):
        fac = args[0].get("factory")
    if fac is None:
        ambient = getattr(_FACTORY_CTX, "factory", None)
        if ambient is not None:
            return normalize_factory(ambient)
    return normalize_factory(fac)


def _with_head_lock(fn):
    """串行化本头 job / last 的读改写。嵌套调用（fit → refresh）只加一次 flock。"""

    @functools.wraps(fn)
    def wrapper(head: str, *args: Any, **kwargs: Any):
        h = _validate_head(head)
        fac = _factory_from_call(*args, **kwargs)
        with factory_scope(fac):
            lk = _lock_for(f"{fac}:{h}")
            lk.acquire(_store_dir(fac), h)
            try:
                return fn(h, *args, **kwargs)
            finally:
                lk.release()

    return wrapper


def _scheme_blob(factory: Optional[str] = None) -> Dict[str, Any]:
    fac = normalize_factory(factory)
    per_head: Dict[str, Any] = {}
    for spec in iter_heads():
        if fac == "L":
            gate_label = "绝对指标 · Arm·L"
        elif fac == "R":
            gate_label = "绝对指标 · Arm·R"
        elif fac == "T":
            gate_label = "绝对指标 · Arm·T"
        else:
            gate_label = (
                "Gate lift"
                if spec.gate == "gate_lift"
                else "Arm·L"
                if spec.gate == "cs_rank_ic"
                else spec.gate
            )
        per_head[spec.id] = {
            "status": spec.status,
            "loss": loss_for_factory(spec, fac),
            "script": build_holdout_cli(spec.id, factory=fac),
            "task_type": spec.task_type,
            "gate": None if fac in ("L", "T", "R") else spec.gate,
            "gate_label": gate_label,
            "factor_pack": spec.factor_pack,
            "target": spec.target_desc,
            "note": spec.role,
        }
    if fac == "L":
        name = "模型中心 · Arm·L（LightGBM）"
        package = "yhat_l"
    elif fac == "R":
        name = "模型中心 · Arm·R（Ridge）"
        package = "yhat_r"
    elif fac == "T":
        name = "模型中心 · Arm·T（Transformer）"
        package = "yhat_t"
    else:
        name = "Scheme LT · LightGBM vs Transformer"
        package = "yhat_transformer"
    if fac == "L":
        arms = [
            {"id": "L", "title": "Arm·L", "desc": "LightGBM / LambdaRank · 全量因子"},
        ]
        gate = "单臂绝对指标；跨厂对照留给研究枢纽；不直连生产 score"
    elif fac == "R":
        arms = [
            {"id": "R", "title": "Arm·R", "desc": "Ridge · 全量因子"},
        ]
        gate = "单臂绝对指标；跨厂对照留给研究枢纽；不直连生产 score"
    elif fac == "T":
        arms = [
            {
                "id": "T",
                "title": "Arm·T",
                "alias": "双支路晚融合",
                "desc": "SeqFactorTransformer（序列塔 ∥ 因子支）",
            },
        ]
        gate = "单臂绝对指标；跨厂对照留给研究枢纽；不直连生产 score"
    else:
        arms = [
            {"id": "L", "title": "Arm·L", "desc": "现网 LightGBM · 全量（硬闸对照）"},
            {
                "id": "T",
                "title": "Arm·T",
                "alias": "双支路晚融合",
                "desc": "双支路晚融合（序列塔 ∥ 因子支；须过 Holdout 硬闸）",
            },
        ]
        gate = "Holdout CS rank IC / ICIR vs Arm·L；不直连生产 score"
    return {
        "name": name,
        "factory": fac,
        "layer": "L4 research-only",
        "package": package,
        "arms": arms,
        "gate": gate,
        "promote": (
            "页面已下线草稿/晋级；正式对照与版本绑定见枢纽；"
            "禁止 live / active（枢纽注册见 model-factory-hub）"
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
    # 两侧都 normalize，避免旧盘缺 dropout/lr 等键被误判成协议漂移
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
        "batch_size": 32,
        "epochs": 12,
        "lr": 1e-3,
        # Arm·T 相对训练段 subsample（%）；默认 10，最大 100=不截断。Arm·L 仍全样本。
        "t_sample_pct": 10.0,
        "arms": "T",
        "device": "cpu",
        "factor_pack": spec.factor_pack,
        "loss": loss_for_factory(spec),
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
    # Arm·L / Arm·R 窗长跟研究枢纽：拟合 Holdout 20；oo 600 / τc 120 / 排序 700。
    # Arm·T 仍用上面的 900/60。
    fac_def = normalize_factory()
    if fac_def in ("L", "R"):
        base["arms"] = fac_def
        base["holdout_trading_days"] = 20
        # 因子臂始终全量 aoo；full_aoo 单选只服务 Arm·T 裁列。
        if spec.id in ("oo", "oo_rank"):
            base["full_aoo"] = True
            base["factor_pack"] = "aoo_full"
        if spec.id == "oo":
            base["lookback"] = 600
        elif spec.id == "tc":
            base["lookback"] = 120
        elif spec.id == "tc_rank":
            base["lookback"] = 700
        if fac_def == "R":
            base["ridge_alpha"] = 1.0
            for key in (
                "d_model",
                "nlayers",
                "nhead",
                "mlp_hidden",
                "dropout",
                "batch_size",
                "epochs",
                "lr",
                "t_sample_pct",
                "seq_feat_set",
                "seq_len",
                "device",
            ):
                base.pop(key, None)
    elif fac_def == "LT":
        base["arms"] = "L,T"
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
        "ridge_alpha",
        "feature_set",
        "train_stage",
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
    # 0 = 执行套全样本，不抬到研究套下限 5。0 是假值，不能写进 or 默认。
    raw_hold = out.get("holdout_trading_days", 20)
    try:
        hold_orig = int(raw_hold)
    except (TypeError, ValueError):
        hold_orig = 20
    if hold_orig == 0:
        out["holdout_trading_days"] = 0
    else:
        _clamp("holdout_trading_days", 20, 5, 90, label="Holdout")
    # d_model：不硬钳上限（曾把 256→128，表现为「点拟合就变回」）
    _floor_int("d_model", 64, 16, label="d_model")
    _clamp("nlayers", 3, 1, 6, label="nlayers")
    # nhead / mlp / dropout / lr：可配置；缺省用默认（不进页面）
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
    out.pop("patience", None)
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

    # 厂按页单臂；只有显式 LT 还写双臂。历史 seeds 字段丢弃（seed 固定 42）。
    fac_now = normalize_factory()
    if fac_now == "L":
        out["arms"] = "L"
        out.pop("ridge_alpha", None)
        if spec.id in ("oo", "oo_rank"):
            # Arm·L 训练始终全量；勿把 Arm·T 的 core 选择写进 L。
            out["full_aoo"] = True
    elif fac_now == "R":
        out["arms"] = "R"
        if spec.id in ("oo", "oo_rank"):
            out["full_aoo"] = True
        from core.research.factor_ols_fit import clamp_ridge_lambda

        raw_alpha = out.get("ridge_alpha", 1.0)
        lam = clamp_ridge_lambda(raw_alpha, 1.0)
        try:
            orig_alpha = float(raw_alpha)
        except (TypeError, ValueError):
            orig_alpha = 1.0
        if orig_alpha != lam or orig_alpha != orig_alpha:
            warns.append(f"ridge_alpha: {raw_alpha}→{lam:g}")
        out["ridge_alpha"] = float(f"{lam:.6g}")
    elif fac_now == "LT":
        out["arms"] = "L,T"
        out.pop("ridge_alpha", None)
    else:
        out["arms"] = "T"
        out.pop("ridge_alpha", None)
    out.pop("seeds", None)
    device = str(out.get("device") or "cpu").strip().lower() or "cpu"
    if device not in ("cpu", "mps", "cuda"):
        warns.append(f"device: {device}→cpu")
        device = "cpu"
    out["device"] = device
    out["head"] = spec.id
    target_loss = loss_for_factory(spec)
    want_loss = str(out.get("loss") or "").strip()
    if want_loss and want_loss != target_loss:
        warns.append(f"loss: {want_loss}→{target_loss}（本头固定）")
    out["loss"] = target_loss
    # 与 Arm·T 实际列宽一致（L 仍始终全量）；勿写死 HeadSpec 的 *_full
    if spec.id in ("tc", "tc_rank"):
        out["factor_pack"] = "atc_full" if bool(out.get("full_atc", True)) else "atc_core"
    else:
        out["factor_pack"] = "aoo_full" if bool(out.get("full_aoo")) else "aoo_core"
    out["pool_n"] = int(out["pool_n"])
    out["watching_limit"] = int(out["pool_n"])
    # 因子厂不训序列塔。默认里带上的 Transformer 字段不要写进 job，避免页上看成 Arm·T。
    if normalize_factory() == "R":
        for key in (
            "d_model",
            "nlayers",
            "nhead",
            "mlp_hidden",
            "dropout",
            "batch_size",
            "epochs",
            "lr",
            "t_sample_pct",
            "seq_feat_set",
            "seq_len",
            "device",
        ):
            out.pop(key, None)
    return out, warns


_RETIRED_PARAM_WARNINGS = (
    "t_sample_pct=100 且 d_model≥256：Arm·T 易 OOM，建议降低采样%或 d_model",
)


def without_retired_param_warnings(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """已落盘的 OOM 提示不再下发。不改原对象，避免 status 回写 job。"""
    if not isinstance(job, dict):
        return job
    warns = job.get("param_warnings")
    if not isinstance(warns, list) or not warns:
        return job
    kept = [w for w in warns if str(w) not in _RETIRED_PARAM_WARNINGS]
    if len(kept) == len(warns):
        return job
    out = dict(job)
    out["param_warnings"] = kept
    return out


def _normalize_fit_params(head: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """合并默认值；``pool_n`` 同步为 ``watching_limit``；loss 按厂锁定。

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
    feature_set = ""
    stage = ""
    if incoming:
        from core.universe.feature_sets import codes_for_feature_set, normalize_feature_set

        stage = str(incoming.get("train_stage") or "").strip().lower()
        if stage not in ("pretrain", "finetune", "chain"):
            stage = ""
        feature_set = normalize_feature_set(incoming.get("feature_set"))
        # 只选沪深300是从头训练。草稿里残留的 chain / pretrain / finetune
        # 不能把截面改回大池，也不能再去找大池预训练权重。
        if normalize_factory() == "T" and feature_set == "hs300":
            stage = ""
        elif normalize_factory() == "T" and stage == "finetune":
            stage = ""
            feature_set = feature_set or "hs300"
        elif normalize_factory() == "T" and stage in ("pretrain", "chain"):
            feature_set = "pool"
    if stage and normalize_factory() == "T":
        merged["train_stage"] = stage
    elif normalize_factory() == "T":
        merged.pop("train_stage", None)
    if feature_set:
        merged["feature_set"] = feature_set
        chosen = codes_for_feature_set(feature_set, fetch=True)
        if len(chosen) >= 2:
            merged["pool_n"] = len(chosen)
            merged["watching_limit"] = len(chosen)
    merged["head"] = spec.id
    merged["loss"] = loss_for_factory(spec)
    hardened, _warns = harden_fit_params(spec.id, merged)
    return hardened


def build_holdout_argv(
    head: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    factory: Optional[str] = None,
) -> List[str]:
    """Holdout 脚本 argv（相对 ``ROOT_DIR`` 的脚本路径段除外，含绝对脚本路径）。"""
    spec = get_head(head)
    fac = normalize_factory(factory)
    with factory_scope(fac):
        p = _normalize_fit_params(spec.id, params)
    script = os.path.join(ROOT_DIR, "scripts", "run_yhat_transformer_holdout.py")
    argv = [
        script,
        "--head",
        spec.id,
        "--factory",
        fac,
        "--watching-limit",
        str(int(p["watching_limit"])),
        "--lookback",
        str(int(p["lookback"])),
        "--holdout-trading-days",
        str(int(p["holdout_trading_days"])),
        "--update-desk",
    ]
    feature_set = str(p.get("feature_set") or "").strip()
    if feature_set:
        from core.universe.feature_sets import codes_for_feature_set

        chosen = codes_for_feature_set(feature_set, fetch=False)
        if len(chosen) >= 2:
            argv.extend(["--codes", ",".join(chosen)])
    stage = str(p.get("train_stage") or "")
    if fac == "T" and stage == "chain":
        argv.append("--chain-finetune")
    elif fac == "T" and stage == "finetune":
        argv.append("--finetune")
    # Arm·T / LT 才拼 Transformer 超参；L 仅因子臂
    if fac in ("T", "LT"):
        argv.extend(
            [
                "--d-model",
                str(int(p["d_model"])),
                "--nhead",
                str(int(p["nhead"])),
                "--nlayers",
                str(int(p["nlayers"])),
                "--mlp-hidden",
                str(
                    int(
                        p.get("mlp_hidden")
                        if p.get("mlp_hidden") is not None
                        else p["d_model"]
                    )
                ),
                "--dropout",
                f"{float(p.get('dropout') if p.get('dropout') is not None else 0.1):.6g}",
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
            ]
        )
        lr = float(p.get("lr") or 1e-3)
        argv.extend(["--lr", f"{lr:.6g}"])
        try:
            pct = float(
                p.get("t_sample_pct") if p.get("t_sample_pct") is not None else 10.0
            )
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
    if fac == "R":
        argv.extend(["--ridge-alpha", f"{float(p.get('ridge_alpha') or 1.0):.6g}"])
    return argv


def build_holdout_cli(
    head: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    factory: Optional[str] = None,
) -> str:
    """按 desk 参数拼可复制 CLI（显式传参，覆盖脚本默认）。"""
    argv = build_holdout_argv(head, params, factory=factory)
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


def _oom_retry_hint(head: str) -> str:
    """8GB 机的重试建议。τc 只在 τc / τc_rank 上出现。"""
    if str(head or "") in ("tc", "tc_rank"):
        return "8GB 机建议 d_model≤64；τc 长窗可下调 lookback / Holdout / 池"
    return "8GB 机建议 d_model≤64，或下调 lookback / 池 / Arm·T 采样%"


def _fit_exit_error_from_log(head: str) -> str:
    """进程无 metrics 退出时，据日志末尾推断更可读原因。"""
    tail = _log_tail(head, n_lines=24)
    low = (tail or "").lower()
    if "holdout 收到" in low and "中途退出" in low:
        return "拟合进程被终止（收到 SIGTERM/SIGINT）；未回写 metrics，请重新拟合"
    hint_8g = _oom_retry_hint(head)
    wide = (
        "τc 因子列更宽"
        if str(head or "") in ("tc", "tc_rank")
        else "因子列较宽"
    )
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
    # live-fit start 也算已进入 LGB（勿只认 "ArmL begin" 字样）
    arm_l_begin = ("arml begin" in low) or ("live-fit start" in low)
    arm_l_done = ("arml rankic" in low) or ("arml 失败" in low)
    arm_l_step = "arml step=" in low
    # 入模列还没抽完就消失：不是 LightGBM 轮次里被杀。
    # columns start 之后、columns F= 之前退出，停在列过滤。
    tc_columns_begun = "arml τc columns start" in low
    tc_columns_ready = "arml τc columns f=" in low
    if tc_columns_begun and not tc_columns_ready and not arm_l_step and not arm_l_done:
        return (
            "拟合进程在 τc 截面列准备中退出（尚未进入 LightGBM）。"
            f"请降低 lookback / 池大小后重试（{hint_8g}）"
        )
    if arm_l_split and not arm_l_begin and not arm_l_done:
        return (
            "拟合进程在 Arm·L 切分后退出（未进入 LGB）。"
            f"请降低 lookback / 池大小后重试（{hint_8g}）"
        )
    if arm_l_begin and not arm_l_done:
        return (
            f"拟合进程在 Arm·L LightGBM 训练中被系统终止（疑似内存不足；{wide}）。"
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


def _archive_terminal_run(
    head: str,
    job: Optional[Dict[str, Any]],
    last: Optional[Dict[str, Any]],
    status: str,
) -> Optional[str]:
    """终态跑次进工厂版本库。归档失败不打断拟合回写。"""
    try:
        from core.research.yhat_factory_versions import archive_factory_run

        return archive_factory_run(
            head,
            job=job if isinstance(job, dict) else None,
            last=last if isinstance(last, dict) else None,
            status=status,
        )
    except Exception:
        logger.exception("yhat factory version archive failed head=%s", head)
        return None


@_with_head_lock
def refresh_fit_job(
    head: str, *, factory: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """读/刷新本头拟合任务；进程退出则标 done/error。"""
    _ = factory  # 锁装饰器已 factory_scope
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
            _archive_terminal_run(
                h,
                job,
                {
                    "action": "holdout_failed",
                    "head": h,
                    "message": job.get("error"),
                    "params": job.get("params"),
                    "updated_at": job.get("finished_at"),
                },
                "error",
            )
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
                _archive_terminal_run(h, job, failed, "error")
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
        # 进程已死时：日志尚在更新 / 刚启动 → 宽限防抖动；否则一次钉死
        # （_pid_alive_confirmed 已多探；无需再等 UI 连刷 miss）
        soft_grace = (
            log_age < _FIT_LOG_STALE_SEC
            or started_age < _FIT_START_GRACE_SEC
        )
        if not alive:
            if soft_grace:
                miss = int(job.get("alive_miss") or 0) + 1
                job["alive_miss"] = miss
                if miss < _FIT_ALIVE_MISS_MAX:
                    job["running"] = True
                    job["status"] = "running"
                    _write_json(_job_path(h), job)
                    return job
            # 日志已过期且进程不在：立即标失败（含 UI 未开、无人轮询的孤儿 job）
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
            _archive_terminal_run(h, job, failed, "error")
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
            _archive_terminal_run(h, job, failed, "error")
            if err != job.get("error"):
                job["error"] = err
                _write_json(_job_path(h), job)
    return job


def reap_orphaned_fit_jobs(
    *, factories: Optional[List[str]] = None
) -> Dict[str, Any]:
    """启动/运维时扫 L/T 各头：死进程 + 过期 running job 立刻结算。

    不依赖页面轮询；与 ``adapters.market.ak_worker.reap_orphaned_pool_workers`` 同级。
    """
    facs = factories or ["L", "T", "R"]
    touched: List[Dict[str, Any]] = []
    for fac in facs:
        fac_n = normalize_factory(fac)
        if fac_n not in ("L", "T", "R"):
            continue
        with factory_scope(fac_n):
            for hid in head_ids():
                try:
                    before = _read_json(_job_path(hid)) or {}
                    job = refresh_fit_job(hid, factory=fac_n)
                except Exception as exc:  # noqa: BLE001
                    touched.append(
                        {
                            "factory": fac_n,
                            "head": hid,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                if not isinstance(job, dict):
                    continue
                was_running = bool(before.get("running")) and str(
                    before.get("status") or ""
                ) in ("running", "starting")
                now_terminal = str(job.get("status") or "") in ("error", "done")
                if was_running and now_terminal:
                    touched.append(
                        {
                            "factory": fac_n,
                            "head": hid,
                            "status": job.get("status"),
                            "exit_hint": job.get("exit_hint"),
                            "error": job.get("error"),
                        }
                    )
    return {"ok": True, "success": True, "reaped": touched}


@_with_head_lock
def seal_chain_pretrain(
    head: str,
    *,
    run_id: str,
    factory: Optional[str] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> bool:
    """两套都选时，把刚训完的大池权重收成预训练版本，任务继续跑微调。"""
    _ = factory
    h = _validate_head(head)
    from core.research.yhat_factory_weights import _read_meta, pending_weights_dir

    meta = _read_meta(pending_weights_dir(h))
    if not meta or str(meta.get("run_id") or "") != str(run_id or ""):
        return False
    job = _read_json(_job_path(h))
    pre_job = dict(job) if isinstance(job, dict) else {}
    params = dict(pre_job.get("params") or {})
    params["train_stage"] = "pretrain"
    params["feature_set"] = "pool"
    pre_job["params"] = params
    pre_job["run_id"] = str(run_id)
    try:
        pre_job["pid"] = int(meta.get("pid") or os.getpid())
    except (TypeError, ValueError):
        pre_job["pid"] = os.getpid()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    last = {
        "action": "holdout_result",
        "head": h,
        "message": "大池预训练完成",
        "params": params,
        "fit_params": params,
        "updated_at": now,
        "live_allowed": False,
    }
    if isinstance(summary, dict):
        metrics = _metrics_from_holdout_summary(summary)
        if metrics:
            last["metrics"] = metrics
        for key in ("parity", "hard_gate_vs_arm_L", "gate", "eval_universe"):
            if summary.get(key) is not None:
                last[key] = summary.get(key)
    from core.research.yhat_factory_versions import archive_factory_run

    vid = archive_factory_run(h, job=pre_job, last=last, status="done")
    return bool(vid)


@_with_head_lock
def start_holdout_fit(
    head: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    factory: Optional[str] = None,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    """后台启动 Holdout 拟合（不写生产 score；工作配置随意图隐式同步）。"""
    _ = factory  # 锁装饰器已 factory_scope
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
    try:
        from core.research.yhat_factory_weights import clear_pending_weights

        clear_pending_weights(spec.id)
    except Exception:
        logger.exception("clear pending factory weights failed head=%s", spec.id)
    log_path = _log_path(spec.id)
    job_path = _job_path(spec.id)

    intent = record_holdout_intent(spec.id, params)
    p = (intent.get("last") or {}).get("params") or _normalize_fit_params(spec.id, params)
    _, clamp_warns = harden_fit_params(spec.id, dict(p))
    cli = intent.get("cli") or build_holdout_cli(spec.id, p)
    argv = build_holdout_argv(spec.id, p)
    if p.get("feature_set") and "--codes" not in argv:
        return {
            "ok": False,
            "success": False,
            "error": "截面名单未就绪，无法按该截面拟合",
        }
    if str(p.get("train_stage") or "") == "finetune":
        from core.research.yhat_factory_versions import latest_transformer_pretrain

        pre = latest_transformer_pretrain(spec.id)
        if not pre.get("success"):
            return {
                "ok": False,
                "success": False,
                "error": str(pre.get("error") or "还没有大池预训练权重"),
            }
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    run_id = f"{spec.id}-{started_at.replace(':', '').replace('-', '')}"
    suite_role = str(suite or "").strip().lower()
    if suite_role not in ("live", "research"):
        suite_role = ""
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
        "suite": suite_role or None,
        "live_allowed": False,
    }
    _write_json(job_path, starting_job)
    # BLAS / OpenMP / LightGBM 保持 1，避免 Arm·L 线程风暴。
    # Arm·T 只加计算线程；父进程里已有的 OMP_NUM_THREADS 也要收成 1。
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONUNBUFFERED", "1")
    for k in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "HORIZON_LGB_NUM_THREADS",
    ):
        child_env[k] = "1"
    child_env["TORCH_NUM_THREADS"] = "4"
    child_env["YHAT_FACTORY_RUN_ID"] = run_id
    if p.get("feature_set"):
        child_env["QUANTLAB_FEATURE_SET"] = str(p.get("feature_set"))
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
            "suite": suite_role or None,
            "error": err,
            "exit_hint": "early_exit",
            "exit_status": early_reap,
            "live_allowed": False,
        }
        _write_json(_job_path(spec.id), failed_job)
        prev_last = _read_json(_path(spec.id, "last")) or {}
        failed_last = {
            **prev_last,
            "role": "yhat_tf_last",
            "head": spec.id,
            "action": "holdout_failed",
            "metrics": None,
            "hard_gate_vs_arm_L": None,
            "gate": None,
            "parity": None,
            "eval_universe": None,
            "message": err,
            "params": p,
            "fit_params": dict(p) if isinstance(p, dict) else None,
            "updated_at": finished,
            "live_allowed": False,
        }
        _write_json(_path(spec.id, "last"), failed_last)
        _archive_terminal_run(spec.id, failed_job, failed_last, "error")
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
        "suite": suite_role or None,
        "live_allowed": False,
    }
    _write_json(_job_path(spec.id), job)
    _write_json(
        _last_global_path(),
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
def mark_fit_failed(
    head: str, error: str, *, factory: Optional[str] = None
) -> Dict[str, Any]:
    """子进程异常退出时回写 job/last，避免 UI 只见「未回写 metrics」。"""
    _ = factory
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
    version_id = _archive_terminal_run(h, job, last, "error")
    return {"ok": True, "success": True, "job": job, "last": last, "version_id": version_id}


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
                "ridge_alpha",
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


_ARM_SUMMARY_KEYS = {"L": "arm_L", "T": "arm_T", "R": "arm_R"}


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
                "cs_ic",
                "cs_icir",
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
        for tag in ("L", "T", "R"):
            pack = _pack_from_row(arms.get(tag))
            if pack:
                out[tag] = pack
    return out


def _tail_file(path: str, *, lines: int = 200) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    parts = text.splitlines()
    if len(parts) <= int(lines):
        return text
    return "\n".join(parts[-int(lines) :])


def _view_after_deleted_failure(
    head: str,
    job: Optional[Dict[str, Any]],
    last: Optional[Dict[str, Any]],
) -> Optional[tuple]:
    """终态跑次已不在版本列表时，概览和拟合日志改看列表里还在的最新一条。

    成功和失败都一样：删掉后不再用磁盘上的 last / job / fit.log。
    返回 ``(job, last, log_tail)``。列表里还有版本则用最新一条；一条都不剩则三者为空。
    返回 None：这次跑次还在列表里，或这头还没有版本索引（归档没写成，磁盘上的上次拟合仍要显示）。
    不改磁盘上的 last / job。进行中的拟合不走这里。
    """
    from core.research.yhat_factory_versions import (
        _archive_id,
        _index_path,
        _log_copy_path,
        _read_index,
        _read_json as _read_version,
        _snap_path,
    )

    index_path = _index_path(head)
    if not os.path.isfile(index_path):
        return None
    vid = _archive_id(
        head,
        job if isinstance(job, dict) else None,
        last if isinstance(last, dict) else None,
    )
    items = _read_index(head)
    if vid and any(str(it.get("id") or "") == vid for it in items):
        return None
    snap = None
    for it in items:
        doc = _read_version(_snap_path(head, str(it.get("id") or "")))
        if isinstance(doc, dict):
            snap = doc
            break
    if snap is None:
        return None, None, ""
    action = str(snap.get("action") or "")
    if not action:
        action = (
            "holdout_failed" if str(snap.get("status") or "") == "error" else "holdout_result"
        )
    params = snap.get("fit_params") if isinstance(snap.get("fit_params"), dict) else None
    finished = str(snap.get("finished_at") or snap.get("started_at") or "") or None
    shown_last = _coerce_slot_params(
        head,
        {
            "role": "yhat_tf_last",
            "head": head,
            "action": action,
            "metrics": snap.get("metrics"),
            "params": params,
            "fit_params": params,
            "message": snap.get("message") or "",
            "updated_at": finished,
            "cli": snap.get("cli"),
            "hard_gate_vs_arm_L": snap.get("hard_gate_vs_arm_L"),
            "gate": snap.get("gate"),
            "parity": snap.get("parity"),
            "eval_universe": snap.get("eval_universe"),
            "live_allowed": False,
        },
        persist_path="",
    )
    status = str(snap.get("status") or "")
    shown_job = {
        "role": "yhat_tf_job",
        "head": head,
        "run_id": str(snap.get("source_run_id") or snap.get("run_id") or ""),
        "status": "error" if status == "error" else "done",
        "running": False,
        "params": params,
        "cli": snap.get("cli") or "",
        "started_at": snap.get("started_at"),
        "finished_at": snap.get("finished_at"),
        "live_allowed": False,
    }
    if status == "error":
        shown_job["error"] = str(snap.get("message") or "")
    elif status == "partial":
        shown_job["error"] = str(snap.get("message") or "")
        shown_job["exit_hint"] = "partial"
    log_tail = _tail_file(_log_copy_path(head, str(snap.get("run_id") or "")))
    return shown_job, shown_last, log_tail


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
    elig = promote_eligibility(spec.id, last=last, draft=draft)
    # 删掉的终态跑次不再当作上次拟合，日志也改看还在列表里的版本。晋级仍看磁盘上的 last。
    view_job, view_last, view_log = job, last, None
    if not running:
        replaced = _view_after_deleted_failure(spec.id, job, last)
        if replaced is not None:
            view_job, view_last, view_log = replaced
    if view_log is None:
        view_action = (view_last or {}).get("action") if isinstance(view_last, dict) else None
        log_tail = (
            _log_for_status(spec.id)
            if (
                running
                or view_job
                or view_action in ("holdout_result", "holdout_failed")
            )
            else ""
        )
    else:
        log_tail = view_log
    return {
        "head": spec.id,
        "label": spec.label,
        "scheme": scheme,
        "draft": draft,
        "research": research,
        "last": view_last,
        "job": without_retired_param_warnings(view_job) if isinstance(view_job, dict) else view_job,
        "fit_running": running,
        "log_tail": log_tail,
        "arms_metrics": _summary_arms(view_last),
        "has_draft": draft is not None,
        "has_research": research is not None,
        "promotable": bool(elig.get("promotable")),
        "promote_reasons": list(elig.get("reasons") or []),
        "parity": elig.get("parity"),
        "live_allowed": False,
    }


def desk_status(*, factory: Optional[str] = None) -> Dict[str, Any]:
    fac = normalize_factory(factory)
    with factory_scope(fac):
        _ensure_dir()
        scheme = _scheme_blob(fac)
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
        last_global = _read_json(_last_global_path())
        return {
            "ok": True,
            "success": True,
            "factory": fac,
            "layer": scheme["layer"],
            "package": scheme.get("package") or "yhat_transformer",
            "scheme": scheme,
            "heads_meta": heads_meta(),
            "heads": heads,
            "last_global": last_global,
            "live_allowed": False,
            "store_dir": _store_dir(),
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
        _last_global_path(),
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
        _last_global_path(),
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
        _last_global_path(),
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
    for k in ("cs_rank_ic", "cs_ic", "cs_icir", "cs_rank_icir", "sign_hit", "cs_top_mean_y", "n", "n_test", "n_train", "n_features", "n_eval"):
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
    for key, tag in (("arm_L", "L"), ("arm_T", "T"), ("arm_R", "R")):
        sliced = _arm_metrics_slice((summary or {}).get(key) if isinstance(summary, dict) else None)
        if sliced:
            arms[tag] = sliced
    splits: Dict[str, Any] = {}
    if isinstance(summary, dict):
        for sk, tag in (("split_L", "L"), ("split_T", "T"), ("split_R", "R")):
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
    for key, tag in (("arm_T", "T"), ("arm_L", "L"), ("arm_R", "R")):
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
    # 双臂皆败时仍回写 gate，避免 last 变成空 metrics / 丢掉硬门结论。
    # 单臂失败不要一律标成 T。
    if not out and not arms:
        if g_t.get("pass") is None and not splits:
            return {}
        out = {"arm": _summary_arm_tag(summary, splits, arms)}
        if g_t.get("pass") is not None:
            out["gate_pass"] = g_t.get("pass")
        if g_t.get("lift") is not None:
            out["gate_lift"] = g_t.get("lift")
    if arms:
        out["arms"] = arms
    if splits:
        out["splits"] = splits
    if "arm" not in out:
        out["arm"] = _summary_arm_tag(summary, splits, arms)
    return out


def _summary_arm_tag(
    summary: Any,
    splits: Dict[str, Any],
    arms: Dict[str, Any],
) -> str:
    fac = str((summary or {}).get("factory") or "").strip().upper() if isinstance(summary, dict) else ""
    if fac in ("L", "T", "R"):
        return fac
    if "T" in arms or "T" in splits:
        return "T"
    if arms:
        return next(iter(arms))
    if splits:
        return next(iter(splits))
    return "T"


def _failed_arm_notes(summary: Dict[str, Any]) -> List[str]:
    """记下失败臂的原因。成功臂不在这里。"""
    notes: List[str] = []
    if not isinstance(summary, dict):
        return notes
    for key in ("arm_L", "arm_T", "arm_R"):
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
    factory: Optional[str] = None,
) -> Dict[str, Any]:
    """Holdout 跑完回写 ``{head}_last.json`` metrics（不写 live score）。"""
    _ = factory  # 锁装饰器从 kwargs / summary.factory 取并 factory_scope
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
        _last_global_path(),
        {
            "action": last["action"],
            "head": h,
            "updated_at": now,
        },
    )
    if result_ok and not arm_errors:
        vstatus = "done"
    elif result_ok:
        vstatus = "partial"
    else:
        vstatus = "error"
    version_id = _archive_terminal_run(
        h, job if isinstance(job, dict) else None, last, vstatus
    )
    return {
        "ok": True,
        "success": result_ok,
        "last": last,
        "metrics": metrics if has_metrics else {},
        "version_id": version_id,
    }
