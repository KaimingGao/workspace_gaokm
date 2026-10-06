/** 研究枢纽 · 日线 / 5 分钟仓完整度格子。展开才读本地仓。 */

const KIND_TITLE = {
  ok: "齐",
  head: "头缺",
  tail: "尾缺",
  both: "头缺且尾缺",
  gap: "中间缺",
  miss: "无 K",
  live: "盘中，不算缺口",
};

function clipName(name, code) {
  const full = String(name || code || "");
  const chars = Array.from(full);
  const shown = chars.length > 6 ? `${chars.slice(0, 6).join("")}…` : full;
  return { full, shown };
}

function summaryDaily(data) {
  const n = Number(data?.miss_names || 0);
  return n > 0 ? `完整度 · 缺 ${n} 只` : "完整度 · 齐";
}

function summaryMinute(data) {
  const bits = [];
  const head = Number(data?.head_names || 0);
  const tail = Number(data?.tail_names || 0);
  const miss = Number(data?.miss_names || 0);
  if (head) bits.push(`头缺 ${head} 只`);
  if (tail) bits.push(`尾缺 ${tail} 只`);
  if (miss) bits.push(`无仓 ${miss} 只`);
  return bits.length ? `完整度 · ${bits.join(" · ")}` : "完整度 · 齐";
}

function axisNote(dates) {
  const list = dates || [];
  if (!list.length) return "";
  return `${list[0]} → ${list[list.length - 1]} · ${list.length} 个交易日 · 右端为最近`;
}

function rowBadge(row, mode) {
  if (mode === "daily") {
    const n = Number(row.miss_days || 0);
    return n
      ? { cls: "is-miss", text: `缺 ${n}` }
      : { cls: "is-ok", text: "齐" };
  }
  const head = Number(row.head_days || 0);
  const tail = Number(row.tail_days || 0);
  const gap = Number(row.gap_days || 0);
  const miss = Number(row.miss_days || 0);
  const cells = row.cells || [];
  const settled = cells.filter((c) => c !== "live");
  const empty = settled.length > 0 && settled.every((c) => c === "miss");
  if (head && tail) return { cls: "is-both", text: `头${head}·尾${tail}` };
  if (head) return { cls: "is-head", text: `头缺 ${head}` };
  if (tail) return { cls: "is-tail", text: `尾缺 ${tail}` };
  if (gap) return { cls: "is-gap", text: `中缺 ${gap}` };
  if (empty) return { cls: "is-miss", text: "无仓" };
  if (miss) return { cls: "is-miss", text: `缺 ${miss}` };
  return { cls: "is-ok", text: "齐" };
}

const LEGEND = {
  daily: [
    ["ok", "齐"],
    ["miss", "无 K"],
    ["live", "盘中"],
  ],
  minute: [
    ["ok", "齐"],
    ["head", "头缺"],
    ["tail", "尾缺"],
    ["both", "头尾缺"],
    ["gap", "中缺"],
    ["miss", "无 K"],
    ["live", "盘中"],
  ],
};

