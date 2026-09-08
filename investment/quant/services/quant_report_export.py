"""量化日报 Markdown / HTML 导出（P14.2 / P15.2）。"""


import logging

logger = logging.getLogger(__name__)
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional

# 章节标题（MD / HTML / TOC 同源）
CROSS_SECTION_TITLE = "横截面 ŷ（predicted_score）"
WEIGHT_SUGGEST_TITLE = "权重建议（遗留诊断）"
CLUSTER_LIVE_TITLE = "分组 live（组ŷ）"
PORTFOLIO_BT_SECTION_TITLE = "历史回测摘要"
SCORING_DEFAULT_NOTE = (
    "选股真源=predicted_score（ŷ）；heuristic 仅作研究 OOS 基线；过门≠自动 promote"
)


def _fmt_yhat(v: Any, *, digits: int = 3) -> str:
    """收益分 ŷ 展示：百分点量纲，带 %。

    调用方应先用 ``_yhat_from_row`` 滤掉 heuristic；此处不再用 ≥10 拒收
    （涨停板 ŷ% 完全可能 ≥10）。
    """
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.{digits}f}%"


def _fmt_heuristic(v: Any, *, digits: int = 1) -> str:
    """规则分 0–100 展示：前缀 H，避免被读成 ŷ%。"""
    if v is None or v == "":
        return "—"
    try:
        return f"H{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _heuristic_from_row(row: Optional[dict]) -> Optional[float]:
    if not isinstance(row, dict):
        return None
    try:
        from core.signal.score_display import looks_like_legacy_heuristic_score
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_report_export.py", exc_info=True)

        def looks_like_legacy_heuristic_score(value):  # type: ignore
            try:
                return float(value) >= 10.0
            except (TypeError, ValueError):
                return False

    for key in ("heuristic_score", "heuristic"):
        v = row.get(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    # 旧样本只把 0–100 写在 score 上
    sc = row.get("score")
    try:
        f = float(sc) if sc is not None and sc != "" else None
    except (TypeError, ValueError):
        f = None
    if f is not None and looks_like_legacy_heuristic_score(f, item=row):
        return f
    return None


def _yhat_from_row(row: Optional[dict]) -> Optional[float]:
    """从簿/成交行取 ŷ%（优先 predicted / blend / cluster，拒收 heuristic）。"""
    if not isinstance(row, dict):
        return None
    try:
        from core.signal.score_display import looks_like_legacy_heuristic_score
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_report_export.py", exc_info=True)

        def looks_like_legacy_heuristic_score(value):  # type: ignore
            try:
                return float(value) >= 10.0
            except (TypeError, ValueError):
                return False

    for key in (
        "predicted_score_blend",
        "predicted_score",
        "predicted_score_eod",
        "score_cluster",
        "score",
        "yhat",
    ):
        v = row.get(key)
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if looks_like_legacy_heuristic_score(f, item=row):
            continue
        return f
    return None


def _fmt_fill_score(row: Optional[dict]) -> str:
    """成交样本分数：有 ŷ 用 ŷ%；否则标 H（规则分），不留空列。"""
    y = _yhat_from_row(row)
    if y is not None:
        return _fmt_yhat(y)
    heu = _heuristic_from_row(row)
    if heu is not None:
        return _fmt_heuristic(heu)
    return "—"


def _scoring_meta(report: Dict[str, Any]) -> Dict[str, Any]:
    scoring = report.get("scoring") if isinstance(report.get("scoring"), dict) else {}
    cfg = report.get("config") if isinstance(report.get("config"), dict) else {}
    cfg_scoring = cfg.get("scoring") if isinstance(cfg.get("scoring"), dict) else {}
    rank_mode = (
        scoring.get("rank_mode")
        or cfg_scoring.get("rank_mode")
        or "predicted_score"
    )
    note = scoring.get("note") or cfg.get("product_note") or SCORING_DEFAULT_NOTE
    return {"rank_mode": rank_mode, "note": note}


def _cross_section_ranked_list(cross_section: Dict[str, Any]) -> List[dict]:
    """横截面 Top 列表（兼容 ranking / ranked / top 字段）。"""
    if not cross_section or not cross_section.get("success"):
        return []
    for key in ("ranking", "ranked", "top"):
        items = cross_section.get(key)
        if isinstance(items, list) and items:
            return items
    return []


def _score_summary_bullet(ranked: List[dict]) -> str:
    scores = []
    for item in ranked:
        y = _yhat_from_row(item if isinstance(item, dict) else None)
        if y is not None:
            scores.append(y)
    if not scores:
        return f"横截面 ŷ：Top {len(ranked)} 只"
    med = median(scores)
    return (
        f"横截面 ŷ：Top {len(ranked)} 只 · "
        f"最高 {_fmt_yhat(max(scores))} · 中位 {_fmt_yhat(med)}"
    )


def summarize_cross_section_scores(cross_section: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """横截面 ŷ 摘要（P77：解读 / compact 共用）。"""
    if not cross_section or not cross_section.get("success"):
        return None
    ranked = _cross_section_ranked_list(cross_section)
    if not ranked:
        return None
    scores: List[float] = []
    top: List[Dict[str, Any]] = []
    for item in ranked[:5]:
        score_f = _yhat_from_row(item if isinstance(item, dict) else None)
        if score_f is not None:
            scores.append(score_f)
        top.append(
            {
                "stock_code": item.get("stock_code"),
                "stock_name": item.get("stock_name"),
                "score": score_f,
                "score_raw": item.get("score_raw"),
            }
        )
    out: Dict[str, Any] = {
        "ranked_count": len(ranked),
        "top": top,
        "summary_line": _score_summary_bullet(ranked),
        "score_semantics": "predicted_score（ŷ，百分点）",
    }
    if scores:
        out["max_score"] = round(max(scores), 3)
        out["median_score"] = round(float(median(scores)), 3)
    neut = cross_section.get("neutralization") or {}
    if neut.get("applied"):
        out["neutralization_applied"] = True
        out["neutralization_method"] = neut.get("method")
    return out


def build_cross_section_export_section(cross_section: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """横截面 ŷ 专节（P80：MD/HTML 导出）。"""
    summary = summarize_cross_section_scores(cross_section)
    if not summary:
        return None
    ranked = _cross_section_ranked_list(cross_section)
    md_lines = [
        f"- {summary['summary_line']}",
        "- 语义：predicted_score（ŷ）· 模型预测前瞻收益百分点 · 非 0–100 规则分",
    ]
    neut = cross_section.get("neutralization") or {}
    if neut.get("applied"):
        md_lines.append(f"- 截面中性化：{neut.get('method') or 'zscore'}")
    for i, row in enumerate(ranked[:10], 1):
        name = row.get("stock_name") or row.get("stock_code") or "—"
        raw = (
            f" · raw {_fmt_yhat(row['score_raw'])}"
            if row.get("score_raw") is not None
            else ""
        )
        md_lines.append(f"- {i}. {name} · ŷ {_fmt_yhat(row.get('score'))}{raw}")
    if cross_section.get("note"):
        md_lines.append(f"- _{cross_section['note']}_")

    tr = ""
    for i, row in enumerate(ranked[:10], 1):
        name = row.get("stock_name") or row.get("stock_code") or "—"
        raw = row.get("score_raw")
        raw_cell = _fmt_yhat(raw) if raw is not None else "—"
        tr += (
            f"<tr><td>{i}</td><td>{name}</td>"
            f"<td class='num'>{_fmt_yhat(row.get('score'))}</td>"
            f"<td class='num'>{raw_cell}</td></tr>"
        )
    html_body = (
        f"<p>{summary['summary_line']}</p>"
        "<p class='meta'>语义：predicted_score（ŷ）· 非 0–100 规则分</p>"
        "<table><thead><tr><th>#</th><th>标的</th><th>ŷ</th><th>raw</th></tr></thead>"
        f"<tbody>{tr}</tbody></table>"
    )
    if cross_section.get("note"):
        html_body += f"<p class='meta'>{cross_section['note']}</p>"

    return {
        "title": CROSS_SECTION_TITLE,
        "anchor": "cross-section",
        "markdown_lines": md_lines,
        "html_body": html_body,
    }


def summarize_factor_ols(factor_ols: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """因子 OLS 摘要（P90：解读 / compact / 导出共用）。"""
    if not factor_ols or not factor_ols.get("success"):
        return None
    coefs = factor_ols.get("coefficients") or {}
    cur = factor_ols.get("current_weights") or {}
    deltas: List[Dict[str, Any]] = []
    for name, ols_val in coefs.items():
        if ols_val is None:
            continue
        cfg_val = cur.get(name)
        if cfg_val is None:
            continue
        try:
            deltas.append(
                {
                    "factor": name,
                    "ols": float(ols_val),
                    "config": float(cfg_val),
                    "delta": round(float(ols_val) - float(cfg_val), 4),
                }
            )
        except (TypeError, ValueError):
            continue
    deltas.sort(key=lambda x: abs(x["delta"]), reverse=True)
    excluded = factor_ols.get("excluded_features") or []
    z_tag = " · z-score β" if factor_ols.get("standardized") else ""
    try:
        lam_f = float(factor_ols.get("ridge_lambda") or 0.0)
    except (TypeError, ValueError):
        lam_f = 0.0
    ridge_tag = f" · Ridge λ={lam_f:g}" if lam_f > 0 else ""
    line = (
        f"因子 OLS：R²={factor_ols.get('r_squared')} · n={factor_ols.get('sample_count')}"
        f"{z_tag}{ridge_tag} （全量因子研究用，不自动写 signal_config）"
    )
    if excluded:
        line += f" · 未入模 {len(excluded)} 个"
    return {
        "summary_line": line,
        "r_squared": factor_ols.get("r_squared"),
        "sample_count": factor_ols.get("sample_count"),
        "stock_code": factor_ols.get("stock_code"),
        "top_deltas": deltas[:5],
        "excluded_features": excluded,
        "note": factor_ols.get("note"),
    }


def build_factor_ols_export_section(factor_ols: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """因子 OLS 专节（P91：MD/HTML 导出）。"""
    summary = summarize_factor_ols(factor_ols)
    if not summary:
        return None
    coefs = factor_ols.get("coefficients") or {}
    cur = factor_ols.get("current_weights") or {}
    md_lines = [f"- {summary['summary_line']}"]
    for row in summary.get("top_deltas") or []:
        md_lines.append(
            f"- {row['factor']}: OLS {row['ols']} vs config {row['config']} (Δ {row['delta']})"
        )
    excluded = summary.get("excluded_features") or []
    if excluded:
        md_lines.append(f"- 常数/零方差剔除：{', '.join(excluded)}")
    if factor_ols.get("note"):
        md_lines.append(f"- _{factor_ols['note']}_")

    tr = ""
    for name in sorted(set(list(coefs.keys()) + list(cur.keys()))):
        ols_cell = coefs.get(name)
        ols_txt = "—" if ols_cell is None else f"{ols_cell}"
        tr += (
            f"<tr><td>{name}</td><td class='num'>{ols_txt}</td>"
            f"<td class='num'>{cur.get(name, '—')}</td></tr>"
        )
    html_body = (
        f"<p>{summary['summary_line']}</p>"
        "<table><thead><tr><th>因子</th><th>OLS</th><th>config</th></tr></thead>"
        f"<tbody>{tr}</tbody></table>"
    )
    if factor_ols.get("note"):
        html_body += f"<p class='meta'>{factor_ols['note']}</p>"

    return {
        "title": "因子 OLS 实验",
        "anchor": "factor-ols",
        "markdown_lines": md_lines,
        "html_body": html_body,
    }


def _lines(title: str, rows: List[str]) -> List[str]:
    out = [f"## {title}", ""]
    out.extend(rows or ["—"])
    out.append("")
    return out


def build_cluster_live_export_section(cl: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """分组 live 组ŷ 状态 + OOS + 簿 Top + 组模型摘要。"""
    if not cl or cl.get("mode") not in ("shadow", "active"):
        return None
    cov = cl.get("coverage")
    cov_s = f"{round(float(cov) * 100)}%" if cov is not None else "—"
    md_lines = [
        f"- mode={cl.get('mode')} · version={cl.get('version')} · 覆盖 {cov_s}",
        f"- 分池簿 {cl.get('book_names') or '—'} 只 · 龄 {cl.get('age_days') or '—'}d"
        f" · 有模型组 {cl.get('n_groups_with_model') or '—'}",
        f"- _{cl.get('note') or '组ŷ live；不写全局 weights；过门≠自动 promote'}_",
    ]
    oos = cl.get("oos_summary") if isinstance(cl.get("oos_summary"), dict) else {}
    if oos:
        md_lines.append(
            f"- OOS（heuristic 基线 vs ŷ）：通过 {oos.get('pass_count', '—')} / "
            f"失败 {oos.get('fail_count', '—')} / 跳过 {oos.get('skip_count', '—')} / "
            f"未知 {oos.get('unknown_count', '—')}"
        )
        if oos.get("note"):
            md_lines.append(f"- _{oos.get('note')}_")
    book_top = cl.get("book_top") if isinstance(cl.get("book_top"), list) else []
    if book_top:
        md_lines.append("- **簿 Top ŷ**")
        for i, row in enumerate(book_top[:8], 1):
            name = row.get("stock_name") or row.get("stock_code") or "—"
            lab = row.get("cluster_label") or "—"
            md_lines.append(
                f"  - {i}. {name} · ŷ {_fmt_yhat(row.get('score'))} · 组 {lab}"
            )
    groups = cl.get("group_models") if isinstance(cl.get("group_models"), list) else []
    if groups:
        md_lines.append("- **组 return_model**")
        for g in groups[:12]:
            oos_flag = (
                "过门"
                if g.get("oos_passed")
                else ("跳过" if g.get("oos_skipped") else "未过/未知")
            )
            md_lines.append(
                f"  - {g.get('label') or '—'} · 成员 {g.get('n_members') or 0} · "
                f"β {g.get('n_coef') or 0} · n={g.get('sample_count') or '—'} · OOS {oos_flag}"
            )
    for a in (cl.get("alerts") or [])[:4]:
        md_lines.append(f"- ⚠ {a}")

    oos_html = ""
    if oos:
        oos_html = (
            f"<p>OOS（heuristic vs ŷ）：通过 {oos.get('pass_count', '—')} / "
            f"失败 {oos.get('fail_count', '—')} / 跳过 {oos.get('skip_count', '—')} / "
            f"未知 {oos.get('unknown_count', '—')} · 过门≠自动 promote</p>"
        )
    book_html = ""
    if book_top:
        rows = "".join(
            f"<tr><td>{i}</td><td>{r.get('stock_name') or r.get('stock_code') or '—'}</td>"
            f"<td>{_fmt_yhat(r.get('score'))}</td>"
            f"<td>{r.get('cluster_label') or '—'}</td></tr>"
            for i, r in enumerate(book_top[:8], 1)
        )
        book_html = (
            "<p><strong>簿 Top ŷ</strong></p>"
            "<table><thead><tr><th>#</th><th>标的</th><th>ŷ</th><th>组</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    groups_html = ""
    if groups:
        grows = "".join(
            "<tr>"
            f"<td>{g.get('label') or '—'}</td>"
            f"<td>{g.get('n_members') or 0}</td>"
            f"<td>{g.get('n_coef') or 0}</td>"
            f"<td>{g.get('sample_count') or '—'}</td>"
            f"<td>{'过门' if g.get('oos_passed') else ('跳过' if g.get('oos_skipped') else '未过/未知')}</td>"
            "</tr>"
            for g in groups[:12]
        )
        groups_html = (
            "<p><strong>组 return_model</strong></p>"
            "<table><thead><tr><th>组</th><th>成员</th><th>β数</th><th>n</th><th>OOS</th></tr></thead>"
            f"<tbody>{grows}</tbody></table>"
        )
    html_body = (
        f"<p>mode={cl.get('mode')} · version={cl.get('version')} · 覆盖 {cov_s} · "
        f"簿 {cl.get('book_names') or '—'} 只 · 有模型组 {cl.get('n_groups_with_model') or '—'}</p>"
        f"<p class='meta'>{cl.get('note') or ''}</p>"
        f"{oos_html}{book_html}{groups_html}"
    )
    return {
        "title": CLUSTER_LIVE_TITLE,
        "anchor": "cluster-live",
        "markdown_lines": md_lines,
        "html_body": html_body,
    }


def build_neutral_compare_export_section(nc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """中性化对照专节（P58：MD/HTML 导出共用）。"""
    if not nc or not nc.get("success"):
        return None

    delta = nc.get("delta") or {}
    stocks = ", ".join(nc.get("loaded_stocks") or []) or "—"
    md_lines = [
        f"- 结论：**{nc.get('winner')}** 更优",
        f"- 中性化累计：**{nc.get('neutralized_total_return_pct')}%** · 胜率 {nc.get('neutralized_win_rate_pct')}%",
        f"- 未中性化ŷ累计：**{nc.get('absolute_total_return_pct')}%** · 胜率 {nc.get('absolute_win_rate_pct')}%",
        f"- Δ累计：{delta.get('total_return_pct')}% · Δ胜率：{delta.get('win_rate_pct')}% · Δ交易：{delta.get('trade_count')}",
        f"- 标的：{stocks}",
        f"- 解读：{nc.get('interpretation') or '—'}",
    ]
    if nc.get("fundamentals_count"):
        md_lines.append(f"- 基本面快照：{nc['fundamentals_count']} 只")
    if nc.get("note"):
        md_lines.append(f"- _{nc['note']}_")

    html_body = (
        "<table>"
        "<thead><tr><th>维度</th><th>中性化</th><th>未中性化ŷ</th><th>Δ</th></tr></thead><tbody>"
        f"<tr><td>累计收益</td><td>{nc.get('neutralized_total_return_pct')}%</td>"
        f"<td>{nc.get('absolute_total_return_pct')}%</td>"
        f"<td>{delta.get('total_return_pct')}%</td></tr>"
        f"<tr><td>胜率</td><td>{nc.get('neutralized_win_rate_pct')}%</td>"
        f"<td>{nc.get('absolute_win_rate_pct')}%</td>"
        f"<td>{delta.get('win_rate_pct')}%</td></tr>"
        f"<tr><td>交易次数</td><td colspan=\"2\">—</td><td>{delta.get('trade_count')}</td></tr>"
        "</tbody></table>"
        f"<p>结论：<strong>{nc.get('winner')}</strong> · {nc.get('interpretation') or '—'}</p>"
        f"<p class='meta'>标的：{stocks} · 「未中性化ŷ」= 未做截面中性化的 predicted_score</p>"
    )
    if nc.get("note"):
        html_body += f"<p class='meta'>{nc['note']}</p>"

    return {
        "title": "中性化对照专节",
        "anchor": "neutral-compare",
        "markdown_lines": md_lines,
        "html_body": html_body,
    }


def build_report_export_toc(report: Dict[str, Any]) -> Dict[str, Any]:
    """导出目录：主叙事组ŷ → 横截面 → 历史回测 → 中性化；单票探针进附录。"""
    entries: List[tuple] = [("一页摘要", "一页摘要")]

    cl = report.get("cluster_live") or {}
    if cl and cl.get("mode") in ("shadow", "active"):
        entries.append((CLUSTER_LIVE_TITLE, "cluster-live"))
    cs_section = build_cross_section_export_section(report.get("cross_section") or {})
    if cs_section:
        entries.append((cs_section["title"], cs_section["anchor"]))
    if (report.get("portfolio_backtest_summary") or {}).get("success"):
        entries.append((PORTFOLIO_BT_SECTION_TITLE, "历史回测摘要"))
    nc_section = build_neutral_compare_export_section(
        report.get("portfolio_neutral_compare_summary") or {}
    )
    if nc_section:
        entries.append((nc_section["title"], nc_section["anchor"]))

    # 附录：单票遗留探针
    if report.get("factor_ic"):
        entries.append(("附录·因子 IC", "因子-ic"))
    ols_section = build_factor_ols_export_section(report.get("factor_ols") or {})
    if ols_section:
        entries.append((f"附录·{ols_section['title']}", ols_section["anchor"]))
    if (report.get("weight_suggest") or {}).get("success"):
        entries.append((f"附录·{WEIGHT_SUGGEST_TITLE}", "权重建议-遗留"))
    if (report.get("threshold_suggest") or {}).get("success"):
        entries.append(("附录·stance 阈值建议", "stance-阈值建议"))

    md_lines = [f"- [{title}](#{anchor})" for title, anchor in entries]
    html_items = "".join(
        f'<li><a href="#{anchor}">{title}</a></li>' for title, anchor in entries
    )
    return {
        "success": True,
        "entries": [{"title": t, "anchor": a} for t, a in entries],
        "markdown_lines": md_lines,
        "html_nav": f'<nav class="report-toc"><ul>{html_items}</ul></nav>',
    }


def build_report_executive_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    """量化日报一页摘要：组ŷ / 簿 / OOS / 横截面 / 历史回测优先。"""
    bullets: List[str] = []
    scoring = _scoring_meta(report)
    bullets.append(
        f"选股真源：{scoring['rank_mode']}（ŷ）· {scoring['note']}"
    )

    cl = report.get("cluster_live") or {}
    if cl and cl.get("mode") in ("shadow", "active"):
        cov = cl.get("coverage")
        cov_s = f"{round(float(cov) * 100)}%" if cov is not None else "—"
        bullets.append(
            f"分组 live：mode={cl.get('mode')} · v{cl.get('version') or '—'} · "
            f"覆盖 {cov_s} · 簿 {cl.get('book_names') or '—'} 只 · "
            f"有模型组 {cl.get('n_groups_with_model') or '—'}"
        )
        oos = cl.get("oos_summary") if isinstance(cl.get("oos_summary"), dict) else {}
        if oos:
            bullets.append(
                f"OOS（heuristic vs ŷ）：通过 {oos.get('pass_count', '—')} / "
                f"失败 {oos.get('fail_count', '—')} / 跳过 {oos.get('skip_count', '—')} / "
                f"未知 {oos.get('unknown_count', '—')} · 过门≠自动 promote"
            )
        book_top = cl.get("book_top") if isinstance(cl.get("book_top"), list) else []
        if book_top:
            tip = book_top[0]
            bullets.append(
                f"簿头：{tip.get('stock_name') or tip.get('stock_code') or '—'} · "
                f"ŷ {_fmt_yhat(tip.get('score'))}"
            )

    cs = report.get("cross_section") or {}
    if cs.get("success"):
        ranked = _cross_section_ranked_list(cs)
        if ranked:
            bullets.append(_score_summary_bullet(ranked))

    ps = report.get("portfolio_backtest_summary") or {}
    if ps.get("success"):
        cfg = _topk_run_config_line(ps)
        if cfg:
            bullets.append(cfg)
        engine = (ps.get("params") or {}).get("engine") or ps.get("engine") or ""
        if engine == "paper_replay":
            bullets.append(
                f"历史回测（rank_lots · 09:30）：累计 {ps.get('total_return_pct')}% · "
                f"胜率 {ps.get('win_rate_pct')}% · 交易 {ps.get('trade_count')}"
                f" · {_topk_score_axis_note(ps)}"
                + (
                    " · 无成交"
                    if (ps.get("trade_count") in (0, None) and ps.get("total_return_pct") is None)
                    else ""
                )
            )
        else:
            bullets.append(
                f"Top-K 研究回测（topk_research / ŷ_EOD）：累计 {ps.get('total_return_pct')}% · "
                f"胜率 {ps.get('win_rate_pct')}% · 交易 {ps.get('trade_count')}"
                f" · {_topk_score_axis_note(ps)}"
                + (
                    " · 无成交"
                    if (ps.get("trade_count") in (0, None) and ps.get("total_return_pct") is None)
                    else ""
                )
            )
            pr = ps.get("paper_replay") or {}
            if pr.get("success"):
                bullets.append(
                    f"纸面回放（paper_replay / 可实现）：累计 {pr.get('total_return_pct')}% · "
                    f"回撤 {pr.get('max_drawdown_pct')}% · 成交 {pr.get('trade_count')}"
                    f" · ≠研究腿聚合"
                )
            elif pr.get("error"):
                bullets.append(f"纸面回放不可用：{pr.get('error')}")

    nc = report.get("portfolio_neutral_compare_summary") or {}
    if nc.get("success"):
        bullets.append(
            f"中性化对照：{nc.get('interpretation') or '—'} "
            f"(Δ累计 {((nc.get('delta') or {}).get('total_return_pct'))}%)"
        )

    yc = report.get("y_check_summary") or {}
    if yc.get("success") and (yc.get("n") or yc.get("summary_line")):
        line = yc.get("summary_line")
        if not line:
            mix = " · ".join(
                f"{(r.get('label') or r.get('check'))} {r.get('n')}"
                for r in (yc.get("by_y_check") or yc.get("rows") or [])[:4]
                if r.get("n")
            )
            line = f"Y校验 {yc.get('as_of') or '—'}：n={yc.get('n')}" + (
                f" · {mix}" if mix else ""
            )
        hr = None
        for r in yc.get("by_y_check") or []:
            if r.get("check") == "ok" and r.get("hit_rate") is not None:
                hr = r.get("hit_rate")
                break
        if hr is None and yc.get("hit_rate") is not None:
            hr = yc.get("hit_rate")
        if hr is not None:
            try:
                line += f" · 全池命中 {float(hr):.0%}"
            except (TypeError, ValueError):
                pass
        bullets.append(line)

    # 附录探针（若有）
    ic = report.get("factor_ic") or {}
    factors = ic.get("factors") or []
    if factors:
        top = factors[0]
        bullets.append(
            f"附录·因子 IC：{top.get('label') or top.get('factor')} {top.get('ic')} "
            f"(样本 n={ic.get('sample_count') or top.get('sample_count') or '—'})"
        )

    ols_sm = summarize_factor_ols(report.get("factor_ols") or {})
    if ols_sm:
        bullets.append(f"附录·{ols_sm['summary_line']}")

    ws = report.get("weight_suggest") or {}
    if ws.get("success"):
        bullets.append(
            "附录·权重建议（遗留）：有 · 不驱动选股 · 须人审，不自动写盘"
        )

    tsug = report.get("threshold_suggest") or {}
    if tsug.get("success"):
        if tsug.get("skipped_apply") or tsug.get("score_scale") == "predicted":
            bullets.append("附录·stance 阈值：有（旧分 OOS 参考 · ŷ% 门槛未改）")
        else:
            bullets.append("附录·stance 阈值建议：有（启发式路径 · 须人审）")

    return {
        "success": True,
        "bullet_count": len(bullets),
        "bullets": bullets,
        "note": "一页摘要；主叙事=组ŷ/簿/OOS/横截面/历史回测；过门≠自动 promote。",
    }


def render_quant_report_markdown(report: Dict[str, Any]) -> str:
    """将 quant_daily 报告渲染为 Markdown（不含自动交易建议）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = ["# 量化研究日报", "", f"_生成时间 {ts}_", ""]

    toc = build_report_export_toc(report)
    if toc.get("markdown_lines"):
        parts.extend(_lines("目录", toc["markdown_lines"]))

    summary = build_report_executive_summary(report)
    if summary.get("bullets"):
        parts.extend(
            _lines(
                "一页摘要",
                [f"- {b}" for b in summary["bullets"]],
            )
        )
        parts.append(f"> {summary.get('note')}")
        parts.append("")

    # 主叙事
    cl_section = build_cluster_live_export_section(report.get("cluster_live") or {})
    if cl_section:
        parts.extend(
            [f"## {cl_section['title']}", ""]
            + cl_section["markdown_lines"]
            + [""]
        )

    cs_section = build_cross_section_export_section(report.get("cross_section") or {})
    if cs_section:
        parts.extend(
            [f"## {cs_section['title']}", ""]
            + cs_section["markdown_lines"]
            + [""]
        )

    ps = report.get("portfolio_backtest_summary") or {}
    if ps.get("success"):
        detail_lines = build_portfolio_backtest_markdown_lines(ps)
        parts.extend(_lines(PORTFOLIO_BT_SECTION_TITLE, detail_lines))

    nc = report.get("portfolio_neutral_compare_summary") or {}
    nc_section = build_neutral_compare_export_section(nc)
    if nc_section:
        parts.extend(
            [f"## {nc_section['title']}", ""]
            + nc_section["markdown_lines"]
            + [""]
        )

    yc = report.get("y_check_summary") or {}
    if yc.get("success") and (yc.get("n") or yc.get("by_y_check") or yc.get("rows")):
        yc_lines = [
            f"- 决策日 **{yc.get('as_of') or '—'}** · 样本 n={yc.get('n') or 0}",
        ]
        if yc.get("summary_line"):
            yc_lines.append(f"- {yc.get('summary_line')}")
        for r in yc.get("by_y_check") or yc.get("rows") or []:
            if not isinstance(r, dict):
                continue
            lab = r.get("label") or r.get("check") or "—"
            bit = f"- **{lab}**：n={r.get('n')}"
            if r.get("hit_rate") is not None:
                try:
                    bit += f" · 命中 {float(r['hit_rate']):.0%}"
                except (TypeError, ValueError):
                    pass
            elif r.get("share") is not None:
                try:
                    bit += f" · 占比 {float(r['share']):.0%}"
                except (TypeError, ValueError):
                    pass
            yc_lines.append(bit)
        parts.extend(_lines("Y(τ) 校验分桶", yc_lines))

    # 附录：单票遗留探针
    appendix_bits: List[str] = []
    ic = report.get("factor_ic") or {}
    if ic.get("factors"):
        fac_lines = [
            f"- **{row.get('label') or row.get('factor')}**: IC {row.get('ic')} (n={row.get('sample_count')})"
            for row in (ic.get("factors") or [])[:8]
        ]
        appendix_bits.extend(_lines("附录·因子 IC", fac_lines))

    ols_section = build_factor_ols_export_section(report.get("factor_ols") or {})
    if ols_section:
        appendix_bits.extend(
            [f"## 附录·{ols_section['title']}", ""]
            + ols_section["markdown_lines"]
            + [""]
        )

    ws = report.get("weight_suggest") or {}
    if ws.get("success"):
        cur = ws.get("current_weights") or {}
        sug = ws.get("suggested_weights") or {}
        wl = [f"| {k} | {cur.get(k)} | {sug.get(k)} |" for k in cur]
        appendix_bits.extend(
            [
                f"## 附录·{WEIGHT_SUGGEST_TITLE}",
                "",
                "> 遗留 IC 小步权诊断；**不**驱动选股。选股真源为 return_model → ŷ。",
                "",
                "| 因子 | 当前 | 建议 |",
                "| --- | ---: | ---: |",
                *wl,
                "",
                *(f"- {r}" for r in (ws.get("rationale") or [])[:5]),
                "",
            ]
        )
        if ws.get("note"):
            appendix_bits.append(f"> _{ws.get('note')}_")
            appendix_bits.append("")

    tsug = report.get("threshold_suggest") or {}
    if tsug.get("success"):
        cur = tsug.get("current_thresholds") or {}
        sug = tsug.get("suggested_thresholds") or {}
        tl = [f"| {k} | {cur.get(k)} | {sug.get(k)} |" for k in cur]
        oos_scale = ((tsug.get("oos") or {}).get("score_scale") or "")
        agg_scale = ((tsug.get("watching_aggregate") or {}).get("score_scale") or "")
        if oos_scale == "predicted_yhat" or agg_scale == "predicted_yhat":
            scale_note = (
                "ŷ% wait OOS；已跳过改门槛（与当前接近或样本不足）。"
                if tsug.get("skipped_apply")
                else "ŷ% wait OOS 建议；须人审后合并 stance_thresholds，不自动写盘。"
            )
        elif tsug.get("skipped_apply") or tsug.get("score_scale") == "predicted":
            scale_note = "OOS 扫旧 0–100 规则分；当前 stance 为 ŷ%，已跳过自动改门槛。"
        else:
            scale_note = "启发式 0–100 门槛路径；须人审后合并，不自动写盘。"
        appendix_bits.extend(
            [
                "## 附录·stance 阈值建议",
                "",
                f"> {scale_note}",
                "",
                "| 阈值 | 当前 | 建议 |",
                "| --- | ---: | ---: |",
                *tl,
                "",
                *(f"- {r}" for r in (tsug.get("rationale") or [])[:5]),
                "",
            ]
        )
        agg = tsug.get("watching_aggregate")
        if agg:
            if (agg.get("score_scale") or "") == "predicted_yhat":
                med = agg.get("median_best_wait", agg.get("median_best_min_score"))
                appendix_bits.append(
                    f"> watching 聚合：{agg.get('stock_count')} 只，中位最优 wait={med}（ŷ%）"
                )
            else:
                appendix_bits.append(
                    f"> watching 聚合：{agg.get('stock_count')} 只，中位最优 min_score={agg.get('median_best_min_score')}（旧分制）"
                )
            appendix_bits.append("")

    if appendix_bits:
        parts.append("## 附录（单票遗留探针）")
        parts.append("")
        parts.append("> 默认生成日报不跑本附录；仅 `include_legacy_probe` 或旧快照才有。")
        parts.append("")
        parts.extend(appendix_bits)

    parts.extend(
        [
            "---",
            "",
            "_以上为量化研究摘要（选股真源=ŷ）；市场有风险，不保证收益，不代客下单。_",
            "",
        ]
    )
    return "\n".join(parts)


def render_quant_report_html(report: Dict[str, Any]) -> str:
    """将 quant_daily 报告渲染为自包含 HTML（P15.2）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    sections: List[str] = []

    def section(title: str, body: str) -> None:
        sections.append(f"<section><h2>{title}</h2>{body}</section>")

    toc = build_report_export_toc(report)
    if toc.get("html_nav"):
        sections.append(f"<section><h2>目录</h2>{toc['html_nav']}</section>")

    summary = build_report_executive_summary(report)
    if summary.get("bullets"):
        bullets = "".join(f"<li>{b}</li>" for b in summary["bullets"])
        section(
            "一页摘要",
            f"<ul>{bullets}</ul><p class='meta'>{summary.get('note') or ''}</p>",
        )

    # 主叙事
    cl_section = build_cluster_live_export_section(report.get("cluster_live") or {})
    if cl_section:
        section(
            cl_section["title"],
            f'<div id="{cl_section["anchor"]}">{cl_section["html_body"]}</div>',
        )

    cs_section = build_cross_section_export_section(report.get("cross_section") or {})
    if cs_section:
        section(cs_section["title"], cs_section["html_body"])

    ps = report.get("portfolio_backtest_summary") or {}
    if ps.get("success"):
        lis = "".join(
            f"<li>{line[2:] if line.startswith('- ') else line}</li>"
            for line in build_portfolio_backtest_markdown_lines(ps)
            if line.startswith("- ")
        )
        section(PORTFOLIO_BT_SECTION_TITLE, f"<ul>{lis}</ul>")

    nc = report.get("portfolio_neutral_compare_summary") or {}
    nc_section = build_neutral_compare_export_section(nc)
    if nc_section:
        section(
            nc_section["title"],
            f"<div id=\"{nc_section['anchor']}\">{nc_section['html_body']}</div>",
        )

    # 附录
    ic = report.get("factor_ic") or {}
    if ic.get("factors"):
        rows = "".join(
            f"<li><strong>{r.get('label') or r.get('factor')}</strong>: IC {r.get('ic')} (n={r.get('sample_count')})</li>"
            for r in (ic.get("factors") or [])[:8]
        )
        section("附录·因子 IC", f"<ul>{rows}</ul>")

    ols_section = build_factor_ols_export_section(report.get("factor_ols") or {})
    if ols_section:
        section(
            f"附录·{ols_section['title']}",
            f'<div id="{ols_section["anchor"]}">{ols_section["html_body"]}</div>',
        )

    ws = report.get("weight_suggest") or {}
    if ws.get("success"):
        cur = ws.get("current_weights") or {}
        sug = ws.get("suggested_weights") or {}
        tr = "".join(
            f"<tr><td>{k}</td><td>{cur.get(k)}</td><td>{sug.get(k)}</td></tr>" for k in cur
        )
        note = ws.get("note") or "遗留 IC 小步权；不驱动选股"
        section(
            f"附录·{WEIGHT_SUGGEST_TITLE}",
            f"<p class='meta'>{note}</p>"
            f"<table><thead><tr><th>因子</th><th>当前</th><th>建议</th></tr></thead><tbody>{tr}</tbody></table>",
        )

    tsug = report.get("threshold_suggest") or {}
    if tsug.get("success"):
        cur = tsug.get("current_thresholds") or {}
        sug = tsug.get("suggested_thresholds") or {}
        tr = "".join(
            f"<tr><td>{k}</td><td>{cur.get(k)}</td><td>{sug.get(k)}</td></tr>" for k in cur
        )
        meta = (
            "OOS 扫旧 0–100 规则分；当前 stance 为 ŷ%，已跳过自动改门槛。"
            if tsug.get("skipped_apply") or tsug.get("score_scale") == "predicted"
            else "启发式 0–100 门槛路径；须人审后合并，不自动写盘。"
        )
        section(
            "附录·stance 阈值建议",
            f"<p class='meta'>{meta}</p>"
            f"<table><thead><tr><th>阈值</th><th>当前</th><th>建议</th></tr></thead><tbody>{tr}</tbody></table>",
        )

    body = "\n".join(sections) or "<p>无报告内容</p>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>量化研究日报</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #111; }}
    h1 {{ font-size: 1.4rem; }}
    h2 {{ font-size: 1.05rem; margin-top: 1.2rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; }}
    th {{ background: #f9fafb; }}
    .meta {{ color: #6b7280; font-size: 0.9rem; }}
    .report-toc ul {{ margin: 0; padding-left: 1.2rem; }}
    .report-toc a {{ color: #2563eb; text-decoration: none; }}
    .foot {{ margin-top: 24px; color: #6b7280; font-size: 0.85rem; }}
  </style>
</head>
<body>
  <h1>量化研究日报</h1>
  <p class="meta">生成时间 {ts}</p>
  {body}
  <p class="foot">以上为量化研究摘要（选股真源=ŷ · 主叙事=组ŷ/簿/OOS/横截面/历史回测）；市场有风险，不保证收益，不代客下单。</p>
</body>
</html>"""

def export_quant_report(
    report: Optional[Dict[str, Any]] = None,
    *,
    fmt: str = "markdown",
) -> Dict[str, Any]:
    if not report:
        return {"success": False, "error": "无报告数据"}
    if report.get("empty"):
        return {"success": False, "error": "quant_daily.json 为空"}

    fmt = (fmt or "markdown").strip().lower()
    if fmt == "html":
        return {
            "success": True,
            "format": "html",
            "filename": "quant_daily.html",
            "content": render_quant_report_html(report),
            "executive_summary": build_report_executive_summary(report),
            "export_toc": build_report_export_toc(report),
        }

    return {
        "success": True,
        "format": "markdown",
        "filename": "quant_daily.md",
        "content": render_quant_report_markdown(report),
        "executive_summary": build_report_executive_summary(report),
        "export_toc": build_report_export_toc(report),
    }


def export_quant_report_markdown(report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return export_quant_report(report, fmt="markdown")


def _portfolio_bt_engine(ps: Optional[dict] = None, params: Optional[dict] = None) -> str:
    p = params if isinstance(params, dict) else {}
    if not p and isinstance(ps, dict):
        p = ps.get("params") if isinstance(ps.get("params"), dict) else {}
    return str((ps or {}).get("engine") or p.get("engine") or "")


def _topk_score_axis_note(ps: Optional[dict] = None) -> str:
    """历史回测 / Top-K 分数口径一句（日报 / 导出共用）。"""
    params = (ps or {}).get("params") if isinstance(ps, dict) else None
    if not isinstance(params, dict):
        params = {}
    if _portfolio_bt_engine(ps, params) == "paper_replay":
        return "每个交易日 09:30 按 y_fuse/y_on ranking 调仓（对齐历史回测页）"
    tau_on = params.get("apply_tau_buy_gate")
    if tau_on is True:
        return "选股键=ŷ_trade · τ 闸开（非默认历史路径）"
    return "选股键=ŷ_EOD · 关 τ 闸（日线无可靠分钟 τ；≠ live ŷ_trade）"


def _paper_max_positions_for_report() -> Optional[int]:
    try:
        from core.strategy import backtest_portfolio_defaults

        v = backtest_portfolio_defaults().get("max_positions")
        return int(v) if v is not None else None
    except Exception:  # noqa: BLE001 — 导出展示用，缺省不挡报告
        logger.debug("paper max_positions fallback failed", exc_info=True)
        return None


def _topk_run_config_line(ps: Optional[dict] = None) -> str:
    """日报 / 导出：历史回测 rank 参数或旧 Top-K 配置一行。"""
    params = (ps or {}).get("params") if isinstance(ps, dict) else None
    if not isinstance(params, dict):
        params = {}
    n = params.get("stock_count")
    if n is None and isinstance(ps, dict):
        n = len(ps.get("loaded_stocks") or []) or None
    lookback = params.get("lookback")
    cost = (ps or {}).get("cost_model") or params.get("cost_model") or params.get("cost_mode")
    apply_costs = params.get("apply_costs")
    if apply_costs is None and cost:
        apply_costs = str(cost) not in ("zero", "off")

    def _cost_bit() -> Optional[str]:
        if apply_costs is True:
            return f"成本={cost or '开'}"
        if apply_costs is False:
            return "成本=关"
        if cost:
            return f"成本={cost}"
        return None

    if _portfolio_bt_engine(ps, params) == "paper_replay":
        bits = [
            "引擎=paper_replay/rank_lots",
            f"ON_Alpha={params.get('y_on_alpha') if params.get('y_on_alpha') is not None else '—'}",
            f"Rank入场={params.get('rank_enter') if params.get('rank_enter') is not None else '—'}",
            f"Rank强={params.get('rank_strong') if params.get('rank_strong') is not None else '—'}",
        ]
        cb = _cost_bit()
        if cb:
            bits.append(cb)
        if lookback is not None:
            bits.append(f"lookback={lookback}")
        if n:
            bits.append(f"池{n}")
        return "配置：" + " · ".join(bits)

    k = params.get("top_k")
    h = params.get("horizon_days")
    yhat_h = params.get("yhat_horizon_days")
    paper_k = params.get("paper_max_positions")
    if paper_k is None:
        paper_k = _paper_max_positions_for_report()
    paper_h = params.get("paper_horizon_days")
    ymin = params.get("min_predicted_score")

    def _as_int(v: Any) -> Optional[int]:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    k_i, paper_k_i = _as_int(k), _as_int(paper_k)
    h_i, paper_h_i, yhat_i = _as_int(h), _as_int(paper_h), _as_int(yhat_h)
    k_bit = f"K={k if k is not None else '—'}"
    if k_i is not None and paper_k_i is not None and k_i != paper_k_i:
        k_bit += f"（≠纸面 {paper_k_i}）"
    elif k_i is not None and paper_k_i is not None:
        k_bit += "（纸面）"

    h_bit = f"持有 h={h if h is not None else '—'}日"
    if h_i is not None and paper_h_i is not None and h_i != paper_h_i:
        h_bit += f"（≠纸面 {paper_h_i}）"

    bits = [k_bit, h_bit]
    if yhat_i is not None:
        bits.append(f"ŷ标签={yhat_i}日")
    if ymin is not None:
        bits.append(f"ŷ≥{ymin}%")
    cb = _cost_bit()
    if cb:
        bits.append(cb)
    if lookback is not None:
        bits.append(f"lookback={lookback}")
    if n:
        bits.append(f"池{n}")
    return "配置：" + " · ".join(bits)


def build_portfolio_backtest_markdown_lines(ps: Dict[str, Any]) -> List[str]:
    """R4.4 · 与回溯页块序对齐的 MD 行（KPI · 成本 · 归因 · PIT · OOS · regime · 信号成交）。"""
    m = ps.get("metrics") or {}
    total_ret = ps.get("total_return_pct")
    if total_ret is None:
        total_ret = m.get("total_return_pct")
    win_rate = ps.get("win_rate_pct")
    if win_rate is None:
        win_rate = m.get("win_rate_pct")
    max_dd = ps.get("max_drawdown_pct")
    if max_dd is None:
        max_dd = m.get("max_drawdown_pct")
    trade_n = ps.get("trade_count")
    if trade_n is None:
        trade_n = m.get("trade_count")
    engine = _portfolio_bt_engine(ps)
    engine_label = engine or "paper_replay"
    if engine == "paper_replay":
        engine_bit = f"- 引擎：{engine_label}（rank_lots · 对齐历史回测）"
    else:
        engine_bit = f"- 引擎：{engine_label}（研究腿聚合）"
    lines: List[str] = [
        f"- {_topk_run_config_line(ps)}",
        f"- 累计收益：**{total_ret}%**",
        f"- 胜率：{win_rate}%",
        f"- 最大回撤：{max_dd}%",
        f"- 交易次数：{trade_n}",
        f"- 分数口径：{_topk_score_axis_note(ps)}",
        f"- 成本：{ps.get('cost_model') or (ps.get('params') or {}).get('cost_mode') or '—'}",
        f"- 标的：{', '.join(ps.get('loaded_stocks') or [])}",
        engine_bit,
    ]
    pr = ps.get("paper_replay") or {}
    if engine != "paper_replay" and pr.get("success"):
        lines.append(
            f"- 纸面回放（可实现）：累计 **{pr.get('total_return_pct')}%** · "
            f"回撤 {pr.get('max_drawdown_pct')}% · 成交 {pr.get('trade_count')}"
            f" · {(pr.get('note') or '')[:80]}"
        )
    elif engine != "paper_replay" and isinstance(pr, dict) and pr.get("error"):
        lines.append(f"- 纸面回放：不可用（{pr.get('error')}）")
    cc = ps.get("cost_compare") or {}
    if cc.get("ok"):
        lines.append(
            f"- 成本对照Δ：{cc.get('return_gap_pp')}pp"
            + (
                f" · 均冲击 {cc.get('avg_impact_bps')}bps"
                if cc.get("avg_impact_bps") is not None
                else ""
            )
        )
    ca = ps.get("cost_assumptions") or {}
    if ca.get("ok"):
        mode = ca.get("cost_mode") or ca.get("model")
        turn = ca.get("turnover_cost_sum_pct")
        turn_s = f" · 累计换手成本 {turn}%" if turn is not None and mode == "turnover" else ""
        lines.append(
            f"- 成本假设：模型 {mode} · 佣金 {ca.get('commission_bps')}bps · "
            f"印花税(卖) {ca.get('stamp_duty_bps_sell')}bps · 滑点 {ca.get('base_slippage_bps')}bps"
            f"{turn_s}"
        )

    attr = ps.get("attribution") or {}
    if attr.get("ok"):
        br = attr.get("brinson") or {}
        lines.append(
            f"- 归因选股超额：{attr.get('selection_excess_pct')}%"
            + (
                f" · Brinson A/S/I={br.get('allocation_pct')}/{br.get('selection_pct')}/{br.get('interaction_pct')}"
                if br.get("ok")
                else ""
            )
        )
        fp = attr.get("factor_proxy") or {}
        if fp.get("ok"):
            lines.append(f"- score 高低半组差：{fp.get('score_spread_pct')}pp")

    pit = ps.get("pit_report") or {}
    if pit:
        lines.append(
            f"- PIT：日线={pit.get('bars_pit')} · 财务={pit.get('fundamentals_pit')}"
        )

    oos = ps.get("oos_summary") or {}
    if oos.get("ok"):
        flag = " **失败**" if oos.get("failed") else ""
        lines.append(
            f"- OOS{flag}：内 {oos.get('is_return_pct')}% / 外 {oos.get('oos_return_pct')}%"
            + (f" · {oos.get('fail_reason')}" if oos.get("fail_reason") else "")
        )

    sic = ps.get("score_ic") or {}
    if sic.get("ok"):
        pos = ""
        if sic.get("positive_ic_days") is not None and sic.get("day_count"):
            pos = f" · 正IC日 {sic.get('positive_ic_days')}/{sic.get('day_count')}"
        lines.append(
            f"- 截面 IC：均值 {sic.get('ic_mean')} · ICIR {sic.get('icir')} · 日数 {sic.get('day_count')}"
            f"{pos}"
            + (
                f" · 滚动窗 {sic.get('roll_window')}"
                if sic.get("roll_window")
                else ""
            )
        )
        # 尾部日度 IC 摘要（最多 8 点）
        series = sic.get("ic_series_tail") or []
        if len(series) >= 3:
            tail = series[-8:]
            parts = [f"{r.get('date')}:{r.get('ic')}" for r in tail]
            lines.append(f"  - 日度IC尾：{' · '.join(parts)}")
    elif sic.get("reason"):
        lines.append(f"- 截面 IC：不可用（{sic.get('reason')}）")

    qb = ps.get("quantile_backtest") or {}
    if qb.get("ok"):
        mono = "单调↑" if qb.get("monotonic_increasing") else "非单调"
        lines.append(
            f"- 分层：{mono} · Q高−Q低 {qb.get('q_high_minus_q_low_pct')}% · 期数 {qb.get('fold_count')}"
        )
        lines.append("")
        lines.append("| 分层 | 累计收益% | 胜率% | 期数 | 终值 |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for row in qb.get("quantiles") or []:
            lines.append(
                f"| {row.get('label') or ''} | {row.get('total_return_pct')} | "
                f"{row.get('win_rate_pct')} | {row.get('trade_count')} | {row.get('final_equity')} |"
            )
        ls_curve = qb.get("long_short_equity_curve") or []
        if ls_curve:
            lines.append(
                f"- Q高−Q低终值：{(ls_curve[-1] or {}).get('equity')}（起点100）"
            )
        lines.append("")

    align = ps.get("ic_equity_align") or {}
    if align.get("ok"):
        pos = align.get("pos_ic") or {}
        neg = align.get("neg_ic") or {}
        favor = "同向" if align.get("aligned_favor_pos_ic") else "⚠正IC窗未优于非正"
        lines.append(
            f"- IC↔净值对齐：{favor} · 正IC窗均 {pos.get('avg_return_pct')}% "
            f"vs 非正 {neg.get('avg_return_pct')}% · 差 {align.get('avg_return_spread_pp')}pp"
        )

    hints = ps.get("promote_hints") or []
    if hints:
        lines.append("- Promote 提示：")
        for h in hints[:8]:
            lines.append(f"  - [{h.get('level') or 'info'}] {h.get('text') or h.get('code')}")

    bench = ps.get("benchmark") or {}
    if bench.get("ok"):
        lines.append(
            f"- 基准（{bench.get('benchmark_label')}）：{bench.get('benchmark_return_pct')}% · "
            f"超额 {bench.get('excess_pct')}%"
            + (
                f" · 年化超额 {bench.get('ann_excess_pct')}%"
                if bench.get("ann_excess_pct") is not None
                else ""
            )
            + (
                f" · IR {bench.get('ann_ir') or bench.get('ir')}"
                if bench.get("ann_ir") is not None or bench.get("ir") is not None
                else ""
            )
            + (
                " · **⚠绝对+超额−**"
                if bench.get("warn_abs_pos_excess_neg")
                else ""
            )
        )

    # P0：强制打印 α/β 分账（有则写；与绝对累计收益分列）
    legs = ps.get("alpha_beta_legs") or m.get("alpha_beta_legs") or {}
    if isinstance(legs, dict) and (
        legs.get("alpha_leg_approx_pct") is not None
        or legs.get("beta_leg_approx_pct") is not None
        or legs.get("total_return_pct") is not None
    ):
        lines.append(
            "- **收益分账（近似）**："
            f"绝对 {legs.get('total_return_pct')}% · "
            f"α腿(超额) {legs.get('alpha_leg_approx_pct')}% · "
            f"β腿≈ {legs.get('beta_leg_approx_pct')}%"
            + (f" · IR {legs.get('ir')}" if legs.get("ir") is not None else "")
            + " · 多头组合绝对收益仍含市场敞口"
        )

    bex = ps.get("benchmark_excess") or {}
    if isinstance(bex, dict) and bex.get("ok"):
        lines.append(
            f"- 北极星超额包：累计超额≈{bex.get('total_excess_approx_pct')}% · "
            f"年化IR {bex.get('ann_ir')} · 对齐日 {bex.get('aligned_days')}"
        )

    req = ps.get("request") or {}
    if req.get("dropout_n"):
        lines.append(f"- TopK-Dropout：dropout_n={req.get('dropout_n')}")
    if req.get("exclude_st"):
        lines.append("- 宇宙过滤：剔 ST")
    if req.get("min_avg_amount_pctile") is not None:
        lines.append(f"- 宇宙过滤：成交额≥池内 {req.get('min_avg_amount_pctile')}% 分位")

    wf = ps.get("wf_slices") or {}
    if wf.get("ok") or wf.get("folds"):
        pos = wf.get("positive_test_folds")
        meas = wf.get("measured_test_folds")
        pos_s = f" · 正窗 {pos}/{meas}" if pos is not None and meas is not None else ""
        lines.append(
            f"- Walk-forward：均收益 {wf.get('mean_test_return_pct')}%{pos_s}"
        )

    regime = ps.get("regime_summary") or {}
    if regime.get("ok"):
        lines.append(f"- Regime（末段）：{regime.get('regime')} · vol={regime.get('vol')}")
    rb = ps.get("regime_buckets") or {}
    if rb.get("ok") and rb.get("buckets"):
        parts = [
            f"{b.get('regime')}:{b.get('avg_return_pct')}%×{b.get('trade_count')}"
            for b in (rb.get("buckets") or [])[:5]
        ]
        lines.append(f"- Regime 分桶：{' · '.join(parts)}")
    mcs = ps.get("macro_context_summary") or {}
    if mcs.get("ok"):
        lines.append(
            f"- 宏观对齐：海外科技均 {mcs.get('avg_overseas_tech_1d_pct')}% · "
            f"A50 {mcs.get('avg_a50_1d_pct')}% · "
            f"压力 {mcs.get('avg_liquidity_stress_score')}"
        )

    sa = ps.get("source_audit") or {}
    if sa.get("status"):
        lines.append(
            f"- 源审计：{sa.get('status')} · fallback {sa.get('fallback_count') or 0}"
        )

    fills = ps.get("signal_fill_sample") or []
    if fills:
        lines.append("")
        lines.append("信号–成交样本（最近）：")
        lines.append("")
        lines.append("| 信号日 | 代码 | ŷ_EOD | 意图价 | 成交价 | 出场价 | 状态 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
        for row in fills[-12:]:
            lines.append(
                f"| {row.get('signal_date') or ''} | {row.get('stock_code') or ''} | "
                f"{_fmt_fill_score(row if isinstance(row, dict) else None)} | "
                f"{row.get('intent_price') if row.get('intent_price') is not None else '—'} | "
                f"{row.get('fill_price') if row.get('fill_price') is not None else '—'} | "
                f"{row.get('exit_price') if row.get('exit_price') is not None else '—'} | "
                f"{row.get('status') or '—'} |"
            )
        lines.append("")
        lines.append(
            "_历史 Top-K：选股键=ŷ_EOD、关 τ 闸；有 ŷ 写 `x.xxx%`，仅规则分写 `Hxx.x`；通常无 ŷ_τ_"
        )

    ns = ps.get("north_star") or {}
    if ns:
        lines.append(
            f"- 北极星引用：Sharpe={ns.get('rolling_sharpe')} · Calmar={ns.get('calmar')}"
        )
    return lines


def render_portfolio_backtest_report_markdown(result: Dict[str, Any]) -> str:
    """单次回测机构报告 MD（回溯页一键导出）。"""
    if not result or not result.get("success"):
        return "# Top-K 回测报告\n\n回测失败或无数据。\n"
    # normalize flat metrics onto summary-like dict
    m = result.get("metrics") or {}
    pack = dict(result)
    pack.setdefault("total_return_pct", m.get("total_return_pct"))
    pack.setdefault("win_rate_pct", m.get("win_rate_pct"))
    pack.setdefault("max_drawdown_pct", m.get("max_drawdown_pct"))
    pack.setdefault("trade_count", m.get("trade_count") or result.get("trade_count"))
    lines = [
        "# Top-K 回测报告（研究）",
        "",
        f"_生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        "## KPI · 成本 · 归因 · PIT · Regime",
        "",
    ]
    lines.extend(build_portfolio_backtest_markdown_lines(pack))
    lines.extend(
        [
            "",
            "---",
            "",
            "_研究近似报告；非交易所仿真，不代客下单。_",
            "",
        ]
    )
    return "\n".join(lines)


def export_portfolio_backtest_report(
    result: Optional[Dict[str, Any]] = None,
    *,
    fmt: str = "markdown",
) -> Dict[str, Any]:
    if not result or not result.get("success"):
        return {"success": False, "error": "无有效回测结果"}
    fmt = (fmt or "markdown").strip().lower()
    md = render_portfolio_backtest_report_markdown(result)
    if fmt == "html":
        # 轻量 HTML：预格式化 MD 转 pre（避免引入 markdown 库）
        import html as html_lib

        body = f"<pre class='md'>{html_lib.escape(md)}</pre>"
        content = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
            "<title>Top-K 回测报告</title>"
            "<style>body{font-family:system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}"
            "pre.md{white-space:pre-wrap;line-height:1.45}</style></head>"
            f"<body>{body}<p>研究近似；不代客下单。</p></body></html>"
        )
        return {
            "success": True,
            "format": "html",
            "filename": "portfolio_backtest_report.html",
            "content": content,
        }
    return {
        "success": True,
        "format": "markdown",
        "filename": "portfolio_backtest_report.md",
        "content": md,
    }
