/** 模型工厂 · /yhat-l | /yhat-t · 四头 Holdout 研究台（⌘⇧K；不写 live） */

import { apiFetch } from "./api_client.js";

const HEADS = ["oo", "tc", "oo_rank", "tc_rank"];
/** @type {"L"|"T"} */
let pageFactory = "T";
function apiBase() {
  return pageFactory === "L" ? "/api/quant/yhat-l" : "/api/quant/yhat-t";
}
const LOSS_CANON = {
  listwise: "listwise",
  listnet: "listwise",
  rank: "listwise",
  smooth_l1: "smooth_l1",
  huber: "smooth_l1",
  regression: "smooth_l1",
  mse: "mse",
  l2: "mse",
};
const GATE_LABEL = {
  oo: "Arm·L",
  tc: "Arm·L",
  oo_rank: "Gate lift",
  tc_rank: "Gate lift",
};
const BOOL_ON = new Set(["1", "true", "yes", "on", "full"]);
const BOOL_OFF = new Set(["0", "false", "no", "off", "core"]);
/** 与 desk `_as_bool` 同一张表。数字跟面板列宽走，改列宽时两处一起改。 */
const PACK_WIDTH = {
  aoo: { core: "core · 68", full: "full · 225" },
  atc: { core: "core · 55", full: "full · 230" },
};

function asBool(raw, fallback) {
  if (raw == null || raw === "") return fallback;
  if (typeof raw === "boolean") return raw;
  if (typeof raw === "number") return raw !== 0 && !Number.isNaN(raw);
  const s = String(raw).trim().toLowerCase();
  if (BOOL_ON.has(s)) return true;
  if (BOOL_OFF.has(s)) return false;
  return fallback;
}

function gateDisplay(st) {
  return (
    st?.scheme?.gate_label ||
    GATE_LABEL[st?.head] ||
    st?.scheme?.gate ||
    "—"
  );
}
const ACTION_SUCCESS = {
  fit: "已启动后台拟合（Holdout；不写 live）",
};
let fitPollTimer = null;

let activeHead = "oo";
let viewMode = "single";
let refreshing = false;
let refreshPromise = null;
let lastGoodData = null;
let toastTimer = null;
const latestHeads = new Map();
const pendingHeads = new Set();
/** 用户改过参数的 head：刷新时勿用 draft 覆盖表单 */
const dirtyParams = new Set();

function $(sel, root = document) {
  return root.querySelector(sel);
}

function $all(sel, root = document) {
  return Array.from(root.querySelectorAll(sel));
}

function panelOf(head) {
  return document.querySelector(`[data-head-panel="${head}"]`);
}

function sectionOf(head) {
  return document.querySelector(`[data-head-section="${head}"]`);
}

function jsonPost(url, body) {
  return apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function stateOf(st) {
  if (st?.fit_running) return "busy";
  const jobStatus = st?.job?.status;
  if (jobStatus === "error" || st?.last?.action === "holdout_failed") return "error";
  const metrics = currentFitMetrics(st);
  // 单厂页：有 arm metrics 即视为就绪（不再用相对硬闸）
  if (st?.last?.action === "holdout_result" && metrics) return "ok";
  if (metrics?.gate_pass === true) return "ok";
  if (st?.last?.action === "holdout_result") return "warn";
  if (jobStatus === "done") return "warn";
  if (st?.scheme?.status === "in_progress") return "busy";
  if (st?.scheme?.status === "planned") return "warn";
  return "idle";
}

function stateLabel(state) {
  return {
    ok: "有指标",
    warn: "已拟合",
    busy: "处理中",
    error: "失败",
    idle: "未拟合",
  }[state] || "未拟合";
}

function headFitOutcome(st) {
  if (st?.fit_running) return "running";
  if (st?.last?.action === "holdout_failed") return "failed";
  const metrics = currentFitMetrics(st);
  if (st?.last?.action === "holdout_result" && metrics) return "gated";
  if (metrics?.gate_pass === true) return "gated";
  if (st?.last?.action === "holdout_result") return "fitted";
  return "none";
}

function setChip(el, state, msg) {
  if (!el) return;
  const chip = el.querySelector(".quant-pro-status-chip");
  const text = el.querySelector(".yhat-tf-status-msg");
  if (chip) {
    chip.setAttribute("data-state", state || "idle");
    chip.textContent = stateLabel(state);
  }
  if (text && msg != null) text.textContent = msg;
}

function showToast(message, tone = "ok") {
  const toast = $("#yhat-tf-toast");
  if (!toast) return;
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.dataset.tone = tone;
  toast.setAttribute("role", tone === "error" ? "alert" : "status");
  toast.hidden = false;
  // warn/error 留久一点；点击可立即关掉（见 bindToastDismiss）
  const ms = tone === "error" ? 12000 : tone === "warn" ? 9000 : 3600;
  toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, ms);
}

function setFitInfoNote(head, message, tone = "ok") {
  const note = document.querySelector(
    `[data-fit-info-for="${head}"] [data-fit-info-note]`
  );
  if (!note) return;
  const text = String(message || "").trim();
  note.hidden = !text;
  note.textContent = text;
  note.dataset.tone = tone || "ok";
}

function showPanelError(head, message) {
  const notice = document.querySelector(`[data-notice-for="${head}"]`);
  if (!notice) return;
  notice.textContent = message;
  notice.hidden = !message;
}

