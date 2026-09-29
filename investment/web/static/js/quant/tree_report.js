/**
 * ŷ_oc_tree / ŷ_τc_tree / ŷ_τ30_tree 影子对照：KPI + 分 τ 曲线 + 增益条。
 * 不写 live / 不进回测。
 */
import { escapeHtml } from "../shared.js";
import { metricCell, researchGridHtml } from "./research_grid.js";

function fmtPct(v) {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  return `${(Number(v) * 100).toFixed(1)}%`;
}

function fmtNum(v, digits = 3) {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  return Number(v).toFixed(digits);
}

function fmtN(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "—";
}

function fmtSec(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 60) return `${n >= 10 ? Math.round(n) : n.toFixed(1)}s`;
  const m = Math.floor(n / 60);
  const s = Math.round(n % 60);
  return `${m}分${String(s).padStart(2, "0")}秒`;
}

function fmtDelta(v, { pct = false, invert = false } = {}) {
  if (v == null || !Number.isFinite(Number(v))) return { text: "—", tone: "flat" };
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  const text = pct ? `${sign}${(n * 100).toFixed(1)}pp` : `${sign}${n.toFixed(3)}`;
  return { text, tone: deltaTone(n, invert) };
}

function deltaTone(n, invert = false) {
  if (vNull(n)) return "flat";
  const x = invert ? -n : n;
  if (Math.abs(n) < 1e-4) return "flat";
  return x > 0 ? "win" : "lose";
}

function vNull(n) {
  return n == null || !Number.isFinite(Number(n));
}

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function metaCell(k, v, extra = "") {
  if (v == null || v === "") return "";
  return (
    `<div class="quant-tree-meta-cell${extra ? ` ${extra}` : ""}">` +
    `<span class="quant-tree-meta-k">${escapeHtml(String(k))}</span>` +
    `<span class="quant-tree-meta-v">${escapeHtml(String(v))}</span>` +
    `</div>`
  );
}

function pickTauAnchors(keys) {
  const want = ["09:30", "09:35", "10:00", "11:00"];
  const have = new Set(keys);
  const out = want.filter((k) => have.has(k));
  if (out.length >= 2) return out;
  if (keys.length <= 4) return keys;
  return [keys[0], keys[Math.floor(keys.length / 2)], keys[keys.length - 1]];
}

function tauHit(by, t) {
  const b = by && by[t];
  const v = b && num(b.sign_hit);
  return v;
}

function dualSpark(keys, treeBy, ridgeBy) {
  if (!Array.isArray(keys) || keys.length < 2) return "";
  const w = 320;
  const h = 72;
  const padX = 6;
  const padY = 8;
  const series = (by) => keys.map((t) => tauHit(by, t));
  const a = series(treeBy);
  const b = series(ridgeBy);
  const vals = [...a, ...b].filter((v) => v != null);
  if (vals.length < 2) return "";
  const lo = Math.min(0.4, ...vals);
  const hi = Math.max(0.6, ...vals);
  const span = Math.max(1e-6, hi - lo);
  const xy = (arr) =>
    arr.map((v, i) => {
      if (v == null) return null;
      const x = padX + (i / Math.max(1, keys.length - 1)) * (w - 2 * padX);
      const y = h - padY - ((v - lo) / span) * (h - 2 * padY);
      return { x, y, t: keys[i], live: keys[i] === "10:00" };
    });
  const pa = xy(a).filter(Boolean);
  const pb = xy(b).filter(Boolean);
  const line = (pts) => pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const y50 =
    lo < 0.5 && hi > 0.5 ? h - padY - ((0.5 - lo) / span) * (h - 2 * padY) : null;
  const live = pa.find((p) => p.live) || pb.find((p) => p.live);
  const yBase = (h - padY).toFixed(1);
  return (
    `<svg class="quant-tree-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">` +
    (y50 != null
      ? `<line class="is-base" x1="${padX}" x2="${w - padX}" y1="${y50.toFixed(
          1
        )}" y2="${y50.toFixed(1)}"></line>`
      : "") +
    (live
      ? `<line class="is-live-x" x1="${live.x.toFixed(1)}" x2="${live.x.toFixed(
          1
        )}" y1="${padY}" y2="${yBase}"></line>`
      : "") +
    (pb.length >= 2
      ? `<polyline class="is-ridge" points="${line(pb)}"></polyline>`
      : "") +
    (pa.length >= 2
      ? `<polyline class="is-tree" points="${line(pa)}"></polyline>`
      : "") +
    pa
      .map((p) => {
        const r = p.live ? 3.1 : p.t === "09:30" ? 2.6 : 1.6;
        return `<circle class="${p.live ? "is-live" : ""}" cx="${p.x.toFixed(
          1
        )}" cy="${p.y.toFixed(1)}" r="${r}"></circle>`;
      })
      .join("") +
    `</svg>`
  );
}

