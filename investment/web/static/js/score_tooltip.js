/** 评分悬浮注释（交易执行持仓 / 数据中心观察表共用）。
 * 主叙事：收益分 ŷ + 因子系数 β（可正可负），非规则权重。
 */

import { escapeText } from "./paper/fmt.js";

const FACTOR_LABELS = {
  momentum: "动量",
  volume_price: "量价",
  relative_strength: "相对强弱",
  volatility: "波动",
  reversal: "反转",
  liquidity: "流动性",
  value: "估值",
  quality: "质量",
  ma_slope: "均线斜率",
  technical_pattern: "技术形态",
  weekly_confirm: "周线确认",
  gap_risk: "跳空风险",
  size: "规模",
  earnings_yield: "盈利收益率",
  growth: "成长",
  dividend: "股息",
  money_flow: "资金流",
  amihud: "Amihud",
  idio_momentum: "特异动量",
  alt_sentiment: "舆情",
};

function fmtSigned(v, digits = 3) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const t = n.toFixed(digits);
  return n > 0 ? `+${t}` : t;
}

function signCls(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "pos" : "neg";
}

function resolveYhat(raw) {
  const candidates = [raw && raw.predicted_score, raw && raw.score];
  if (raw && raw.formula_terms && raw.formula_terms.total != null) {
    candidates.unshift(raw.formula_terms.total);
  }
  for (const c of candidates) {
    const n = Number(c);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

export function formatScoreHero(raw) {
  const y = resolveYhat(raw);
  const yTxt = y == null ? "—" : `${fmtSigned(y, 3)}%`;
  const below = !!raw.below_min_score;
  const floor =
    raw && raw.min_score != null && Number.isFinite(Number(raw.min_score))
      ? Number(raw.min_score)
      : null;
  let gate = "";
    if (floor != null) {
    gate = `<div class="score-hero-gate${below ? " is-warn" : ""}">ŷ门槛 ${escapeText(
      Number.isFinite(floor) ? `${floor}%` : String(floor)
    )}${below ? " · 当前低于门槛" : ""}</div>`;
  }
  return (
    `<div class="score-hero">` +
    `<div class="score-hero-label">收益分 ŷ（表格 score）</div>` +
    `<div class="score-hero-value ${signCls(y)}">${escapeText(yTxt)}</div>` +
    `<div class="score-hero-hint">ŷ = α + Σ β·z（百分点）</div>` +
    `<div class="score-hero-semantics">` +
    `语义：模型预测的前瞻收益（%）· 非 0–100 规则分 · β 可正可负` +
    `</div>` +
    gate +
    `</div>`
  );
}

export function formatWeightSourceNote(raw) {
  const src = String((raw && raw.weight_source) || "").trim();
  const mode = String((raw && raw.cluster_mode) || "").trim();
  const rms = String((raw && raw.return_model_source) || "").trim();
  const label = raw && raw.cluster_label ? String(raw.cluster_label) : "";
  const ver =
    raw && raw.cluster_version != null && raw.cluster_version !== ""
      ? `v${raw.cluster_version}`
      : "";
  let line = "全局收益分模型";
  if (rms === "cluster_group_beta" || src.startsWith("cluster:")) {
    line = `分组因子系数 · ${
      src.startsWith("cluster:") ? src.slice("cluster:".length) : label || "组"
    }`;
    if (ver) line += ` · ${ver}`;
  } else if (src === "global+shadow") {
    line = "主分=全局 ŷ · 影子已算组 ŷ";
    if (label) line += `（${label}）`;
  } else if (src === "global_fallback") {
    line = "全局收益分回退（未映射到分组）";
  } else if (rms === "global" || src === "global" || !src) {
    if (mode && mode !== "off") line = `全局收益分 · mode=${mode}`;
  } else if (src) {
    line = src;
  }

  const bits = [
    `<div class="score-section-title">模型来源</div>`,
    `<div class="score-weight-source">${escapeText(line)}</div>`,
  ];
  return `<div class="score-weight-section">${bits.join("")}</div>`;
}

/** 分项拆解表：因子 / β / z / 贡献。有 terms 时优先于纯系数表。 */
export function formatFormulaTermsSection(raw) {
  const expl = raw && (raw.formula_terms || raw.score_formula_terms);
  if (!expl || typeof expl !== "object") return "";
  const terms = Array.isArray(expl.terms) ? expl.terms : [];
  if (!terms.length) return "";

  const rows = terms
    .map((t) => {
      const name = t.label || FACTOR_LABELS[t.key] || t.key || "—";
      const gated = !!t.gated;
      const nameExtra = gated ? "（闸关）" : t.note ? `（${t.note}）` : "";
      const beta = fmtSigned(t.beta, 3);
      const z = gated ? "—" : fmtSigned(t.z, 2);
      const contrib = gated ? "0" : fmtSigned(t.contrib, 3);
      return (
        `<tr${gated ? ' class="score-ft-gated"' : ""}>` +
        `<td class="score-ft-name" title="${escapeText(t.key || "")}">${escapeText(
          name + nameExtra
        )}</td>` +
        `<td class="num ${signCls(t.beta)}">${escapeText(beta)}</td>` +
        `<td class="num ${signCls(t.z)}">${escapeText(z)}</td>` +
        `<td class="num ${signCls(t.contrib)}">${escapeText(contrib)}</td>` +
        `</tr>`
      );
    })
    .join("");

  const alpha = fmtSigned(expl.intercept, 3);
  const total = fmtSigned(expl.total, 3);

  // 贡献条：用已有 contrib，不依赖异步模块（tooltip 内联）
  const barTerms = terms
    .filter((t) => t && !t.gated && Number.isFinite(Number(t.contrib)))
    .map((t) => ({
      key: t.key,
      label: t.label || FACTOR_LABELS[t.key] || t.key,
      contrib: Number(t.contrib),
    }));
  const peak = Math.max(...barTerms.map((t) => Math.abs(t.contrib)), 1e-9);
  const barsHtml = barTerms.length
    ? `<div class="yhat-contrib-bars" aria-label="因子贡献">` +
      barTerms
        .slice(0, 10)
        .map((r) => {
          const pct = Math.min(100, (Math.abs(r.contrib) / peak) * 100);
          const side = r.contrib >= 0 ? "pos" : "neg";
          const sign = r.contrib > 0 ? "+" : "";
          return (
            `<div class="yhat-contrib-row" title="${escapeText(r.key || "")}">` +
            `<span class="yhat-contrib-name">${escapeText(r.label)}</span>` +
            `<span class="yhat-contrib-track">` +
            `<span class="yhat-contrib-bar ${side}" style="width:${pct.toFixed(1)}%"></span>` +
            `</span>` +
            `<span class="yhat-contrib-val ${side}">${escapeText(
              `${sign}${r.contrib.toFixed(3)}`
            )}</span>` +
            `</div>`
          );
        })
        .join("") +
      `</div>`
    : "";

  return (
    `<div class="score-formula-section">` +
    `<div class="score-section-title">分项拆解</div>` +
    barsHtml +
    `<table class="score-formula-table">` +
    `<thead><tr>` +
    `<th>因子</th><th>β</th><th>z</th><th>贡献</th>` +
    `</tr></thead>` +
    `<tbody>` +
    `<tr class="score-ft-alpha">` +
    `<td>截距 α</td>` +
    `<td class="num">—</td>` +
    `<td class="num">—</td>` +
    `<td class="num ${signCls(expl.intercept)}">${escapeText(alpha)}</td>` +
    `</tr>` +
    rows +
    `<tr class="score-ft-total">` +
    `<td>合计 ŷ</td>` +
    `<td class="num">—</td>` +
    `<td class="num">—</td>` +
    `<td class="num ${signCls(expl.total)}">${escapeText(total)}%</td>` +
    `</tr>` +
    `</tbody></table>` +
    `<div class="score-formula-caption">β×z = 贡献；条长∝|贡献|；舆情闸关时贡献为 0</div>` +
    `</div>`
  );
}

/** 特征同构（X 轨）：财务 PIT / 指数 / 深度边界。 */
export function formatFeatureIsoSection(raw) {
  if (!raw) return "";
  const bits = [];
  const pit = raw.fundamentals_pit || {};
  if (pit && (pit.mode || pit.as_of || pit.decision_as_of)) {
    const mode = String(pit.mode || "—");
    const asOf = String(pit.as_of || pit.decision_as_of || "—");
    bits.push(`财务 PIT · mode=${mode} · as_of=${asOf}`);
    if (pit.ann_missing) bits.push("ann_missing（可用日缺公告日）");
    if (pit.non_pit) bits.push("非严格 PIT 快照");
    if (pit.ok === false) bits.push("财务点缺失/失败");
  }
  const idx = raw.index_meta || {};
  if (idx && (idx.benchmark || idx.reason || idx.ok != null)) {
    if (idx.ok) {
      bits.push(
        `指数 ${String(idx.benchmark || "")} · ${Number(idx.bar_count) || "?"} 根`
      );
    } else {
      bits.push(`指数不可用 · ${String(idx.reason || "no_index")}`);
    }
  }
  const depth = String(raw.fundamentals_depth || "").trim();
  if (depth) {
    const depthNote =
      (raw.fundamentals_depth_meta && raw.fundamentals_depth_meta.note) || "";
    bits.push(
      depth === "cn_full"
        ? "财务深度 · A 股完整（需 ingest）"
        : depth === "hk_shallow"
          ? "财务深度 · 港股浅"
          : depth === "us_shallow"
            ? "财务深度 · 美股/其他浅"
            : `财务深度 · ${depth}`
    );
    if (depthNote && depth !== "cn_full") bits.push(String(depthNote));
  }
  if (!bits.length) return "";
  return (
    `<div class="score-feature-iso-section">` +
    `<div class="score-section-title">特征同构</div>` +
    bits.map((b) => `<div class="score-sentiment-line">${escapeText(b)}</div>`).join("") +
    `</div>`
  );
}

/** 舆情先验（ŷ 外）旁路提示。 */
export function formatSentimentGateSection(raw) {
  if (!raw) return "";
  const bits = [];
  const prior = raw.sentiment_prior || {};
  const mode = String(prior.mode || "").trim();
  bits.push("舆情 = 先验旁路 · 不进 predicted_score / 非因子");
  if (mode) {
    const active = prior.active ? "触发" : "未触发";
    bits.push(`prior.mode=${mode} · ${active}`);
  } else if (
    raw.sentiment_include_in_score === false ||
    raw.sentiment_include_in_score === true
  ) {
    bits.push(
      raw.sentiment_include_in_score
        ? "遗留：include_in_score=true（不推荐；应走 prior.mode）"
        : "include_in_score=false（硬闸）"
    );
  }
  const hints = Array.isArray(raw.risk_hints) ? raw.risk_hints : [];
  for (const h of hints.slice(0, 2)) {
    if (h && h.note) bits.push(String(h.note));
  }
  const warns = Array.isArray(raw.warnings) ? raw.warnings : [];
  for (const w of warns.slice(0, 3)) {
    if (w) bits.push(String(w));
  }
  if (!bits.length) return "";
  return (
    `<div class="score-sentiment-section">` +
    `<div class="score-section-title">舆情先验</div>` +
    bits.map((b) => `<div class="score-sentiment-line">${escapeText(b)}</div>`).join("") +
    `</div>`
  );
}

/** 仅系数表（无分项拆解时回退）。 */
export function formatFactorWeightsSection(raw) {
  if (raw && (raw.formula_terms || raw.score_formula_terms)) {
    const expl = raw.formula_terms || raw.score_formula_terms;
    if (expl && Array.isArray(expl.terms) && expl.terms.length) return "";
  }
  const coefs = raw && (raw.factor_coefficients || raw.coefficients);
  const fw = raw && raw.factor_weights;
  const useCoefs = coefs && typeof coefs === "object" && Object.keys(coefs).length > 0;
  const map = useCoefs ? coefs : fw;
  if (!map || typeof map !== "object") return "";
  const keys = Object.keys(map).sort(
    (a, b) =>
      Math.abs(Number(map[b] || 0)) - Math.abs(Number(map[a] || 0)) ||
      String(a).localeCompare(String(b))
  );
  if (!keys.length) return "";
  const src = String((raw && raw.weight_source) || "").trim();
  const label = raw && raw.cluster_label ? String(raw.cluster_label) : "";
  const title = useCoefs
    ? src.startsWith("cluster:") || label
      ? `因子系数 β${label ? ` · ${label}` : ""}`
      : "因子系数 β"
    : src.startsWith("cluster:") || label
      ? `展示权(|β|)${label ? ` · ${label}` : ""}`
      : "展示权(|β|)";
  const rows = keys
    .slice(0, 12)
    .map((k) => {
      const v = Number(map[k]);
      const txt = Number.isFinite(v)
        ? useCoefs
          ? fmtSigned(v, 4)
          : v.toFixed(2)
        : String(map[k] ?? "—");
      const name = FACTOR_LABELS[k] || k;
      return (
        `<tr><td class="score-fw-name" title="${escapeText(k)}">${escapeText(name)}</td>` +
        `<td class="score-fw-val num ${signCls(v)}">${escapeText(txt)}</td></tr>`
      );
    })
    .join("");
  return (
    `<div class="score-factors-section">` +
    `<div class="score-section-title">${escapeText(title)}</div>` +
    `<table class="score-factor-weights"><tbody>${rows}</tbody></table>` +
    `</div>`
  );
}

function formatReasonsSection(reasons) {
  const list = Array.isArray(reasons) ? reasons.slice(0, 5) : [];
  if (!list.length) return "";
  let html =
    `<div class="score-reasons-section">` +
    `<div class="score-section-title">评分理由</div>` +
    `<ul class="score-reasons">`;
  list.forEach((rsn) => {
    let cls = "neutral";
    if (/强于|高于|上升|增加|优秀|良好|高/.test(rsn)) cls = "pos";
    else if (/弱于|低于|下降|减少|较差|低/.test(rsn)) cls = "neg";
    html += `<li class="${cls}">${escapeText(rsn)}</li>`;
  });
  if (Array.isArray(reasons) && reasons.length > 5) {
    html += `<li class="neutral score-reasons-more">另有 ${reasons.length - 5} 条…</li>`;
  }
  html += "</ul></div>";
  return html;
}

const T0_FEAT_LABELS = {
  gap_pct: "跳空 %",
  yclose_loc: "昨收位置",
  mom3_pct: "近3日动量 %",
  gap_atr: "gap / ATR",
  atr_pct: "ATR %",
};

/** 做 T 开盘方向分悬浮（≠ 选股 ŷ）。 */
export function formatT0DirectionDetail(raw) {
  const score = Number(raw && raw.direction_score);
  const scoreTxt = Number.isFinite(score) ? fmtSigned(score, 2) : "—";
  const dir = String((raw && raw.direction) || "");
  const dirLabel =
    dir === "long_t" ? "正 T" : dir === "reverse_t" ? "反 T" : dir || "—";
  const enter =
    raw && raw.dir_enter != null && Number.isFinite(Number(raw.dir_enter))
      ? Number(raw.dir_enter)
      : 0.35;
  let decision = "低置信跳过";
  if (Number.isFinite(score)) {
    if (score >= enter) decision = "正 T（先卖后买）";
    else if (score <= -enter) decision = "反 T（先买后卖）";
  } else if (dir === "long_t" || dir === "reverse_t") {
    decision = dirLabel;
  }
  let html = '<div class="score-detail">';
  html +=
    `<div class="score-hero">` +
    `<div class="score-hero-label">做 T 方向分（表格「分」）</div>` +
    `<div class="score-hero-value ${signCls(score)}">${escapeText(scoreTxt)}</div>` +
    `<div class="score-hero-hint">约 -1～+1 · 开盘可用特征 · 无前视</div>` +
    `<div class="score-hero-semantics">` +
    `语义：决定当天正 T / 反 T / 跳过 · <strong>不是</strong> 选股 predicted_score（ŷ）` +
    `</div>` +
    `<div class="score-hero-gate">门槛 ±${escapeText(String(enter))} · 判定 ${escapeText(
      decision
    )}</div>` +
    `</div>`;

  const feats = (raw && raw.features) || (raw && raw.direction_features) || {};
  const featKeys = ["gap_pct", "yclose_loc", "mom3_pct", "gap_atr", "atr_pct"];
  const featRows = featKeys
    .map((k) => {
      const v = feats[k];
      if (v == null || v === "") return "";
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      return (
        `<tr>` +
        `<td class="score-fw-name">${escapeText(T0_FEAT_LABELS[k] || k)}</td>` +
        `<td class="num score-fw-val ${signCls(n)}">${escapeText(txt)}</td>` +
        `</tr>`
      );
    })
    .filter(Boolean);
  if (featRows.length) {
    html +=
      `<div class="score-factors-section">` +
      `<div class="score-section-title">开盘特征</div>` +
      `<table class="score-factor-weights"><tbody>${featRows.join("")}</tbody></table>` +
      `<div class="score-hero-hint" style="margin-top:6px">权重默认 跳空0.45 · 昨位0.20 · mom3 0.20 · gap/ATR 0.15</div>` +
      `</div>`;
  }

  const reason = String((raw && (raw.direction_reason || raw.reason)) || "").trim();
  if (reason) {
    html +=
      `<div class="score-reasons-section">` +
      `<div class="score-section-title">选向说明</div>` +
      `<ul class="score-reasons"><li class="neutral">${escapeText(reason)}</li></ul>` +
      `</div>`;
  }

  if (raw && raw.stock_code) {
    html +=
      `<div class="score-hero-hint">` +
      `${escapeText(raw.stock_code)}` +
      (raw.date ? ` · ${escapeText(raw.date)}` : "") +
      (dirLabel !== "—" ? ` · ${escapeText(dirLabel)}` : "") +
      `</div>`;
  }
  html += "</div>";
  return html;
}

export function createScoreTooltipController() {
  let tipEl = null;
  let tipAnchor = null;

  function hide() {
    if (tipEl) {
      tipEl.remove();
      tipEl = null;
    }
    tipAnchor = null;
  }

  function showPlain(anchor, text) {
    const msg = String(text || "").trim();
    if (!anchor || !msg) return;
    hide();
    const tip = document.createElement("div");
    tip.className = "score-tooltip plain-hover-tip";
    tip.innerHTML = `<div class="plain-hover-tip-body">${escapeText(msg)}</div>`;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = anchor;
    place(tip, anchor);
  }

  function showHtml(anchor, html, { className = "score-tooltip" } = {}) {
    const body = String(html || "").trim();
    if (!anchor || !body) return;
    hide();
    const tip = document.createElement("div");
    tip.className = className;
    tip.innerHTML = body;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = anchor;
    place(tip, anchor);
  }

  function bindAttrTip(
    host,
    {
      selector = "[data-tip-html]",
      htmlAttr = "data-tip-html",
      className = "score-tooltip plain-hover-tip",
      buildHtml = null,
    } = {}
  ) {
    if (!host || host.dataset.attrTipWired === "1") return;
    host.dataset.attrTipWired = "1";
    host.addEventListener("mouseover", (e) => {
      const el = e.target.closest(selector);
      if (!el || !host.contains(el)) return;
      if (tipAnchor === el && tipEl) return;
      let html = "";
      if (typeof buildHtml === "function") {
        html = buildHtml(el) || "";
      } else {
        html = el.getAttribute(htmlAttr) || "";
      }
      if (!html) return;
      showHtml(el, html, { className });
    });
    host.addEventListener("mouseout", (e) => {
      const from = e.target.closest(selector);
      if (!from) return;
      const to = e.relatedTarget;
      if (to && from.contains(to)) return;
      if (tipEl && to && tipEl.contains(to)) return;
      if (tipEl && tipEl.dataset.sticky === "1") return;
      hide();
    });
  }

  function show(cell, { sticky = false } = {}) {
    let raw;
    try {
      raw = JSON.parse(cell.dataset.scoreDetail || "{}");
    } catch (_) {
      raw = {};
    }
    if (raw && raw.kind === "t0_direction") {
      const html = formatT0DirectionDetail(raw);
      hide();
      const tip = document.createElement("div");
      tip.className = "score-tooltip";
      tip.innerHTML = html;
      tip.setAttribute("role", "tooltip");
      if (sticky) tip.dataset.sticky = "1";
      document.body.appendChild(tip);
      tipEl = tip;
      tipAnchor = cell;
      place(tip, cell);
      return;
    }
    // 兼容旧字段名
    if (!raw.formula_terms && raw.score_formula_terms) {
      raw.formula_terms = raw.score_formula_terms;
    }
    const formula = raw.formula || "";
    const reasons = raw.reasons || [];
    const hardReject = raw.hard_reject;
    const rejectReason = raw.reject_reason || "";
    const hasWeight = !!(
      raw.weight_source ||
      raw.cluster_mode ||
      raw.cluster_label ||
      raw.return_model_source
    );
    const hasTerms =
      raw.formula_terms &&
      Array.isArray(raw.formula_terms.terms) &&
      raw.formula_terms.terms.length > 0;
    const hasCoefs =
      (raw.factor_coefficients &&
        typeof raw.factor_coefficients === "object" &&
        Object.keys(raw.factor_coefficients).length > 0) ||
      (raw.coefficients &&
        typeof raw.coefficients === "object" &&
        Object.keys(raw.coefficients).length > 0);
    const hasFactorWeights =
      raw.factor_weights &&
      typeof raw.factor_weights === "object" &&
      Object.keys(raw.factor_weights).length > 0;

    if (
      !formula &&
      !reasons.length &&
      !hardReject &&
      !hasWeight &&
      !hasTerms &&
      !hasCoefs &&
      !hasFactorWeights &&
      resolveYhat(raw) == null &&
      raw.sentiment_include_in_score == null &&
      !(Array.isArray(raw.risk_hints) && raw.risk_hints.length) &&
      !(Array.isArray(raw.warnings) && raw.warnings.length) &&
      !raw.fundamentals_pit &&
      !raw.index_meta &&
      !raw.fundamentals_depth
    )
      return;

    let html = '<div class="score-detail">';
    html += formatScoreHero(raw);
    html += formatWeightSourceNote(raw);
    html += formatFormulaTermsSection(raw);
    html += formatFactorWeightsSection(raw);
    html += formatFeatureIsoSection(raw);
    html += formatSentimentGateSection(raw);
    // 有分项表时不再堆一行长公式
    if (formula && !hasTerms) {
      html += `<div class="score-formula-section">
        <div class="score-section-title">收益分公式</div>
        <div class="score-formula">${escapeText(formula)}</div>
      </div>`;
    }
    if (hardReject && rejectReason) {
      html += `<div class="score-detail-reject">
        <span class="score-detail-reject-icon">⚠</span>
        <span class="score-detail-reject-text">${escapeText(rejectReason)}</span>
      </div>`;
    }
    html += formatReasonsSection(reasons);
    html += "</div>";

    hide();
    const tip = document.createElement("div");
    tip.className = "score-tooltip";
    tip.innerHTML = html;
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = cell;
    place(tip, cell);

    if (sticky) {
      tip.dataset.sticky = "1";
      const close = (ev) => {
        if (tip.contains(ev.target) || cell.contains(ev.target)) return;
        hide();
        document.removeEventListener("click", close, true);
      };
      setTimeout(() => document.addEventListener("click", close, true), 0);
    }
  }

  function place(tip, anchor) {
    const rect = anchor.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
    let top = rect.bottom + 6;
    if (top + tipRect.height > window.innerHeight - 8) {
      top = rect.top - tipRect.height - 6;
    }
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }

  function bindHost(host, { scoreSelector = "[data-score-detail]" } = {}) {
    if (!host || host.dataset.scoreTipWired === "1") return;
    host.dataset.scoreTipWired = "1";
    host.addEventListener("click", (e) => {
      const cell = e.target.closest(scoreSelector);
      if (!cell || !host.contains(cell)) return;
      e.preventDefault();
      e.stopPropagation();
      show(cell, { sticky: true });
    });
    host.addEventListener("mouseover", (e) => {
      const cell = e.target.closest(scoreSelector);
      if (!cell || !host.contains(cell)) return;
      if (tipAnchor === cell && tipEl) return;
      show(cell, { sticky: false });
    });
    host.addEventListener("mouseout", (e) => {
      const from = e.target.closest(scoreSelector);
      if (!from) return;
      const to = e.relatedTarget;
      if (to && from.contains(to)) return;
      if (tipEl && to && tipEl.contains(to)) return;
      if (tipEl && tipEl.dataset.sticky === "1") return;
      hide();
    });
  }

  return {
    show,
    showPlain,
    showHtml,
    hide,
    bindHost,
    bindAttrTip,
    get tipEl() {
      return tipEl;
    },
    get tipAnchor() {
      return tipAnchor;
    },
  };
}
