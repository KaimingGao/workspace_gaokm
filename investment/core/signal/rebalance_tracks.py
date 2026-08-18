"""调仓双轨：predicted ŷ% + heuristic 0–100，并按 OOS 是否通过分流。

策略（``cluster_scoring.rebalance_tracks.oos_fail_policy``）：
- ``exclude``（现行默认 / 推荐）：OOS 失败组 **禁止新买入**；已持仓优先用
  全局/生产 ŷ 的 predicted hold 门槛；仅无 ŷ 的 heuristic 轨才用
  heuristic_hold_floor（H > 阈值保持，否则卖出）
- ``heuristic_sleeve`` / ``predicted_degrade``：历史别名，行为已与 ``exclude`` 对齐
  （不再进买簿 / 袖仓加仓）

买卖门槛分轨，禁止 0–100 与 ŷ% 混比。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

TRACK_PREDICTED = "predicted"
TRACK_HEURISTIC = "heuristic"

OOS_PASS = "pass"
OOS_FAIL = "fail"
OOS_UNKNOWN = "unknown"

POLICY_EXCLUDE = "exclude"
POLICY_HEURISTIC_SLEEVE = "heuristic_sleeve"
POLICY_PREDICTED_DEGRADE = "predicted_degrade"

_DEFAULT_TRACKS: Dict[str, Any] = {
    "enabled": True,
    "oos_fail_policy": POLICY_EXCLUDE,
    "predicted_buy_floor": None,  # → scoring.min_predicted_score
    "predicted_hold_floor": None,  # → scoring.min_hold_predicted_score
    "heuristic_buy_floor": 55.0,  # 保留字段；OOS 失败不再走买入
    "heuristic_hold_floor": 45.0,  # OOS 失败持仓：H > 该值才保持
    "heuristic_sleeve_max": 0,
    "heuristic_apply_tau_gate": False,
}


def get_rebalance_tracks_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    """读双轨调仓配置（缺省安全：OOS 失败仍 exclude）。"""
    try:
        from core.signal.config import load_signal_config

        cfg = config if isinstance(config, dict) else load_signal_config()
    except Exception:
        cfg = config if isinstance(config, dict) else {}
    cs = (cfg or {}).get("cluster_scoring") if isinstance(cfg, dict) else {}
    if not isinstance(cs, dict):
        cs = {}
    raw = cs.get("rebalance_tracks") if isinstance(cs.get("rebalance_tracks"), dict) else {}
    out = dict(_DEFAULT_TRACKS)
    out.update({k: v for k, v in raw.items() if v is not None or k in raw})
    pol = str(out.get("oos_fail_policy") or POLICY_EXCLUDE).strip().lower()
    if pol in ("sleeve", "heuristic"):
        pol = POLICY_HEURISTIC_SLEEVE
    if pol in ("degrade", "global", "predicted"):
        pol = POLICY_PREDICTED_DEGRADE
    if pol not in (POLICY_EXCLUDE, POLICY_HEURISTIC_SLEEVE, POLICY_PREDICTED_DEGRADE):
        pol = POLICY_EXCLUDE
    out["oos_fail_policy"] = pol
    out["enabled"] = bool(out.get("enabled", True))
    try:
        out["heuristic_buy_floor"] = float(out.get("heuristic_buy_floor", 55.0))
    except (TypeError, ValueError):
        out["heuristic_buy_floor"] = 55.0
    try:
        out["heuristic_hold_floor"] = float(out.get("heuristic_hold_floor", 45.0))
    except (TypeError, ValueError):
        out["heuristic_hold_floor"] = 45.0
    try:
        out["heuristic_sleeve_max"] = max(0, min(int(out.get("heuristic_sleeve_max") or 0), 40))
    except (TypeError, ValueError):
        out["heuristic_sleeve_max"] = 8
    out["heuristic_apply_tau_gate"] = bool(out.get("heuristic_apply_tau_gate", False))
    return out


def resolve_oos_status(
    item: Optional[dict],
    *,
    oos_failed_labels: Optional[set] = None,
) -> str:
    if not isinstance(item, dict):
        return OOS_UNKNOWN
    src = str(item.get("return_model_source") or "")
    if src.startswith("oos_failed"):
        return OOS_FAIL
    if item.get("oos_blocked") or item.get("oos_status") == OOS_FAIL:
        return OOS_FAIL
    lab = str(item.get("cluster_label") or "").strip()
    if oos_failed_labels is not None and lab and lab in oos_failed_labels:
        return OOS_FAIL
    if lab:
        try:
            from core.signal.cluster_oos_labels import is_oos_failed_cluster_label

            if is_oos_failed_cluster_label(lab):
                return OOS_FAIL
        except Exception:
            pass
    if src in ("cluster_group_beta", "global", "cluster_shadow_fallback"):
        return OOS_PASS
    return OOS_UNKNOWN


def heuristic_score_value(item: Optional[dict]) -> Optional[float]:
    """读取 0–100 heuristic；不与 ŷ% ``score`` 混用。

    优先 ``heuristic_score``；仅当旧脏行把 0–100 写进 ``score``（|v|>20）时回退。
    """
    if not isinstance(item, dict):
        return None
    for key in ("heuristic_score",):
        v = item.get(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    # 旧簿：score 曾被写成 heuristic
    v = item.get("score")
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if abs(f) > 20:
        return f
    return None


def table_yhat_score_value(item: Optional[dict]) -> Optional[float]:
    """表列 / 簿 ``score`` 应用的 ŷ%：组 → 全局 → 已是 ŷ 量级的 score。"""
    if not isinstance(item, dict):
        return None
    for key in ("score_cluster", "score_global", "predicted_score_blend", "predicted_score"):
        v = item.get(key)
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if abs(f) <= 20:
            return f
    v = item.get("score")
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if abs(f) <= 20:
        return f
    return None


def resolve_score_track(item: Optional[dict]) -> str:
    """行上的调仓轨：显式 ``score_track`` 优先，否则按 return_model_source / score_scale。"""
    if not isinstance(item, dict):
        return TRACK_PREDICTED
    tagged = str(item.get("score_track") or "").strip().lower()
    if tagged in (TRACK_PREDICTED, TRACK_HEURISTIC):
        return tagged
    try:
        from core.signal.dual_score import is_heuristic_score_scale

        if is_heuristic_score_scale(item):
            return TRACK_HEURISTIC
    except Exception:
        if str(item.get("return_model_source") or "") == "oos_failed_heuristic":
            return TRACK_HEURISTIC
        if str(item.get("score_scale") or "") == "heuristic_0_100":
            return TRACK_HEURISTIC
    return TRACK_PREDICTED


def tag_item_tracks(
    item: dict,
    *,
    oos_failed_labels: Optional[set] = None,
    force_track: Optional[str] = None,
) -> dict:
    """就地写入 ``score_track`` / ``oos_status``。"""
    if force_track in (TRACK_PREDICTED, TRACK_HEURISTIC):
        item["score_track"] = force_track
    else:
        item["score_track"] = resolve_score_track(item)
    item["oos_status"] = resolve_oos_status(item, oos_failed_labels=oos_failed_labels)
    return item


def predicted_floors(tracks_cfg: Optional[dict] = None) -> Tuple[float, float]:
    from core.signal.score_display import resolve_buy_floor, resolve_hold_floor

    cfg = tracks_cfg or get_rebalance_tracks_cfg()
    buy = cfg.get("predicted_buy_floor")
    hold = cfg.get("predicted_hold_floor")
    buy_f = resolve_buy_floor(explicit=buy if buy is not None else None)
    hold_f = (
        float(hold)
        if hold is not None and hold != ""
        else float(resolve_hold_floor())
    )
    buy_f = float(buy_f)
    hold_f = float(hold_f)
    # 滞回：持有门槛不得高于买入门槛
    if hold_f > buy_f:
        hold_f = buy_f
    return buy_f, hold_f


def heuristic_floors(tracks_cfg: Optional[dict] = None) -> Tuple[float, float]:
    cfg = tracks_cfg or get_rebalance_tracks_cfg()
    try:
        buy = float(cfg.get("heuristic_buy_floor", 55.0))
    except (TypeError, ValueError):
        buy = 55.0
    try:
        hold = float(cfg.get("heuristic_hold_floor", 45.0))
    except (TypeError, ValueError):
        hold = 45.0
    if hold > buy:
        hold = buy
    return buy, hold


def buy_gate_for_item(
    item: Optional[dict],
    *,
    tracks_cfg: Optional[dict] = None,
    predicted_buy_floor: Optional[float] = None,
) -> Tuple[bool, Optional[float], str, Optional[str]]:
    """返回 (ok, gate_score, track, skip_reason)。"""
    if not isinstance(item, dict):
        return False, None, TRACK_PREDICTED, "empty_item"
    cfg = tracks_cfg or get_rebalance_tracks_cfg()
    # 保守：OOS 失败组一律禁止新买入（不论 heuristic 多高）
    if resolve_oos_status(item) == OOS_FAIL or bool(item.get("oos_sleeve")):
        hs = heuristic_score_value(item)
        return False, hs, TRACK_HEURISTIC, "oos_failed_no_buy"
    track = resolve_score_track(item)
    if track == TRACK_HEURISTIC:
        # 非 OOS 失败的 heuristic 轨（极少）；仍禁止当买入主路径误用
        buy_f, _ = heuristic_floors(cfg)
        gate = heuristic_score_value(item)
        if gate is None:
            return False, None, track, "missing_heuristic_score"
        if gate < buy_f:
            return False, gate, track, f"heuristic<{buy_f}"
        return True, gate, track, None

    buy_f = (
        float(predicted_buy_floor)
        if predicted_buy_floor is not None
        else predicted_floors(cfg)[0]
    )
    try:
        from core.signal.dual_score import eod_gate_score_for_item

        gate = eod_gate_score_for_item(item)
    except Exception:
        gate = item.get("predicted_score")
    try:
        gate_f = float(gate) if gate is not None and gate != "" else None
    except (TypeError, ValueError):
        gate_f = None
    if gate_f is None:
        return False, None, track, "missing_predicted_eod"
    if gate_f < buy_f:
        return False, gate_f, track, f"ŷ_EOD<{buy_f}"
    return True, gate_f, track, None


def _oos_predicted_hold_score(item: Optional[dict]) -> Optional[float]:
    """OOS 失败组若仍有生产 ŷ（全局降级），用 ŷ% 做留/卖，避免与 heuristic 45 混比。

    ``oos_failed_heuristic``（无 ŷ）返回 None，调用方回退 heuristic 门槛。
    """
    if not isinstance(item, dict):
        return None
    src = str(item.get("return_model_source") or "")
    if src == "oos_failed_heuristic":
        return None
    try:
        from core.signal.dual_score import (
            decision_score_for_item,
            eod_gate_score_for_item,
            is_heuristic_score_scale,
        )

        if src != "oos_failed_global" and is_heuristic_score_scale(item):
            if item.get("predicted_score") is None and item.get("predicted_score_eod") is None:
                return None
        sc = decision_score_for_item(item)
        if sc is None:
            sc = eod_gate_score_for_item(item)
        if sc is None:
            ps = item.get("predicted_score")
            sc = float(ps) if ps is not None and ps != "" else None
        if sc is None:
            return None
        sc_f = float(sc)
        # 0–100 启发式不得当 ŷ hold
        if abs(sc_f) > 20:
            return None
        return sc_f
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


def hold_decision_for_item(
    item: Optional[dict],
    *,
    tracks_cfg: Optional[dict] = None,
    predicted_hold_floor: Optional[float] = None,
) -> Tuple[bool, Optional[float], float, str]:
    """是否因分数触发卖出。返回 (should_sell, decision_score, hold_floor, track)。

    ``should_sell``：有分且低于该轨 hold 门槛。
    OOS 失败持仓：优先用全局/生产 ŷ（与正常组同一 predicted hold 门槛）；
    仅 ``oos_failed_heuristic``（无 ŷ）才用 heuristic_hold_floor
    （H > 阈值保持，无 heuristic 亦卖，保守）。
    """
    cfg = tracks_cfg or get_rebalance_tracks_cfg()
    oos_fail = False
    if isinstance(item, dict):
        oos_fail = (
            resolve_oos_status(item) == OOS_FAIL
            or bool(item.get("oos_sleeve"))
            or bool(item.get("oos_blocked"))
        )
    if oos_fail:
        yhat = _oos_predicted_hold_score(item)
        if yhat is not None:
            hold_f = (
                float(predicted_hold_floor)
                if predicted_hold_floor is not None
                else predicted_floors(cfg)[1]
            )
            return bool(float(yhat) < float(hold_f)), float(yhat), hold_f, TRACK_PREDICTED
        _, hold_f = heuristic_floors(cfg)
        sc = heuristic_score_value(item) if isinstance(item, dict) else None
        if sc is None:
            return True, None, hold_f, TRACK_HEURISTIC
        # 用户口径：H > 阈值保持；否则卖
        return bool(float(sc) <= float(hold_f)), float(sc), hold_f, TRACK_HEURISTIC

    track = resolve_score_track(item) if isinstance(item, dict) else TRACK_PREDICTED
    if track == TRACK_HEURISTIC:
        _, hold_f = heuristic_floors(cfg)
        sc = heuristic_score_value(item)
        if sc is None:
            return False, None, hold_f, track
        # 与 OOS 失败分支同一口径：H > 阈值保持；否则卖（含等于阈值）
        return bool(float(sc) <= float(hold_f)), sc, hold_f, track

    hold_f = (
        float(predicted_hold_floor)
        if predicted_hold_floor is not None
        else predicted_floors(cfg)[1]
    )
    sc = None
    if isinstance(item, dict):
        try:
            from core.signal.dual_score import decision_score_for_item, is_heuristic_score_scale

            if is_heuristic_score_scale(item):
                # 不应落入 predicted 轨；兜底不卖（避免 50 比 ŷ 门槛）
                return False, None, hold_f, TRACK_HEURISTIC
            sc = decision_score_for_item(item)
            if sc is not None:
                sc = float(sc)
        except Exception:
            sc = None
        if sc is None:
            try:
                ps = item.get("predicted_score_blend")
                if ps is None:
                    ps = item.get("predicted_score")
                sc = float(ps) if ps is not None and ps != "" else None
            except (TypeError, ValueError):
                sc = None
    if sc is None:
        return False, None, hold_f, track
    return bool(sc < hold_f), sc, hold_f, track


def should_apply_tau_gate(item: Optional[dict], *, tracks_cfg: Optional[dict] = None) -> bool:
    cfg = tracks_cfg or get_rebalance_tracks_cfg()
    if resolve_score_track(item) == TRACK_HEURISTIC:
        return bool(cfg.get("heuristic_apply_tau_gate"))
    return True


def sleeve_policy_allows(tracks_cfg: Optional[dict] = None) -> bool:
    """袖仓加仓已关闭：OOS 失败组不再进入买入后续。"""
    del tracks_cfg
    return False


def classify_oos_fail_book_path(
    item: dict,
    *,
    tracks_cfg: Optional[dict] = None,
) -> str:
    """OOS 失败票入簿路径：恒为 ``skip``（禁止新买入 / 不进袖仓）。"""
    del item, tracks_cfg
    return "skip"