function verdictPack(delta, openTree, openRidge) {
  const ic = num(delta.ic);
  const hit = num(delta.sign_hit);
  const open = num(delta.open_sign_hit);
  const mse = num(delta.residual_var);
  const overallWin =
    (ic != null && ic > 0.01) || (hit != null && hit > 0.01 && (ic == null || ic >= 0));
  const overallLose =
    (ic != null && ic < -0.01) ||
    (hit != null && hit < -0.005 && (ic == null || ic <= 0.002));
  const openUp = open != null && open > 0.004;
  const openDown = open != null && open < -0.004;
  let tone = "flat";
  let title = "与 Ridge 持平";
  let sub = "同面板 Holdout；差额在噪声量级";
  if (overallWin) {
    tone = "tree";
    title = "浅树略优于 Ridge";
    sub = "非线性在这套 X 上有一点增益";
  } else if (overallLose) {
    tone = "ridge";
    title = "Ridge 更稳";
    sub = openUp
      ? "混合钟被前缀线性项主导；开盘钟树略好"
      : "线性已够用，浅树多引入方差";
  } else if (openUp) {
    tone = "split";
    title = "总体持平 · 开盘钟略好";
    sub = "09:30 尚无开→τ 前缀，树才有机会";
  } else if (openDown) {
    tone = "flat";
    title = "与 Ridge 持平";
    sub = "开盘钟也未见稳定优势";
  }
  const bits = [];
  if (ic != null) bits.push(`IC ${fmtDelta(ic).text}`);
  if (hit != null) bits.push(`命中 ${fmtDelta(hit, { pct: true }).text}`);
  if (mse != null) bits.push(`MSE ${fmtDelta(mse, { invert: true }).text}`);
  if (open != null) bits.push(`开盘 ${fmtDelta(open, { pct: true }).text}`);
  if (openTree != null && openRidge != null) {
    bits.push(`开盘 ${fmtPct(openTree)} / Ridge ${fmtPct(openRidge)}`);
  }
  return { tone, title, sub, bits };
}

function kpiCard(label, treeVal, ridgeVal, delta, tip) {
  return (
    `<article class="quant-tree-kpi is-${escapeHtml(delta.tone)}" title="${escapeHtml(
      tip || label
    )}">` +
    `<span class="quant-tree-kpi-k">${escapeHtml(label)}</span>` +
    `<span class="quant-tree-kpi-v">${escapeHtml(treeVal)}</span>` +
    `<span class="quant-tree-kpi-sub">` +
    `<span class="quant-tree-kpi-ridge">Ridge ${escapeHtml(ridgeVal)}</span>` +
    (delta.text && delta.text !== "—"
      ? `<span class="quant-tree-delta is-${delta.tone}">${escapeHtml(delta.text)}</span>`
      : "") +
    `</span>` +
    `</article>`
  );
}

function gainRows(rows) {
  const list = Array.isArray(rows) ? rows.slice(0, 8) : [];
  if (!list.length) return "";
  const max = Math.max(
    0.01,
    ...list.map((r) => Math.abs(Number(r.share) || Number(r.gain) || 0))
  );
  const body = list
    .map((r, i) => {
      const share = num(r.share);
      const pct = share != null ? share * 100 : null;
      const w = Math.max(2, ((Math.abs(share || 0) / max) * 100).toFixed(1));
      const label = String(r.label || r.key || "—");
      return (
        `<div class="quant-tree-gain-row" title="${escapeHtml(
          `${label} · ${(pct != null ? pct.toFixed(1) : "—")}%`
        )}">` +
        `<span class="quant-tree-gain-rank">${String(i + 1).padStart(2, "0")}</span>` +
        `<span class="quant-tree-gain-name">${escapeHtml(label)}</span>` +
        `<span class="quant-tree-gain-track"><span class="quant-tree-gain-fill" style="width:${w}%"></span></span>` +
        `<span class="quant-tree-gain-n">${
          pct != null ? `${pct.toFixed(0)}%` : "—"
        }</span>` +
        `</div>`
      );
    })
    .join("");
  return (
    `<section class="quant-tree-panel quant-tree-panel--gain" aria-label="增益份额">` +
    `<header class="quant-tree-panel-head"><h4>增益</h4><span>前 ${list.length} · 归一化 gain</span></header>` +
    `<div class="quant-tree-gain">${body}</div>` +
    `</section>`
  );
}

