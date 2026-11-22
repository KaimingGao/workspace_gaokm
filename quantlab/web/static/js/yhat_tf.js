/** 模型工厂 · /yhat-l | /yhat-t | /yhat-r · 四头 Holdout；/yhat-bt 回测中心（⌘⇧K；不写 live） */

import { apiFetch } from "./api_client.js";
import { paintFitCurves } from "./fit_curves.js";

const HEADS = ["oo", "tc", "oo_rank", "tc_rank"];
/** @type {"L"|"T"|"R"} */
let pageFactory = "T";
const mixCatalog = new Map();
const BT_API = "/api/quant/yhat-bt";
function isFactorFactory() {
  return pageFactory === "L" || pageFactory === "R";
}
function factoryTag() {
  return isFactorFactory() ? pageFactory : "T";
}
function apiBase() {
  if (pageFactory === "L") return "/api/quant/yhat-l";
  if (pageFactory === "R") return "/api/quant/yhat-r";
  return "/api/quant/yhat-t";
}
function schemeTitle(fallback) {
  if (fallback) return fallback;
  if (pageFactory === "L") return "模型中心 · Arm·L";
  if (pageFactory === "R") return "模型中心 · Arm·R";
  return "模型中心 · Arm·T";
}
const GATE_LABEL = {
  oo: "Arm·L",
  tc: "Arm·L",
  oo_rank: "Gate lift",
  tc_rank: "Gate lift",
};
/** 数字跟面板列宽走，改列宽时两处一起改。 */
const PACK_WIDTH = {
  aoo: { core: "core · 65", full: "full · 222" },
  atc: { core: "core · 61", full: "full · 218" },
};

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
/** 选中的历史跑次预览。拟合中不拿它画运行台。 */
const viewing = new Map();
const viewSeq = new Map();
const versionLists = new Map();

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
  if (text && msg != null) {
    const body = String(msg).trim();
    text.textContent = body;
    text.hidden = !body;
  }
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
  if (key === "lambdarank") return "lambdarank";
  return "mse";
}

/** Arm·L 回归头固定 l2，排序头固定 LambdaRank。 */
function shownLoss(head) {
  if (head === "oo_rank" || head === "tc_rank") return "lambdarank";
  return "l2";
}

function scrubArmLLoss(value, head) {
  if (pageFactory !== "L" || value == null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((item) => scrubArmLLoss(item, head));
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (key === "loss") out[key] = shownLoss(head);
    else if (item && typeof item === "object") out[key] = scrubArmLLoss(item, head);
    else out[key] = item;
  }
  return out;
}

/** 因子厂参数里残留的 Transformer 字段不进技术详情。 */
const FACTOR_PARAM_NOISE = new Set([
  "d_model",
  "nlayers",
  "nhead",
  "mlp_hidden",
  "dropout",
  "patience",
  "batch_size",
  "epochs",
  "lr",
  "t_sample_pct",
  "seq_feat_set",
  "seq_len",
  "device",
]);

function scrubFactorParams(value) {
  if (!isFactorFactory() || value == null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((item) => scrubFactorParams(item));
  const isParam =
    "lookback" in value || "pool_n" in value || "ridge_alpha" in value || "d_model" in value;
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (isParam && FACTOR_PARAM_NOISE.has(key)) continue;
    out[key] = item && typeof item === "object" ? scrubFactorParams(item) : item;
  }
  return out;
}

function normalizeSeqFeatSet(raw) {
  const s = String(raw || "seq_core").trim().toLowerCase();
  if (s === "8" || s === "seq8" || s === "base") return "seq8";
  if (s === "16" || s === "seq16") return "seq16";
  if (s === "32" || s === "seq32") return "seq32";
  if (s === "64" || s === "seq64") return "seq64";
  if (s === "full" || s === "seq_full") return "seq_full";
  if (s === "core" || s === "seq_core") return "seq_core";
  return "seq_core";
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
  return normalizeSeqFeatSet(readRadioParam(panel, "seq_feat_set", "seq_core"));
}

function applySeqFeatSet(panel, raw) {
  applyRadioParam(panel, "seq_feat_set", normalizeSeqFeatSet(raw));
}

function seqWidthTable(pack) {
  const name = String(pack || "");
  if (name === "atc" || name.startsWith("atc")) return PACK_WIDTH.atc;
  return PACK_WIDTH.aoo;
}

function seqFeatChip(featSet, pack) {
  const key = normalizeSeqFeatSet(featSet);
  const table = seqWidthTable(pack);
  if (key === "seq_full") return table.full;
  if (key === "seq_core") return table.core;
  if (key === "seq8") return "8维";
  if (key === "seq16") return "16维";
  if (key === "seq32") return "32维";
  if (key === "seq64") return "64维";
  return table.core;
}

function formatTSamplePct(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return "T采样100%";
  if (n >= 100) return "T采样100%";
  const s = Number.isInteger(n) ? String(n) : String(n).replace(/\.?0+$/, "");
  return `T采样${s}%`;
}

function updateProtocolPreviews(panel) {
  if (!panel) return;
  const pack = panel.querySelector("[data-protocol-card]")?.getAttribute("data-head-pack");
  const f = seqFeatChip(readSeqFeatSet(panel), pack);
  const card = panel.querySelector("[data-protocol-card]");
  if (card) card.dataset.seqDim = String(f);
}

function poolLabel(params) {
  if (!params || typeof params !== "object") return "";
  const stage = String(params.train_stage || "");
  const fs = String(params.feature_set || "");
  if (stage === "chain") return "大池→沪深300";
  if (stage === "finetune" || fs === "hs300") return "沪深300";
  if (stage === "pretrain" || fs === "pool") return "大池";
  const n = params.watching_limit ?? params.pool_n;
  return n == null || n === "" ? "" : String(n);
}

function readPool(panel) {
  const boxes = $all("[data-pool]", panel);
  if (!boxes.length) return null;
  const radio = boxes.some((el) => el.type === "radio");
  if (radio) {
    const on = boxes.find((el) => el.checked);
    return { feature_set: on ? on.value : "pool" };
  }
  const picked = boxes.filter((el) => el.checked).map((el) => el.value);
  if (picked.includes("pool") && picked.includes("hs300")) {
    return { feature_set: "pool", train_stage: "chain" };
  }
  if (picked.includes("hs300")) {
    return { feature_set: "hs300", train_stage: "" };
  }
  if (picked.includes("pool")) {
    return { feature_set: "pool", train_stage: "pretrain" };
  }
  return null;
}

function applyPool(panel, params) {
  const boxes = $all("[data-pool]", panel);
  if (!boxes.length || !params) return;
  const stage = String(params.train_stage || "");
  const fs = String(params.feature_set || "");
  if (!stage && !fs) return;
  const want = new Set();
  if (stage === "chain") {
    want.add("pool");
    want.add("hs300");
  } else if (stage === "finetune" || fs === "hs300") {
    want.add("hs300");
  } else {
    want.add("pool");
  }
  boxes.forEach((el) => {
    el.checked = want.has(el.value);
  });
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
  out.arms = factoryTag();
  if (
    panel.querySelector('[data-param-radio="seq_feat_set"]') ||
    panel.querySelector('[data-param="seq_feat_set"]')
  ) {
    out.seq_feat_set = readSeqFeatSet(panel);
  }
  const pool = readPool(panel);
  if (pool?.feature_set) out.feature_set = pool.feature_set;
  // 空字符串也要提交。漏掉时草稿里的 chain 会留下来，回写又把大池勾上。
  if (pool && Object.prototype.hasOwnProperty.call(pool, "train_stage")) {
    out.train_stage = pool.train_stage;
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
  applyPool(panel, params);
  updateProtocolPreviews(panel);
}

function pretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch (_) {
    return String(obj);
  }
}