function setLoadError(message) {
  const box = $("#yhat-tf-load-error");
  if (!box) return;
  box.hidden = !message;
  const text = $("[data-load-error-text]", box);
  if (text && message) text.textContent = message;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ");
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function normalizeLoss(raw) {
  const key = String(raw || "")
    .trim()
    .toLowerCase();
  return LOSS_CANON[key] || key || "smooth_l1";
}

/** Arm·L 不沿用 HeadSpec 的 Transformer 损失名。 */
function shownLoss(head, raw) {
  const loss = String(raw || "").trim();
  if (pageFactory !== "L") return loss;
  if (head === "oo_rank" || head === "tc_rank") return "lambdarank";
  if (
    !loss ||
    loss === "smooth_l1" ||
    loss === "huber" ||
    loss === "regression" ||
    loss === "mse" ||
    loss === "l2"
  ) {
    return "l2";
  }
  return loss;
}

function scrubArmLLoss(value, head) {
  if (pageFactory !== "L" || value == null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((item) => scrubArmLLoss(item, head));
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (key === "loss") out[key] = shownLoss(head, item);
    else if (item && typeof item === "object") out[key] = scrubArmLLoss(item, head);
    else out[key] = item;
  }
  return out;
}

function normalizeSeqFeatSet(raw) {
  const s = String(raw || "seq16").trim().toLowerCase();
  if (s === "8" || s === "seq8" || s === "base") return "seq8";
  if (s === "32" || s === "seq32") return "seq32";
  if (s === "64" || s === "seq64") return "seq64";
  if (s === "16" || s === "seq16") return "seq16";
  return "seq16";
}

function readRadioParam(panel, key, fallback) {
  const checked = panel.querySelector(`[data-param-radio="${key}"]:checked`);
  if (checked) return String(checked.value ?? "").trim();
  const sel = panel.querySelector(`[data-param="${key}"]`);
  if (sel) return String(sel.value ?? "").trim();
  return fallback;
}

function applyRadioParam(panel, key, raw) {
  const val = String(raw ?? "").trim();
  const radios = $all(`[data-param-radio="${key}"]`, panel);
  if (radios.length) {
    radios.forEach((el) => {
      el.checked = String(el.value) === val;
    });
  }
  const sel = panel.querySelector(`[data-param="${key}"]`);
  if (sel) sel.value = val;
}

function readSeqFeatSet(panel) {
  return normalizeSeqFeatSet(readRadioParam(panel, "seq_feat_set", "seq16"));
}

function applySeqFeatSet(panel, raw) {
  applyRadioParam(panel, "seq_feat_set", normalizeSeqFeatSet(raw));
}

function readFullAoo(panel) {
  const pack = panel.querySelector("[data-protocol-card]")?.getAttribute("data-head-pack");
  if (pack === "atc") return false;
  return asBool(readRadioParam(panel, "full_aoo", "0"), false);
}

function applyFullAoo(panel, raw) {
  const pack = panel.querySelector("[data-protocol-card]")?.getAttribute("data-head-pack");
  if (pack === "atc") return;
  applyRadioParam(panel, "full_aoo", asBool(raw, false) ? "1" : "0");
}

function readFullAtc(panel) {
  const pack = panel.querySelector("[data-protocol-card]")?.getAttribute("data-head-pack");
  if (pack !== "atc") return false;
  return asBool(readRadioParam(panel, "full_atc", "1"), true);
}

function applyFullAtc(panel, raw) {
  const pack = panel.querySelector("[data-protocol-card]")?.getAttribute("data-head-pack");
  if (pack !== "atc") return;
  applyRadioParam(panel, "full_atc", asBool(raw, true) ? "1" : "0");
}

function isFullAtc(raw) {
  return asBool(raw, true);
}

function seqFeatDim(featSet) {
  const key = normalizeSeqFeatSet(featSet);
  if (key === "seq8") return 8;
  if (key === "seq32") return 32;
  if (key === "seq64") return 64;
  return 16;
}

function factorLabel(panel) {
  const pack = panel.querySelector("[data-protocol-card]")?.getAttribute("data-head-pack");
  const table = pack === "atc" ? PACK_WIDTH.atc : PACK_WIDTH.aoo;
  const on = pack === "atc" ? readFullAtc(panel) : readFullAoo(panel);
  const tag = on ? table.full : table.core;
  const n = Number(tag.split("·").pop());
  return { short: tag, tag, n: Number.isFinite(n) ? n : 0 };
}

function formatTSamplePct(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return "T采样10%";
  if (n >= 100) return "T采样100%";
  const s = Number.isInteger(n) ? String(n) : String(n).replace(/\.?0+$/, "");
  return `T采样${s}%`;
}

function protocolTagText(panel) {
  if (pageFactory === "L") {
    const pool = panel.querySelector('[data-param="pool_n"]')?.value || "—";
    const lb = panel.querySelector('[data-param="lookback"]')?.value || "—";
    const ho = panel.querySelector('[data-param="holdout_trading_days"]')?.value || "—";
    return `L · 池${pool} · 窗${lb} · HO${ho}`;
  }
  const seqLen = Number(panel.querySelector('[data-param="seq_len"]')?.value) || 20;
  const f = seqFeatDim(readSeqFeatSet(panel));
  const fac = factorLabel(panel);
  const cap = formatTSamplePct(panel.querySelector('[data-param="t_sample_pct"]')?.value);
  return `T · L${seqLen}×${f} · ${fac.tag} · ${cap}`;
}

function ensureProtocolChrome(panel) {
  const slot = panel?.querySelector?.("[data-run-id-slot]");
  if (!slot) return null;

  if (slot.dataset.chromeReady !== "1") {
    slot.dataset.chromeReady = "1";
    slot.classList.add("yhat-tf-run-id");
    const blank = pageFactory === "L" ? "L · —" : "T · —";
    slot.innerHTML =
      '<span class="yhat-tf-k yhat-tf-run-id-k">协议</span>' +
      `<span class="yhat-tf-protocol-tag" data-protocol-tag title="当前对照协议">${blank}</span>` +
      '<button type="button" class="yhat-tf-protocol-copy" data-copy-protocol title="复制协议标签">复制</button>';
    slot.querySelector("[data-copy-protocol]")?.addEventListener("click", async () => {
      const tag = String(slot.querySelector("[data-protocol-tag]")?.textContent || "").trim();
      if (!tag) return;
      try {
        await navigator.clipboard.writeText(tag);
        showToast(`已复制协议 · ${tag}`, "ok");
      } catch (_) {
        showToast("复制协议标签失败", "warn");
      }
    });
  }
  const card = panel.querySelector("[data-protocol-card]");
  if (card) card.dataset.chromeReady = "1";
  return card;
}

function updateProtocolPreviews(panel) {
  if (!panel) return;
  ensureProtocolChrome(panel);
  const feat = readSeqFeatSet(panel);
  const f = seqFeatDim(feat);
  const fac = factorLabel(panel);
  const tagEl = panel.querySelector("[data-protocol-tag]");
  if (tagEl) {
    const text = protocolTagText(panel);
    tagEl.textContent = text;
    tagEl.title = text || "当前对照协议";
  }
  const card = panel.querySelector("[data-protocol-card]");
  if (card) {
    card.dataset.seqDim = String(f);
    card.dataset.factorMode = fac.tag;
  }
}

function readParams(head) {
  const panel = panelOf(head);
  const out = {};
  if (!panel) return out;
  $all("[data-param]", panel).forEach((inp) => {
    const key = inp.getAttribute("data-param");
    if (!key) return;
    const raw = String(inp.value ?? "").trim();
    if (inp.type === "number") {
      const n = Number(raw);
      out[key] = Number.isFinite(n) ? n : raw;
    } else if (key === "loss") {
      out[key] = normalizeLoss(raw);
    } else {
      out[key] = raw;
    }
  });
  if (pageFactory !== "L") out.arms = "L,T";
  if (
    panel.querySelector('[data-param-radio="seq_feat_set"]') ||
    panel.querySelector('[data-param="seq_feat_set"]')
  ) {
    out.seq_feat_set = readSeqFeatSet(panel);
  }
  if (panel.querySelector('[data-param-radio="full_aoo"]')) {
    out.full_aoo = readFullAoo(panel);
  }
  if (panel.querySelector('[data-param-radio="full_atc"]')) {
    out.full_atc = readFullAtc(panel);
  }
  return out;
}

function applyParams(head, params) {
  const panel = panelOf(head);
  if (!panel || !params || typeof params !== "object") return;
  $all("[data-param]", panel).forEach((inp) => {
    const key = inp.getAttribute("data-param");
    if (!key || params[key] == null) return;
    let val = String(params[key]);
    if (key === "loss") val = normalizeLoss(val);
    inp.value = val;
  });
  if (
    params.seq_feat_set != null &&
    (panel.querySelector('[data-param-radio="seq_feat_set"]') ||
      panel.querySelector('[data-param="seq_feat_set"]'))
  ) {
    applySeqFeatSet(panel, params.seq_feat_set);
  }
  if (params.full_aoo != null) {
    applyFullAoo(panel, params.full_aoo);
  }
  if (params.full_atc != null) {
    applyFullAtc(panel, params.full_atc);
  }
  updateProtocolPreviews(panel);
}

function pretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch (_) {
    return String(obj);
  }
}