function timingPanel(timing) {
  const t = timing && typeof timing === "object" ? timing : {};
  const segs = [
    { id: "bars", k: "行情", s: num(t.bars_s), cls: "is-bars" },
    { id: "panel", k: "面板", s: num(t.panel_s), cls: "is-panel" },
    { id: "tree", k: "树", s: num(t.tree_s), cls: "is-tree" },
    { id: "ridge", k: "Ridge", s: num(t.ridge_s), cls: "is-ridge" },
  ].filter((x) => x.s != null && x.s >= 0);
  const total = num(t.total_s) != null ? num(t.total_s) : segs.reduce((a, x) => a + x.s, 0);
  if (!segs.length || !(total > 0)) return "";
  const fills = segs
    .map((x) => {
      const w = Math.max(0.6, (x.s / total) * 100);
      return `<span class="quant-tree-time-fill ${x.cls}" style="width:${w.toFixed(
        2
      )}%" title="${escapeHtml(`${x.k} ${fmtSec(x.s)}`)}"></span>`;
    })
    .join("");
  const legend = segs
    .map(
      (x) =>
        `<span class="quant-tree-time-leg ${x.cls}"><i></i>${escapeHtml(x.k)} ${escapeHtml(
          fmtSec(x.s)
        )}</span>`
    )
    .join("");
  return (
    `<section class="quant-tree-panel" aria-label="用时">` +
    `<header class="quant-tree-panel-head"><h4>用时</h4><span>${escapeHtml(
      fmtSec(total)
    )}</span></header>` +
    `<div class="quant-tree-time-track">${fills}</div>` +
    `<div class="quant-tree-time-legs">${legend}</div>` +
    `</section>`
  );
}

function tauPanel(treeOos, ridgeOos) {
  const treeBy = (treeOos && treeOos.by_tau) || {};
  const ridgeBy = (ridgeOos && ridgeOos.by_tau) || {};
  const keys = Object.keys(treeBy)
    .filter((k) => treeBy[k] && num(treeBy[k].n) > 0)
    .sort();
  if (keys.length < 2) return "";
  const anchors = pickTauAnchors(keys);
  const first = tauHit(treeBy, keys[0]);
  const last = tauHit(treeBy, keys[keys.length - 1]);
  const span =
    first != null && last != null
      ? `${(first * 100).toFixed(0)}→${(last * 100).toFixed(0)}%`
      : "";
  const ticks = anchors
    .map((t) => {
      const tv = tauHit(treeBy, t);
      const rv = tauHit(ridgeBy, t);
      const d = tv != null && rv != null ? tv - rv : null;
      const tone = d == null ? "flat" : deltaTone(d);
      const live = t === "10:00";
      const open = t === "09:30";
      return (
        `<span class="quant-tree-tau-tick${live ? " is-live" : ""}${
          open ? " is-open" : ""
        }" title="${escapeHtml(
          `${t} · 树 ${fmtPct(tv)} · Ridge ${fmtPct(rv)} · n=${treeBy[t]?.n ?? "—"}`
        )}">` +
        `<span class="quant-tree-tau-k">${escapeHtml(t)}</span>` +
        `<span class="quant-tree-tau-v">${escapeHtml(fmtPct(tv))}</span>` +
        `<span class="quant-tree-delta is-${tone}">${escapeHtml(
          d == null ? "—" : fmtDelta(d, { pct: true }).text
        )}</span>` +
        `</span>`
      );
    })
    .join("");
  return (
    `<section class="quant-tree-panel quant-tree-panel--tau" aria-label="分 τ 命中">` +
    `<header class="quant-tree-panel-head">` +
    `<h4>分 τ 命中</h4>` +
    `<span>${span ? `${escapeHtml(span)} · ` : ""}树实线 · Ridge 虚线</span>` +
    `</header>` +
    `<div class="quant-tree-tau-body">` +
    `<div class="quant-tree-spark-wrap">${dualSpark(keys, treeBy, ridgeBy)}</div>` +
    `<div class="quant-tree-tau-ticks">${ticks}</div>` +
    `</div>` +
    `</section>`
  );
}

