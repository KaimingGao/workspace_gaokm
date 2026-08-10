/**
 * 观察页建仓流水（按日分组）。
 */
import { escapeHtml } from "../shared.js";

function fmtMoney(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  if (abs >= 10000) {
    const w = n / 10000;
    return `${w.toFixed(Math.abs(w) >= 100 || Number.isInteger(w * 10) ? 1 : 2)}万`;
  }
  return n.toLocaleString("zh-CN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: Number.isInteger(n) ? 0 : 2,
  });
}

function fmtPrice(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  return n.toLocaleString("zh-CN", {
    maximumFractionDigits: n >= 100 ? 2 : 3,
  });
}

function fmtTs(ts) {
  if (!ts) return { date: "—", time: "", title: "" };
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) {
      const raw = String(ts);
      return {
        date: raw.slice(0, 10) || "—",
        time: raw.slice(11, 16) || "",
        title: raw,
      };
    }
    const pad = (n) => String(n).padStart(2, "0");
    const y = d.getFullYear();
    const md = `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const today = new Date();
    return {
      date: y === today.getFullYear() ? md : `${y}-${md}`,
      time: hm,
      title: `${y}-${md} ${hm}`,
    };
  } catch (_) {
    const raw = String(ts);
    return { date: raw.slice(0, 10) || "—", time: raw.slice(11, 16) || "", title: raw };
  }
}

function dayKey(ts) {
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "未知日期";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  } catch (_) {
    return "未知日期";
  }
}

function dayLabel(key, n) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return key;
  const today = new Date();
  const pad = (x) => String(x).padStart(2, "0");
  const todayKey = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
  const y = new Date(today);
  y.setDate(y.getDate() - 1);
  const yKey = `${y.getFullYear()}-${pad(y.getMonth() + 1)}-${pad(y.getDate())}`;
  let base = key.slice(5);
  if (key === todayKey) base = `今天 ${base}`;
  else if (key === yKey) base = `昨天 ${base}`;
  return n ? `${base} · ${n} 笔` : base;
}

/**
 * @param {Record<string, object>} holdingsMap
 * @param {string[]} names
 * @param {Array<object>} buildLogs
 * @param {{ sectionEl: HTMLElement|null, tableEl: HTMLElement|null }} els
 */
export function renderWatchingHoldings(holdingsMap, names, buildLogs, els) {
  const section = els && els.sectionEl;
  const tableEl = els && els.tableEl;
  if (!section || !tableEl) return;

  let records = Array.isArray(buildLogs) ? buildLogs.slice() : [];
  if (!records.length) {
    const nameByCode = {};
    (names || []).forEach((n) => {
      if (n && typeof n === "string") nameByCode[String(n).trim()] = String(n).trim();
    });
    Object.entries(holdingsMap || {}).forEach(([code, h]) => {
      if (!h) return;
      records.push({
        type: "sync_paper",
        type_label: "建仓",
        ts: h.bought_at || h.added_at || "",
        detail: "",
        meta: {
          stock_code: code,
          stock_name: h.stock_name || nameByCode[code] || "",
          shares: h.shares,
          price: h.cost != null ? h.cost : h.price,
          amount:
            h.actual_cost != null
              ? h.actual_cost
              : Number(h.shares) && Number(h.cost)
                ? Number(h.shares) * Number(h.cost)
                : h.market_value,
          origin: "manual",
        },
      });
    });
  }

  if (!records.length) {
    section.hidden = false;
    tableEl.innerHTML = "";
    const desc = document.getElementById("watching-build-log-desc");
    if (desc) desc.textContent = "暂无流水";
    return;
  }
  section.hidden = false;

  const desc = document.getElementById("watching-build-log-desc");
  if (desc) desc.textContent = `共 ${records.length} 条 · 从观察「加入纸面」`;

  try {
    const sorted = records
      .slice()
      .sort((a, b) => {
        const ta = new Date(String(a.ts || 0)).getTime() || 0;
        const tb = new Date(String(b.ts || 0)).getTime() || 0;
        return tb - ta;
      })
      .slice(0, 40);

    const groups = [];
    let cur = null;
    sorted.forEach((l) => {
      const key = dayKey(l.ts);
      if (!cur || cur.key !== key) {
        cur = { key, items: [] };
        groups.push(cur);
      }
      cur.items.push(l);
    });

    tableEl.innerHTML = groups
      .map((g) => {
        const items = g.items
          .map((l) => {
            const meta = l.meta || {};
            const code = String(meta.stock_code || "").trim();
            const name = String(meta.stock_name || "").trim();
            const shares = Number(meta.shares);
            const price = Number(meta.price);
            const amount = Number(
              meta.amount != null ? meta.amount : meta.actual_cost
            );
            const bits = [];
            bits.push(name || code || "—");
            if (Number.isFinite(shares)) bits.push(`${shares}股`);
            if (Number.isFinite(price)) bits.push(`@${fmtPrice(price)}`);
            if (Number.isFinite(amount)) bits.push(`${fmtMoney(amount)}元`);
            const secondary = [code && name ? code : "", "观察建仓"]
              .filter(Boolean)
              .join(" · ");
            const when = fmtTs(l.ts);
            return (
              `<div class="paper-log-item is-sync" title="${escapeHtml(when.title || "")}">` +
              `<span class="paper-log-type">${escapeHtml(l.type_label || "建仓")}</span>` +
              `<div class="paper-log-body">` +
              `<div class="paper-log-primary">${escapeHtml(bits.join(" · "))}</div>` +
              (secondary
                ? `<div class="paper-log-secondary">${escapeHtml(secondary)}</div>`
                : "") +
              `</div>` +
              `<span class="paper-log-ts">` +
              `<span class="paper-log-date">${escapeHtml(when.date)}</span>` +
              `<span class="paper-log-time">${escapeHtml(when.time)}</span>` +
              `</span>` +
              `</div>`
            );
          })
          .join("");
        return (
          `<div class="paper-log-day">` +
          `<div class="paper-log-day-label">${escapeHtml(dayLabel(g.key, g.items.length))}</div>` +
          items +
          `</div>`
        );
      })
      .join("");
  } catch (err) {
    console.error("renderWatchingHoldings error:", err);
    tableEl.innerHTML = `<p class="watching-table-empty">记录加载失败</p>`;
  }
}