/** 信息窗只用「本次」拟合：进行中不展示旧 OOS；完成态认本 run 的 last.metrics */
function currentFitMetrics(st) {
  if (st?.fit_running) return null;
  const last = st?.last;
  const job = st?.job;
  // holdout_result=有成功臂；holdout_failed 也可能带 gate_pass（双败）
  if (
    !last ||
    (last.action !== "holdout_result" && last.action !== "holdout_failed")
  ) {
    return null;
  }
  const metrics = last.metrics;
  if (!metrics || typeof metrics !== "object" || !Object.keys(metrics).length) return null;
  if (job?.started_at && last.updated_at && String(last.updated_at) < String(job.started_at)) {
    return null;
  }
  return metrics;
}

/** 仅渲染传入的本次 metrics；勿回退 research/draft，避免旧 OOS 残留 */
function metricSummary(metrics) {
  if (!metrics || typeof metrics !== "object") return "";
  const entries = [
    ["Rank IC", metrics.cs_rank_ic],
    ["Gate lift", metrics.gate_lift],
  ].filter(([, value]) => value != null && value !== "" && Number.isFinite(Number(value)));
  return entries.map(([label, value]) => `${label} ${Number(value).toFixed(4)}`).join(" · ");
}

function paramChipPairs(params) {
  if (!params || typeof params !== "object") return [];
  const packName = String(params.factor_pack || "");
  const fac = packName.startsWith("atc")
    ? isFullAtc(params.full_atc == null ? true : params.full_atc)
      ? "full"
      : "core"
    : params.full_aoo == null
      ? ""
      : asBool(params.full_aoo, false)
        ? "full"
        : "core";
  const dim = seqFeatDim(params.seq_feat_set);
  const pairs = [
    ["池子", params.watching_limit ?? params.pool_n],
    ["训练窗", params.lookback],
    ["Holdout", params.holdout_trading_days],
  ];
  if (pageFactory !== "L") {
    pairs.push(
      ["序列", dim ? `${dim}维` : params.seq_feat_set],
      ["T因子", fac],
      ["d_model", params.d_model],
      ["layers", params.nlayers],
      ["batch", params.batch_size],
      ["epochs", params.epochs]
    );
  }
  return pairs.filter(([, v]) => v != null && v !== "");
}

function renderParamChips(el, params) {
  if (!el) return;
  const pairs = paramChipPairs(params);
  const key = pairs.map(([k, v]) => `${k}=${v}`).join("|");
  el.hidden = !pairs.length;
  if (el.dataset.bits === key) return;
  el.dataset.bits = key;
  el.replaceChildren();
  for (const [k, v] of pairs) {
    const chip = document.createElement("span");
    chip.className = "yhat-tf-fit-chip";
    const name = document.createElement("span");
    name.textContent = k;
    const value = document.createElement("strong");
    value.textContent = String(v);
    chip.append(name, value);
    el.append(chip);
  }
}

function renderMetaBits(el, bits) {
  if (!el) return;
  const key = bits.join("|");
  if (el.dataset.bits === key) return;
  el.dataset.bits = key;
  el.replaceChildren();
  for (const text of bits) {
    const item = document.createElement("span");
    item.textContent = text;
    el.append(item);
  }
}

function fmtIc(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(4) : "—";
}

/** 与枢纽因子表 KPI 同一位：IC / Rank IC / ICIR / Top10% y 三位。 */
function fmtKpi3(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(3) : "—";
}

function fmtPct(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}

function trainHint(lines) {
  let hint = "";
  for (const line of lines || []) {
    const s = String(line);
    const ep = s.match(/\bep=(\d+\/\d+)/);
    if (ep) hint = ep[1];
    else {
      const step = s.match(/\bstep=(\d+\/\d+)/);
      if (step) hint = step[1];
    }
  }
  return hint;
}

function fmtSec(s) {
  return `${String(s).padStart(2, "0")}s`;
}