/**
 * @param {object} data 拟合报告
 * @param {{ head?: "tau"|"r"|"t30"|"t45"|"t60"|"t75"|"t90" }} [opts]
 */
export function treeReportHtml(data, opts = {}) {
  if (!data || typeof data !== "object" || !data.success) return "";
  const isR = opts.head === "r";
  const isOo = opts.head === "oo";
  const isCo = opts.head === "co";
  const isT30 = opts.head === "t30";
  const isT45 = opts.head === "t45";
  const isT60 = opts.head === "t60";
  const isT75 = opts.head === "t75";
  const isT90 = opts.head === "t90";
  const headName = isT90
    ? "ŷ_τ90_tree"
    : isT75
    ? "ŷ_τ75_tree"
    : isT60
    ? "ŷ_τ60_tree"
    : isT45
    ? "ŷ_τ45_tree"
    : isT30
      ? "ŷ_τ30_tree"
      : isR
        ? "ŷ_τc_tree"
        : isOo
          ? "ŷ_oo_tree"
          : isCo
            ? "ŷ_co_tree"
            : "ŷ_oc_tree";
  const isHorizon = isT30 || isT45 || isT60 || isT75 || isT90;
  const ySpec = isT90
    ? "I(mean(price(τ⊕85/90/95))/price(τ)−1>0)"
    : isT75
    ? "I(mean(price(τ⊕70/75/80))/price(τ)−1>0)"
    : isT60
    ? "I(mean(price(τ⊕55/60/65))/price(τ)−1>0)"
    : isT45
    ? "I(mean(price(τ⊕40/45/50))/price(τ)−1>0)"
    : isT30
    ? "I(mean(price(τ⊕25/30/35))/price(τ)−1>0)"
    : isR
      ? "close/price(τ)−1"
      : isOo
        ? "open[T+1]/open[T]−1"
        : isCo
          ? "open[T+1]/close[T]−1"
          : "open→close";
  const headKey = isT90
    ? "t90"
    : isT75
      ? "t75"
      : isT60
        ? "t60"
        : isT45
          ? "t45"
          : isT30
            ? "t30"
            : isR
              ? "r"
              : isOo
                ? "oo"
                : isCo
                  ? "co"
                  : "tau";
  const boost = data.oos || {};
  const ridge = data.ridge_oos || {};
  const delta = data.delta_vs_ridge || {};
  const openB = (boost.by_tau && boost.by_tau["09:30"]) || {};
  const openR = (ridge.by_tau && ridge.by_tau["09:30"]) || {};
  const b06b = (boost.buckets && boost.buckets.abs_ge_0_6) || {};
  const b06r = (ridge.buckets && ridge.buckets.abs_ge_0_6) || {};
  const hyper = data.hyperparams || {};
  const nEst =
    hyper.n_estimators != null && Number.isFinite(Number(hyper.n_estimators))
      ? Number(hyper.n_estimators)
      : 80;
  const depth =
    hyper.max_depth != null && Number.isFinite(Number(hyper.max_depth))
      ? Number(hyper.max_depth)
      : 3;
  const engine = (window.formatTreeBackend ? window.formatTreeBackend(data.backend || "") : String(data.backend || ""));
  const beLower = String(data.backend || "").toLowerCase();
  const engineChip =
    beLower === "xgboost" || beLower === "xgb"
      ? "is-xgb"
      : beLower === "lightgbm" || beLower === "lgb"
        ? "is-lgb"
        : beLower === "lambdarank" || beLower === "lightgbm_lambda"
          ? "is-lgb"
          : "is-numpy";
  const dIc = fmtDelta(delta.ic);
  const dHit = fmtDelta(delta.sign_hit, { pct: true });
  const dMse = fmtDelta(delta.residual_var, { invert: true });
  const dStrong = fmtDelta(delta.strong_sign_hit, { pct: true });
  const dOpen = fmtDelta(delta.open_sign_hit, { pct: true });
  const dAuc = fmtDelta(delta.auc);
  const dAcc = fmtDelta(delta.acc_at_50, { pct: true });
  const dBrier = fmtDelta(delta.brier, { invert: true });
  const verdict = verdictPack(delta, num(openB.sign_hit), num(openR.sign_hit));

  const meta =
    `<div class="quant-tree-meta">` +
    [
      metaCell("角色", "影子 · 不写 live · 不进回测", "is-flags"),
      metaCell("引擎", engine, engineChip),
      metaCell("结构", `${nEst} 棵 · 深 ${depth}`),
      boost.holdout_trading_days != null
        ? metaCell("Holdout", `${Number(boost.holdout_trading_days)}日`)
        : "",
      boost.n_train != null || boost.n_test != null
        ? metaCell(
            "训 / 测",
            `${boost.n_train != null ? fmtN(boost.n_train) : "—"} / ${
              boost.n_test != null ? fmtN(boost.n_test) : "—"
            }`
          )
        : "",
      data.minute_codes_hit != null
        ? metaCell("分钟", `${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"}`)
        : data.minute_cache_hit != null
          ? metaCell("分钟", `${data.minute_cache_hit}/${data.minute_cache_universe ?? "—"}`)
          : "",
      Array.isArray(data.tree_shape_features) && data.tree_shape_features.length
        ? metaCell("X", `Ridge Z + ${data.tree_shape_features.length} 路径形状`)
        : "",
    ]
      .filter(Boolean)
      .join("") +
    `</div>`;

  const kpis = isHorizon
    ? `<div class="quant-tree-kpis">` +
      kpiCard("AUC", fmtNum(boost.auc), fmtNum(ridge.auc), dAuc, "Holdout ROC-AUC") +
      kpiCard(
        "acc@0.5",
        fmtPct(boost.acc_at_50 != null ? boost.acc_at_50 : boost.sign_hit),
        fmtPct(ridge.acc_at_50 != null ? ridge.acc_at_50 : ridge.sign_hit),
        boost.acc_at_50 != null ? dAcc : dHit,
        "p_up>0.5 对窗收益>0"
      ) +
      kpiCard(
        "Brier",
        fmtNum(boost.brier != null ? boost.brier : boost.residual_var, 4),
        fmtNum(ridge.brier != null ? ridge.brier : ridge.residual_var, 4),
        dBrier,
        "越小越好；0.25≈瞎猜"
      ) +
      kpiCard(
        "开盘命中",
        fmtPct(openB.sign_hit),
        fmtPct(openR.sign_hit),
        dOpen,
        "09:30 尚无分钟前缀"
      ) +
      `</div>`
    : `<div class="quant-tree-kpis">` +
      kpiCard("OOS IC", fmtNum(boost.ic), fmtNum(ridge.ic), dIc, "时间切分样本外 IC") +
      kpiCard("命中", fmtPct(boost.sign_hit), fmtPct(ridge.sign_hit), dHit, "方向命中") +
      kpiCard(
        "MSE",
        fmtNum(boost.residual_var, 4),
        fmtNum(ridge.residual_var, 4),
        dMse,
        "残差方差 · 越低越好"
      ) +
      kpiCard(
        "|ŷ|≥0.6",
        fmtPct(b06b.sign_hit),
        fmtPct(b06r.sign_hit),
        dStrong,
        "强信号方向命中"
      ) +
      kpiCard(
        "开盘命中",
        fmtPct(openB.sign_hit),
        fmtPct(openR.sign_hit),
        dOpen,
        "09:30 尚无开→τ 前缀"
      ) +
      `</div>`;

  const cols = isHorizon
    ? [
        { id: "model", label: "模型", widthPct: 18 },
        { id: "auc", label: "AUC", num: true, widthPct: 14 },
        { id: "acc", label: "acc@0.5", num: true, widthPct: 14 },
        { id: "brier", label: "Brier", num: true, widthPct: 16 },
        { id: "open", label: "开盘命中", num: true, widthPct: 18 },
        { id: "n", label: "测 n", num: true, widthPct: 10 },
      ]
    : [
        { id: "model", label: "模型", widthPct: 18 },
        { id: "ic", label: "OOS IC", num: true, widthPct: 14 },
        { id: "hit", label: "命中", num: true, widthPct: 14 },
        { id: "mse", label: "MSE", num: true, widthPct: 16 },
        { id: "strong", label: "|ŷ|≥0.6", num: true, widthPct: 14 },
        { id: "open", label: "开盘命中", num: true, widthPct: 14 },
        { id: "n", label: "测 n", num: true, widthPct: 10 },
      ];
  const rows = isHorizon
    ? [
        {
          id: "ridge",
          model: "Ridge",
          auc: fmtNum(ridge.auc),
          acc: fmtPct(ridge.acc_at_50 != null ? ridge.acc_at_50 : ridge.sign_hit),
          brier: fmtNum(ridge.brier != null ? ridge.brier : ridge.residual_var, 4),
          open: fmtPct(openR.sign_hit),
          n: fmtN(ridge.n_test),
        },
        {
          id: "tree",
          model: engine,
          auc: fmtNum(boost.auc),
          acc: fmtPct(boost.acc_at_50 != null ? boost.acc_at_50 : boost.sign_hit),
          brier: fmtNum(boost.brier != null ? boost.brier : boost.residual_var, 4),
          open: fmtPct(openB.sign_hit),
          n: fmtN(boost.n_test),
        },
        {
          id: "delta",
          model: "树 − Ridge",
          auc: dAuc.text,
          acc: (boost.acc_at_50 != null ? dAcc : dHit).text,
          brier: dBrier.text,
          open: dOpen.text,
          n: "—",
          tones: {
            auc: dAuc.tone,
            acc: (boost.acc_at_50 != null ? dAcc : dHit).tone,
            brier: dBrier.tone,
            open: dOpen.tone,
          },
        },
      ]
    : [
    {
      id: "ridge",
      model: "Ridge",
      ic: fmtNum(ridge.ic),
      hit: fmtPct(ridge.sign_hit),
      mse: fmtNum(ridge.residual_var, 4),
      strong: fmtPct(b06r.sign_hit),
      open: fmtPct(openR.sign_hit),
      n: fmtN(ridge.n_test),
    },
    {
      id: "tree",
      model: engine,
      ic: fmtNum(boost.ic),
      hit: fmtPct(boost.sign_hit),
      mse: fmtNum(boost.residual_var, 4),
      strong: fmtPct(b06b.sign_hit),
      open: fmtPct(openB.sign_hit),
      n: fmtN(boost.n_test),
    },
    {
      id: "delta",
      model: "树 − Ridge",
      ic: dIc.text,
      hit: dHit.text,
      mse: fmtDelta(delta.residual_var).text,
      strong: dStrong.text,
      open: dOpen.text,
      n: "—",
      tones: {
        ic: dIc.tone,
        hit: dHit.tone,
        mse: dMse.tone,
        strong: dStrong.tone,
        open: dOpen.tone,
      },
    },
  ];
  const grid = researchGridHtml(
    cols,
    rows,
    (col, row) => {
      if (col.id === "model") {
        return `<span class="quant-tree-model">${escapeHtml(row.model)}</span>`;
      }
      if (row.id === "delta" && col.id !== "n") {
        const tone = (row.tones && row.tones[col.id]) || "flat";
        return `<span class="quant-tree-delta is-${tone}">${escapeHtml(
          row[col.id] ?? "—"
        )}</span>`;
      }
      if (col.num && col.id !== "n") {
        return metricCell(escapeHtml(row[col.id] ?? "—"), "");
      }
      return escapeHtml(row[col.id] ?? "—");
    },
    { rowClass: (row) => (row.id === "delta" ? "is-delta" : row.id === "tree" ? "is-tree" : "") }
  );

  const gain = gainRows(data.feature_importance);
  const tau = tauPanel(boost, ridge);
  const lower =
    gain && tau
      ? `<div class="quant-tree-lower">${gain}<div class="quant-tree-tau-slot">${tau}</div></div>`
      : gain || tau
        ? `<div class="quant-tree-lower">${gain}${tau}</div>`
        : "";
  return (
    `<div class="quant-tree-report" data-head="${headKey}">` +
    meta +
    `<div class="quant-tree-verdict is-${escapeHtml(verdict.tone)}">` +
    `<div class="quant-tree-verdict-main">` +
    `<span class="quant-tree-verdict-k">${escapeHtml(headName)}</span>` +
    `<strong>${escapeHtml(verdict.title)}</strong>` +
    `</div>` +
    `<span class="quant-tree-verdict-y">${escapeHtml(ySpec)}</span>` +
    `<p class="quant-tree-verdict-sub">${escapeHtml(verdict.sub)}</p>` +
    `</div>` +
    kpis +
    `<div class="quant-tree-table">${grid}</div>` +
    lower +
    timingPanel(data.timing) +
    `</div>`
  );
}