/** 信息窗只用「本次」拟合：进行中不展示旧 OOS；完成态认本 run 的 last.metrics。勿回退 research/draft。 */
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

function paramChipPairs(params) {
  if (!params || typeof params !== "object") return [];
  const packName = String(params.factor_pack || "");
  const dim = seqFeatChip(params.seq_feat_set, packName);
  const pairs = [
    ["池子", poolLabel(params)],
    ["训练窗", params.lookback],
    ["Holdout", params.holdout_trading_days],
  ];
  if (isFactorFactory()) {
    if (pageFactory === "R" && params.ridge_alpha != null && params.ridge_alpha !== "") {
      pairs.push(["λ", params.ridge_alpha]);
    }
  } else {
    pairs.push(
      ["序列", dim || params.seq_feat_set],
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

/** 卡片数字统一三位。 */
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
function backtestSpent(startedAt, finishedAt) {
  const parse = (raw) => {
    const text = String(raw || "").trim();
    if (!text) return NaN;
    return new Date(text.includes("T") ? text : text.replace(" ", "T")).getTime();
  };
  const t0 = parse(startedAt);
  if (Number.isNaN(t0)) return "";
  const t1 = parse(finishedAt);
  const end = Number.isNaN(t1) ? NaN : t1;
  if (Number.isNaN(end)) return "";
  return fmtDur(Math.max(0, Math.floor((end - t0) / 1000)));
}

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
  const R = [];
  const shared = [];
  for (const line of lines) {
    const s = line.trim();
    if (!s) continue;
    if (/^ArmR\b/.test(s) || /^R_ridge\b/.test(s)) R.push(line);
    else if (
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
  return { L, T, R, shared };
}

function numOrNull(raw) {
  if (raw == null || raw === "None" || raw === "null" || raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** 每个 epoch 的 Holdout 行，不是该臂结束。结束行是 ArmL / ArmT seed=。 */
function isEpochHoldoutLine(line) {
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
    // 每轮 Holdout 带 rankIC= / 「失败」，不能当成该臂结束；后面的 step= 仍是训练中。
    // 指标先记下来，卡片在训练中就能看到这一轮。
    if (isEpochHoldoutLine(s)) {
      const preview = readArmMetricPack(s);
      if (preview) pack = preview;
      if (phase === "idle") phase = "busy";
      continue;
    }
    if (/失败/.test(s)) {
      phase = "fail";
      pack = { success: false };
      continue;
    }
    // 对比表：Rank IC、RankICIR、Pearson ICIR、命中、NDCG@30、Top10% y。
    const row = s.match(
      /^(?:L_lightgbm|L_lambdarank|T_transformer|R_ridge)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$/
    );
    if (row) {
      const nums = [row[1], row[2], row[3], row[4], row[5], row[6]].map(numOrNull);
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
        cs_rank_icir: nums[1],
        cs_icir: nums[2],
        sign_hit: nums[3],
        cs_ndcg_30: nums[4],
        cs_top_mean_y: nums[5],
        mse: pack?.mse ?? null,
        elapsed_sec: pack?.elapsed_sec ?? null,
      };
      continue;
    }
    const packed = readArmMetricPack(s);
    if (!packed) continue;
    phase = "ok";
    pack = packed;
  }
  if (!started) return { phase: "idle", pack: null };
  if (phase === "idle") return { phase: "busy", pack: null };
  return { phase, pack };

  function readArmMetricPack(s) {
    const ic = s.match(/rankIC=(\S+)/);
    if (!ic) return null;
    const icir = s.match(/(?:^|\s)ICIR=(\S+)/);
    const rankIcir = s.match(/(?:^|\s)rankICIR=(\S+)/);
    const hit = s.match(/sign_hit=(\S+)/);
    const ndcg = s.match(/ndcg30=(\S+)/);
    const top = s.match(/(?:topY|cs_top_mean_y)=(\S+)/);
    const mse = s.match(/(?:^|\s)mse=(\S+)/);
    const vals = [
      numOrNull(ic[1]),
      icir ? numOrNull(icir[1]) : null,
      hit ? numOrNull(hit[1]) : null,
      top ? numOrNull(top[1]) : null,
    ];
    if (vals.every((n) => n == null)) return null;
    const sec = s.match(/([0-9]+(?:\.[0-9]+)?)s\b/);
    return {
      success: true,
      cs_rank_ic: vals[0],
      cs_icir: vals[1],
      cs_rank_icir: rankIcir ? numOrNull(rankIcir[1]) : null,
      sign_hit: vals[2],
      cs_ndcg_30: ndcg ? numOrNull(ndcg[1]) : null,
      cs_top_mean_y: vals[3],
      mse: mse ? numOrNull(mse[1]) : null,
      elapsed_sec: sec ? numOrNull(sec[1]) : null,
    };
  }
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

const METRIC_NAME = "rankIC|ICIR|sign_hit|ndcg30|mse|cs_rank_ic|cs_rank_icir|cs_ndcg_30|cs_top_mean_y|topY";

function paintPct(escaped) {
  return escaped.replace(/([+-]?\d+(?:\.\d+)?)%/g, (full, num) => {
    const v = Number(num);
    if (!Number.isFinite(v) || v === 0) return full;
    return `<span class="${v > 0 ? "ytf-hit" : "ytf-miss"}">${full}</span>`;
  });
}

function highlightFillLine(line) {
  let html = escConsole(line);
  html = html.replace(
    /(fuse|y_oo|y_τc|oo_rank|τc_rank|开|收|成)(?= )/g,
    '<span class="ytf-k">$1</span>'
  );
  return paintPct(html);
}

function fillActionClass(raw) {
  if (raw === "开" || raw === "持") return "ytf-hit";
  if (raw === "清") return "ytf-miss";
  return "";
}

function renderFillGrid(line, head) {
  const body = line.split(" · ").map((raw, i) => {
    const text = escConsole(raw);
    const shown = head ? text : paintPct(text);
    const classes = [];
    if (i === 3) classes.push("ytf-name");
    if (!head && i === 1) {
      const tone = fillActionClass(raw.trim());
      if (tone) classes.push(tone);
    }
    const cls = classes.length ? ` class="${classes.join(" ")}"` : "";
    const title = i === 3 ? ` title="${text.replace(/"/g, "&quot;")}"` : "";
    return `<span${cls}${title}>${shown}</span>`;
  }).join("");
  return `<span class="ytf-fill${head ? " ytf-fill-head" : ""}">${body}</span>`;
}

/** 日总结里的持仓用红。开 / 清只在成交行的动作列上色。 */
function paintDayBookLine(line) {
  if (!line.startsWith("现金 ")) return "";
  return highlightPlain(line, /持仓 \d+/g, (m) => `<span class="ytf-hit">${escConsole(m[0])}</span>`);
}

function highlightLogLine(line) {
  const book = paintDayBookLine(line);
  if (book) return book;
  if (line.startsWith("日期 · 动作 · 代码 · ")) return renderFillGrid(line, true);
  if (/^\d{4}-\d{2}-\d{2} · (?:开|加|减|清|买|卖) · /.test(line)) return renderFillGrid(line, false);
  if (/^\d{4}-\d{2}-\d{2} (?:开|加|减|清|买|卖) /.test(line)) {
    return highlightFillLine(line);
  }
  if (/^(?:L_lightgbm|L_lambdarank|T_transformer|R_ridge)\b/.test(line)) {
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

/** 运行台原文。只去掉首尾空白，不按臂、不按关键词删行。 */
function rawLogLines(raw) {
  const text = String(raw || "").trim();
  return text ? text.split("\n") : [];
}

/** 本厂一张运行卡：指标和日志只跟当前臂。 */
function renderContrast(root, st, metrics) {
  const box = root.querySelector("[data-fit-contrast]");
  if (!box) return;
  const head = root.getAttribute("data-fit-info-for") || st?.head || "";
  const params = st?.job?.params || st?.last?.params || null;
  const logs = splitFitLog(st?.log_tail);
  // arms_metrics 恒为 dict；空 {} 在 JS 里 truthy，不能盖住 metrics.arms
  const am = st?.arms_metrics;
  const amOk = am && typeof am === "object" && Object.keys(am).length > 0;
  const finished = (amOk ? am : null) || metrics?.arms || {};
  let packL = finished?.L || null;
  let packT = finished?.T || (metrics?.arm === "T" ? metrics : null);
  let packR = finished?.R || (metrics?.arm === "R" ? metrics : null);
  let liveL = null;
  let liveT = null;
  let liveR = null;
  if (st?.fit_running) {
    liveL = liveArmFromLog(logs.L);
    liveT = liveArmFromLog(logs.T);
    liveR = liveArmFromLog(logs.R);
    packL = liveL.pack;
    packT = liveT.pack;
    packR = liveR.pack;
  }
  const icNum = (pack) => {
    const n = Number(pack?.cs_rank_ic);
    return pack && Number.isFinite(n) ? n : null;
  };
  const icL = icNum(packL);
  const icT = icNum(packT);
  const icR = icNum(packR);
  const packs = { L: packL, T: packT, R: packR };
  const ics = { L: icL, T: icT, R: icR };
  const lives = { L: liveL, T: liveT, R: liveR };

  const setMetric = (tag, key, value) => {
    const el = box.querySelector(`[data-arm-metric="${tag}:${key}"]`);
    if (!el) return;
    el.textContent = value;
    el.classList.toggle("is-empty", value === "—");
  };

  const tags = [factoryTag()];
  for (const tag of tags) {
    const pack = packs[tag];
    const ic = ics[tag];
    const other = tag === "L" ? icT : tag === "R" ? icL : icL;
    const section = box.querySelector(`[data-arm-console="${tag}"]`);
    if (section && tags.length > 1) {
      section.classList.toggle("is-lead", ic != null && (other == null || ic > other));
    }
    const stateEl = box.querySelector(`[data-arm-state="${tag}"]`);
    const state = armStatus(st, tag, pack, lives[tag]);
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
    const rankHead = head === "oo_rank" || head === "tc_rank";
    const labs = section?.querySelectorAll(".yhat-tf-arm-metrics > div > span") || [];
    if (rankHead) {
      if (labs[0]) {
        labs[0].textContent = "Rank IC";
        labs[0].title = "每日截面 Spearman 再对日平均";
      }
      if (labs[1]) {
        labs[1].textContent = "ICIR";
        labs[1].title = "日频 IC 均值/标准差";
      }
      if (labs[2]) {
        labs[2].textContent = "NDCG@30";
        labs[2].title = "Holdout 日均 NDCG@30";
      }
      if (labs[3]) {
        labs[3].textContent = "Top10% y";
        labs[3].title = "Holdout 日截面 ŷ 头部 10% 实现收益均值";
      }
      setMetric(tag, "ic", fmtKpi3(pack?.cs_rank_ic));
      setMetric(tag, "icir", fmtKpi3(pack?.cs_icir));
      setMetric(tag, "hit", fmtKpi3(pack?.cs_ndcg_30));
      setMetric(tag, "topy", fmtKpi3(pack?.cs_top_mean_y));
    } else {
      if (labs[0]) {
        labs[0].textContent = "MSE";
        labs[0].title = "Holdout 逐点均方误差。预测和实现都是百分点，不截断";
      }
      if (labs[1]) {
        labs[1].textContent = "Rank IC";
        labs[1].title = "每日截面 Spearman 再对日平均";
      }
      if (labs[2]) {
        labs[2].textContent = "ICIR";
        labs[2].title = "日频 Pearson IC 均值/标准差";
      }
      if (labs[3]) {
        labs[3].textContent = "命中";
        labs[3].title = "样本外方向命中率，|ŷ|<0.05 个百分点不计";
      }
      if (labs[4]) {
        labs[4].textContent = "Top10% y";
        labs[4].title = "Holdout 日截面 ŷ 头部 10% 实现收益均值";
      }
      setMetric(tag, "mse", fmtKpi3(pack?.mse));
      setMetric(tag, "rankic", fmtKpi3(pack?.cs_rank_ic));
      setMetric(tag, "csicir", fmtKpi3(pack?.cs_icir));
      setMetric(tag, "hit", fmtPct(pack?.sign_hit));
      setMetric(tag, "topy", fmtKpi3(pack?.cs_top_mean_y));
    }
    const own = logs[tag];
    // Arm·T 运行台显示这次日志全文。按臂拆行只用来判断训练状态。
    const show =
      tag === "T"
        ? rawLogLines(st?.log_tail)
        : own.length
          ? own
          : st?.fit_running
            ? logs.shared
            : [];
    setArmLog(box.querySelector(`[data-arm-log="${tag}"]`), show);
    if (tag === "T") paintFitCurves(root, st?.fit_curve);
  }

  const tagEl = root.querySelector("[data-contrast-protocol]");
  if (tagEl) {
    const body =
      isFactorFactory()
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
  const pool = poolLabel(params);
  const lb = params?.lookback;
  const ho = params?.holdout_trading_days;
  if (pool) bits.push(`池${pool}`);
  if (lb != null && lb !== "") bits.push(`窗${lb}`);
  if (ho != null && ho !== "") bits.push(`HO${ho}`);
  bits.push("全量", pageFactory === "R" ? "Ridge" : "L=仅因子");
  if (pageFactory === "R" && params?.ridge_alpha != null && params.ridge_alpha !== "") {
    bits.push(`λ${params.ridge_alpha}`);
  }
  return bits;
}

function protocolTagFromParams(params) {
  if (!params || typeof params !== "object") return "";
  const seqLen = Number(params.seq_len) || 60;
  const pack = String(params.factor_pack || "");
  const f = seqFeatChip(params.seq_feat_set, pack);
  const cap = formatTSamplePct(params.t_sample_pct);
  return `T · L${seqLen}×${f} · ${cap}`;
}

function fitActionLabel(st) {
  if (st?.fit_running) {
    const logs = splitFitLog(st.log_tail);
    if (!logs.L.length && !logs.T.length && !(logs.R || []).length) return "准备数据";
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

function shownStatus(st) {
  if (!st) return st;
  const running =
    !!st.fit_running || st.job?.status === "running" || st.job?.status === "starting";
  const view = viewing.get(st.head);
  if (running || !view) return st;
  const metrics = view.metrics && typeof view.metrics === "object" ? view.metrics : null;
  const failed = view.status === "error" || view.action === "holdout_failed";
  const partial = view.status === "partial";
  return {
    ...st,
    draft: null,
    research: null,
    fit_running: false,
    log_tail: view.log_tail || "",
    fit_curve: view.fit_curve || null,
    arms_metrics: metrics?.arms || null,
    job: {
      run_id: view.run_id,
      status: failed ? "error" : "done",
      error: failed || partial ? view.message || "" : "",
      params: view.fit_params || null,
      param_warnings: Array.isArray(view.param_warnings) ? view.param_warnings : [],
      cli: view.cli || "",
      started_at: view.started_at,
      finished_at: view.finished_at,
      exit_hint: view.exit_hint,
    },
    last: {
      action: view.action || (failed ? "holdout_failed" : "holdout_result"),
      params: view.fit_params || null,
      fit_params: view.fit_params || null,
      metrics,
      hard_gate_vs_arm_L: view.hard_gate_vs_arm_L || null,
      parity: view.parity || null,
      eval_universe: view.eval_universe || null,
      message: view.message || "",
      updated_at: view.finished_at || view.started_at,
      cli: view.cli || "",
    },
  };
}

function versionParts(label) {
  const bits = String(label || "")
    .split(" · ")
    .map((bit) => bit.trim())
    .filter(Boolean);
  return { stamp: bits[0] || "—", meta: bits.slice(1) };
}

function versionChipTone(text, status) {
  if (text === "进行中") return "warn";
  if (text === "失败" || status === "error") return "bad";
  if (text === "未过闸" || text === "部分完成") return "warn";
  if (text === "正式" || text === "过闸") return "ok";
  return "";
}

/** 下拉跟按钮同宽。清掉行内 left/right，避免盖住盒子上的左右对齐。 */
function placeVersionMenu(root) {
  const menu = root?.querySelector(".yhat-tf-version-menu");
  if (!menu) return;
  menu.style.left = "";
  menu.style.right = "";
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

function versionTrail(id, enabledId, stamp, title) {
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
  del.title = title || "删除该版本";
  del.setAttribute("aria-label", `${del.title} ${stamp}`);
  return del;
}

function renderVersionMenu(menu, items, currentId, opts) {
  const deletable = !opts || opts.deletable !== false;
  const deleteTitle = String(opts?.deleteTitle || "删除该版本");
  const enabledId = String(opts?.enabledId || "");
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
    if (enabledId && id === enabledId) row.classList.add("is-enabled");
    row.dataset.versionLabel = String(it.label || id);
    const parts = versionParts(it.label || id);
    const stamp = document.createElement("span");
    stamp.className = "yhat-tf-version-stamp";
    stamp.textContent = parts.stamp;
    const metric = document.createElement("span");
    metric.className = "yhat-tf-version-metric";
    const ic = parts.meta.find((bit) => bit.startsWith("RankIC")) || "";
    metric.textContent = ic;
    const chips = document.createElement("span");
    chips.className = "yhat-tf-version-meta";
    for (const bit of parts.meta) {
      if (bit === ic) continue;
      const chip = document.createElement("span");
      chip.className = "yhat-tf-version-chip";
      const tone = versionChipTone(bit, it.status);
      if (tone) chip.classList.add(`is-${tone}`);
      chip.textContent = bit;
      chips.append(chip);
    }
    const running = String(it?.status || "") === "running";
    if (deletable && !running) {
      row.append(stamp, metric, chips, versionTrail(id, enabledId, parts.stamp, deleteTitle));
    } else row.append(stamp, metric, chips);
    menu.append(row);
  }
}

function fillVersionSelect(head) {
  const root = panelOf(head)?.querySelector("[data-version-for]");
  const btn = root?.querySelector(".yhat-tf-version-btn");
  const menu = root?.querySelector(".yhat-tf-version-menu");
  if (!root || !btn || !menu) return;
  const items = versionLists.get(head) || [];
  const enabled = enabledIds.get(head) || "";
  const key = `${enabled}\n${items.map((it) => `${it.id}\t${it.label || ""}\t${it.status || ""}`).join("\n")}`;
  const want = viewing.get(head)?.run_id || "";
  const has = !!(want && items.some((it) => String(it.id) === want));
  if (want && !has) viewing.delete(head);
  const latest = String(items[0]?.id || "");
  const next = has ? want : latest;
  const shown = items.find((it) => String(it.id) === next);
  btn.textContent = shown ? String(shown.label || shown.id) : "尚无版本";
  btn.title = shown?.model_id ? String(shown.model_id) : btn.textContent;
  if (root.dataset.itemsKey === key && root.dataset.value === next) return;
  root.dataset.itemsKey = key;
  root.dataset.value = next;
  renderVersionMenu(menu, items, next, { enabledId: enabled });
}

async function loadVersionList(head) {
  const seq = (viewSeq.get(`list:${head}`) || 0) + 1;
  viewSeq.set(`list:${head}`, seq);
  const { ok, data } = await apiFetch(
    `${apiBase()}/versions?head=${encodeURIComponent(head)}&suite=research`
  );
  if (seq !== viewSeq.get(`list:${head}`)) return;
  if (!ok) return;
  versionLists.set(head, Array.isArray(data?.items) ? data.items : []);
  if (data?.enabled_id) enabledIds.set(head, String(data.enabled_id));
  else enabledIds.delete(head);
  fillVersionSelect(head);
  fillBacktestSelects();
  const st = latestHeads.get(head);
  if (
    st &&
    !st.fit_running &&
    String(st.job?.suite || "") === "live" &&
    !viewing.get(head) &&
    !dirtyParams.has(head)
  ) {
    const latest = String((versionLists.get(head) || [])[0]?.id || "");
    if (latest) void onVersionChange(head, latest);
  }
}

function releaseViewing(head) {
  if (!viewing.has(head)) return false;
  viewing.delete(head);
  viewSeq.set(head, (viewSeq.get(head) || 0) + 1);
  fillVersionSelect(head);
  return true;
}

async function onVersionDelete(head, runId) {
  if (!runId) return;
  const items = versionLists.get(head) || [];
  const it = items.find((row) => String(row.id) === runId);
  const label = String(it?.label || runId);
  if (!window.confirm(`删除版本「${label}」？只去掉这条归档、日志和权重，当前拟合不动。`)) return;
  const { ok, data, error } = await apiFetch(
    `${apiBase()}/versions?head=${encodeURIComponent(head)}&run_id=${encodeURIComponent(runId)}&suite=research`,
    { method: "DELETE" }
  );
  if (!ok) {
    showToast(error || "删除失败", "warn");
    return;
  }
  const wasViewing = viewing.get(head)?.run_id === runId;
  versionLists.set(head, Array.isArray(data?.items) ? data.items : []);
  if (wasViewing) viewing.delete(head);
  const root = panelOf(head)?.querySelector("[data-version-for]");
  if (root) delete root.dataset.itemsKey;
  const st = latestHeads.get(head);
  if (wasViewing && st) renderHead(st);
  else fillVersionSelect(head);
  const menu = root?.querySelector(".yhat-tf-version-menu");
  const btn = root?.querySelector(".yhat-tf-version-btn");
  if (menu && btn && !btn.disabled) {
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    placeVersionMenu(root);
  }
  showToast("已删除版本", "ok");
  void refreshStatus({ quiet: true });
}

async function onVersionChange(head, runId) {
  const seq = (viewSeq.get(head) || 0) + 1;
  viewSeq.set(head, seq);
  if (!runId) {
    viewing.delete(head);
    dirtyParams.delete(head);
    const st = latestHeads.get(head);
    if (st) renderHead(st);
    return;
  }
  const { ok, data, error } = await apiFetch(
    `${apiBase()}/versions/preview?head=${encodeURIComponent(head)}&run_id=${encodeURIComponent(runId)}`
  );
  if (seq !== viewSeq.get(head)) return;
  if (!ok || !data?.run_id) {
    showToast(error || "版本读取失败", "warn");
    fillVersionSelect(head);
    return;
  }
  const live = latestHeads.get(head);
  if (live?.fit_running || live?.job?.status === "running") return;
  viewing.set(head, data);
  dirtyParams.delete(head);
  if (live) renderHead(live);
}

function renderFitInfo(st, { replay = false } = {}) {
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

  const label = `${replay ? "回看 · " : ""}${fitActionLabel(st) || "本次拟合"}`;
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
      if (!prep.L.length && !prep.T.length && !(prep.R || []).length) {
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
  const versionBtn = panel.querySelector("[data-version-for] .yhat-tf-version-btn");
  if (versionBtn) {
    versionBtn.disabled = fitRunning;
    if (fitRunning) closeVersionMenus();
  }
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
    if (isFactorFactory()) {
      const rankHead = head === "oo_rank" || head === "tc_rank";
      const metrics = currentFitMetrics(st);
      if (rankHead) {
        const ic = Number(metrics?.cs_rank_ic);
        gate.textContent = Number.isFinite(ic) ? `Rank IC ${ic.toFixed(3)}` : "绝对指标";
      } else {
        const mse = Number(metrics?.mse);
        gate.textContent = Number.isFinite(mse) ? `MSE ${mse.toFixed(3)}` : "MSE —";
      }
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

function renderResult(st, { replay = false } = {}) {
  const panel = panelOf(st.head);
  if (!panel) return;

  renderFitInfo(st, { replay });

  const resultEl = panel.querySelector(`[data-result-for="${st.head}"]`);
  if (resultEl) {
    const blob = pretty(
      scrubFactorParams(
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

/** 模型中心不把已经结束的执行套跑次当成当前版本。进行中的拟合仍占住按钮。 */
function hideFinishedExecution(st) {
  if (!st || st.fit_running) return st;
  if (String(st.job?.suite || "") !== "live") return st;
  if (viewing.get(st.head)) return st;
  return {
    ...st,
    last: null,
    job: null,
    log_tail: "",
    arms_metrics: null,
    fit_curve: null,
  };
}

function renderHead(st) {
  const head = st.head;
  latestHeads.set(head, st);
  const shown = shownStatus(hideFinishedExecution(st));
  const replay = shown !== st;
  const scheme = shown.scheme || {};
  const outcome = headFitOutcome(shown);
  const outcomeLabel =
    {
      running: "拟合中",
      gated: isFactorFactory() ? "有指标" : "硬门过闸",
      fitted: "已拟合",
      failed: "拟合失败",
      none: "未拟合",
    }[outcome] || "未拟合";
  const chipText = stateLabel(stateOf(shown));
  const msgBits = [
    st.fit_running && outcomeLabel !== chipText ? "拟合中" : null,
    !st.fit_running && shown.job?.status === "error" && outcomeLabel !== chipText
      ? "拟合失败"
      : null,
    !st.fit_running &&
    outcome !== "none" &&
    outcome !== "running" &&
    outcomeLabel !== chipText &&
    !(shown.job?.status === "error" && outcome === "failed")
      ? outcomeLabel
      : null,
    isFactorFactory() ? null : scheme.loss || null,
  ].filter(Boolean);
  setChip(document.querySelector(`[data-status-for="${head}"]`), stateOf(shown), msgBits.join(" · "));

  renderHeadSummary(shown);
  renderResult(shown, { replay });
  // 表单回填：用户改过则不覆盖。
  // 回看历史版：用该版 fit_params，不写 draft。
  // 未在跑：优先工作配置（拟合意图隐式同步的 draft），切勿让旧 job 盖回。
  // 拟合中：用本次 job/last，与信息窗一致。
  if (!dirtyParams.has(head)) {
    const running =
      !!st.fit_running || st.job?.status === "running" || st.job?.status === "starting";
    const viewParams = !running && viewing.get(head)?.fit_params;
    const lastParams =
      st.last?.params &&
      ["holdout_intent", "holdout_result", "holdout_failed"].includes(st.last.action)
        ? st.last.params
        : null;
    const formParams = viewParams
      ? viewParams
      : running
        ? st.job?.params || lastParams || st.draft?.params || null
        : st.draft?.params || lastParams || st.job?.params || null;
    if (formParams) applyParams(head, formParams);
  }
  fillVersionSelect(head);
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
        const schemeName = schemeTitle(data.scheme?.name);
        const next = `${schemeName} · 有指标 ${nReady}/4 · 已拟合 ${nFitted}/4${
          nRun ? ` · 拟合中 ${nRun}` : ""
        } · 不写 live`;
        if (meta.textContent !== next) meta.textContent = next;
      }
      scheduleFitPoll(heads);
      HEADS.forEach((head) => {
        void loadVersionList(head);
      });
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
  viewing.delete(head);
  viewSeq.set(head, (viewSeq.get(head) || 0) + 1);
  fillVersionSelect(head);
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

let btTimer = 0;
let btViewRun = "";
const enabledIds = new Map();
const BT_PICKS_KEY = "yhat_bt_head_picks";

function readSavedBtPicks() {
  try {
    const raw = JSON.parse(localStorage.getItem(BT_PICKS_KEY) || "null");
    return raw && typeof raw === "object" ? raw : {};
  } catch (_) {
    return {};
  }
}

function currentBtPicks() {
  const out = {};
  document.querySelectorAll("[data-bt-head]").forEach((el) => {
    const head = el.dataset.btHead;
    const value = String(el.dataset.btValue || "");
    const cut = value.indexOf(":");
    const factory = cut > 0 ? value.slice(0, cut) : btArmOf(head);
    out[head] = {
      factory,
      run_id: cut > 0 ? value.slice(cut + 1) : "",
    };
  });
  return out;
}

function saveBtPicks() {
  try {
    localStorage.setItem(BT_PICKS_KEY, JSON.stringify(currentBtPicks()));
  } catch (_) {
    /* 隐私模式写不进时，这次刷新仍用页面上的选择 */
  }
}

function restoreBtPicks() {
  const saved = readSavedBtPicks();
  document.querySelectorAll("[data-bt-arm]").forEach((el) => {
    const fac = String(saved[el.dataset.btArm]?.factory || "").toUpperCase();
    if (fac === "R" || fac === "L" || fac === "T") el.value = fac;
  });
  document.querySelectorAll("[data-bt-head]").forEach((btn) => {
    const row = saved[btn.dataset.btHead] || {};
    const fac = String(row.factory || "").toUpperCase();
    const run = String(row.run_id || "");
    if ((fac === "R" || fac === "L" || fac === "T") && run) {
      btn.dataset.btValue = `${fac}:${run}`;
    }
  });
}

function btArmOf(head) {
  const raw = String(document.querySelector(`[data-bt-arm="${head}"]`)?.value || "").toUpperCase();
  if (raw === "R" || raw === "L" || raw === "T") return raw;
  return head === "oo" || head === "tc" ? "R" : "L";
}

function backtestItems(head) {
  const fac = btArmOf(head);
  return (mixCatalog.get(head) || [])
    .filter((it) => String(it.factory || "") === fac)
    .map((it) => {
      const factory = String(it.factory || "");
      const base = String(it.label || it.id || "");
      const marked = factory && base.split(" · ").pop() === factory;
      const label = marked || !factory ? base : `${base} · ${factory}`;
      return {
        ...it,
        id: `${factory}:${it.id}`,
        run_id: it.id,
        label,
      };
    });
}

function mixButtonText(it) {
  if (!it) return "";
  return String(it.label || it.id || "");
}

function fillBacktestSelects() {
  document.querySelectorAll("[data-bt-head]").forEach((btn) => {
    const head = btn.dataset.btHead;
    const prev = btn.dataset.btValue || "";
    const menu = btn.parentElement?.querySelector(".yhat-tf-version-menu");
    const items = backtestItems(head);
    if (!items.length) {
      btn.disabled = true;
      btn.textContent = "尚无带权重的版本";
      btn.title = btn.textContent;
      delete btn.dataset.btValue;
      btn.setAttribute("aria-expanded", "false");
      if (menu) {
        menu.hidden = true;
        renderVersionMenu(menu, [], "", { deletable: false });
      }
      return;
    }
    btn.disabled = false;
    const enabled = enabledIds.get(head) || "";
    const preferred = String(items[0].id);
    const next = prev && items.some((it) => String(it.id) === prev)
      ? prev
      : enabled && items.some((it) => String(it.id) === enabled)
        ? enabled
        : preferred || String(items[0].id);
    const shown = items.find((it) => String(it.id) === next);
    btn.dataset.btValue = next;
    btn.textContent = shown ? mixButtonText(shown) : next;
    btn.title = btn.textContent;
    if (menu) renderVersionMenu(menu, items, next, { deletable: false });
  });
  syncBacktestRun();
  const empty = $("[data-bt-empty]");
  if (empty) {
    const picks = [...document.querySelectorAll("[data-bt-head]")];
    empty.hidden = picks.some((btn) => btn.dataset.btValue);
  }
}

function syncBacktestRun() {
  const btn = $("#yhat-factory-backtest-run");
  if (!btn) return;
  const picks = [...document.querySelectorAll("[data-bt-head]")];
  const ready = picks.length === 4 && picks.every((el) => el.dataset.btValue);
  btn.disabled = !ready || btn.dataset.running === "1";
}

function placeBacktestMenu(box) {
  const menu = box?.querySelector(".yhat-tf-version-menu");
  if (!menu) return;
  menu.style.left = "0";
  menu.style.right = "auto";
  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth - 8) {
    menu.style.left = "auto";
    menu.style.right = "0";
  }
}

let btRulesDirty = false;
let btRulesTimer = 0;

function btField(name) {
  return document.querySelector(`#yhat-factory-backtest-form [data-bt-field="${name}"]`);
}

function readBacktestRules() {
  const num = (name) => {
    const el = btField(name);
    if (!el || el.type === "checkbox") return null;
    const raw = String(el.value || "").trim();
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  };
  const pct = (name) => {
    const n = num(name);
    return n == null ? null : n / 100;
  };
  return {
    w_oo: num("w_oo"),
    w_tc: num("w_tc"),
    fuse_enter: pct("fuse_enter_pct"),
    fuse_strong: pct("fuse_strong_pct"),
    oo_rank_max: num("oo_rank_max"),
    oo_rank_strong_max: num("oo_rank_strong_max"),
    lot_base_amount: num("lot_base_amount"),
    lot_strong_amount: num("lot_strong_amount"),
    start_cash: num("start_cash"),
    y_oo_gt0: readToggle("y_oo_gt0"),
    morph_gate_enabled: readToggle("morph_gate_enabled"),
    feature_set:
      document.querySelector("#yhat-factory-backtest-form [data-feature-set]")?.value || "hs300",
  };
}

function syncBacktestScope() {
  const el = document.querySelector("[data-bt-score-scope]");
  if (!el) return;
  const feature = document.querySelector("#yhat-factory-backtest-form [data-feature-set]");
  const label = feature?.selectedOptions?.[0]?.textContent || "观察池";
  el.textContent = `09:30 开盘 · ${label}`;
}

function readToggle(name) {
  const on = document.querySelector(`#yhat-factory-backtest-form [data-bt-field="${name}"][value="1"]`);
  return !!(on && on.checked);
}

function setToggle(name, on) {
  const want = on ? "1" : "0";
  document.querySelectorAll(`#yhat-factory-backtest-form [data-bt-field="${name}"]`).forEach((el) => {
    el.checked = el.value === want;
  });
}

function setBtNumber(name, value, digits) {
  const el = btField(name);
  if (!el || document.activeElement === el) return;
  if (value == null || value === "") {
    el.value = "";
    return;
  }
  const n = Number(value);
  if (!Number.isFinite(n)) return;
  el.value = digits == null ? String(Math.round(n)) : n.toFixed(digits);
}

function setBtRank(name, rules, key, fallback) {
  if (!rules || !Object.prototype.hasOwnProperty.call(rules, key)) {
    setBtNumber(name, fallback, 0);
    return;
  }
  setBtNumber(name, rules[key], 0);
}

function fillBacktestForm(rules) {
  const form = $("#yhat-factory-backtest-form");
  if (!rules || !form || btRulesDirty) return;
  if (form.contains(document.activeElement)) return;
  setBtNumber("w_oo", rules.w_oo, 2);
  setBtNumber("w_tc", rules.w_tc, 2);
  setBtNumber("fuse_enter_pct", Number(rules.fuse_enter) * 100, 2);
  setBtNumber("fuse_strong_pct", Number(rules.fuse_strong) * 100, 2);
  // 缺键用 6 / 12。旧回包没有这两项时，空值会把框刷成占位「关」，离焦保存再刷一次。
  setBtRank("oo_rank_max", rules, "oo_rank_max", 6);
  setBtRank("oo_rank_strong_max", rules, "oo_rank_strong_max", 12);
  setBtNumber("lot_base_amount", rules.lot_base_amount, 0);
  setBtNumber("lot_strong_amount", rules.lot_strong_amount, 0);
  setBtNumber("start_cash", rules.start_cash, 0);
  setToggle("y_oo_gt0", !!rules.y_oo_gt0);
  setToggle("morph_gate_enabled", !!rules.morph_gate_enabled);
  const feature = form.querySelector("[data-feature-set]");
  if (feature && rules.feature_set && document.activeElement !== feature) {
    feature.value = rules.feature_set;
  }
  syncBacktestScope();
  form.dataset.ready = "1";
}

async function saveBacktestRules(apply) {
  const { ok, data, error } = await apiFetch(`${BT_API}/backtest/rules`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rules: readBacktestRules() }),
  });
  if (!ok) {
    showToast(error || "规则没能保存", "error");
    return null;
  }
  btRulesDirty = false;
  if (apply) fillBacktestForm(data?.rules);
  return data?.rules || null;
}

function scheduleBacktestRulesSave() {
  btRulesDirty = true;
  window.clearTimeout(btRulesTimer);
  btRulesTimer = window.setTimeout(() => void saveBacktestRules(false), 400);
}

function paintBacktestBlend(blend) {
  const blendEl = document.querySelector("[data-bt-blend]");
  if (!blendEl) return;
  if (blend === "day_rank") {
    blendEl.hidden = false;
    blendEl.textContent = "买序 · 当日名次";
    return;
  }
  blendEl.hidden = true;
  blendEl.textContent = "";
}

function renderBacktest(data) {
  const reportEl = $("[data-bt-report]");
  const errorEl = $("[data-bt-error]");
  const btn = $("#yhat-factory-backtest-run");
  const running = data?.status === "running";
  if (btn) btn.dataset.running = running ? "1" : "0";
  syncBacktestRun();
  const form = $("#yhat-factory-backtest-form");
  if (form) {
    form.querySelectorAll("input").forEach((el) => {
      if (el.readOnly) return;
      el.disabled = running;
    });
  }
  document.querySelectorAll("[data-bt-arm]").forEach((el) => {
    el.disabled = running;
  });
  const report = data?.report;
  const spent = backtestSpent(data?.started_at, running ? "" : data?.finished_at);
  let consoleState = { text: "尚未跑过", tone: "idle" };
  if (running) {
    consoleState = {
      text: "进行中",
      tone: "busy",
      clockFrom: data?.started_at || "",
      clock: elapsedClock(data?.started_at),
    };
  } else if (data?.status === "error") {
    consoleState = { text: "失败", tone: "fail", dur: spent };
  } else if (report) {
    consoleState = { text: "已完成", tone: "ok", dur: spent };
  }
  renderArmState($("[data-bt-console-state]"), consoleState);
  syncArmClock();
  if (errorEl) {
    const message = !running && data?.status === "error" ? String(data.error || "").trim() : "";
    errorEl.hidden = !message;
    errorEl.textContent = message;
  }
  const consoleEl = $("[data-bt-console]");
  if (consoleEl && typeof data?.log_tail === "string") {
    const text = data.log_tail.trim();
    if (!text) {
      consoleEl.hidden = true;
      consoleEl.textContent = "";
    } else {
      setArmLog(consoleEl, text.split("\n"));
    }
  }
  const paint = (el, text, tone) => {
    if (!el) return;
    el.textContent = text;
    el.title = text === "—" ? "" : text;
    el.classList.toggle("is-empty", text === "—");
    el.classList.toggle("is-up", tone === "up");
    el.classList.toggle("is-down", tone === "down");
  };
  const signed = (n) =>
    typeof n === "number" && Number.isFinite(n) ? (n > 0 ? "up" : n < 0 ? "down" : "") : "";
  if (!reportEl) {
    syncBacktestOutput();
    renderBacktestHistory(data);
    return;
  }
  if (!report) {
    const showMetrics = running || data?.status === "error";
    reportEl.hidden = !showMetrics;
    if (showMetrics) {
      for (const el of [
        $("[data-bt-window]"),
        $("[data-bt-return]"),
        $("[data-bt-excess]"),
        $("[data-bt-dd]"),
        $("[data-bt-dec]"),
        $("[data-bt-acc]"),
        $("[data-bt-hit]"),
      ]) {
        paint(el, "—");
      }
    }
    const picksEl = document.querySelector("[data-bt-picks]");
    if (picksEl && !data?.picks) picksEl.hidden = true;
    paintBacktestBlend(String(data?.rank_blend || ""));
    syncBacktestOutput();
    renderBacktestHistory(data);
    return;
  }
  reportEl.hidden = false;
  const pct = (v) =>
    typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(2)}%` : "—";
  const points = (v, digits) =>
    typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(digits)}%` : "—";
  const windowEl = $("[data-bt-window]");
  const retEl = $("[data-bt-return]");
  const excessEl = $("[data-bt-excess]");
  const ddEl = $("[data-bt-dd]");
  const decEl = $("[data-bt-dec]");
  const accEl = $("[data-bt-acc]");
  const hitEl = $("[data-bt-hit]");
  paint(windowEl, `${report.window_start || "—"} → ${report.window_end || "—"}`);
  const picksEl = document.querySelector("[data-bt-picks]");
  if (picksEl) {
    const picks = String(report.picks || data?.picks || "");
    picksEl.textContent = picks;
    picksEl.hidden = !picks;
  }
  paintBacktestBlend(String(report.rank_blend || data?.rank_blend || ""));
  paint(retEl, pct(report.cumulative_return), signed(report.cumulative_return));
  paint(excessEl, points(report.excess_pct, 2), signed(report.excess_pct));
  if (excessEl) excessEl.title = report.benchmark_label ? `相对${report.benchmark_label}` : "相对观察池等权";
  paint(
    ddEl,
    points(report.max_drawdown_pct, 2),
    typeof report.max_drawdown_pct === "number" && report.max_drawdown_pct > 0 ? "down" : "",
  );
  paint(decEl, points(report.decision_return_pct, 2), signed(report.decision_return_pct));
  if (accEl) {
    const n = report.accuracy_n;
    const hits = report.accuracy_hits;
    paint(accEl, points(report.accuracy_pct, 1));
    accEl.title = n > 0 ? `${hits}/${n} 笔 · 今开→明开` : "今开→明开";
  }
  if (hitEl) {
    const n = report.hit_n;
    const hits = report.hit_hits;
    paint(hitEl, points(report.hit_rate_pct, 1));
    hitEl.title = n > 0 ? `${hits}/${n} 笔 · 看多 · 今开→明开` : "看多 · 今开→明开";
  }
  syncBacktestOutput();
  renderBacktestHistory(data);
}

function backtestHistoryTitle(run) {
  const versions = run?.versions;
  if (!versions || typeof versions !== "object") return String(run?.label || "");
  const picks = run?.picks ? String(run.picks) : "";
  const bits = ["oo", "tc", "oo_rank", "tc_rank"]
    .map((head) => (versions[head] ? `${head}=${versions[head]}` : ""))
    .filter(Boolean);
  const windowBit = run.window_start && run.window_end ? `${run.window_start}→${run.window_end}` : "";
  return [run.label || "", picks, windowBit, bits.join(" ")].filter(Boolean).join(" · ");
}

function renderBacktestHistory(data) {
  const box = document.querySelector("[data-bt-history]");
  const btn = document.querySelector("[data-bt-history-btn]");
  const menu = document.querySelector("[data-bt-history-menu]");
  if (!box || !btn || !menu) return;
  const runs = Array.isArray(data?.runs) ? data.runs : [];
  box.hidden = false;
  if (!runs.length) {
    btn.dataset.btValue = "";
    btn.textContent = "尚无记录";
    btn.title = "还没有可回看的组合回测";
    btn.disabled = true;
    menu.hidden = true;
    menu.replaceChildren();
    return;
  }
  const current = String(data?.run_id || "");
  const shown = runs.find((it) => String(it.id) === current) || runs[0];
  btn.dataset.btValue = shown ? String(shown.id) : "";
  btn.textContent = shown ? String(shown.label || shown.id) : "记录";
  btn.title = backtestHistoryTitle(shown);
  btn.disabled = false;
  renderVersionMenu(menu, runs, btn.dataset.btValue, { deleteTitle: "删除该记录" });
}

async function onBacktestDelete(runId) {
  if (!runId) return;
  const row = document.querySelector(
    `[data-bt-history-menu] [data-version-id="${CSS.escape(runId)}"]`,
  );
  const label = String(row?.dataset.versionLabel || runId);
  if (!window.confirm(`删除记录「${label}」？只去掉这条归档和日志。成功的结果还留在页面上，失败的错误提示和这次日志一起去掉。`)) return;
  const { ok, error } = await apiFetch(
    `${BT_API}/backtest?run_id=${encodeURIComponent(runId)}`,
    { method: "DELETE" },
  );
  if (!ok) {
    showToast(error || "删除失败", "warn");
    return;
  }
  if (btViewRun === runId) btViewRun = "";
  showToast("已删除记录", "ok");
  await pollBacktest();
  const menu = document.querySelector("[data-bt-history-menu]");
  const btn = document.querySelector("[data-bt-history-btn]");
  if (!menu || !btn || btn.disabled) return;
  menu.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  placeBacktestMenu(btn.closest(".yhat-tf-version-box"));
}

function syncBacktestOutput() {
  const shell = $("[data-bt-output]");
  if (!shell) return;
  const reportOn = $("[data-bt-report]") && !$("[data-bt-report]").hidden;
  const logOn = $("[data-bt-console]") && !$("[data-bt-console]").hidden;
  shell.hidden = !(reportOn || logOn);
}

async function pollBacktest() {
  window.clearTimeout(btTimer);
  const query = btViewRun ? `?run=${encodeURIComponent(btViewRun)}` : "";
  const { ok, data, error } = await apiFetch(`${BT_API}/backtest${query}`);
  if (!ok) {
    const errorEl = $("[data-bt-error]");
    renderArmState($("[data-bt-console-state]"), { text: "失败", tone: "fail" });
    if (errorEl) {
      errorEl.hidden = false;
      errorEl.textContent = error || "无法读取组合回测";
    }
    return;
  }
  if (btViewRun && data && !data.viewing && String(data.run_id || "") !== btViewRun) {
    btViewRun = "";
  }
  renderBacktest(data);
  fillBacktestForm(data?.rules);
  if (data?.live_status === "running" || data?.status === "running") {
    btTimer = window.setTimeout(() => void pollBacktest(), 2000);
  }
}

function bindBacktest() {
  restoreBtPicks();
  fillBacktestSelects();
  const form = $("#yhat-factory-backtest-form");
  form?.addEventListener("submit", (ev) => ev.preventDefault());
  form?.addEventListener("input", () => {
    syncBacktestScope();
    scheduleBacktestRulesSave();
  });
  form?.addEventListener("change", () => {
    syncBacktestScope();
    scheduleBacktestRulesSave();
  });
  form?.addEventListener("focusout", () => {
    window.setTimeout(() => {
      if (form.contains(document.activeElement)) return;
      void saveBacktestRules(true);
    }, 0);
  });
  document.querySelectorAll("[data-bt-arm]").forEach((el) => {
    el.addEventListener("change", () => {
      const btn = document.querySelector(`[data-bt-head="${el.dataset.btArm}"]`);
      if (btn) delete btn.dataset.btValue;
      closeVersionMenus();
      fillBacktestSelects();
      saveBtPicks();
    });
  });
  document.querySelectorAll(".yhat-tf-backtest-picks .yhat-tf-version-box").forEach((box) => {
    box.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const btn = box.querySelector("[data-bt-head]");
      const menu = box.querySelector(".yhat-tf-version-menu");
      if (!btn || !menu || btn.disabled) return;
      const picked = ev.target.closest("[data-version-id]");
      if (picked) {
        const head = btn.dataset.btHead;
        const id = picked.dataset.versionId || "";
        const items = backtestItems(head);
        const shown = items.find((it) => String(it.id) === id);
        btn.dataset.btValue = id;
        btn.textContent = shown ? mixButtonText(shown) : id;
        btn.title = btn.textContent;
        renderVersionMenu(menu, items, id, { deletable: false });
        closeVersionMenus();
        syncBacktestRun();
        saveBtPicks();
        return;
      }
      if (!ev.target.closest(".yhat-tf-version-btn")) return;
      const willOpen = menu.hidden;
      closeVersionMenus();
      if (!willOpen) return;
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      placeBacktestMenu(box);
      menu.querySelector(".is-current")?.scrollIntoView({ block: "nearest" });
    });
  });
  document.querySelector("[data-bt-history]")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const btn = document.querySelector("[data-bt-history-btn]");
    const menu = document.querySelector("[data-bt-history-menu]");
    if (!btn || !menu || btn.disabled) return;
    const del = ev.target.closest("[data-version-delete]");
    if (del) {
      ev.preventDefault();
      void onBacktestDelete(del.dataset.versionDelete || "");
      return;
    }
    const picked = ev.target.closest("[data-version-id]");
    if (picked) {
      btViewRun = picked.dataset.versionId || "";
      closeVersionMenus();
      void pollBacktest();
      return;
    }
    if (!ev.target.closest("[data-bt-history-btn]")) return;
    const willOpen = menu.hidden;
    closeVersionMenus();
    if (!willOpen) return;
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    placeBacktestMenu(btn.closest(".yhat-tf-version-box"));
    menu.querySelector(".is-current")?.scrollIntoView({ block: "nearest" });
  });
  $("#yhat-factory-backtest-run")?.addEventListener("click", async () => {
    const versions = {};
    document.querySelectorAll("[data-bt-head]").forEach((el) => {
      const value = el.dataset.btValue || "";
      const cut = value.indexOf(":");
      versions[el.dataset.btHead] = {
        factory: cut > 0 ? value.slice(0, cut) : "",
        run_id: cut > 0 ? value.slice(cut + 1) : value,
      };
    });
    window.clearTimeout(btRulesTimer);
    const rules = (await saveBacktestRules(true)) || readBacktestRules();
    btViewRun = "";
    const btn = $("#yhat-factory-backtest-run");
    if (btn) btn.dataset.running = "1";
    syncBacktestRun();
    const { ok, data, error } = await apiFetch(`${BT_API}/backtest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ versions, rules }),
    });
    if (!ok) {
      if (btn) btn.dataset.running = "0";
      syncBacktestRun();
      const errorEl = $("[data-bt-error]");
      renderArmState($("[data-bt-console-state]"), { text: "失败", tone: "fail" });
      if (errorEl) {
        errorEl.hidden = false;
        errorEl.textContent = error || "组合回测未能启动";
      }
      return;
    }
    renderBacktest({ ...data, status: "running" });
    void pollBacktest();
  });
}

async function loadMixCatalog() {
  const { ok, data, error } = await apiFetch(`${BT_API}/versions`);
  mixCatalog.clear();
  if (!ok) {
    showToast(error || "无法读取三家工厂的版本", "error");
    return;
  }
  HEADS.forEach((head) => {
    const rows = Array.isArray(data?.heads?.[head]) ? data.heads[head] : [];
    mixCatalog.set(
      head,
      rows.filter((it) => it && it.id && it.factory),
    );
  });
}

export async function initYhatBt() {
  bindCompactHead();
  bindToastDismiss();
  document.addEventListener("click", () => closeVersionMenus());
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeVersionMenus();
  });
  await loadMixCatalog();
  bindBacktest();
  document.getElementById("yhat-bt-refresh")?.addEventListener("click", async () => {
    await loadMixCatalog();
    fillBacktestSelects();
    void pollBacktest();
  });
  void pollBacktest();
}

export function initYhatTf(_ctx, factory = "T") {
  pageFactory = factory === "L" || factory === "R" ? factory : "T";
  const pageRoot = $(".yhat-tf-page");
  if (pageRoot) pageRoot.dataset.factory = pageFactory;
  const hashHead = HEADS.find((head) => location.hash === `#${sectionOf(head)?.id}`);
  if (hashHead) activeHead = hashHead;
  applyView();
  bindCompactHead();
  bindToastDismiss();
  document.addEventListener("click", () => closeVersionMenus());
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeVersionMenus();
  });

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
          '[data-param-radio="seq_feat_set"], [data-param="seq_len"], [data-param="t_sample_pct"], [data-pool], [data-param="lookback"], [data-param="holdout_trading_days"], [data-param="ridge_alpha"]'
        )
      ) {
        updateProtocolPreviews(panel);
      }
    };
    const markDirty = (ev) => {
      if (ev?.target?.matches?.("[data-version-for]")) return;
      if (releaseViewing(head)) {
        const st = latestHeads.get(head);
        if (st) {
          renderHeadSummary(st);
          renderResult(st);
        }
      }
      dirtyParams.add(head);
    };
    const versionRoot = panel.querySelector("[data-version-for]");
    versionRoot?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const btn = versionRoot.querySelector(".yhat-tf-version-btn");
      const menu = versionRoot.querySelector(".yhat-tf-version-menu");
      if (!btn || !menu || btn.disabled) return;
      const del = ev.target.closest("[data-version-delete]");
      if (del) {
        ev.preventDefault();
        void onVersionDelete(head, del.dataset.versionDelete || "");
        return;
      }
      const picked = ev.target.closest("[data-version-id]");
      if (picked) {
        closeVersionMenus();
        void onVersionChange(head, picked.dataset.versionId || "");
        return;
      }
      if (!ev.target.closest(".yhat-tf-version-btn")) return;
      const willOpen = menu.hidden;
      closeVersionMenus();
      if (!willOpen) return;
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      placeVersionMenu(versionRoot);
      menu.querySelector(".is-current")?.scrollIntoView({ block: "nearest" });
    });
    panel.addEventListener("input", markDirty);
    panel.querySelectorAll('input[type="radio"][data-pool]').forEach((el) => {
      el.name = `pool_${head}`;
    });
    panel.addEventListener("change", (ev) => {
      if (ev.target?.matches?.("[data-version-for]")) {
        void onVersionChange(head, ev.target.value);
        return;
      }
      const box = ev.target;
      if (box?.matches?.('input[type="checkbox"][data-pool]') && !box.checked) {
        const any = panel.querySelector('input[type="checkbox"][data-pool]:checked');
        if (!any) box.checked = true;
      }
      markDirty(ev);
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
