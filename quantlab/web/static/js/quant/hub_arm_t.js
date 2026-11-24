/** 研究枢纽四头：每头在 Arm·R / Arm·L / Arm·T 里三选一。训练走对应工厂。 */

import { apiFetch } from "../api_client.js";
import { paintFitCurves } from "../fit_curves.js";
import { readWatchingPoolLimit } from "./params.js";

const PREFIX_CLOCKS = (() => {
  const out = [];
  let total = 9 * 60 + 30;
  while (total <= 11 * 60) {
    const h = Math.floor(total / 60);
    const m = total % 60;
    out.push(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
    total += 5;
  }
  return out;
})();

const ARMS_URL = "/api/quant/hub/arms";
const ARM_API = {
  R: {
    status: "/api/quant/yhat-r/status",
    fit: "/api/quant/yhat-r/fit",
    versions: "/api/quant/yhat-r/versions",
  },
  L: {
    status: "/api/quant/yhat-l/status",
    fit: "/api/quant/yhat-l/fit",
    versions: "/api/quant/yhat-l/versions",
  },
  T: {
    status: "/api/quant/yhat-t/status",
    fit: "/api/quant/yhat-t/fit",
    versions: "/api/quant/yhat-t/versions",
  },
};
const ARM_COPY = {
  R: { title: "Arm·R", desc: "Ridge · 全量" },
  L: { title: "Arm·L", desc: "LightGBM · 全量" },
  T: { title: "Arm·T", desc: "序列 Transformer" },
};
const ARM_DEFAULTS = {
  T: { holdout: 60, lookback: { oo: 1000, tc: 1000, oo_rank: 1000, tc_rank: 1000 } },
  L: { holdout: 20, lookback: { oo: 600, tc: 120, oo_rank: 700, tc_rank: 700 } },
  R: { holdout: 20, lookback: { oo: 600, tc: 120, oo_rank: 700, tc_rank: 700 } },
};

const HEADS = [
  {
    ui: "oo",
    api: "oo",
    fitId: "quant-return-model-fit",
    liveId: "quant-return-model-promote",
    statusId: "quant-ols-summary",
    holdoutId: "quant-holdout-days",
    lookbackId: "quant-fit-lookback-oo",
    lookback: 1000,
    loss: "mse",
    fullAoo: true,
  },
  {
    ui: "tau",
    api: "tc",
    fitId: "quant-tau-lgb-run",
    liveId: "quant-tau-lgb-persist",
    statusId: "quant-tau-summary",
    holdoutId: "quant-holdout-days-tc",
    lookbackId: "quant-fit-lookback-tc",
    lookback: 1000,
    loss: "mse",
    fullAtc: true,
  },
  {
    ui: "oo_rank",
    api: "oo_rank",
    fitId: "quant-oo-rank-run",
    liveId: "quant-oo-rank-persist",
    statusId: "quant-oo-rank-summary",
    holdoutId: "quant-holdout-days-oo_rank",
    lookbackId: "quant-fit-lookback-oo_rank",
    lookback: 1000,
    loss: "lambdarank",
    fullAoo: true,
  },
  {
    ui: "tc_rank",
    api: "tc_rank",
    fitId: "quant-tc-rank-run",
    liveId: "quant-tc-rank-persist",
    statusId: "quant-tc-rank-summary",
    holdoutId: "quant-holdout-days-tc_rank",
    lookbackId: "quant-fit-lookback-tc_rank",
    lookback: 1000,
    loss: "lambdarank",
    fullAtc: true,
  },
];

const pending = new Set();
const armOf = new Map();
const versionLists = new Map();
const viewing = new Map();
const chosen = new Map();
const enabledIds = new Map();
const liveSince = new Map();
const liveLabel = new Map();
let timer = 0;
let clockTimer = 0;
let versionMenusWired = false;

function arm(spec) {
  const tag = armOf.get(spec.api);
  return tag === "L" || tag === "R" ? tag : "T";
}

function armName(spec) {
  return ARM_COPY[arm(spec)].title;
}

function factoryUrls(spec) {
  return ARM_API[arm(spec)];
}

function defaultsFor(tag, api) {
  const row = ARM_DEFAULTS[tag] || ARM_DEFAULTS.T;
  return { holdout: row.holdout, lookback: row.lookback[api] || 900 };
}

function parseStamp(raw) {
  const text = String(raw || "").trim();
  if (!text) return NaN;
  return new Date(text.includes("T") ? text : text.replace(" ", "T")).getTime();
}

function fmtDur(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n) || n < 0) return "";
  const total = Math.max(0, Math.round(n));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const secBit = `${String(s).padStart(2, "0")}s`;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m ${secBit}`;
  if (m > 0) return `${m}m ${secBit}`;
  return secBit;
}

function elapsedClock(startedAt) {
  const t0 = parseStamp(startedAt);
  if (Number.isNaN(t0)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t0) / 10000) * 10);
  return fmtDur(sec) || "00s";
}

function spentDur(startedAt, finishedAt, fallbackSec) {
  const t0 = parseStamp(startedAt);
  const t1 = parseStamp(finishedAt);
  if (!Number.isNaN(t0) && !Number.isNaN(t1) && t1 >= t0) {
    return fmtDur(Math.floor((t1 - t0) / 1000));
  }
  return fmtDur(fallbackSec);
}

function rememberLiveStart(api, startedAt) {
  if (startedAt) liveSince.set(api, String(startedAt));
  else if (!liveSince.has(api)) liveSince.set(api, new Date().toISOString());
  return liveSince.get(api) || "";
}

function tickHubClocks() {
  for (const spec of HEADS) {
    const state = sectionOf(spec.ui)?.querySelector(
      "[data-recipe-replay-state][data-clock-from]"
    );
    if (!state) continue;
    const text = elapsedClock(state.dataset.clockFrom);
    const node = state.querySelector("[data-role='clock']");
    if (node && text && node.textContent !== text) node.textContent = text;
    const msg = document
      .getElementById(spec.statusId)
      ?.querySelector(".quant-rem-status-msg");
    const prefix = `${armName(spec)} 拟合`;
    if (!msg || !text || !msg.textContent.startsWith(prefix)) continue;
    const base = msg.textContent.replace(/ · \d[\dhm s]*$/, "");
    const next = `${base} · ${text}`;
    if (msg.textContent !== next) msg.textContent = next;
  }
}

function syncHubClock() {
  const any = document.querySelector("[data-recipe-replay-state][data-clock-from]");
  if (!any) {
    if (clockTimer) {
      window.clearInterval(clockTimer);
      clockTimer = 0;
    }
    return;
  }
  if (clockTimer) return;
  clockTimer = window.setInterval(tickHubClocks, 10000);
}

function num(id, fallback) {
  const n = Number(document.getElementById(id)?.value);
  return Number.isFinite(n) ? n : fallback;
}

function fmt3(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(3) : "—";
}

function fmtHit(v) {
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}

function sectionOf(ui) {
  return document.querySelector(`[data-head-section="${ui}"]`);
}

function readPrefixHm() {
  const el = document.getElementById("quant-fit-tau-end-tc");
  const raw = Number(el?.value);
  const idx = Math.max(
    0,
    Math.min(PREFIX_CLOCKS.length - 1, Math.round(Number.isFinite(raw) ? raw : 0))
  );
  return PREFIX_CLOCKS[idx] || "09:30";
}

function paintPrefixHm(hm) {
  const clock = PREFIX_CLOCKS.includes(String(hm || "").slice(0, 5))
    ? String(hm).slice(0, 5)
    : "09:30";
  const idx = String(Math.max(0, PREFIX_CLOCKS.indexOf(clock)));
  for (const id of ["quant-fit-tau-end-tc", "quant-fit-tau-end-tc_rank"]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.value = idx;
    el.setAttribute("aria-valuenow", idx);
    el.setAttribute("aria-valuetext", clock);
    const out = document.getElementById(`${id}-val`);
    if (out) out.textContent = clock;
  }
}

function sectionNum(section, key, fallback) {
  const el = section?.querySelector(`[data-param="${key}"]`);
  const n = Number(el?.value);
  return Number.isFinite(n) ? n : fallback;
}

function sectionRadio(section, key, fallback) {
  const el = section?.querySelector(`[data-param-radio="${key}"]:checked`);
  return el ? el.value : fallback;
}

function packOf(st, tag) {
  const arms = st?.arms_metrics;
  if (arms && typeof arms === "object" && arms[tag]) return arms[tag];
  const last = st?.last;
  const action = last?.action;
  if (action !== "holdout_result" && action !== "holdout_failed") return null;
  const metrics = last?.metrics;
  if (!metrics || typeof metrics !== "object") return null;
  if (metrics.arm === tag) return metrics;
  if (metrics.arms && metrics.arms[tag]) return metrics.arms[tag];
  if (metrics.cs_rank_ic != null && (!metrics.arm || metrics.arm === tag)) return metrics;
  return null;
}

function paintGate(ui, text) {
  const gate = document.querySelector(`[data-head-summary-gate="${ui}"]`);
  if (!gate) return;
  gate.dataset.armGate = text;
  gate.textContent = text;
}

function paintMetrics(ui, pack) {
  const section = sectionOf(ui);
  const box = section?.querySelector(".quant-recipe-console .yhat-tf-arm-metrics");
  const rankHead = ui === "oo_rank" || ui === "tc_rank";
  if (box) {
    const slots = box.querySelectorAll(":scope > div");
    const labels = rankHead
      ? [
          ["Rank IC", "每日截面 Spearman 再对日平均"],
          ["ICIR", "日频 IC 均值/标准差"],
          ["NDCG@30", "Holdout 日均 NDCG@30"],
          ["Top10% y", "Holdout 日截面 ŷ 头部 10% 实现收益均值"],
        ]
      : [
          ["MSE", "Holdout 逐点均方误差。预测和实现都是百分点，不截断"],
          ["Rank IC", "每日截面 Spearman 再对日平均"],
          ["ICIR", "日频 Pearson IC 均值/标准差"],
          ["命中", "样本外方向命中率，|ŷ|<0.05 个百分点不计"],
          ["Top10% y", "Holdout 日截面 ŷ 头部 10% 实现收益均值"],
        ];
    slots.forEach((slot, i) => {
      const lab = slot.querySelector("span");
      if (!lab || !labels[i]) return;
      lab.textContent = labels[i][0];
      lab.title = labels[i][1];
    });
    const cells = box.querySelectorAll("strong");
    const values = rankHead
      ? [
          fmt3(pack?.cs_rank_ic),
          fmt3(pack?.cs_icir),
          fmt3(pack?.cs_ndcg_30),
          fmt3(pack?.cs_top_mean_y),
        ]
      : [
          fmt3(pack?.mse),
          fmt3(pack?.cs_rank_ic),
          fmt3(pack?.cs_icir),
          fmtHit(pack?.sign_hit),
          fmt3(pack?.cs_top_mean_y),
        ];
    cells.forEach((el, i) => {
      const text = values[i] || "—";
      el.textContent = text;
      el.classList.toggle("is-empty", text === "—");
    });
  }
  if (rankHead) {
    const ic = fmt3(pack?.cs_rank_ic);
    paintGate(ui, ic === "—" ? "Rank IC —" : `Rank IC ${ic}`);
    return;
  }
  const mse = fmt3(pack?.mse);
  paintGate(ui, mse === "—" ? "MSE —" : `MSE ${mse}`);
}

function paintCardMessage(spec, text) {
  const root = document.getElementById(spec.statusId);
  if (!root) return;
  root.querySelectorAll(".quant-pro-status-chip").forEach((chip) => chip.remove());
  let msg = root.querySelector(".quant-rem-status-msg");
  if (!msg) {
    msg = document.createElement("span");
    msg.className = "quant-rem-status-msg";
    root.appendChild(msg);
  }
  const next = text ? String(text) : "";
  msg.textContent = next;
  root.hidden = !next;
}

function recipeParams(spec, role) {
  const section = sectionOf(spec.ui);
  const tag = arm(spec);
  const preset = defaultsFor(tag, spec.api);
  const params = {
    pool_n: sectionNum(section, "pool_n", readWatchingPoolLimit()),
    lookback: sectionNum(section, "lookback", preset.lookback),
    holdout_trading_days: role === "live" ? 0 : sectionNum(section, "holdout_trading_days", preset.holdout),
    arms: tag,
  };
  if (tag === "T") {
    params.d_model = sectionNum(section, "d_model", 128);
    params.nlayers = sectionNum(section, "nlayers", 2);
    params.nhead = 4;
    params.dropout = 0.1;
    params.batch_size = sectionNum(section, "batch_size", 2048);
    params.epochs = sectionNum(section, "epochs", 20);
    params.t_sample_pct = sectionNum(section, "t_sample_pct", 100);
    params.device = "cpu";
    params.seq_feat_set = sectionRadio(section, "seq_feat_set", "seq_core");
    params.seq_len = sectionNum(section, "seq_len", 60);
    params.loss = spec.loss;
    if (params.pool_n === 300) params.feature_set = "hs300";
    params.lr = params.feature_set === "hs300" ? 0.0001 : 0.001;
    if (spec.fullAoo) {
      params.full_aoo = true;
      params.factor_pack = "aoo_full";
    }
    if (spec.fullAtc) {
      params.full_atc = true;
      params.factor_pack = "atc_full";
    }
  } else {
    const pool = section?.querySelector("[data-hub-pool]:checked");
    params.feature_set = pool?.value || "pool";
    if (tag === "R") params.ridge_alpha = sectionNum(section, "ridge_alpha", 1);
  }
  if (spec.fullAtc) params.tau_end = readPrefixHm();
  return params;
}

function roleChoice(api) {
  let slot = chosen.get(api);
  if (!slot) {
    slot = { research: "", live: "" };
    chosen.set(api, slot);
  }
  return slot;
}

function versionRoot(ui, role) {
  return document.querySelector(
    `[data-version-for="${ui}"][data-version-role="${role}"]`
  );
}

function versionParts(label) {
  const bits = String(label || "")
    .split(" · ")
    .map((bit) => bit.trim())
    .filter(Boolean);
  return { stamp: bits[0] || "—", meta: bits.slice(1) };
}

const VERSION_BADGE = {
  正: "正式",
  预: "预训练",
  过: "过闸",
  未: "未过闸",
  败: "失败",
  半: "部分完成",
  正式: "正式",
  预训练: "预训练",
  过闸: "过闸",
  未过闸: "未过闸",
  失败: "失败",
  部分完成: "部分完成",
};

function versionChipTone(text, status) {
  const name = VERSION_BADGE[text] || text;
  if (name === "失败" || status === "error") return "bad";
  if (name === "未过闸" || name === "部分完成") return "warn";
  if (name === "正式" || name === "过闸") return "ok";
  return "";
}

function closeVersionMenus(except) {
  document.querySelectorAll(".yhat-tf-version-menu").forEach((menu) => {
    if (except && menu === except) return;
    menu.hidden = true;
    menu.parentElement
      ?.querySelector(".yhat-tf-version-btn")
      ?.setAttribute("aria-expanded", "false");
  });
}

function versionTrail(id, enabledId, stamp) {
  if (enabledId && id === enabledId) {
    const mark = document.createElement("span");
    mark.className = "yhat-tf-version-on";
    mark.title = "已启用";
    mark.setAttribute("aria-label", "已启用");
    return mark;
  }
  const del = document.createElement("button");
  del.type = "button";
  del.className = "yhat-tf-version-del";
  del.dataset.versionDelete = id;
  del.textContent = "×";
  del.title = "删除该版本";
  del.setAttribute("aria-label", `删除 ${stamp}`);
  return del;
}

function renderVersionMenu(menu, items, currentId, enabledId) {
  const onId = String(enabledId || "");
  menu.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "yhat-tf-version-empty";
    empty.textContent = "尚无版本";
    menu.append(empty);
    return;
  }
  for (const it of items) {
    const id = String(it?.id || "");
    if (!id) continue;
    const row = document.createElement("div");
    row.className = "yhat-tf-version-item";
    row.dataset.versionId = id;
    row.setAttribute("role", "option");
    const current = id === currentId;
    row.setAttribute("aria-selected", current ? "true" : "false");
    if (current) row.classList.add("is-current");
    if (onId && id === onId) row.classList.add("is-enabled");
    const parts = versionParts(it.label || id);
    const stamp = document.createElement("span");
    stamp.className = "yhat-tf-version-stamp";
    stamp.textContent = parts.stamp;
    const metric = document.createElement("span");
    metric.className = "yhat-tf-version-metric";
    const ic =
      parts.meta.find((bit) => bit.startsWith("MSE")) ||
      parts.meta.find((bit) => bit.startsWith("RankIC")) ||
      "";
    metric.textContent = ic;
    const chips = document.createElement("span");
    chips.className = "yhat-tf-version-meta";
    for (const bit of parts.meta) {
      if (bit === ic) continue;
      const chip = document.createElement("span");
      chip.className = "yhat-tf-version-chip";
      const tone = versionChipTone(bit, it.status);
      if (tone) chip.classList.add(`is-${tone}`);
      const full = VERSION_BADGE[bit];
      if (full && full !== bit) {
        chip.title = full;
        chip.setAttribute("aria-label", full);
      }
      chip.textContent = bit;
      chips.append(chip);
    }
    row.append(stamp, metric, chips, versionTrail(id, onId, parts.stamp));
    menu.append(row);
  }
}

function listsOf(api) {
  const bag = versionLists.get(api);
  if (bag && (Array.isArray(bag.research) || Array.isArray(bag.live))) return bag;
  return { research: [], live: [] };
}

function findVersion(api, runId) {
  const bag = listsOf(api);
  for (const role of ["research", "live"]) {
    const hit = (bag[role] || []).find((it) => String(it.id) === runId);
    if (hit) return hit;
  }
  return null;
}

function suiteItems(items, role) {
  return (items || []).filter((it) => {
    const marked = String(it?.suite || "");
    if (marked === "live" || marked === "research") return marked === role;
    const holdout = Number(it?.holdout_trading_days);
    if (role === "live") return holdout === 0;
    return !Number.isFinite(holdout) || holdout !== 0;
  });
}

function paintEnable(spec) {
  const on = enabledIds.get(spec.api) || {};
  const slot = roleChoice(spec.api);
  for (const role of ["research", "live"]) {
    const pick = versionRoot(spec.ui, role)?.closest(".quant-suite-fit-pick");
    const btn = pick?.querySelector("[data-version-enable]");
    if (!btn) continue;
    const id = slot[role] || "";
    const active = !!(id && on[role] === id);
    if (!btn.dataset.idleTitle) btn.dataset.idleTitle = btn.title || "";
    btn.textContent = "启用";
    btn.classList.remove("is-on");
    btn.disabled = !id || active || pending.has(spec.api);
    btn.title = active ? "当前所选已是启用版本" : btn.dataset.idleTitle;
  }
}

function fillVersion(spec) {
  const bag = listsOf(spec.api);
  const slot = roleChoice(spec.api);
  for (const role of ["research", "live"]) {
    const items = suiteItems(bag[role], role);
    const root = versionRoot(spec.ui, role);
    const btn = root?.querySelector(".yhat-tf-version-btn");
    const menu = root?.querySelector(".yhat-tf-version-menu");
    if (!root || !btn || !menu) continue;
    const enabled = (enabledIds.get(spec.api) || {})[role] || "";
    const key = `${enabled}\n${items.map((it) => `${it.id}\t${it.label || ""}`).join("\n")}`;
    const want = slot[role] || "";
    const has = !!(want && items.some((it) => String(it.id) === want));
    const next = has ? want : String(items[0]?.id || "");
    slot[role] = next;
    const shown = items.find((it) => String(it.id) === next);
    btn.textContent = shown ? String(shown.label || shown.id) : "尚无版本";
    btn.title = shown?.model_id ? String(shown.model_id) : btn.textContent;
    if (root.dataset.itemsKey === key && root.dataset.value === next) continue;
    root.dataset.itemsKey = key;
    root.dataset.value = next;
    renderVersionMenu(menu, items, next, enabled);
  }
  paintEnable(spec);
}

async function loadVersions(spec, { replay = false } = {}) {
  const head = encodeURIComponent(spec.api);
  const VERSION_URL = factoryUrls(spec).versions;
  const [research, live] = await Promise.all([
    apiFetch(`${VERSION_URL}?head=${head}&suite=research`),
    apiFetch(`${VERSION_URL}?head=${head}&suite=live`),
  ]);
  if (!research.ok && !live.ok) return;
  versionLists.set(spec.api, {
    research: research.ok && Array.isArray(research.data?.items) ? research.data.items : [],
    live: live.ok && Array.isArray(live.data?.items) ? live.data.items : [],
  });
  enabledIds.set(spec.api, {
    research: research.ok ? String(research.data?.enabled_id || "") : "",
    live: live.ok ? String(live.data?.enabled_id || "") : "",
  });
  fillVersion(spec);
  if (!replay || pending.has(spec.api) || viewing.get(spec.api)) return;
  const shown = versionRoot(spec.ui, "research")?.dataset.value || "";
  if (shown) await openVersion(spec, shown, "research");
}

function metricsPack(metrics, tag) {
  if (!metrics || typeof metrics !== "object") return null;
  if (metrics.cs_rank_ic != null && (!metrics.arm || metrics.arm === tag)) return metrics;
  if (metrics.arms && metrics.arms[tag]) return metrics.arms[tag];
  if (metrics[tag] && metrics[tag].cs_rank_ic != null) return metrics[tag];
  return null;
}

function seqFeatValue(raw) {
  const s = String(raw || "").trim().toLowerCase();
  if (s === "full" || s === "seq_full") return "seq_full";
  if (s === "core" || s === "seq_core") return "seq_core";
  return "";
}

function applyRecipeParams(spec, params) {
  const section = sectionOf(spec.ui);
  if (!section || !params || typeof params !== "object") return;
  section.querySelectorAll("[data-param]").forEach((inp) => {
    if (inp.disabled) return;
    const key = inp.getAttribute("data-param");
    if (!key || params[key] == null || params[key] === "") return;
    inp.value = String(params[key]);
  });
  const pick = (key, value) => {
    if (value == null || value === "") return;
    const el = section.querySelector(
      `[data-param-radio="${key}"][value="${value}"]`
    );
    if (el) el.checked = true;
  };
  pick("seq_feat_set", seqFeatValue(params.seq_feat_set));
  const pool = String(params.feature_set || "");
  if (pool === "pool" || pool === "hs300") {
    const el = section.querySelector(`[data-hub-pool="${pool}"]`);
    if (el) el.checked = true;
  }
  const prefix = params.tau_end || params.tau_end_hm;
  if (spec.fullAtc && prefix) paintPrefixHm(prefix);
}

function replayChips(params) {
  if (!params || typeof params !== "object") return [];
  const tag = String(params.arms || "T").toUpperCase();
  const pool =
    params.feature_set === "hs300"
      ? "沪深300"
      : params.feature_set === "pool"
        ? "大池"
        : params.pool_n;
  if (tag === "R" || tag === "L") {
    return [
      ["池子", pool],
      ["训练窗", params.lookback],
      ["Holdout", params.holdout_trading_days],
      ["λ", tag === "R" ? params.ridge_alpha : ""],
    ].filter(([, value]) => value != null && value !== "");
  }
  const seqKey = String(params.seq_feat_set || "").trim().toLowerCase();
  const seqTable = String(params.factor_pack || "").includes("atc")
    ? { core: "core · 61", full: "full · 218" }
    : { core: "core · 65", full: "full · 222" };
  const dim =
    seqKey === "seq_full" || seqKey === "full"
      ? seqTable.full
      : seqKey === "seq8" || seqKey === "8"
        ? "8维"
        : seqKey === "seq16" || seqKey === "16"
          ? "16维"
          : seqKey === "seq32" || seqKey === "32"
            ? "32维"
            : seqKey === "seq64" || seqKey === "64"
              ? "64维"
              : seqKey
                ? seqTable.core
                : "";
  const sample = params.t_sample_pct;
  return [
    ["池子", params.pool_n],
    ["训练窗", params.lookback],
    ["Holdout", params.holdout_trading_days],
    ["序列", dim],
    ["batch", params.batch_size],
    ["epochs", params.epochs],
    ["T采样%", sample == null || sample === "" ? "" : String(Number(sample))],
    ["d_model", params.d_model],
    ["layers", params.nlayers],
  ].filter(([, value]) => value != null && value !== "");
}

function replayStamp(raw) {
  const match = String(raw || "").match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : String(raw || "");
}

function replayLog(raw) {
  return String(raw || "").trim();
}

function paintReplay(spec, doc) {
  const box = sectionOf(spec.ui)?.querySelector(".quant-recipe-console");
  if (!box) return;
  const info = box.querySelector("[data-recipe-replay]");
  const status = box.querySelector("[data-recipe-replay-status]");
  const meta = box.querySelector("[data-recipe-replay-meta]");
  const paramsEl = box.querySelector("[data-recipe-replay-params]");
  const logEl = box.querySelector("[data-recipe-replay-log]");
  if (info) info.hidden = false;
  const failed = doc?.status === "error" || doc?.action === "holdout_failed";
  if (status) status.textContent = failed ? "回看 · 拟合失败" : "回看 · 拟合完成";
  liveSince.delete(spec.api);
  paintArmState(box, failed ? "失败" : "完成", failed ? "fail" : "ok", {
    dur: spentDur(
      doc?.started_at,
      doc?.finished_at,
      metricsPack(doc?.metrics, arm(spec))?.elapsed_sec
    ),
  });
  if (meta) {
    meta.replaceChildren();
    const stamp = replayStamp(doc?.finished_at);
    for (const bit of [doc?.run_id, stamp].filter(Boolean)) {
      const item = document.createElement("span");
      item.textContent = String(bit);
      meta.append(item);
    }
  }
  if (paramsEl) {
    paramsEl.replaceChildren();
    for (const [name, value] of replayChips(doc?.fit_params)) {
      const chip = document.createElement("span");
      chip.className = "yhat-tf-fit-chip";
      const label = document.createElement("span");
      label.textContent = name;
      const strong = document.createElement("strong");
      strong.textContent = String(value);
      chip.append(label, strong);
      paramsEl.append(chip);
    }
  }
  const text = replayLog(doc?.log_tail);
  if (logEl) {
    logEl.hidden = !text;
    logEl.textContent = text;
  }
  paintFitCurves(box, arm(spec) === "T" ? doc?.fit_curve : null);
}

function paintArmState(box, text, tone, extra = {}) {
  const state = box?.querySelector("[data-recipe-replay-state]");
  if (!state) return;
  state.dataset.state = tone;
  if (extra.clockFrom) state.dataset.clockFrom = extra.clockFrom;
  else delete state.dataset.clockFrom;
  let label = state.querySelector(":scope > span:not(.yhat-tf-arm-state-dur)");
  if (!label) {
    label = document.createElement("span");
    state.appendChild(label);
  }
  label.textContent = text;
  const shown = extra.clock || extra.dur || "";
  let dur = state.querySelector(".yhat-tf-arm-state-dur");
  if (!shown) {
    dur?.remove();
    syncHubClock();
    return;
  }
  if (!dur) {
    dur = document.createElement("span");
    dur.className = "yhat-tf-arm-state-dur";
    state.appendChild(dur);
  }
  dur.dataset.role = extra.clock ? "clock" : "dur";
  if (dur.textContent !== shown) dur.textContent = shown;
  syncHubClock();
}

function paintLive(spec, st, label) {
  const box = sectionOf(spec.ui)?.querySelector(".quant-recipe-console");
  if (!box) return;
  const info = box.querySelector("[data-recipe-replay]");
  if (info) info.hidden = true;
  if (label) liveLabel.set(spec.api, label);
  const shown = liveLabel.get(spec.api) || "拟合中";
  const from = rememberLiveStart(spec.api, st?.job?.started_at);
  const clock = elapsedClock(from);
  paintArmState(box, shown, "busy", { clockFrom: from, clock });
  paintCardMessage(spec, clock ? `${armName(spec)} ${shown} · ${clock}` : `${armName(spec)} ${shown}`);
  paintMetrics(spec.ui, null);
  paintFitCurves(box, arm(spec) === "T" ? st?.fit_curve : null);
  const logEl = box.querySelector("[data-recipe-replay-log]");
  const text = replayLog(st?.log_tail);
  if (!logEl) return;
  const stick = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 48;
  logEl.hidden = !text;
  if (logEl.textContent !== text) logEl.textContent = text;
  if (text && stick) logEl.scrollTop = logEl.scrollHeight;
}

function clearReplay(spec) {
  const box = sectionOf(spec.ui)?.querySelector(".quant-recipe-console");
  const info = box?.querySelector("[data-recipe-replay]");
  const logEl = box?.querySelector("[data-recipe-replay-log]");
  if (info) info.hidden = true;
  paintArmState(box, "待运行", "idle");
  if (logEl) {
    logEl.hidden = true;
    logEl.textContent = "";
  }
  paintFitCurves(box, null);
}

async function openVersion(spec, runId, role = "research") {
  if (role === "research" || role === "live") roleChoice(spec.api)[role] = runId;
  viewing.set(spec.api, runId);
  fillVersion(spec);
  const VERSION_URL = factoryUrls(spec).versions;
  const res = await apiFetch(
    `${VERSION_URL}/preview?head=${encodeURIComponent(spec.api)}&run_id=${encodeURIComponent(runId)}`
  );
  if (!res.ok) {
    paintCardMessage(spec, `${armName(spec)} ${res.error || "版本预览失败"}`);
    return;
  }
  applyRecipeParams(spec, res.data?.fit_params);
  paintReplay(spec, res.data);
  const pack = metricsPack(res.data?.metrics, arm(spec));
  if (pack) paintMetrics(spec.ui, pack);
  paintCardMessage(spec, "");
}

async function deleteVersion(spec, runId) {
  const it = findVersion(spec.api, runId);
  const label = String(it?.label || runId);
  if (!window.confirm(`删除版本「${label}」？只去掉这条归档、日志和权重，当前拟合不动。`)) return;
  const VERSION_URL = factoryUrls(spec).versions;
  const res = await apiFetch(
    `${VERSION_URL}?head=${encodeURIComponent(spec.api)}&run_id=${encodeURIComponent(runId)}`,
    { method: "DELETE" }
  );
  if (!res.ok) {
    paintCardMessage(spec, `${armName(spec)} ${res.error || "删除失败"}`);
    return;
  }
  const slot = roleChoice(spec.api);
  for (const role of ["research", "live"]) {
    if (slot[role] === runId) slot[role] = "";
  }
  const wasViewing = viewing.get(spec.api) === runId;
  if (wasViewing) {
    viewing.delete(spec.api);
    clearReplay(spec);
  }
  await loadVersions(spec, { replay: wasViewing });
  await refresh();
}

async function enableVersion(spec, role) {
  const runId = roleChoice(spec.api)[role] || "";
  if (!runId || pending.has(spec.api)) return;
  const VERSION_URL = factoryUrls(spec).versions;
  const res = await apiFetch(`${VERSION_URL}/enable`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ head: spec.api, run_id: runId, suite: role }),
  });
  if (!res.ok) {
    paintCardMessage(spec, `${armName(spec)} ${res.error || "启用失败"}`);
    return;
  }
  const bag = enabledIds.get(spec.api) || { research: "", live: "" };
  bag[role] = runId;
  enabledIds.set(spec.api, bag);
  paintEnable(spec);
  fillVersion(spec);
  paintCardMessage(
    spec,
    role === "live" ? `${armName(spec)} 已启用为 live 版本` : `${armName(spec)} 已启用为回测版本`
  );
}

function wireEnable(spec) {
  const section = sectionOf(spec.ui);
  section?.querySelectorAll("[data-version-enable]").forEach((btn) => {
    if (btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      void enableVersion(spec, btn.getAttribute("data-version-enable") || "research");
    });
  });
}

function wireVersion(spec) {
  for (const role of ["research", "live"]) {
    const root = versionRoot(spec.ui, role);
    if (!root || root.dataset.wired === "1") continue;
    root.dataset.wired = "1";
    root.addEventListener("click", (ev) => {
      const btn = root.querySelector(".yhat-tf-version-btn");
      const menu = root.querySelector(".yhat-tf-version-menu");
      const del = ev.target.closest?.("[data-version-delete]");
      if (del) {
        ev.preventDefault();
        ev.stopPropagation();
        void deleteVersion(spec, del.dataset.versionDelete || "");
        return;
      }
      const picked = ev.target.closest?.("[data-version-id]");
      if (picked && menu?.contains(picked)) {
        ev.preventDefault();
        closeVersionMenus();
        void openVersion(spec, picked.dataset.versionId || "", role);
        return;
      }
      if (!ev.target.closest?.(".yhat-tf-version-btn")) return;
      ev.preventDefault();
      ev.stopPropagation();
      const open = menu?.hidden;
      closeVersionMenus(open ? menu : null);
      if (menu && btn && open) {
        menu.hidden = false;
        btn.setAttribute("aria-expanded", "true");
      }
    });
  }
}

function wireVersionDismiss() {
  if (versionMenusWired) return;
  versionMenusWired = true;
  document.addEventListener("click", (ev) => {
    if (ev.target?.closest?.(".yhat-tf-version")) return;
    closeVersionMenus();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeVersionMenus();
  });
}

function applyHead(spec, st) {
  const pack = packOf(st, arm(spec));
  const busy = !!st?.fit_running || pending.has(spec.api);
  // 拟合中只锁版本和拟合按钮。概览臂下拉仍可换，换臂不中断已经拉起的这一次。
  const armPick = document.querySelector(`[data-hub-arm-select="${spec.ui}"]`);
  if (armPick) {
    armPick.disabled = false;
    if (busy) armPick.title = "正在拟合，换臂不中断这一次";
    else armPick.removeAttribute("title");
  }
  for (const id of [spec.fitId, spec.liveId]) {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = busy;
  }
  for (const role of ["research", "live"]) {
    const versionBtn = versionRoot(spec.ui, role)?.querySelector(".yhat-tf-version-btn");
    if (versionBtn) versionBtn.disabled = busy;
  }
  if (st?.fit_running || pending.has(spec.api)) {
    viewing.delete(spec.api);
    paintLive(spec, st?.fit_running ? st : null);
    paintGate(spec.ui, "拟合中");
    return;
  }
  liveSince.delete(spec.api);
  liveLabel.delete(spec.api);
  if (viewing.get(spec.api)) return;
  clearReplay(spec);
  paintMetrics(spec.ui, pack);
  const rankHead = spec.ui === "oo_rank" || spec.ui === "tc_rank";
  const dur = spentDur(st?.job?.started_at, st?.job?.finished_at, pack?.elapsed_sec);
  const box = sectionOf(spec.ui)?.querySelector(".quant-recipe-console");
  const failed =
    st?.job?.status === "error" || st?.last?.action === "holdout_failed";
  const curve = arm(spec) === "T" ? st?.fit_curve : null;
  if (failed) {
    const err = st?.job?.error || st?.last?.error || "拟合失败";
    paintArmState(box, "失败", "fail", { dur });
    paintFitCurves(box, curve);
    paintCardMessage(spec, dur ? `${armName(spec)} ${err} · ${dur}` : `${armName(spec)} ${err}`);
    return;
  }
  if (pack && (rankHead ? pack.cs_rank_ic != null : pack.mse != null || pack.cs_rank_ic != null || pack.sign_hit != null)) {
    paintArmState(box, "完成", "ok", { dur });
    paintFitCurves(box, curve);
    const spent = dur ? ` · ${dur}` : "";
    paintCardMessage(
      spec,
      rankHead
        ? `${armName(spec)} Rank IC ${fmt3(pack.cs_rank_ic)} · ICIR ${fmt3(pack.cs_icir)}${spent}`
        : `${armName(spec)} MSE ${fmt3(pack.mse)} · Rank IC ${fmt3(pack.cs_rank_ic)}${spent}`
    );
    return;
  }
  const holdout = Number(st?.job?.params?.holdout_trading_days);
  if (st?.job?.status === "done" && holdout === 0) {
    paintArmState(box, "完成", "ok", { dur });
    paintFitCurves(box, curve);
    paintGate(spec.ui, "全样本");
    paintCardMessage(
      spec,
      dur
        ? `${armName(spec)} 全样本已写入，无评估窗 · ${dur}`
        : `${armName(spec)} 全样本已写入，无评估窗`
    );
  }
}

async function refresh() {
  const used = [...new Set(HEADS.map((spec) => arm(spec)))];
  const bags = await Promise.all(
    used.map(async (tag) => {
      const res = await apiFetch(ARM_API[tag].status);
      return [tag, res.ok ? res.data : null];
    })
  );
  const byFactory = new Map(bags);
  if (![...byFactory.values()].some(Boolean)) return false;
  let running = false;
  for (const spec of HEADS) {
    const data = byFactory.get(arm(spec));
    const st = (data?.heads || []).find((row) => row?.head === spec.api);
    if (st?.fit_running) running = true;
    if (st) applyHead(spec, st);
    void loadVersions(spec, {
      replay: !st?.fit_running && !pending.has(spec.api) && !viewing.get(spec.api),
    });
  }
  schedule(running ? 2000 : 12000);
  return true;
}

function schedule(ms) {
  if (timer) window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    if (document.hidden) {
      schedule(ms);
      return;
    }
    refresh().catch(() => schedule(12000));
  }, ms);
}

async function startFit(spec, role) {
  if (pending.has(spec.api)) return;
  pending.add(spec.api);
  viewing.delete(spec.api);
  for (const id of [spec.fitId, spec.liveId]) {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = true;
  }
  paintLive(spec, null, "拟合中");
  try {
    const note =
      arm(spec) === "T"
        ? role === "live"
          ? "hub arm-t live"
          : "hub arm-t research"
        : `hub arm-${arm(spec).toLowerCase()} ${role}`;
    const res = await apiFetch(factoryUrls(spec).fit, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        head: spec.api,
        params: recipeParams(spec, role),
        note,
      }),
    });
    if (!res.ok) throw new Error(res.error || "拟合启动失败");
    await refresh();
    schedule(2000);
  } catch (err) {
    const dur = elapsedClock(liveSince.get(spec.api));
    liveSince.delete(spec.api);
    liveLabel.delete(spec.api);
    paintArmState(
      sectionOf(spec.ui)?.querySelector(".quant-recipe-console"),
      "失败",
      "fail",
      { dur }
    );
    paintCardMessage(
      spec,
      dur ? `${armName(spec)} ${err?.message || err} · ${dur}` : `${armName(spec)} ${err?.message || err}`
    );
    for (const id of [spec.fitId, spec.liveId]) {
      const btn = document.getElementById(id);
      if (btn) btn.disabled = false;
    }
  } finally {
    pending.delete(spec.api);
  }
}

function setField(section, key, value) {
  const el = section?.querySelector(`[data-param="${key}"]`);
  if (el && !el.disabled) el.value = String(value);
}

function adoptDefaults(spec, fromTag, toTag) {
  const section = sectionOf(spec.ui);
  const prev = defaultsFor(fromTag, spec.api);
  const next = defaultsFor(toTag, spec.api);
  if (sectionNum(section, "lookback", prev.lookback) === prev.lookback) {
    setField(section, "lookback", next.lookback);
  }
  if (sectionNum(section, "holdout_trading_days", prev.holdout) === prev.holdout) {
    setField(section, "holdout_trading_days", next.holdout);
  }
}

function paintArmChrome(spec) {
  const tag = arm(spec);
  const copy = ARM_COPY[tag];
  const section = sectionOf(spec.ui);
  const config = section?.querySelector(".quant-fit-params");
  if (config) config.dataset.hubLayout = tag;
  section?.querySelectorAll("[data-hub-fields]").forEach((el) => {
    const want = el.getAttribute("data-hub-fields") || "";
    el.hidden = !want.includes(tag);
  });
  const picked = document.querySelector(`[data-hub-arm-select="${spec.ui}"]`);
  if (picked && picked.value !== tag) picked.value = tag;
  const consoleBox = section?.querySelector("[data-arm-console]");
  if (consoleBox) {
    consoleBox.dataset.armConsole = tag;
    const badge = consoleBox.querySelector(".yhat-tf-arm-badge");
    if (badge) badge.textContent = tag;
    const title = consoleBox.querySelector(".yhat-tf-arm-title strong");
    const desc = consoleBox.querySelector(".yhat-tf-arm-title span");
    if (title) title.textContent = copy.title;
    if (desc) desc.textContent = copy.desc;
    if (tag !== "T") paintFitCurves(section?.querySelector(".quant-recipe-console"), null);
  }
  const fit = document.getElementById(spec.fitId);
  if (fit) fit.title = `按当前配置拟合 ${copy.title}`;
}

function chooseArm(spec, raw) {
  const next = raw === "L" || raw === "R" ? raw : "T";
  const prev = arm(spec);
  if (next === prev || pending.has(spec.api)) {
    paintArmChrome(spec);
    return;
  }
  adoptDefaults(spec, prev, next);
  armOf.set(spec.api, next);
  viewing.delete(spec.api);
  versionLists.delete(spec.api);
  chosen.delete(spec.api);
  enabledIds.delete(spec.api);
  for (const role of ["research", "live"]) {
    const root = versionRoot(spec.ui, role);
    if (!root) continue;
    delete root.dataset.itemsKey;
    delete root.dataset.value;
  }
  clearReplay(spec);
  paintMetrics(spec.ui, null);
  paintCardMessage(spec, "");
  paintArmChrome(spec);
  void saveArms();
  void loadVersions(spec, { replay: true });
  void refresh();
}

function wireArm(spec) {
  const el = document.querySelector(`[data-hub-arm-select="${spec.ui}"]`);
  if (!el || el.dataset.wired === "1") return;
  el.dataset.wired = "1";
  el.addEventListener("click", (ev) => ev.stopPropagation());
  el.addEventListener("change", () => {
    chooseArm(spec, String(el.value || "T").toUpperCase());
  });
}

async function saveArms() {
  const arms = {};
  for (const spec of HEADS) arms[spec.api] = arm(spec);
  await apiFetch(ARMS_URL, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arms }),
  });
}

async function loadArms() {
  const res = await apiFetch(ARMS_URL);
  const arms = res.ok ? res.data?.arms : null;
  for (const spec of HEADS) {
    const raw = String(arms?.[spec.api] || "T").toUpperCase();
    const next = raw === "L" || raw === "R" ? raw : "T";
    if (next !== "T") adoptDefaults(spec, "T", next);
    armOf.set(spec.api, next);
    paintArmChrome(spec);
  }
}

export function installHubArmT() {
  const page = document.querySelector(".quant-page");
  if (!page || page.getAttribute("data-quant-recipe") !== "transformer") return;
  if (page.dataset.hubArmT === "1") return;
  page.dataset.hubArmT = "1";
  for (const spec of HEADS) {
    const fit = document.getElementById(spec.fitId);
    if (fit) {
      fit.addEventListener(
        "click",
        (e) => {
          e.preventDefault();
          e.stopImmediatePropagation();
          void startFit(spec, "research");
        },
        true
      );
    }
    const live = document.getElementById(spec.liveId);
    if (live) {
      live.addEventListener(
        "click",
        (e) => {
          e.preventDefault();
          e.stopImmediatePropagation();
          void startFit(spec, "live");
        },
        true
      );
    }
    wireVersion(spec);
    wireEnable(spec);
    wireArm(spec);
    const gate = document.querySelector(`[data-head-summary-gate="${spec.ui}"]`);
    if (gate && !gate.dataset.armGate) {
      const blank =
        spec.ui === "oo_rank" || spec.ui === "tc_rank" ? "Rank IC —" : "MSE —";
      gate.dataset.armGate = blank;
      gate.textContent = blank;
    }
  }
  wireVersionDismiss();
  loadArms()
    .catch(() => {})
    .then(() => refresh())
    .then((ok) => {
      if (ok === false) schedule(12000);
    })
    .catch(() => schedule(12000));
}
