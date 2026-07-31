"""量化日报 Markdown / HTML 导出（P14.2 / P15.2）。"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional


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
        raw = item.get("score")
        if raw is None:
            continue
        try:
            scores.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not scores:
        return f"横截面：Top {len(ranked)} 只"
    med = median(scores)
    return (
        f"横截面 score：Top {len(ranked)} 只 · 最高 {max(scores):.1f} · 中位 {med:.1f}"
    )


def summarize_cross_section_scores(cross_section: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """横截面 score 摘要（P77：解读 / compact 共用）。"""
    if not cross_section or not cross_section.get("success"):
        return None
    ranked = _cross_section_ranked_list(cross_section)
    if not ranked:
        return None
    scores: List[float] = []
    top: List[Dict[str, Any]] = []
    for item in ranked[:5]:
        raw_score = item.get("score")
        score_f: Optional[float] = None
        if raw_score is not None:
            try:
                score_f = float(raw_score)
                scores.append(score_f)
            except (TypeError, ValueError):
                pass
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
    }
    if scores:
        out["max_score"] = round(max(scores), 1)
        out["median_score"] = round(float(median(scores)), 1)
    neut = cross_section.get("neutralization") or {}
    if neut.get("applied"):
        out["neutralization_applied"] = True
        out["neutralization_method"] = neut.get("method")
    return out


def build_cross_section_export_section(cross_section: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """横截面 score 专节（P80：MD/HTML 导出）。"""
    summary = summarize_cross_section_scores(cross_section)
    if not summary:
        return None
    ranked = _cross_section_ranked_list(cross_section)
    md_lines = [f"- {summary['summary_line']}"]
    neut = cross_section.get("neutralization") or {}
    if neut.get("applied"):
        md_lines.append(f"- 截面中性化：{neut.get('method') or 'zscore'}")
    for i, row in enumerate(ranked[:10], 1):
        name = row.get("stock_name") or row.get("stock_code") or "—"
        raw = f" · raw {row['score_raw']}" if row.get("score_raw") is not None else ""
        md_lines.append(f"- {i}. {name} · score {row.get('score')}{raw}")
    if cross_section.get("note"):
        md_lines.append(f"- _{cross_section['note']}_")

    tr = ""
    for i, row in enumerate(ranked[:10], 1):
        name = row.get("stock_name") or row.get("stock_code") or "—"
        raw = row.get("score_raw")
        raw_cell = f"{raw}" if raw is not None else "—"
        tr += (
            f"<tr><td>{i}</td><td>{name}</td>"
            f"<td class='num'>{row.get('score')}</td>"
            f"<td class='num'>{raw_cell}</td></tr>"
        )
    html_body = (
        f"<p>{summary['summary_line']}</p>"
        "<table><thead><tr><th>#</th><th>标的</th><th>score</th><th>score_raw</th></tr></thead>"
        f"<tbody>{tr}</tbody></table>"
    )
    if cross_section.get("note"):
        html_body += f"<p class='meta'>{cross_section['note']}</p>"

    return {
        "title": "横截面 score",
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
    line = (
        f"因子 OLS：R²={factor_ols.get('r_squared')} · n={factor_ols.get('sample_count')}"
        f"{z_tag} （全量因子研究用，不自动写 signal_config）"
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


def build_neutral_compare_export_section(nc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """中性化对照专节（P58：MD/HTML 导出共用）。"""
    if not nc or not nc.get("success"):
        return None

    delta = nc.get("delta") or {}
    stocks = ", ".join(nc.get("loaded_stocks") or []) or "—"
    md_lines = [
        f"- 结论：**{nc.get('winner')}** 更优",
        f"- 中性化累计：**{nc.get('neutralized_total_return_pct')}%** · 胜率 {nc.get('neutralized_win_rate_pct')}%",
        f"- 绝对分累计：**{nc.get('absolute_total_return_pct')}%** · 胜率 {nc.get('absolute_win_rate_pct')}%",
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
        "<thead><tr><th>维度</th><th>中性化</th><th>绝对分</th><th>Δ</th></tr></thead><tbody>"
        f"<tr><td>累计收益</td><td>{nc.get('neutralized_total_return_pct')}%</td>"
        f"<td>{nc.get('absolute_total_return_pct')}%</td>"
        f"<td>{delta.get('total_return_pct')}%</td></tr>"
        f"<tr><td>胜率</td><td>{nc.get('neutralized_win_rate_pct')}%</td>"
        f"<td>{nc.get('absolute_win_rate_pct')}%</td>"
        f"<td>{delta.get('win_rate_pct')}%</td></tr>"
        f"<tr><td>交易次数</td><td colspan=\"2\">—</td><td>{delta.get('trade_count')}</td></tr>"
        "</tbody></table>"
        f"<p>结论：<strong>{nc.get('winner')}</strong> · {nc.get('interpretation') or '—'}</p>"
        f"<p class='meta'>标的：{stocks}</p>"
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
    """导出目录（P65：含 neutral-compare 锚点）。"""
    entries: List[tuple] = [("一页摘要", "一页摘要")]

    if report.get("factor_ic"):
        entries.append(("因子 IC", "因子-ic"))
    ols_section = build_factor_ols_export_section(report.get("factor_ols") or {})
    if ols_section:
        entries.append((ols_section["title"], ols_section["anchor"]))
    cs_section = build_cross_section_export_section(report.get("cross_section") or {})
    if cs_section:
        entries.append((cs_section["title"], cs_section["anchor"]))
    if (report.get("weight_suggest") or {}).get("success"):
        entries.append(("权重建议", "权重建议"))
    if (report.get("threshold_suggest") or {}).get("success"):
        entries.append(("stance 阈值建议", "stance-阈值建议"))
    if (report.get("portfolio_backtest_summary") or {}).get("success"):
        entries.append(("Top-K 回测摘要", "topk-回测摘要"))

    nc_section = build_neutral_compare_export_section(
        report.get("portfolio_neutral_compare_summary") or {}
    )
    if nc_section:
        entries.append((nc_section["title"], nc_section["anchor"]))

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
    """量化日报一页摘要（P27.1，供 MD/HTML 顶部卡片）。"""
    bullets: List[str] = []

    ic = report.get("factor_ic") or {}
    factors = ic.get("factors") or []
    if factors:
        top = factors[0]
        bullets.append(
            f"因子 IC：{top.get('label') or top.get('factor')} {top.get('ic')} "
            f"(样本 n={ic.get('sample_count') or top.get('sample_count') or '—'})"
        )

    ols_sm = summarize_factor_ols(report.get("factor_ols") or {})
    if ols_sm:
        bullets.append(ols_sm["summary_line"])

    cs = report.get("cross_section") or {}
    if cs.get("success"):
        ranked = _cross_section_ranked_list(cs)
        if ranked:
            bullets.append(_score_summary_bullet(ranked))

    ws = report.get("weight_suggest") or {}
    if ws.get("success"):
        bullets.append("权重建议：有（须手动 merge diff，不自动改配置）")

    tsug = report.get("threshold_suggest") or {}
    if tsug.get("success"):
        bullets.append("stance 阈值建议：有")

    ps = report.get("portfolio_backtest_summary") or {}
    if ps.get("success"):
        bullets.append(
            f"Top-K 回测：累计 {ps.get('total_return_pct')}% · "
            f"胜率 {ps.get('win_rate_pct')}% · 交易 {ps.get('trade_count')}"
        )

    nc = report.get("portfolio_neutral_compare_summary") or {}
    if nc.get("success"):
        bullets.append(
            f"中性化对照：{nc.get('interpretation') or '—'} "
            f"(Δ累计 {((nc.get('delta') or {}).get('total_return_pct'))}%)"
        )

    return {
        "success": True,
        "bullet_count": len(bullets),
        "bullets": bullets,
        "note": "一页摘要；详细见下文各节。",
    }


def render_quant_report_markdown(report: Dict[str, Any]) -> str:
    """将 quant_daily 报告渲染为 Markdown（不含自动交易建议）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"# 量化研究日报", "", f"_生成时间 {ts}_", ""]

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

    ic = report.get("factor_ic") or {}
    if ic:
        fac_lines = []
        for row in (ic.get("factors") or [])[:8]:
            fac_lines.append(
                f"- **{row.get('label') or row.get('factor')}**: IC {row.get('ic')} (n={row.get('sample_count')})"
            )
        parts.extend(_lines("因子 IC", fac_lines or ["无因子 IC 数据"]))

    ols_section = build_factor_ols_export_section(report.get("factor_ols") or {})
    if ols_section:
        parts.extend(
            [f"## {ols_section['title']}", ""]
            + ols_section["markdown_lines"]
            + [""]
        )

    cs_section = build_cross_section_export_section(report.get("cross_section") or {})
    if cs_section:
        parts.extend(
            [f"## {cs_section['title']}", ""]
            + cs_section["markdown_lines"]
            + [""]
        )

    ws = report.get("weight_suggest") or {}
    if ws.get("success"):
        cur = ws.get("current_weights") or {}
        sug = ws.get("suggested_weights") or {}
        wl = [f"| {k} | {cur.get(k)} | {sug.get(k)} |" for k in cur]
        parts.extend(
            [
                "## 权重建议",
                "",
                "| 因子 | 当前 | 建议 |",
                "| --- | ---: | ---: |",
                *wl,
                "",
                *(f"- {r}" for r in (ws.get("rationale") or [])[:5]),
                "",
            ]
        )

    tsug = report.get("threshold_suggest") or {}
    if tsug.get("success"):
        cur = tsug.get("current_thresholds") or {}
        sug = tsug.get("suggested_thresholds") or {}
        tl = [f"| {k} | {cur.get(k)} | {sug.get(k)} |" for k in cur]
        parts.extend(
            [
                "## stance 阈值建议",
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
            parts.append(
                f"> watching 聚合：{agg.get('stock_count')} 只，中位最优 min_score={agg.get('median_best_min_score')}"
            )
            parts.append("")

    ps = report.get("portfolio_backtest_summary") or {}
    if ps.get("success"):
        detail_lines = build_portfolio_backtest_markdown_lines(ps)
        parts.extend(_lines("Top-K 回测摘要", detail_lines))

    nc = report.get("portfolio_neutral_compare_summary") or {}
    nc_section = build_neutral_compare_export_section(nc)
    if nc_section:
        parts.extend(
            [f"## {nc_section['title']}", ""]
            + nc_section["markdown_lines"]
            + [""]
        )

    parts.extend(
        [
            "---",
            "",
            "_以上为量化研究摘要，市场有风险，不保证收益，不代客下单。_",
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

    ic = report.get("factor_ic") or {}
    if ic.get("factors"):
        rows = "".join(
            f"<li><strong>{r.get('label') or r.get('factor')}</strong>: IC {r.get('ic')} (n={r.get('sample_count')})</li>"
            for r in (ic.get("factors") or [])[:8]
        )
        section("因子 IC", f"<ul>{rows}</ul>")

    ols_section = build_factor_ols_export_section(report.get("factor_ols") or {})
    if ols_section:
        section(
            ols_section["title"],
            f'<div id="{ols_section["anchor"]}">{ols_section["html_body"]}</div>',
        )

    cs_section = build_cross_section_export_section(report.get("cross_section") or {})
    if cs_section:
        section(cs_section["title"], cs_section["html_body"])

    ws = report.get("weight_suggest") or {}
    if ws.get("success"):
        cur = ws.get("current_weights") or {}
        sug = ws.get("suggested_weights") or {}
        tr = "".join(
            f"<tr><td>{k}</td><td>{cur.get(k)}</td><td>{sug.get(k)}</td></tr>" for k in cur
        )
        section(
            "权重建议",
            f"<table><thead><tr><th>因子</th><th>当前</th><th>建议</th></tr></thead><tbody>{tr}</tbody></table>",
        )

    tsug = report.get("threshold_suggest") or {}
    if tsug.get("success"):
        cur = tsug.get("current_thresholds") or {}
        sug = tsug.get("suggested_thresholds") or {}
        tr = "".join(
            f"<tr><td>{k}</td><td>{cur.get(k)}</td><td>{sug.get(k)}</td></tr>" for k in cur
        )
        section(
            "stance 阈值建议",
            f"<table><thead><tr><th>阈值</th><th>当前</th><th>建议</th></tr></thead><tbody>{tr}</tbody></table>",
        )

    ps = report.get("portfolio_backtest_summary") or {}
    if ps.get("success"):
        lis = "".join(
            f"<li>{line[2:] if line.startswith('- ') else line}</li>"
            for line in build_portfolio_backtest_markdown_lines(ps)
            if line.startswith("- ")
        )
        section("Top-K 回测摘要", f"<ul>{lis}</ul>")

    nc = report.get("portfolio_neutral_compare_summary") or {}
    nc_section = build_neutral_compare_export_section(nc)
    if nc_section:
        section(
            nc_section["title"],
            f"<div id=\"{nc_section['anchor']}\">{nc_section['html_body']}</div>",
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
  <p class="foot">以上为量化研究摘要，市场有风险，不保证收益，不代客下单。</p>
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


def build_portfolio_backtest_markdown_lines(ps: Dict[str, Any]) -> List[str]:
    """R4.4 · 与回溯页块序对齐的 MD 行（KPI · 成本 · 归因 · PIT · OOS · regime · 信号成交）。"""
    lines: List[str] = [
        f"- 累计收益：**{ps.get('total_return_pct')}%**",
        f"- 胜率：{ps.get('win_rate_pct')}%",
        f"- 最大回撤：{ps.get('max_drawdown_pct')}%",
        f"- 交易次数：{ps.get('trade_count')}",
        f"- 标的：{', '.join(ps.get('loaded_stocks') or [])}",
    ]
    m = ps.get("metrics") or {}
    if m.get("total_return_pct") is not None and ps.get("total_return_pct") is None:
        lines[0] = f"- 累计收益：**{m.get('total_return_pct')}%**"
    if m.get("win_rate_pct") is not None and ps.get("win_rate_pct") is None:
        lines[1] = f"- 胜率：{m.get('win_rate_pct')}%"
    if m.get("max_drawdown_pct") is not None and ps.get("max_drawdown_pct") is None:
        lines[2] = f"- 最大回撤：{m.get('max_drawdown_pct')}%"

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
        lines.append("| 信号日 | 代码 | score | 意图价 | 成交价 | 出场价 | 状态 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
        for row in fills[-12:]:
            lines.append(
                f"| {row.get('signal_date') or ''} | {row.get('stock_code') or ''} | "
                f"{row.get('score') if row.get('score') is not None else '—'} | "
                f"{row.get('intent_price') if row.get('intent_price') is not None else '—'} | "
                f"{row.get('fill_price') if row.get('fill_price') is not None else '—'} | "
                f"{row.get('exit_price') if row.get('exit_price') is not None else '—'} | "
                f"{row.get('status') or '—'} |"
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