function fmtDur(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return "";
  const total = Math.round(n);
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const secBit = fmtSec(s);
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m ${secBit}`;
  if (m > 0) return `${m}m ${secBit}`;
  return secBit;
}

/** 拟合已进行多久。按 10 秒取整，状态旁的数字也按这个节奏变。秒始终两位。 */
function elapsedClock(startedAt) {
  const raw = String(startedAt || "").trim();
  if (!raw) return "";
  const date = new Date(raw.includes("T") ? raw : raw.replace(" ", "T"));
  const t0 = date.getTime();
  if (Number.isNaN(t0)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t0) / 10000) * 10);
  return fmtDur(sec) || "00s";
}

const ARM_CLOCK_MS = 10000;
let armClockTimer = null;

function tickArmClocks() {
  document.querySelectorAll("[data-arm-state][data-clock-from]").forEach((el) => {
    const text = elapsedClock(el.dataset.clockFrom);
    const node = el.querySelector("[data-role='clock']");
    if (!node || !text || node.textContent === text) return;
    node.textContent = text;
    const label = el.querySelector("span:not(.yhat-tf-arm-state-dur)");
    const hint = [...el.querySelectorAll(".yhat-tf-arm-state-dur")].find(
      (n) => n.dataset.role !== "clock"
    );
    el.dataset.bits = `${label?.textContent || ""}|${text}|${hint?.textContent || ""}|${el.dataset.state || ""}`;
  });
}

function syncArmClock() {
  const any = document.querySelector("[data-arm-state][data-clock-from]");
  if (!any) {
    if (armClockTimer) {
      clearInterval(armClockTimer);
      armClockTimer = null;
    }
    return;
  }
  if (armClockTimer) return;
  armClockTimer = setInterval(tickArmClocks, ARM_CLOCK_MS);
}

/** 日志按臂拆分：ArmL/ArmT 各自训练与测试行，其余归入共享的数据准备。 */
function splitFitLog(raw) {
  const lines = String(raw || "").split("\n");
  const L = [];
  const T = [];
  const shared = [];
  for (const line of lines) {
    const s = line.trim();
    if (!s) continue;
    if (
      /^ArmL\b/.test(s) ||
      /^L_lightgbm\b/.test(s) ||
      /^L_lambdarank\b/.test(s) ||
      /\bfit drop\b/i.test(s)
    )
      L.push(line);
    else if (/^ArmT\b/.test(s) || /^transformer\b/.test(s) || /^T_transformer\b/.test(s)) T.push(line);
    else if (
      /^={3,}/.test(s) ||
      /^arm(\s+rankIC)?\s*$/i.test(s) ||
      /run_yhat_transformer_holdout\.py\b/.test(s)
    ) continue;
    else shared.push(line);
  }
  return { L, T, shared };
}

function numOrNull(raw) {
  if (raw == null || raw === "None" || raw === "null" || raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** 轮次 Holdout 探针，不是该臂结束。结束行是 ArmL / ArmT seed= 或对比表。 */
function isEpochHoldoutProbe(line) {
  return /^transformer\s+ep=\d+\/\d+\s+holdout\b/.test(line);
}

/** 本次日志里该臂走到哪一步。完成行一出现就定状态，不跟另一臂对齐。 */
export function liveArmFromLog(lines) {
  let started = false;
  let phase = "idle";
  let pack = null;
  for (const line of lines || []) {
    const s = String(line).trim();
    if (!s) continue;
    started = true;
    // 每轮探针带 rankIC= / 「失败」，不能当成该臂结束；后面的 step= 仍是训练中
    if (isEpochHoldoutProbe(s)) continue;
    if (/失败/.test(s)) {
      phase = "fail";
      pack = { success: false };
      continue;
    }
    const row = s.match(
      /^(?:L_lightgbm|L_lambdarank|T_transformer)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$/
    );
    if (row) {
      const nums = [row[1], row[2], row[3], row[4]].map(numOrNull);
      if (nums.every((n) => n == null)) {
        if (phase !== "ok") {
          phase = "fail";
          pack = { success: false };
        }
        continue;
      }
      phase = "ok";
      pack = {
        success: true,
        cs_rank_ic: nums[0],
        cs_icir: nums[1],
        sign_hit: nums[2],
        cs_top_mean_y: nums[3],
        elapsed_sec: pack?.elapsed_sec ?? null,
      };
      continue;
    }
    const ic = s.match(/rankIC=(\S+)/);
    if (!ic) continue;
    const icir = s.match(/ICIR=(\S+)/);
    const hit = s.match(/sign_hit=(\S+)/);
    const top = s.match(/(?:topY|cs_top_mean_y)=(\S+)/);
    const vals = [
      numOrNull(ic[1]),
      icir ? numOrNull(icir[1]) : null,
      hit ? numOrNull(hit[1]) : null,
      top ? numOrNull(top[1]) : null,
    ];
    if (vals.every((n) => n == null)) continue;
    const sec = s.match(/([0-9]+(?:\.[0-9]+)?)s\b/);
    phase = "ok";
    pack = {
      success: true,
      cs_rank_ic: vals[0],
      cs_icir: vals[1],
      sign_hit: vals[2],
      cs_top_mean_y: vals[3],
      elapsed_sec: sec ? numOrNull(sec[1]) : null,
    };
  }
  if (!started) return { phase: "idle", pack: null };
  if (phase === "idle") return { phase: "busy", pack: null };
  return { phase, pack };
}

export function armStatus(st, tag, pack, live) {
  if (st?.fit_running && live) {
    if (live.phase === "fail") return { text: "失败", tone: "fail" };
    if (live.phase === "ok") {
      return { text: "完成", dur: fmtDur(live.pack?.elapsed_sec), tone: "ok" };
    }
    if (live.phase === "busy") return { text: "训练中", tone: "busy" };
    return { text: "准备中", tone: "busy" };
  }
  if (pack?.success === false) return { text: "失败", tone: "fail" };
  if (pack) {
    return { text: "完成", dur: fmtDur(pack.elapsed_sec), tone: "ok" };
  }
  if (st?.fit_running) return { text: "训练中", tone: "busy" };
  if (st?.job?.status === "error") return { text: "未产出", tone: "fail" };
  if (st?.job?.status === "done") return { text: "无指标", tone: "warn" };
  return { text: "待运行", tone: "idle" };
}

function escConsole(s) {
  return String(s).replace(/[&<>]/g, (c) => (c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;"));
}

function highlightPlain(text, re, paint) {
  const src = String(text ?? "");
  re.lastIndex = 0;
  let html = "";
  let i = 0;
  for (const m of src.matchAll(re)) {
    html += escConsole(src.slice(i, m.index));
    html += paint(m);
    i = m.index + m[0].length;
  }
  html += escConsole(src.slice(i));
  return html;
}

const METRIC_NAME = "rankIC|ICIR|sign_hit|cs_rank_ic|cs_rank_icir|cs_top_mean_y|topY";

function highlightLogLine(line) {
  if (/^(?:L_lightgbm|L_lambdarank|T_transformer)\b/.test(line)) {
    return line.replace(
      /^(\S+)((?:\s+(?:-?\d+(?:\.\d+)?|nan))+)\s*$/,
      (_, head, nums) => escConsole(head) + nums.replace(
        /-?\d+(?:\.\d+)?|nan/g,
        (n) => `<span class="ytf-m">${escConsole(n)}</span>`
      )
    );
  }
  const re = new RegExp(`\\b(${METRIC_NAME})(=|:)\\s*(-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?|nan|None)`, "g");
  return highlightPlain(line, re, (m) => {
    const gap = m[0].slice(m[1].length, m[0].length - m[3].length);
    return `<span class="ytf-k">${escConsole(m[1])}</span>${escConsole(gap)}<span class="ytf-m">${escConsole(m[3])}</span>`;
  });
}

function highlightLog(text) {
  return String(text ?? "").split("\n").map(highlightLogLine).join("\n");
}

function highlightCli(text) {
  return escConsole(text);
}

function highlightJson(text) {
  const re = new RegExp(`"(${METRIC_NAME})"(\\s*:)\\s*(-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)`, "g");
  return highlightPlain(text, re, (m) => (
    `<span class="ytf-k">"${escConsole(m[1])}"</span>${escConsole(m[2])}<span class="ytf-m">${escConsole(m[3])}</span>`
  ));
}

function renderArmState(el, state) {
  if (!el || !state) return;
  el.dataset.state = state.tone;
  if (state.clockFrom) el.dataset.clockFrom = state.clockFrom;
  else delete el.dataset.clockFrom;
  const clock = state.clock || "";
  const key = `${state.text}|${clock}|${state.dur || ""}|${state.tone}`;
  if (el.dataset.bits === key) return;
  el.dataset.bits = key;
  el.replaceChildren();
  const label = document.createElement("span");
  label.textContent = state.text;
  el.append(label);
  if (clock) {
    const node = document.createElement("span");
    node.className = "yhat-tf-arm-state-dur";
    node.dataset.role = "clock";
    node.textContent = clock;
    el.append(node);
  }
  if (state.dur) {
    const dur = document.createElement("span");
    dur.className = "yhat-tf-arm-state-dur";
    dur.textContent = state.dur;
    el.append(dur);
  }
}
function setConsoleHtml(el, text, html) {
  if (!el) return;
  if (el.textContent !== text) el.innerHTML = html;
}

function setArmLog(el, lines) {
  if (!el) return;
  const text = lines.join("\n");
  const stick = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  el.hidden = false;
  setConsoleHtml(el, text, text ? highlightLog(text) : "");
  if (text && stick) el.scrollTop = el.scrollHeight;
}

/** 双臂工作台：每臂一张卡，各自指标和日志；L 的完成不跟 T 对齐。 */
function renderContrast(root, st, metrics) {
  const box = root.querySelector("[data-fit-contrast]");
  if (!box) return;
  const params = st?.job?.params || st?.last?.params || null;
  const logs = splitFitLog(st?.log_tail);
  // arms_metrics 恒为 dict；空 {} 在 JS 里 truthy，不能盖住 metrics.arms
  const am = st?.arms_metrics;
  const amOk = am && typeof am === "object" && Object.keys(am).length > 0;
  const finished = (amOk ? am : null) || metrics?.arms || {};
  let packL = finished?.L || null;
  let packT = finished?.T || (metrics?.arm === "T" ? metrics : null);
  let liveL = null;
  let liveT = null;
  if (st?.fit_running) {
    liveL = liveArmFromLog(logs.L);
    liveT = liveArmFromLog(logs.T);
    packL = liveL.pack;
    packT = liveT.pack;
  }
  const icNum = (pack) => {
    const n = Number(pack?.cs_rank_ic);
    return pack && Number.isFinite(n) ? n : null;
  };
  const icL = icNum(packL);
  const icT = icNum(packT);

  const setMetric = (tag, key, value) => {
    const el = box.querySelector(`[data-arm-metric="${tag}:${key}"]`);
    if (!el) return;
    el.textContent = value;
    el.classList.toggle("is-empty", value === "—");
  };

  const tags = pageFactory === "L" ? ["L"] : ["L", "T"];
  for (const tag of tags) {
    const pack = tag === "L" ? packL : packT;
    const ic = tag === "L" ? icL : icT;
    const other = tag === "L" ? icT : icL;
    const section = box.querySelector(`[data-arm-console="${tag}"]`);
    if (section && tags.length > 1) {
      section.classList.toggle("is-lead", ic != null && (other == null || ic > other));
    }
    const stateEl = box.querySelector(`[data-arm-state="${tag}"]`);
    const state = armStatus(st, tag, pack, tag === "L" ? liveL : liveT);
    if (state.text === "训练中" && !state.dur) state.dur = trainHint(logs[tag]);
    if (
      st?.fit_running &&
      (state.text === "准备中" || state.text === "训练中") &&
      st.job?.started_at
    ) {
      state.clockFrom = st.job.started_at;
      state.clock = elapsedClock(st.job.started_at);
    }
    renderArmState(stateEl, state);
    setMetric(tag, "ic", fmtKpi3(pack?.cs_rank_ic));
    setMetric(tag, "icir", fmtKpi3(pack?.cs_icir));
    setMetric(tag, "hit", fmtPct(pack?.sign_hit));
    setMetric(tag, "topy", fmtKpi3(pack?.cs_top_mean_y));
    const own = logs[tag];
    const show = own.length ? own : (st?.fit_running ? logs.shared : []);
    setArmLog(box.querySelector(`[data-arm-log="${tag}"]`), show);
  }

  const tagEl = root.querySelector("[data-contrast-protocol]");
  if (tagEl) {
    const body =
      pageFactory === "L"
        ? armLProtocolBits(params).join(" · ")
        : params
          ? protocolTagFromParams(params)
          : "";
    tagEl.textContent = body ? `协议 · ${body}` : "";
    tagEl.hidden = !body;
  }
  syncArmClock();
}

/** Arm·L 只吃全量因子，不带序列形状、T 因子宽和 T 采样。 */
function armLProtocolBits(params) {
  const bits = [];
  const pool = params?.watching_limit ?? params?.pool_n;
  const lb = params?.lookback;
  const ho = params?.holdout_trading_days;
  if (pool != null && pool !== "") bits.push(`池${pool}`);
  if (lb != null && lb !== "") bits.push(`窗${lb}`);
  if (ho != null && ho !== "") bits.push(`HO${ho}`);
  bits.push("全量", "L=仅因子");
  return bits;
}

function protocolTagFromParams(params) {
  if (!params || typeof params !== "object") return "";
  const seqLen = Number(params.seq_len) || 20;
  const f = seqFeatDim(params.seq_feat_set);
  const pack = String(params.factor_pack || "");
  const table = pack.startsWith("atc") ? PACK_WIDTH.atc : PACK_WIDTH.aoo;
  const fac = pack.startsWith("atc")
    ? isFullAtc(params.full_atc == null ? true : params.full_atc)
      ? table.full
      : table.core
    : asBool(params.full_aoo, false)
      ? table.full
      : table.core;
  const cap = formatTSamplePct(
    params.t_sample_pct == null ? 10 : params.t_sample_pct
  );
  return `T · L${seqLen}×${f} · ${fac} · ${cap}`;
}

function fitActionLabel(st) {
  if (st?.fit_running) {
    const logs = splitFitLog(st.log_tail);
    if (!logs.L.length && !logs.T.length) return "准备数据";
    return "拟合进行中";
  }
  const job = st?.job;
  if (job?.status === "done" && job?.error) return "部分完成";
  if (job?.status === "done") return "拟合完成";
  if (job?.status === "error") return "拟合失败";
  const action = st?.last?.action;
  if (action === "holdout_result") return "拟合完成";
  if (action === "holdout_failed") return "拟合失败";
  if (action === "holdout_intent" || action === "fit_start") return "已记录拟合意图";
  return "";
}

function renderFitInfo(st) {
  const root = document.querySelector(`[data-fit-info-for="${st.head}"]`);
  if (!root) return;
  const job = st.job || null;
  const last = st.last || null;
  // 仅本次：有 job / 正在跑 / 本轮 holdout_result|holdout_failed
  const sameRun =
    !job?.started_at || !last?.updated_at || String(last.updated_at) >= String(job.started_at);
  const isCurrentResult = !!last && last.action === "holdout_result" && sameRun;
  const isCurrentFail = !!last && last.action === "holdout_failed" && sameRun;
  const show = !!(st.fit_running || job || isCurrentResult || isCurrentFail);
  root.hidden = !show;
  if (!show) return;

  const params =
    job?.params || ((isCurrentResult || isCurrentFail) ? last?.params : null) || null;
  const metrics = currentFitMetrics(st);

  const statusEl = root.querySelector("[data-fit-info-status]");
  const metaEl = root.querySelector("[data-fit-info-meta]");
  const paramsEl = root.querySelector("[data-fit-info-params]");
  const noteEl = root.querySelector("[data-fit-info-note]");

  const label = fitActionLabel(st) || "本次拟合";
  if (statusEl) {
    statusEl.textContent = label;
    statusEl.dataset.state = stateOf(st);
  }
  const metaBits = [];
  if (job?.run_id) metaBits.push(String(job.run_id));
  if (st.fit_running && job?.pid) metaBits.push(`pid ${job.pid}`);
  if (job?.started_at) metaBits.push(`开始 ${formatTime(job.started_at)}`);
  if (
    !st.fit_running &&
    (job?.finished_at || ((isCurrentResult || isCurrentFail) && last?.updated_at))
  ) {
    metaBits.push(`更新 ${formatTime(job?.finished_at || last?.updated_at)}`);
  }
  if (metaEl) renderMetaBits(metaEl, metaBits);

  renderParamChips(paramsEl, params);
  renderContrast(root, st, metrics);
  if (noteEl) {
    const warns = (Array.isArray(job?.param_warnings) ? job.param_warnings : []).filter((w) =>
      String(w || "").trim()
    );
    let note = "";
    let tone = "ok";
    if (job?.status === "error" && job?.error) {
      note = `失败 · ${job.error}`;
      tone = "error";
    } else if (!st.fit_running && job?.error) {
      note = `未完成 · ${job.error}`;
      tone = "warn";
    } else if (warns.length) {
      note = `提示 · ${warns.slice(0, 4).join("；")}`;
      tone = "warn";
    } else if (st.fit_running) {
      const prep = splitFitLog(st?.log_tail);
      const lastLine = prep.shared.length ? String(prep.shared[prep.shared.length - 1]).trim() : "";
      if (!prep.L.length && !prep.T.length) {
        note = lastLine ? `准备数据 · ${lastLine}` : `准备数据 · pid=${job?.pid || "—"}`;
        tone = "ok";
      }
    }
    noteEl.hidden = !note;
    noteEl.textContent = note;
    noteEl.dataset.tone = tone;
  }
}

function updateActionStates(head) {
  const panel = panelOf(head);
  if (!panel) return;
  const st = latestHeads.get(head);
  const fitRunning = !!st?.fit_running;
  const busy = pendingHeads.has(head) || fitRunning;
  panel.setAttribute("aria-busy", String(busy));
  $all("[data-act]", panel).forEach((btn) => {
    const act = btn.dataset.act;
    // 勿因全局 refreshing 禁用拟合：否则点刷新瞬间无法操作，且易误判「点了没反应」
    let disabled = busy;
    if (act === "fit") {
      disabled = pendingHeads.has(head) || fitRunning;
      btn.textContent = fitRunning ? "运行中…" : "拟合";
    }
    btn.disabled = disabled;
  });
}

function fitPollDelayMs() {
  // 后台标签页降频，前台保持 4s；省资源且避免隐藏页堆积请求
  try {
    if (typeof document !== "undefined" && document.hidden) return 15000;
  } catch (_) {
    /* ignore */
  }
  return 4000;
}

function scheduleFitPoll(heads) {
  const anyRunning = (heads || []).some((h) => h?.fit_running);
  if (fitPollTimer) {
    clearTimeout(fitPollTimer);
    fitPollTimer = null;
  }
  if (!anyRunning) return;
  fitPollTimer = setTimeout(() => {
    fitPollTimer = null;
    // 后台轮询安静刷新，避免顶栏「刷新/刷新中」来回闪
    void refreshStatus({ quiet: true });
  }, fitPollDelayMs());
}

function applyView({ scroll = false } = {}) {
  HEADS.forEach((head) => {
    const section = sectionOf(head);
    const selected = head === activeHead;
    if (section) section.hidden = viewMode === "single" && !selected;
    if (selected) {
      const panel = panelOf(head);
      if (panel) panel.open = true;
    }
    const card = document.querySelector(`[data-head-select="${head}"]`);
    if (card) {
      const focused = viewMode === "single" && selected;
      card.classList.toggle("is-active", focused);
      card.setAttribute("aria-pressed", String(focused));
      card.title = focused ? "再点一次展开全部四头" : "聚焦此头";
    }
  });

  if (viewMode === "all") {
    HEADS.forEach((head) => {
      const panel = panelOf(head);
      if (panel) panel.open = true;
    });
  }

  if (scroll) {
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
    sectionOf(activeHead)?.scrollIntoView({ block: "start", behavior });
  }
}

function selectHead(head, { scroll = true } = {}) {
  if (!HEADS.includes(head)) return;
  // 单头下再点当前卡 → 回到四头全开
  if (viewMode === "single" && head === activeHead) {
    viewMode = "all";
    applyView({ scroll: false });
    return;
  }
  activeHead = head;
  viewMode = "single";
  applyView({ scroll });
}

function overviewCounts(heads) {
  const nReady = heads.filter((h) => headFitOutcome(h) === "gated").length;
  const nFitted = heads.filter((h) =>
    ["gated", "fitted"].includes(headFitOutcome(h))
  ).length;
  return { nReady, nFitted };
}

function renderHeadSummary(st) {
  const head = st.head;
  const state = stateOf(st);
  const chip = document.querySelector(`[data-head-summary-status="${head}"]`);
  if (chip) {
    chip.dataset.state = state;
    chip.textContent = stateLabel(state);
  }
  const gate = document.querySelector(`[data-head-summary-gate="${head}"]`);
  if (gate) {
    if (pageFactory === "L") {
      const ic = Number(currentFitMetrics(st)?.cs_rank_ic);
      gate.textContent = Number.isFinite(ic) ? `Rank IC ${ic.toFixed(3)}` : "绝对指标";
    } else {
      const g =
        st?.last?.hard_gate_vs_arm_L && typeof st.last.hard_gate_vs_arm_L === "object"
          ? st.last.hard_gate_vs_arm_L.T
          : null;
      const pass = g?.pass ?? currentFitMetrics(st)?.gate_pass;
      gate.textContent =
        pass === true
          ? "Gate · pass"
          : pass === false
            ? "Gate · fail"
            : `Gate · ${gateDisplay(st)}`;
    }
  }
}

function renderResult(st) {
  const panel = panelOf(st.head);
  if (!panel) return;

  renderFitInfo(st);

  const resultEl = panel.querySelector(`[data-result-for="${st.head}"]`);
  if (resultEl) {
    const blob = pretty(
      scrubArmLLoss(
        {
          job: st.job || null,
          last: st.last || null,
          draft: st.draft || null,
          research: st.research || null,
          metrics: currentFitMetrics(st),
          fit_running: !!st.fit_running,
          scheme: st.scheme || null,
          live_allowed: false,
        },
        st.head
      )
    );
    setConsoleHtml(resultEl, blob, highlightJson(blob));
  }

  const cli = panel.querySelector(`[data-cli-for="${st.head}"]`);
  if (cli) {
    const cmd = st.job?.cli || st.last?.cli || st.scheme?.script;
    cli.hidden = !cmd;
    const text = cmd ? `CLI · ${cmd}` : "";
    setConsoleHtml(cli, text, text ? highlightCli(text) : "");
  }
}

function renderHead(st) {
  const head = st.head;
  latestHeads.set(head, st);
  const scheme = st.scheme || {};
  const outcome = headFitOutcome(st);
  const outcomeLabel =
    {
      running: "拟合中",
      gated: pageFactory === "L" ? "有指标" : "硬门过闸",
      fitted: "已拟合",
      failed: "拟合失败",
      none: "未拟合",
    }[outcome] || "未拟合";
  const msgBits = [
    st.fit_running ? "拟合中" : null,
    !st.fit_running && st.job?.status === "error" ? "拟合失败" : null,
    !st.fit_running && outcome !== "none" && outcome !== "running"
      ? outcomeLabel
      : null,
    pageFactory === "L" ? null : scheme.loss || null,
  ].filter(Boolean);
  setChip(document.querySelector(`[data-status-for="${head}"]`), stateOf(st), msgBits.join(" · ") || "—");

  renderHeadSummary(st);
  renderResult(st);
  // 表单回填：用户改过则不覆盖。
  // 未在跑：优先工作配置（拟合意图隐式同步的 draft），切勿让旧 job 盖回。
  // 拟合中：用本次 job/last，与信息窗一致。
  if (!dirtyParams.has(head)) {
    const running = !!st.fit_running || st.job?.status === "running";
    const lastParams =
      st.last?.params &&
      ["holdout_intent", "holdout_result", "holdout_failed"].includes(st.last.action)
        ? st.last.params
        : null;
    const formParams = running
      ? st.job?.params || lastParams || st.draft?.params || null
      : st.draft?.params || lastParams || st.job?.params || null;
    if (formParams) applyParams(head, formParams);
  }
  updateActionStates(head);
}

function setRefreshing(value) {
  refreshing = value;
  const page = $(".yhat-tf-page");
  if (page) page.setAttribute("aria-busy", String(value));
  const refresh = $("#yhat-tf-refresh");
  if (refresh) {
    refresh.disabled = value;
    refresh.classList.toggle("is-busy", value);
  }
  const label = $("[data-refresh-label]");
  if (label) label.textContent = value ? "刷新中…" : "刷新";
  HEADS.forEach(updateActionStates);
}

function refreshStatus({ quiet = false } = {}) {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    const meta = $("#yhat-tf-meta");
    if (!quiet) setRefreshing(true);
    if (!lastGoodData && meta) meta.textContent = "正在读取模型状态…";
    try {
      const { ok, data, error } = await apiFetch(`${apiBase()}/status`);
      if (!ok) throw new Error(error || "status failed");
      lastGoodData = data;
      const heads = Array.isArray(data.heads) ? data.heads : [];
      heads.forEach(renderHead);
      const { nReady, nFitted } = overviewCounts(heads);
      const nRun = heads.filter((h) => h.fit_running).length;
      if (meta) {
        const schemeName =
          data.scheme?.name ||
          (pageFactory === "L" ? "模型中心 · Arm·L" : "模型中心 · Arm·T");
        const next = `${schemeName} · 有指标 ${nReady}/4 · 已拟合 ${nFitted}/4${
          nRun ? ` · 拟合中 ${nRun}` : ""
        } · 不写 live`;
        if (meta.textContent !== next) meta.textContent = next;
      }
      scheduleFitPoll(heads);
      setLoadError("");
      return data;
    } catch (err) {
      const message = err?.message || String(err);
      if (meta) meta.textContent = lastGoodData ? "状态刷新失败 · 展示上次成功数据" : "状态加载失败 · 请重试";
      setLoadError(
        lastGoodData
          ? `状态刷新失败：${message}。当前保留上次成功数据。`
          : `状态加载失败：${message}。请点击重试。`
      );
      return null;
    } finally {
      if (!quiet) setRefreshing(false);
    }
  })();
  return refreshPromise.finally(() => {
    refreshPromise = null;
  });
}

async function onAct(head, act) {
  if (pendingHeads.has(head)) return;
  if (act !== "fit") return;

  const statusEl = document.querySelector(`[data-status-for="${head}"]`);
  pendingHeads.add(head);
  updateActionStates(head);
  showPanelError(head, "");
  setChip(statusEl, "busy", "处理中…");
  try {
    const res = await jsonPost(`${apiBase()}/fit`, {
      head,
      params: readParams(head),
    });
    if (!res?.ok) throw new Error(res?.error || "请求失败");
    // 先用本次 job/intent 参数回填，再清 dirty，避免刷新瞬间闪回旧 draft
    const fitParams =
      res?.data?.job?.params ||
      res?.data?.last?.params ||
      res?.data?.draft_params ||
      null;
    if (fitParams) applyParams(head, fitParams);
    dirtyParams.delete(head);
    const refreshed = await refreshStatus();
    if (!refreshed) setChip(statusEl, "warn", "操作已完成 · 状态刷新失败");
    let toastMsg =
      act === "fit" && res?.data?.message
        ? res.data.message
        : ACTION_SUCCESS[act] || "操作完成";
    const warns = act === "fit" ? res?.data?.param_warnings : null;
    let toastTone = "ok";
    // message 里已含「提示：…」时不再拼一遍，避免右下角重复
    if (
      Array.isArray(warns) &&
      warns.length &&
      !String(toastMsg).includes(" · 提示：")
    ) {
      toastMsg = `${toastMsg} · 提示：${warns.slice(0, 3).join("；")}`;
      toastTone = "warn";
    } else if (Array.isArray(warns) && warns.length) {
      toastTone = "warn";
    }
    showToast(toastMsg, toastTone);
  } catch (err) {
    const message = err?.message || String(err);
    setChip(statusEl, "error", message);
    showPanelError(head, message);
    setFitInfoNote(head, message, "error");
    showToast(message, "error");
    // 拟合失败（含启动即崩）后拉一次状态，避免按钮/信息窗停在半截
    if (act === "fit") {
      try {
        await refreshStatus({ quiet: true });
      } catch (_) {
        /* ignore */
      }
    }
  } finally {
    pendingHeads.delete(head);
    updateActionStates(head);
  }
}

async function copyCli(head) {
  const cli = document.querySelector(`[data-cli-for="${head}"]`);
  const command = cli?.textContent?.replace(/^CLI · /, "").trim();
  if (!command) {
    showToast("暂无可复制的 CLI", "warn");
    return;
  }
  try {
    await navigator.clipboard.writeText(command);
    showToast("CLI 已复制");
  } catch (_) {
    showToast("浏览器未授权剪贴板，请展开技术详情手动复制", "warn");
  }
}

function bindCompactHead() {
  const scroller = $(".page-main");
  const head = $(".yhat-tf-head");
  if (!scroller || !head) return;
  // 滞后：收起/展开阈值不同，避免临界点高度变化导致顶栏来回闪
  const ENTER = 96;
  const EXIT = 36;
  let compact = head.classList.contains("is-compact");
  const update = () => {
    const y = scroller.scrollTop;
    const next = compact ? y > EXIT : y > ENTER;
    if (next === compact) return;
    compact = next;
    head.classList.toggle("is-compact", compact);
  };
  scroller.addEventListener("scroll", update, { passive: true });
  update();
}

function bindToastDismiss() {
  const toast = $("#yhat-tf-toast");
  if (!toast || toast.dataset.boundDismiss) return;
  toast.dataset.boundDismiss = "1";
  toast.addEventListener("click", () => {
    window.clearTimeout(toastTimer);
    toast.hidden = true;
  });
}

export function initYhatTf(_ctx, factory = "T") {
  pageFactory = factory === "L" ? "L" : "T";
  const pageRoot = $(".yhat-tf-page");
  if (pageRoot) pageRoot.dataset.factory = pageFactory;
  const hashHead = HEADS.find((head) => location.hash === `#${sectionOf(head)?.id}`);
  if (hashHead) activeHead = hashHead;
  applyView();
  bindCompactHead();
  bindToastDismiss();

  $all("[data-head-select]").forEach((btn) => {
    btn.addEventListener("click", () => selectHead(btn.dataset.headSelect));
  });
  HEADS.forEach((head) => {
    const panel = panelOf(head);
    if (!panel) return;
    $all("[data-act]", panel).forEach((btn) => {
      btn.addEventListener("click", () => onAct(head, btn.dataset.act));
    });
    const onProtocolChange = (ev) => {
      const t = ev.target;
      if (
        t?.matches?.(
          '[data-param-radio="seq_feat_set"], [data-param-radio="full_aoo"], [data-param-radio="full_atc"], [data-param="seq_len"], [data-param="t_sample_pct"], [data-param="pool_n"], [data-param="lookback"], [data-param="holdout_trading_days"]'
        )
      ) {
        updateProtocolPreviews(panel);
      }
    };
    const markDirty = () => dirtyParams.add(head);
    panel.addEventListener("input", markDirty);
    panel.addEventListener("change", (ev) => {
      markDirty();
      onProtocolChange(ev);
    });
    updateProtocolPreviews(panel);
  });
  $all("[data-copy-cli]").forEach((btn) => {
    btn.addEventListener("click", () => copyCli(btn.dataset.copyCli));
  });
  $("#yhat-tf-refresh")?.addEventListener("click", refreshStatus);
  $("#yhat-tf-retry")?.addEventListener("click", refreshStatus);
  window.addEventListener("hashchange", () => {
    const hashHead = HEADS.find((head) => location.hash === `#${sectionOf(head)?.id}`);
    if (hashHead) selectHead(hashHead);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    const heads = Array.isArray(lastGoodData?.heads) ? lastGoodData.heads : [];
    if (heads.some((h) => h?.fit_running)) {
      void refreshStatus({ quiet: true });
    }
  });
  refreshStatus();
}
