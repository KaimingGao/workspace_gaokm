/**
 * Y(τ) 路径可视化：分段瀑布 + μ±σ 带 + 复盘分歧散点。
 */

import { escapeText } from "./paper/fmt.js?v=p1169";

function fmtSignedPct(v, digits = 2) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const t = n.toFixed(digits);
  return (n > 0 ? `+${t}` : t) + "%";
}

const CHECK_LABEL = {
  ok: "校验通过",
  conflict: "双头分歧",
  low_conf: "低置信",
  missing_tau: "缺 τ",
  single_head: "单头",
};

const CHECK_COLOR = {
  ok: "#047857",
  conflict: "#b42318",
  low_conf: "#b45309",
  missing_tau: "#64748b",
  single_head: "#6366f1",
};

function num(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function signCls(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "pos" : "neg";
}

function tauCloseSrcLabel(src) {
  const s = String(src || "");
  if (s === "feature") return "分钟特征";
  if (s === "tau_oc_adj") return "ŷ_τ−open→τ";
  if (s === "tau_rem") return "ŷ_τ(rem)";
  if (s === "eod_rem_proxy") return "EOD_rem 代理";
  if (s === "tau_oc_proxy") return "ŷ_τ 代理";
  return s || "代理";
}

/** 从 API 行 / tip raw 抽取 Y(τ) 可视化状态。 */
export function extractYPathState(raw) {
  if (!raw || typeof raw !== "object") return null;
  const st = raw.y_state && typeof raw.y_state === "object" ? raw.y_state : {};
  const segs =
    st.segments && typeof st.segments === "object"
      ? st.segments
      : {};
  const gap = num(raw.gap_pct) ?? num(segs.gap) ?? num(raw.features_tau?.gap_pct);
  const openToTau =
    num(segs.open_to_tau) ??
    num(raw.ret_open_to_tau) ??
    num(raw.features_tau?.ret_open_to_tau);
  let tauToClose = num(raw.y_tau_to_close) ?? num(segs.tau_to_close);
  const tauToCloseSrc =
    raw.y_tau_to_close_src || segs.tau_to_close_src || "";
  const eodCc = num(segs.eod_close_close) ?? num(raw.predicted_score_eod) ?? num(raw.predicted_score);
  const mu = num(raw.y_mu) ?? num(st.mu_path) ?? num(raw.predicted_score_blend);
  const sigma = num(raw.y_sigma) ?? num(st.sigma_path);
  const disagree = num(raw.y_disagree) ?? num(st.disagree);
  const yCheck = String(raw.y_check || st.check || "");
  const trust = num(raw.eod_trust) ?? num(st.eod_trust);
  const asOfTau = String(raw.as_of_tau || raw.rem_tau || st.as_of_tau || "open");
  const window = String(raw.dual_score_window || st.window || "intraday");

  if (tauToClose == null) {
    const yt =
      num(raw.predicted_score_tau) ??
      num(raw.score_rem) ??
      num(st.heads?.tau);
    if (yt != null && openToTau != null) {
      const denom = 1 + openToTau / 100;
      if (Math.abs(denom) > 1e-9) {
        tauToClose = ((1 + yt / 100) / denom - 1) * 100;
      } else {
        tauToClose = yt;
      }
    } else if (yt != null) {
      tauToClose = yt;
    }
  }

  const parts = [gap, openToTau, tauToClose].filter((x) => x != null);
  const pathSum =
    parts.length >= 2
      ? parts.reduce((acc, v, i) => {
          if (i === 0) return 1 + v / 100;
          return acc * (1 + v / 100);
        }, 1) * 100 -
        100
      : null;

  return {
    gap,
    openToTau,
    tauToClose,
    tauToCloseSrc,
    eodCc,
    mu,
    sigma,
    disagree,
    yCheck,
    trust,
    asOfTau,
    window,
    pathSum,
    hasSegments: gap != null || openToTau != null || tauToClose != null,
  };
}

function segmentBarHtml(label, value, maxAbs, opts = {}) {
  const v = num(value);
  if (v == null) {
    return (
      `<div class="y-path-seg">` +
      `<span class="y-path-seg-label">${escapeText(label)}</span>` +
      `<span class="y-path-seg-bar-wrap"><span class="y-path-seg-empty">—</span></span>` +
      `<span class="y-path-seg-val muted">—</span>` +
      `</div>`
    );
  }
  const pct = maxAbs > 0 ? Math.min(100, (Math.abs(v) / maxAbs) * 100) : 0;
  const cls = signCls(v);
  const side = v >= 0 ? "pos" : "neg";
  return (
    `<div class="y-path-seg">` +
    `<span class="y-path-seg-label">${escapeText(label)}</span>` +
    `<span class="y-path-seg-bar-wrap ${side}">` +
    `<span class="y-path-seg-bar ${cls}" style="width:${pct.toFixed(1)}%"></span>` +
    `</span>` +
    `<span class="y-path-seg-val num ${cls}">${escapeText(fmtSignedPct(v, 2))}` +
    (opts.hint ? `<span class="y-path-seg-hint">${escapeText(opts.hint)}</span>` : "") +
    `</span>` +
    `</div>`
  );
}

/** Tip / 内联 HTML：Y(τ) 路径分段 + μ±σ。 */
export function renderYPathVizHtml(raw, opts = {}) {
  const st = extractYPathState(raw);
  if (!st) return "";
  const compact = !!opts.compact;
  const showBand = st.mu != null;
  const showSegs = st.hasSegments;
  if (!showBand && !showSegs && !st.yCheck) return "";

  const maxAbs = Math.max(
    0.35,
    Math.abs(st.gap || 0),
    Math.abs(st.openToTau || 0),
    Math.abs(st.tauToClose || 0),
    Math.abs(st.mu || 0),
    Math.abs(st.eodCc || 0),
    (st.sigma || 0) * 2
  );

  let bandHtml = "";
  if (showBand) {
    const lo = st.sigma != null ? st.mu - st.sigma : null;
    const hi = st.sigma != null ? st.mu + st.sigma : null;
    const span = hi != null && lo != null ? hi - lo : 0;
    const centerPct =
      span > 1e-9 && lo != null
        ? ((st.mu - lo) / span) * 100
        : 50;
    const widthPct =
      span > 1e-9 && st.sigma != null
        ? Math.min(100, ((st.sigma * 2) / span) * 100)
        : 8;
    bandHtml =
      `<div class="y-path-band">` +
      `<div class="y-path-band-track">` +
      (lo != null && hi != null
        ? `<span class="y-path-band-range" title="μ±σ">${escapeText(
            `${fmtSignedPct(lo, 2)} ~ ${fmtSignedPct(hi, 2)}`
          )}</span>`
        : "") +
      `<span class="y-path-band-fill" style="left:${Math.max(
        0,
        centerPct - widthPct / 2
      ).toFixed(1)}%;width:${widthPct.toFixed(1)}%"></span>` +
      `<span class="y-path-band-mu num ${signCls(st.mu)}" style="left:${centerPct.toFixed(
        1
      )}%" title="路径均值 μ">${escapeText(fmtSignedPct(st.mu, 2))}</span>` +
      `</div>` +
      `<div class="y-path-band-meta">` +
      `<span>μ ${escapeText(fmtSignedPct(st.mu, 2))}</span>` +
      (st.sigma != null
        ? `<span>σ≈${escapeText(st.sigma.toFixed(2))}</span>`
        : "") +
      (st.trust != null ? `<span>trust=${escapeText(st.trust.toFixed(2))}</span>` : "") +
      `</div>` +
      `</div>`;
  }

  let segHtml = "";
  if (showSegs) {
    segHtml =
      `<div class="y-path-segments">` +
      segmentBarHtml("缺口", st.gap, maxAbs) +
      segmentBarHtml(`开→${st.asOfTau}`, st.openToTau, maxAbs) +
      segmentBarHtml("τ→收", st.tauToClose, maxAbs, {
        hint: st.tauToCloseSrc ? tauCloseSrcLabel(st.tauToCloseSrc) : "",
      }) +
      (st.pathSum != null && st.gap != null && st.openToTau != null && st.tauToClose != null
        ? segmentBarHtml("路径复合", st.pathSum, maxAbs, { hint: "几何链" })
        : "") +
      (st.eodCc != null
        ? segmentBarHtml("EOD CC", st.eodCc, maxAbs, { hint: "昨收→收" })
        : "") +
      `</div>`;
  }

  const warn = st.yCheck && st.yCheck !== "ok";
  const head =
    `<div class="y-path-viz${warn ? " is-warn" : ""}${compact ? " is-compact" : ""}">` +
    `<div class="y-path-viz-head">` +
    `<span class="y-path-viz-title">Y(τ) 路径</span>` +
    (st.yCheck
      ? `<span class="y-path-viz-check${warn ? " is-warn" : ""}">${escapeText(
          CHECK_LABEL[st.yCheck] || st.yCheck
        )}${
          st.disagree != null ? ` · |Δ|=${st.disagree.toFixed(2)}` : ""
        }</span>`
      : "") +
    `</div>`;

  const foot =
    `<div class="y-path-viz-foot">${escapeText(
      st.window === "eod_next"
        ? "收盘后窗 · 校验软处理"
        : `决策窗 ${st.asOfTau} · 分段为期望/代理`
    )}</div>` +
    `</div>`;

  return head + bandHtml + segHtml + foot;
}

/** 复盘：Y 校验分桶水平条（CSS）。 */
export function renderYCheckBucketBars(rows) {
  if (!Array.isArray(rows) || !rows.length) return "";
  const maxN = Math.max(...rows.map((r) => Number(r.n) || 0), 1);
  const parts = rows.slice(0, 6).map((r) => {
    const ck = String(r.check || "");
    const n = Number(r.n) || 0;
    const hit =
      r.hit_rate != null && Number.isFinite(Number(r.hit_rate))
        ? `${(Number(r.hit_rate) * 100).toFixed(0)}%`
        : "—";
    const w = Math.max(4, (n / maxN) * 100);
    const col = CHECK_COLOR[ck] || "#64748b";
    return (
      `<div class="y-check-bar-row" title="${escapeText(
        CHECK_LABEL[ck] || ck
      )} · n=${n} · 命中 ${hit}">` +
      `<span class="y-check-bar-label">${escapeText(CHECK_LABEL[ck] || ck)}</span>` +
      `<span class="y-check-bar-track">` +
      `<span class="y-check-bar-fill" style="width:${w.toFixed(1)}%;background:${col}"></span>` +
      `</span>` +
      `<span class="y-check-bar-meta">${escapeText(String(n))} · ${escapeText(hit)}</span>` +
      `</div>`
    );
  });
  return `<div class="y-check-bar-chart">${parts.join("")}</div>`;
}

function cssVar(el, name, fallback) {
  try {
    const v = getComputedStyle(el || document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  } catch (_) {
    return fallback;
  }
}

/** 复盘 canvas：|EOD_rem − τ| vs 实现收益，按 y_check 着色。 */
export function paintYDisagreeScatter(canvas, points, opts = {}) {
  if (!canvas || typeof canvas.getContext !== "function") return null;
  const pts = (points || [])
    .map((p) => ({
      x: num(p.y_disagree),
      y: num(p.realized_h != null ? p.realized_h : p.realized),
      check: String(p.y_check || ""),
      hit: p.hit,
      code: String(p.code || p.stock_code || "").trim(),
      name: String(p.name || p.stock_name || "").trim(),
      yhat: num(p.yhat),
      yhat_tau: num(p.yhat_tau),
    }))
    .filter((p) => p.x != null && p.y != null);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = canvas.clientWidth || opts.width || 480;
  const cssH = canvas.clientHeight || opts.height || 200;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const muted = cssVar(canvas, "--ink-3", "#94a3b8");
  const border = cssVar(canvas, "--line", "#e2e8f0");
  const text = cssVar(canvas, "--ink", "#1e293b");
  const pad = { t: 12, r: 14, b: 34, l: 44 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;

  ctx.fillStyle = muted;
  ctx.font = '11px Manrope, "PingFang SC", sans-serif';
  if (pts.length < 2) {
    ctx.fillText("样本不足或无 |Δ| 字段", pad.l, pad.t + 14);
    return { n: pts.length };
  }

  let xMax = Math.max(...pts.map((p) => p.x), 0.05);
  let yMin = Math.min(...pts.map((p) => p.y), 0);
  let yMax = Math.max(...pts.map((p) => p.y), 0);
  if (yMax <= yMin) {
    yMin -= 1;
    yMax += 1;
  }
  const yPad = (yMax - yMin) * 0.1;
  yMin -= yPad;
  yMax += yPad;

  const sx = (v) => pad.l + (v / xMax) * w;
  const sy = (v) => pad.t + h - ((v - yMin) / (yMax - yMin)) * h;

  ctx.strokeStyle = border;
  ctx.strokeRect(pad.l + 0.5, pad.t + 0.5, w - 1, h - 1);
  const zy = sy(0);
  if (zy >= pad.t && zy <= pad.t + h) {
    ctx.strokeStyle = muted;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(pad.l, zy);
    ctx.lineTo(pad.l + w, zy);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  for (const p of pts) {
    const col = CHECK_COLOR[p.check] || "#64748b";
    ctx.fillStyle = col;
    ctx.beginPath();
    ctx.arc(sx(p.x), sy(p.y), 4.5, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.fillStyle = text;
  ctx.fillText("|Δ| (pp)", pad.l + w / 2 - 18, cssH - 8);
  ctx.save();
  ctx.translate(12, pad.t + h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("实现 h%", 0, 0);
  ctx.restore();

  const pack = { n: pts.length, points: pts, layout: { sx, sy, pad, cssW, cssH } };
  if (typeof opts.onPoint === "function") {
    const st = canvas.__yDisagree || (canvas.__yDisagree = {});
    st.pack = pack;
    const onMove = (ev) => {
      const rect = canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;
      let best = null;
      let bestD = 999;
      for (const p of pts) {
        const px = sx(p.x);
        const py = sy(p.y);
        const d = (px - mx) ** 2 + (py - my) ** 2;
        if (d < bestD) {
          bestD = d;
          best = p;
        }
      }
      canvas.style.cursor = bestD < 144 ? "pointer" : "crosshair";
      st.hover = bestD < 144 ? best : null;
    };
    const onClick = () => {
      if (st.hover) opts.onPoint(st.hover);
    };
    canvas.removeEventListener("pointermove", st.onMove);
    canvas.removeEventListener("click", st.onClick);
    st.onMove = onMove;
    st.onClick = onClick;
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("click", onClick);
    canvas.style.cursor = "crosshair";
  }
  return pack;
}
