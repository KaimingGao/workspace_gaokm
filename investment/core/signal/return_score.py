"""收益导向打分：系统仅认 ``predicted_score``（收益分 ŷ%）。

- ``predicted_score``（收益分）：OLS/Ridge 预测前瞻收益（%）— **唯一排序键**
- 规则分（0–100 加权综合）已全局退役，不再写入 ``heuristic_score``
不自动写 ``signal_config``。
"""


import logging

logger = logging.getLogger(__name__)
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

RANK_MODES = ("predicted_score",)
DEFAULT_RANK_MODE = "predicted_score"
# 研究 OOS 对照允许的排序键（生产 live 仍只认 predicted_score）
RESEARCH_OOS_RANK_MODES = ("predicted_score", "heuristic_score")


@dataclass
class ReturnScoreModel:
    """把因子 sub_scores 映射为收益分 predicted_score（百分点量纲的 ŷ）。"""

    intercept: float
    coefficients: Dict[str, float]
    z_means: Dict[str, float] = field(default_factory=dict)
    z_stds: Dict[str, float] = field(default_factory=dict)
    standardized: bool = True
    horizon_days: int = 3
    sample_count: int = 0
    ridge_lambda: float = 0.0
    solver: str = "qr"
    fitted_as_of: Optional[str] = None
    note: str = ""
    y_spec: Optional[Dict[str, Any]] = None

    def predict(self, sub_scores: Optional[Dict[str, float]]) -> Optional[float]:
        subs = sub_scores or {}
        if not self.coefficients:
            return None
        total = float(self.intercept)
        used = 0
        for name, beta in self.coefficients.items():
            raw = subs.get(name)
            if raw is None:
                continue
            try:
                x = float(raw)
            except (TypeError, ValueError):
                continue
            if self.standardized:
                mu = float(self.z_means.get(name) or 0.0)
                sd = float(self.z_stds.get(name) or 1.0)
                if sd < 1e-12:
                    sd = 1.0
                x = (x - mu) / sd
            total += float(beta) * x
            used += 1
        if used == 0:
            return None
        return round(total, 6)

    def explain_prediction(
        self, sub_scores: Optional[Dict[str, float]]
    ) -> Optional[Dict[str, Any]]:
        """结构化拆解 ŷ（与 ``predict`` 同口径），供悬浮注释表格渲染。"""
        from core.signal.factors.meta.registry import factor_label

        subs = sub_scores or {}
        if not self.coefficients:
            return None
        terms: List[Dict[str, Any]] = []
        total = float(self.intercept)
        for name, beta in self.coefficients.items():
            raw = subs.get(name)
            if raw is None:
                continue
            try:
                x = float(raw)
            except (TypeError, ValueError):
                continue
            z = x
            if self.standardized:
                mu = float(self.z_means.get(name) or 0.0)
                sd = float(self.z_stds.get(name) or 1.0)
                if sd < 1e-12:
                    sd = 1.0
                z = (x - mu) / sd
            contrib = float(beta) * z
            total += contrib
            terms.append(
                {
                    "key": str(name),
                    "label": factor_label(name),
                    "beta": round(float(beta), 6),
                    "z": round(float(z), 4),
                    "contrib": round(float(contrib), 6),
                }
            )
        if not terms:
            return None
        terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0)))
        return {
            "intercept": round(float(self.intercept), 6),
            "terms": terms,
            "total": round(total, 6),
        }

    def format_formula(self, sub_scores: Optional[Dict[str, float]]) -> str:
        """生成一行摘要公式；UI 优先用 ``explain_prediction`` 表格。"""
        expl = self.explain_prediction(sub_scores)
        if not expl:
            return ""
        parts = [
            f"{t['label']}(β={t['beta']:+.3f}, z={t['z']:+.2f} → {t['contrib']:+.3f})"
            for t in expl["terms"]
        ]
        return (
            f"ŷ = α{expl['intercept']:+.3f} + "
            + " + ".join(parts)
            + f" = {expl['total']:.3f}%"
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["ReturnScoreModel"]:
        if not data:
            return None
        coefs = data.get("coefficients") or {}
        clean: Dict[str, float] = {}
        for k, v in coefs.items():
            if str(k) in ("intercept", "_intercept", "const"):
                continue
            if v is None:
                continue
            try:
                clean[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        try:
            intercept = float(data.get("intercept") or 0.0)
        except (TypeError, ValueError):
            intercept = 0.0

        def _float_map(raw: Any) -> Dict[str, float]:
            out: Dict[str, float] = {}
            for k, v in (raw or {}).items():
                if v is None:
                    continue
                try:
                    out[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            return out

        return cls(
            intercept=intercept,
            coefficients=clean,
            z_means=_float_map(data.get("z_means") or data.get("zscore_means")),
            z_stds=_float_map(data.get("z_stds") or data.get("zscore_stds")),
            standardized=bool(data.get("standardized", True)),
            horizon_days=int(data.get("horizon_days") or 3),
            sample_count=int(data.get("sample_count") or 0),
            ridge_lambda=float(data.get("ridge_lambda") or 0.0),
            solver=str(data.get("solver") or "qr"),
            fitted_as_of=data.get("fitted_as_of"),
            note=str(data.get("note") or ""),
            y_spec=data.get("y_spec") if isinstance(data.get("y_spec"), dict) else None,
        )

    @classmethod
    def from_ols_report(
        cls,
        report: Optional[Dict[str, Any]],
        *,
        fitted_as_of: Optional[str] = None,
    ) -> Optional["ReturnScoreModel"]:
        if not report or not report.get("success"):
            return None
        coefs = dict(report.get("coefficients") or {})
        intercept = coefs.pop("intercept", None)
        if intercept is None:
            intercept = report.get("intercept")
        try:
            intercept_f = float(intercept or 0.0)
        except (TypeError, ValueError):
            intercept_f = 0.0
        clean: Dict[str, float] = {}
        for k, v in coefs.items():
            if str(k) in ("_intercept", "const"):
                continue
            if v is None:
                continue
            try:
                clean[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        if not clean:
            return None

        def _float_map(raw: Any) -> Dict[str, float]:
            out: Dict[str, float] = {}
            for k, v in (raw or {}).items():
                if v is None:
                    continue
                try:
                    out[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            return out

        # factor_ols 报告字段为 zscore_*；兼容 z_*
        z_means = report.get("z_means") or report.get("zscore_means")
        z_stds = report.get("z_stds") or report.get("zscore_stds")
        y_spec = report.get("y_spec")
        if not isinstance(y_spec, dict):
            try:
                from core.research.beta_accuracy import build_y_spec

                y_spec = build_y_spec(horizon_days=int(report.get("horizon_days") or 3))
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in return_score.py", exc_info=True)
                y_spec = None
        return cls(
            intercept=intercept_f,
            coefficients=clean,
            z_means=_float_map(z_means),
            z_stds=_float_map(z_stds),
            standardized=bool(report.get("standardized", True)),
            horizon_days=int(report.get("horizon_days") or 3),
            sample_count=int(report.get("sample_count") or 0),
            ridge_lambda=float(
                report.get("ridge_lambda_selected")
                if report.get("ridge_lambda_selected") is not None
                else (report.get("ridge_lambda") or 0.0)
            ),
            solver=str(report.get("solver") or "qr"),
            fitted_as_of=fitted_as_of,
            note="由 OLS/Ridge 报告构建；预测值为前瞻收益百分点，非 0–100 score。",
            y_spec=y_spec,
        )


def _stamp_formula_terms(item: dict, model: Optional[ReturnScoreModel]) -> None:
    """把 ŷ 组成写进条目，供悬浮 tip 的因子表。"""
    if model is None or not isinstance(item, dict):
        return
    try:
        expl = model.explain_prediction(item.get("sub_scores") or {})
    except Exception:  # noqa: BLE001 — 组成失败不挡 ŷ
        logger.debug("explain_prediction failed", exc_info=True)
        return
    if not expl:
        return
    item["score_formula_terms"] = expl
    item["formula_terms"] = expl


def apply_predicted_scores_by_model(
    entries: Sequence[dict],
    model_for_code,
    *,
    write_rank_score: bool = True,
    default_model: Optional[ReturnScoreModel] = None,
) -> List[dict]:
    """按代码解析收益分模型（分组 β）；无映射时用 ``default_model``。

    ``model_for_code``: ``(stock_code) -> Optional[ReturnScoreModel]`` 或
    ``Dict[str, ReturnScoreModel]``。
    """
    out: List[dict] = []
    resolver = model_for_code
    if isinstance(model_for_code, dict):
        resolver = lambda code, _m=model_for_code: _m.get(str(code or "").strip())

    for raw in entries:
        item = dict(raw)
        code = str(item.get("stock_code") or "").strip()
        item.pop("heuristic_score", None)
        item.pop("rule_score", None)
        model = None
        try:
            model = resolver(code) if callable(resolver) else None
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in return_score.py", exc_info=True)
            model = None
        if model is None:
            model = default_model
        pred = model.predict(item.get("sub_scores") or {}) if model is not None else None
        item["predicted_score"] = pred
        item["rank_mode"] = "predicted_score"
        if pred is not None:
            item["y_oo"] = pred
            item["predicted_score_oo"] = pred
        if model is not None:
            item["return_model_source"] = item.get("return_model_source") or "mapped"
        if write_rank_score and pred is not None:
            item["score"] = pred
        _stamp_formula_terms(item, model)
        out.append(item)
    return out


def clamp_rank_mode(mode: Optional[str] = None) -> str:
    """生产路径一律收益分；旧 heuristic/rule 别名亦映射为 predicted_score。"""
    return "predicted_score"


def resolve_research_rank_mode(mode: Optional[str] = None) -> str:
    """研究/OOS 回测：允许 heuristic_score 作对照基线，其余 → predicted_score。"""
    m = (mode or "").strip().lower()
    if m in ("heuristic", "heuristic_score", "rule", "rule_score"):
        return "heuristic_score"
    return "predicted_score"


def apply_predicted_scores(
    entries: Sequence[dict],
    model: ReturnScoreModel,
    *,
    write_rank_score: bool = True,
) -> List[dict]:
    """为条目写入 ``predicted_score``（收益分）。"""
    out: List[dict] = []
    for raw in entries:
        item = dict(raw)
        item.pop("heuristic_score", None)
        item.pop("rule_score", None)
        pred = model.predict(item.get("sub_scores") or {})
        item["predicted_score"] = pred
        item["rank_mode"] = "predicted_score"
        if pred is not None:
            item["y_oo"] = pred
            item["predicted_score_oo"] = pred
        if write_rank_score and pred is not None:
            item["score"] = pred
        _stamp_formula_terms(item, model)
        out.append(item)
    return out


def rank_by_predicted_score(
    items: Sequence[dict],
    *,
    min_predicted_score: Optional[float] = None,
    top_k: Optional[int] = None,
) -> List[Tuple[str, float]]:
    """按 ``predicted_score``（收益分；缺则跳过）降序。"""
    floor = None if min_predicted_score is None else float(min_predicted_score)
    picks: List[Tuple[str, float]] = []
    for item in items:
        code = str(item.get("stock_code") or "").strip()
        if not code:
            continue
        pred = item.get("predicted_score")
        if pred is None:
            pred = item.get("return_score")
        if pred is None:
            continue
        try:
            yhat = float(pred)
        except (TypeError, ValueError):
            continue
        if floor is not None and yhat < floor:
            continue
        picks.append((code, yhat))
    picks.sort(key=lambda x: (-float(x[1]), str(x[0])))
    if top_k is not None:
        picks = picks[: max(1, int(top_k))]
    return picks


def fit_return_model_from_panel(
    xs: List[Dict[str, Optional[float]]],
    ys: List[float],
    *,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    fitted_as_of: Optional[str] = None,
    min_samples: int = 24,
) -> Tuple[Optional[ReturnScoreModel], Dict[str, Any]]:
    """对已对齐面板拟合，返回模型 + OLS 报告摘要。"""
    from core.research.factor_ols_fit import fit_factor_ols_from_panel

    if len(ys) < max(8, int(min_samples or 24)):
        return None, {
            "success": False,
            "error": f"训练样本不足（{len(ys)} < {min_samples}）",
            "sample_count": len(ys),
        }
    report = fit_factor_ols_from_panel(
        xs,
        ys,
        horizon_days=horizon_days,
        fundamentals_used=False,
        pit_fundamentals=False,
        mode="watching_pooled",
        ridge_lambda=ridge_lambda,
    )
    model = ReturnScoreModel.from_ols_report(report, fitted_as_of=fitted_as_of)
    return model, report