export function installBarsIntegrityUi(q) {
  const { apiFetch, escapeHtml } = q;
  const esc = typeof escapeHtml === "function" ? escapeHtml : (s) => String(s ?? "");
  const dailyFold = document.getElementById("quant-daily-integrity");
  const minuteFold = document.getElementById("quant-minute-integrity");
  const dailyLabel = document.getElementById("quant-daily-integrity-label");
  const minuteLabel = document.getElementById("quant-minute-integrity-label");
  const dailyBody = document.getElementById("quant-daily-integrity-body");
  const minuteBody = document.getElementById("quant-minute-integrity-body");
  if (!dailyFold || !minuteFold || !dailyBody || !minuteBody) return;

  let pop = null;

  function closePop() {
    if (pop) {
      pop.remove();
      pop = null;
    }
    if (minuteBody) {
      minuteBody.querySelectorAll(".quant-integrity-cell.is-picked").forEach((el) => {
        el.classList.remove("is-picked");
      });
    }
  }

  document.addEventListener("click", (ev) => {
    if (!pop) return;
    const t = ev.target;
    if (t && pop.contains(t)) return;
    if (t && t.closest && t.closest(".quant-integrity-cell")) return;
    closePop();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closePop();
  });

  function legendHtml(mode) {
    return (LEGEND[mode] || [])
      .map(
        ([k, label]) =>
          `<span class="quant-integrity-key"><i data-k="${esc(k)}"></i>${esc(label)}</span>`
      )
      .join("");
  }

  function dateCols(dates) {
    return dates
      .map((day, i) => {
        const prev = dates[i - 1] || "";
        const month = String(day).slice(5, 7);
        const dom = String(day).slice(8, 10);
        const showMonth = !prev || prev.slice(5, 7) !== month;
        return (
          `<span class="quant-integrity-colhead" title="${esc(day)}">` +
          `<span class="quant-integrity-mon">${showMonth ? esc(month) : ""}</span>` +
          `<span class="quant-integrity-dom">${esc(dom)}</span>` +
          `</span>`
        );
      })
      .join("");
  }

  function paintGrid(host, data, { clickable, mode }) {
    const dates = data.dates || [];
    const rows = data.rows || [];
    const names = [];
    const tracks = [];
    const stats = [];
    rows.forEach((row, idx) => {
      const alt = idx % 2 === 1 ? " is-alt" : "";
      const name = clipName(row.stock_name, row.stock_code);
      const badge = rowBadge(row, mode);
      const cells = (row.cells || [])
        .map((kind, i) => {
          const day = dates[i] || "";
          const title = `${name.full} ${row.stock_code} ${day} · ${KIND_TITLE[kind] || kind}`;
          const attrs =
            `class="quant-integrity-cell" data-k="${esc(kind)}" data-code="${esc(row.stock_code)}" data-date="${esc(day)}" title="${esc(title)}"`;
          if (clickable) {
            return `<button type="button" ${attrs} aria-label="${esc(title)}"></button>`;
          }
          return `<span ${attrs}></span>`;
        })
        .join("");
      names.push(
        `<div class="quant-integrity-name${alt}" title="${esc(name.full)} ${esc(row.stock_code)}">` +
          `<span class="quant-integrity-cname">${esc(name.shown)}</span>` +
          `<span class="quant-integrity-code">${esc(row.stock_code)}</span>` +
          `</div>`
      );
      tracks.push(`<div class="quant-integrity-cells${alt}">${cells}</div>`);
      stats.push(
        `<div class="quant-integrity-stat${alt}"><span class="quant-integrity-badge ${esc(badge.cls)}">${esc(badge.text)}</span></div>`
      );
    });
    const cols = dates.length || 60;
    host.innerHTML =
      `<div class="quant-integrity-toolbar">` +
      `<p class="quant-integrity-note">${esc(axisNote(dates))}</p>` +
      `<div class="quant-integrity-legend">${legendHtml(mode)}</div>` +
      `</div>` +
      (rows.length
        ? `<div class="quant-integrity-sheet" style="--cols:${cols}">` +
          `<div class="quant-integrity-sheet-head">` +
          `<div class="quant-integrity-name quant-integrity-name--head">股票</div>` +
          `<div class="quant-integrity-xhead"><div class="quant-integrity-cells">${dateCols(dates)}</div></div>` +
          `<div class="quant-integrity-stat quant-integrity-name--head">状态</div>` +
          `</div>` +
          `<div class="quant-integrity-sheet-body">` +
          `<div class="quant-integrity-names">${names.join("")}</div>` +
          `<div class="quant-integrity-x">${tracks.join("")}</div>` +
          `<div class="quant-integrity-stats">${stats.join("")}</div>` +
          `</div></div>`
        : `<p class="quant-integrity-note">观察池为空</p>`);
    const x = host.querySelector(".quant-integrity-x");
    const xHead = host.querySelector(".quant-integrity-xhead");
    if (x && xHead) {
      x.addEventListener("scroll", () => {
        if (xHead.scrollLeft !== x.scrollLeft) xHead.scrollLeft = x.scrollLeft;
      });
    }
  }

  async function loadDaily() {
    dailyBody.textContent = "读取本地日 K…";
    const { ok, data, error } = await apiFetch(
      "/api/quant/bars/integrity?days=60"
    );
    if (!ok || !data || data.success === false) {
      dailyBody.textContent = (data && data.error) || error || "日线完整度读取失败";
      return;
    }
    if (dailyLabel) dailyLabel.textContent = summaryDaily(data);
    paintGrid(dailyBody, data, { clickable: false, mode: "daily" });
    dailyFold.dataset.loaded = "1";
  }

  async function loadMinute() {
    minuteBody.textContent = "读取本地 5 分钟 K…";
    const { ok, data, error } = await apiFetch(
      "/api/quant/minute/integrity?days=60"
    );
    if (!ok || !data || data.success === false) {
      minuteBody.textContent = (data && data.error) || error || "分钟完整度读取失败";
      return;
    }
    if (minuteLabel) minuteLabel.textContent = summaryMinute(data);
    paintGrid(minuteBody, data, { clickable: true, mode: "minute" });
    minuteFold.dataset.loaded = "1";
  }

  function placePop(box, rect) {
    const pad = 8;
    const maxLeft = Math.max(pad, window.innerWidth - box.offsetWidth - pad);
    box.style.left = `${Math.min(Math.max(pad, rect.left), maxLeft)}px`;
    const below = rect.bottom + 6;
    if (below + box.offsetHeight > window.innerHeight - pad) {
      box.style.top = `${Math.max(pad, rect.top - box.offsetHeight - 6)}px`;
    } else {
      box.style.top = `${below}px`;
    }
  }

  function slotRow(slots) {
    return (slots || [])
      .map((s) => {
        const on = s && s.ok ? " is-on" : "";
        const hm = (s && s.hm) || "";
        return `<span class="quant-integrity-slot${on}" title="${esc(hm)}${s && s.ok ? " 有" : " 缺"}"></span>`;
      })
      .join("");
  }

  function rulerHtml(slots) {
    const list = slots || [];
    const pick = (i) => (list[i] && list[i].hm) || "";
    const mid = Math.floor((list.length - 1) / 2);
    const marks = [pick(0), pick(mid), pick(list.length - 1)].filter(Boolean);
    return marks.map((hm) => `<span>${esc(hm)}</span>`).join("");
  }

  async function openDay(btn) {
    const code = btn.getAttribute("data-code") || "";
    const date = btn.getAttribute("data-date") || "";
    if (!code || !date) return;
    closePop();
    minuteBody.querySelectorAll(".quant-integrity-cell.is-picked").forEach((el) => {
      el.classList.remove("is-picked");
    });
    btn.classList.add("is-picked");
    const box = document.createElement("div");
    box.className = "quant-integrity-pop";
    box.innerHTML = `<p class="quant-integrity-pop-title">${esc(code)} ${esc(date)}</p>`;
    document.body.appendChild(box);
    pop = box;
    const rect = btn.getBoundingClientRect();
    placePop(box, rect);
    const { ok, data, error } = await apiFetch(
      `/api/quant/minute/integrity-day?code=${encodeURIComponent(code)}&date=${encodeURIComponent(date)}`
    );
    if (!pop || pop !== box) return;
    if (!ok || !data || data.success === false) {
      box.innerHTML = `<p class="quant-integrity-pop-title">${esc((data && data.error) || error || "读取失败")}</p>`;
      return;
    }
    const name = data.stock_name || code;
    const kind = KIND_TITLE[data.kind] || data.kind || "";
    const first = data.first_hm ? `首根 ${data.first_hm}` : "无首根";
    const last = data.last_hm ? `末根 ${data.last_hm}` : "无末根";
    box.innerHTML =
      `<div class="quant-integrity-pop-head">` +
      `<div><strong>${esc(name)}</strong><span class="quant-integrity-code">${esc(code)}</span></div>` +
      `<span class="quant-integrity-badge is-${esc(data.kind || "miss")}">${esc(kind)}</span>` +
      `</div>` +
      `<p class="quant-integrity-pop-meta">${esc(date)} · ${esc(String(data.n ?? 0))}/48 · ${esc(first)} · ${esc(last)}</p>` +
      `<div class="quant-integrity-band"><span class="quant-integrity-band-k">上午</span>` +
      `<div><div class="quant-integrity-slots">${slotRow(data.morning)}</div>` +
      `<div class="quant-integrity-rulerline">${rulerHtml(data.morning)}</div></div></div>` +
      `<div class="quant-integrity-band"><span class="quant-integrity-band-k">下午</span>` +
      `<div><div class="quant-integrity-slots">${slotRow(data.afternoon)}</div>` +
      `<div class="quant-integrity-rulerline">${rulerHtml(data.afternoon)}</div></div></div>`;
    placePop(box, rect);
  }

  dailyFold.addEventListener("toggle", () => {
    if (dailyFold.open && dailyFold.dataset.loaded !== "1") loadDaily();
  });
  minuteFold.addEventListener("toggle", () => {
    if (minuteFold.open && minuteFold.dataset.loaded !== "1") loadMinute();
  });
  minuteBody.addEventListener("click", (ev) => {
    const btn = ev.target && ev.target.closest && ev.target.closest("button.quant-integrity-cell");
    if (!btn) return;
    ev.preventDefault();
    openDay(btn);
  });
}
