/**
 * 分组拟合档 A/B/C 徽标：四页股票名共用。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";

export const FIT_TIER_LABEL = { A: "强", B: "中", C: "弱" };
export const FIT_TIER_TIP = {
  A: "A 强：OOS 过门且截面 IC、ICIR>0 且 ŷOOS>0",
  B: "B 中：OOS 过门且 ŷOOS>0，未达 A",
  C: "C 弱：未过/ŷOOS≤0/跳过/单票/无模型（默认不进 live/回测）",
};

let _map = {};
let _loaded = false;

export function normalizeFitTier(raw) {
  const t = String(raw || "").trim().toUpperCase();
  return t === "A" || t === "B" || t === "C" ? t : "";
}

export function getFitTierMap() {
  return _map;
}

export function getFitTierForCode(code) {
  const c = String(code || "").trim();
  return c ? normalizeFitTier(_map[c]) : "";
}

export function ingestFitTierMap(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const next = {};
  Object.keys(src).forEach((k) => {
    const t = normalizeFitTier(src[k]);
    const code = String(k || "").trim();
    if (code && t) next[code] = t;
  });
  _map = next;
  _loaded = true;
  return _map;
}

export function fitTierBadgeHtml(tier, { escapeHtml, reason } = {}) {
  const esc = escapeHtml || defaultEscapeHtml;
  const t = normalizeFitTier(tier);
  if (!t) return "";
  const label = FIT_TIER_LABEL[t] || t;
  const base = FIT_TIER_TIP[t] || `拟合档 ${t}`;
  const tip = reason ? `${base} · ${reason}` : base;
  return (
    `<span class="watching-fit-tier-badge is-${t}" data-tier="${t}" ` +
    `title="${esc(`${t} ${label} · ${tip}`)}">${esc(t)}</span>`
  );
}

export function fitTierBadgeForCode(code, opts) {
  return fitTierBadgeHtml(getFitTierForCode(code), opts);
}

function codeFromHost(host) {
  if (!host || typeof host.closest !== "function") return "";
  const tagged = host.closest("[data-code]");
  if (tagged) {
    const c = String(tagged.getAttribute("data-code") || "").trim();
    if (c) return c;
  }
  const wrap = host.closest(
    ".watching-stock, .paper-wl-name, .rebalance-stock, .quant-cluster-member"
  );
  const sub = (wrap || host).querySelector(
    ".watching-code-sub, .paper-wl-code, .rebalance-stock-code, .quant-cluster-member-code"
  );
  if (!sub) return "";
  const first = sub.childNodes && sub.childNodes[0] ? sub.childNodes[0].textContent : "";
  return String(first || sub.textContent || "")
    .trim()
    .replace(/[^0-9A-Za-z].*$/, "")
    .slice(0, 6);
}

export function stampFitTierBadges(root) {
  const scope = root && root.querySelectorAll ? root : document;
  const hosts = scope.querySelectorAll(
    ".watching-name-row, .rebalance-stock-name, .quant-cluster-member"
  );
  hosts.forEach((host) => {
    const code = codeFromHost(host);
    const html = fitTierBadgeForCode(code);
    const existing = host.querySelector(".watching-fit-tier-badge");
    if (!html) {
      if (existing) existing.remove();
      return;
    }
    if (existing) {
      if (existing.getAttribute("data-tier") === getFitTierForCode(code)) return;
      existing.outerHTML = html;
      return;
    }
    const name = host.querySelector(
      ".watching-name-text, .paper-wl-name-text, .rebalance-stock-name-text, .quant-cluster-member-name"
    );
    if (name) name.insertAdjacentHTML("afterend", html);
    else host.insertAdjacentHTML("afterbegin", html);
  });
}

export async function ensureFitTierMap(root) {
  // cluster_retired：分组拟合档已退役，不再请求 /api/quant/cluster-live/fit-tiers
  _loaded = true;
  _map = {};
  stampFitTierBadges(root);
  return _map;
}
