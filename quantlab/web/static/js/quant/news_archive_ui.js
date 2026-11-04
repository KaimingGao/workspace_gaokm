/** 数据中心 · 新闻舆情折叠。与观察池徽章同一本地库；闸只读 09:30 前。 */

import { escapeHtml } from "../shared.js";

function slotClock(key, sessionDate) {
  const m = String(key || "").match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  if (!m) return "—";
  if (sessionDate && m[1] === sessionDate) return m[2];
  return `${m[1].slice(5)} ${m[2]}`;
}

function hm(iso) {
  const m = String(iso || "").match(/T(\d{2}:\d{2})/);
  return m ? m[1] : "";
}

const STOP_TEXT = {
  rate_limit: "已停止，避免继续请求被封",
  fail_streak: "连续失败，剩余股票未请求",
};

export function newsArchiveSummary(data) {
  const last = data && data.last;
  if (!last || !last.ran_at) return "尚无采集";
  const clock = hm(last.ran_at) || slotClock(last.slot, data.decision_date);
  const skipped = Number(last.n_skipped || 0);
  const tail = skipped ? ` · 未请求 ${skipped}` : "";
  return `${clock} · 成功 ${Number(last.n_ok || 0)} · 失败 ${Number(last.n_fail || 0)}${tail}`;
}

function shortDay(raw) {
  const s = String(raw || "");
  return s.length >= 10 ? s.slice(5) : s;
}

function portraitSpan(minDay, maxDay) {
  const a = shortDay(minDay);
  const b = shortDay(maxDay);
  if (a && b && a !== b) return `${a} ~ ${b}`;
  return a || b;
}

function pctLabel(part, total) {
  const n = Number(part || 0);
  const d = Number(total || 0);
  if (!d) return "—";
  const value = (100 * n) / d;
  if (value > 0 && value < 1) return "<1%";
  return `${Math.round(value)}%`;
}

function leadCells(data) {
  const src = data || {};
  const last = src.last || {};
  const skipped = Number(last.n_skipped || 0);
  const recentClock = last.ran_at
    ? hm(last.ran_at) || slotClock(last.slot, src.decision_date)
    : "—";
  const recentSub = last.ran_at
    ? `成功 ${Number(last.n_ok || 0)} · 失败 ${Number(last.n_fail || 0)}${skipped ? ` · 未请求 ${skipped}` : ""}`
    : "尚无采集";
  const clock = String(src.fill_clock || "09:30").slice(0, 5);
  const day = shortDay(src.decision_date) || String(src.decision_date || "");
  return [
    { label: "最近", value: recentClock, sub: recentSub, lead: true, empty: !last.ran_at },
    {
      label: "下一档",
      value: slotClock(src.next_slot, src.decision_date),
      sub: "同一档只采一次",
      lead: true,
      empty: !src.next_slot,
    },
    {
      label: "观察池",
      value: src.n_universe != null ? String(Number(src.n_universe || 0)) : "—",
      sub: day ? `决策日 ${day} ${clock}` : "观察池",
      lead: true,
      empty: !Number(src.n_universe || 0),
    },
  ];
}

export function newsArchivePortraitHtml(portrait, data) {
  const src = portrait || {};
  const labels = src.labels || {};
  const nTitles = Number(src.n_titles || 0);
  const nTime = Number(src.n_timestamp || 0);
  const nDate = Number(src.n_date_only || 0);
  const covered =
    Number(labels.bullish || 0) +
    Number(labels.bearish || 0) +
    Number(labels.neutral || 0) +
    Number(labels.mixed || 0);
  const labelLine =
    `多 ${Number(labels.bullish || 0)} · 空 ${Number(labels.bearish || 0)} · ` +
    `中 ${Number(labels.neutral || 0)} · 混合 ${Number(labels.mixed || 0)}`;
  const clockWidth = nTitles ? Math.max(0, Math.min(100, (100 * nTime) / nTitles)) : 0;
  const cells = [
    {
      label: "标题",
      value: nTitles ? String(nTitles) : "—",
      sub: nTitles ? portraitSpan(src.published_min, src.published_max) || "无发布日" : "尚无标题",
      empty: !nTitles,
    },
    {
      label: "有时点",
      value: nTitles ? pctLabel(nTime, nTitles) : "—",
      sub: nTitles ? `${nTime} 条 · 仅日期 ${nDate}` : "发布时间",
      empty: !nTitles,
      bar: nTitles ? clockWidth : null,
    },
    {
      label: "每只",
      value: nTitles ? String(Number(src.titles_median || 0)) : "—",
      sub: nTitles ? `中位 · 最多 ${Number(src.titles_max || 0)}` : "中位标题",
      empty: !nTitles,
    },
    {
      label: "今日标签",
      value: covered ? String(covered) : "—",
      sub: covered
        ? `${labelLine} · 命中 ${Number(src.n_hit_today || 0)}/${Number(src.n_titles_today || 0)}`
        : "决策窗尚无标题",
      empty: !covered,
    },
  ];
  const shown = leadCells(data).concat(cells);
  const foot = Number(src.n_rounds || 0)
    ? `<p class="watching-news-portrait-foot">累计 ${Number(src.n_rounds)} 轮 · 见到 ${Number(src.n_seen || 0)} · 新写入 ${Number(src.n_inserted || 0)}</p>`
    : "";
  return (
    `<div class="watching-news-portrait" aria-label="统计画像">` +
    shown
      .map((cell) => {
        const bar =
          cell.bar == null
            ? ""
            : `<span class="watching-news-mini"><i style="width:${Number(cell.bar).toFixed(2)}%"></i></span>`;
        return (
          `<div class="watching-news-stat${cell.lead ? " is-lead" : ""}${cell.empty ? " is-empty" : ""}">` +
          `<span>${escapeHtml(cell.label)}</span>` +
          `<strong>${escapeHtml(cell.value)}</strong>` +
          bar +
          `<em>${escapeHtml(cell.sub)}</em>` +
          `</div>`
        );
      })
      .join("") +
    `</div>` +
    foot
  );
}

function sampleTitleHtml(title, url) {
  const text = escapeHtml(title);
  const href = String(url || "").trim();
  if (/^https?:\/\//i.test(href)) {
    return (
      `<div class="watching-news-title">` +
      `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${text}</a>` +
      `</div>`
    );
  }
  return `<div class="watching-news-title">${text}</div>`;
}

const SAMPLE_BADGE = {
  bullish: "is-bull",
  bearish: "is-bear",
  mixed: "is-mixed",
  neutral: "is-neutral",
  missing: "is-neutral",
};

const SAMPLE_LABEL = {
  bullish: "多",
  bearish: "空",
  mixed: "杂",
  neutral: "中",
  missing: "—",
};

function stockLabel(item) {
  const name = String((item && item.name) || "").trim();
  const stockCode = String((item && item.code) || "");
  return name ? `${name} ${stockCode}` : stockCode;
}

function matchNewsStocks(stocks, raw) {
  const q = String(raw || "").trim().toLowerCase();
  if (!q) return [];
  const compact = q.replace(/\s+/g, "");
  const digits = compact.replace(/\D/g, "");
  const hits = [];
  for (const item of stocks || []) {
    const code = String((item && item.code) || "");
    const name = String((item && item.name) || "").trim();
    const nameHit = name.toLowerCase().includes(q);
    const codeHit = Boolean(
      compact && (code.toLowerCase().includes(compact) || (digits.length >= 2 && code.includes(digits)))
    );
    if (!nameHit && !codeHit) continue;
    const label = stockLabel(item).toLowerCase();
    const exact = code === digits || code.toLowerCase() === compact || name.toLowerCase() === q || label === q;
    hits.push({ item, exact, nameHit });
  }
  hits.sort(
    (a, b) =>
      Number(b.exact) - Number(a.exact) ||
      Number(b.nameHit) - Number(a.nameHit) ||
      String(a.item.code).localeCompare(String(b.item.code))
  );
  return hits.slice(0, 8).map((hit) => hit.item);
}

function resolveNewsStock(stocks, raw) {
  const q = String(raw || "").trim();
  if (!q) return "";
  const compact = q.toLowerCase().replace(/\s+/g, "");
  const digits = compact.replace(/\D/g, "");
  const list = stocks || [];
  const byCode = list.find((item) => {
    const code = String(item.code || "");
    return code === q || code === digits || code.toLowerCase() === compact;
  });
  if (byCode && (digits.length >= 6 || q === String(byCode.code))) return String(byCode.code);
  const byLabel = list.filter((item) => {
    const name = String(item.name || "").trim();
    return name === q || stockLabel(item) === q;
  });
  if (byLabel.length === 1) return String(byLabel[0].code);
  const hits = matchNewsStocks(list, q);
  if (hits.length === 1) return String(hits[0].code);
  return "";
}

function dateOptions(dates, selected) {
  const head = `<option value="">全部日期</option>`;
  const body = (dates || [])
    .map((item) => {
      const value = String(item || "");
      const on = value && value === selected ? " selected" : "";
      return `<option value="${escapeHtml(value)}"${on}>${escapeHtml(value)}</option>`;
    })
    .join("");
  return head + body;
}

export function newsArchiveSamplesHtml(samples, meta) {
  const src = meta || {};
  const rows = Array.isArray(samples) ? samples : [];
  const dates = Array.isArray(src.sample_dates) ? src.sample_dates : [];
  const stocks = Array.isArray(src.sample_stocks) ? src.sample_stocks : [];
  if (!rows.length && !dates.length && !stocks.length) return "";
  const date = String(src.sample_date || "");
  const code = String(src.sample_code || "");
  const picked = (stocks || []).find((item) => String((item && item.code) || "") === code);
  const shown = picked ? stockLabel(picked) : code;
  const filters =
    `<div class="watching-news-sample-filters">` +
    `<span class="watching-news-sample-field-control is-date"><select id="watching-news-sample-date" aria-label="日期">${dateOptions(dates, date)}</select></span>` +
    `<span class="watching-news-sample-field-control is-name">` +
    `<input id="watching-news-sample-name" type="search" aria-label="股票" placeholder="股票名 / 代码" autocomplete="off" spellcheck="false" value="${escapeHtml(shown)}" data-code="${escapeHtml(code)}" data-label="${escapeHtml(shown)}" />` +
    `<div class="watching-news-sample-suggest" hidden></div>` +
    `</span>` +
    `</div>`;
  if (!rows.length) {
    return (
      filters +
      `<p class="watching-news-portrait-foot">没有符合的标题</p>`
    );
  }
  const body = rows
    .map((row) => {
      const code = String((row && row.code) || "");
      const name = String((row && row.name) || "").trim();
      const who = name ? `${name} ${code}` : code || "—";
      const published =
        row && row.clock_quality === "date_only"
          ? `${shortDay(row.published_date)} 仅日期`
          : slotClock(row && row.published_at, "") || shortDay(row && row.published_date) || "—";
      const decision = shortDay(row && row.decision_date) || "—";
      const labelKey = String((row && row.label) || "");
      const label = SAMPLE_LABEL[labelKey] || "中";
      const badgeCls = SAMPLE_BADGE[labelKey] || "is-neutral";
      const title = String((row && row.title) || "");
      const nameHtml = name
        ? `<b>${escapeHtml(name)}</b><i>${escapeHtml(code)}</i>`
        : `<b>${escapeHtml(who)}</b>`;
      return (
        `<li>` +
        `<div class="watching-news-sample-who">${nameHtml}</div>` +
        `<span class="watching-sent-badge ${badgeCls}">${escapeHtml(label)}</span>` +
        sampleTitleHtml(title, row && row.url) +
        `<div class="watching-news-sample-when">${escapeHtml(published)}</div>` +
        `<div class="watching-news-sample-decision">${escapeHtml(decision)}</div>` +
        `</li>`
      );
    })
    .join("");
  const foot = date || code
    ? ""
    : "未筛选时每条标题不同，优先带出多、空、混合。选定日期或股票后列出对应标题。";
  return (
    filters +
    `<div class="watching-news-samples">` +
    `<div class="watching-news-samples-head"><span>股票</span><span>舆情</span><span>标题</span><span>发布时间</span><span>计入</span></div>` +
    `<ol>${body}</ol>` +
    `</div>` +
    (foot ? `<p class="watching-news-portrait-foot">${escapeHtml(foot)}</p>` : "")
  );
}

function meterBar(parts) {
  const total = parts.reduce((sum, part) => sum + Number(part.n || 0), 0);
  if (!total) return `<div class="watching-news-meter-track is-empty"></div>`;
  const segs = parts
    .filter((part) => Number(part.n) > 0)
    .map((part) => {
      const width = Math.max(0, Math.min(100, (100 * Number(part.n)) / total));
      return `<span class="watching-news-meter-seg is-${part.key}" style="width:${width.toFixed(2)}%"></span>`;
    })
    .join("");
  return `<div class="watching-news-meter-track">${segs}</div>`;
}

function meterLegend(parts) {
  return (
    `<div class="watching-news-meter-legend">` +
    parts
      .map(
        (part) =>
          `<span><i class="is-${part.key}"></i>${escapeHtml(part.name)} ${Number(part.n || 0)}</span>`
      )
      .join("") +
    `</div>`
  );
}

export function newsArchiveTableHtml(data) {
  const src = data || {};
  const last = src.last || {};
  const cov = src.coverage || {};
  const portrait = src.portrait || {};
  const stop = STOP_TEXT[String(last.stop_reason || "")] || "";
  const clock = String(src.fill_clock || "09:30").slice(0, 5);
  const fill = `${String(src.decision_date || "")}T${clock}:00`;
  const late =
    last.ran_at && src.decision_date && String(last.ran_at) > fill
      ? `手动执行只读 ${clock} 前。本次采集更晚，不进当日闸。历史回测按发布时间`
      : `手动执行只读 ${clock} 前已入库。历史回测按发布时间`;
  const universe = Number(src.n_universe || 0);
  const archived = Math.min(universe || Number(portrait.n_codes || 0), Number(portrait.n_codes || 0));
  const pending = Math.max(0, universe - archived);
  const archiveParts = [
    { key: "have", name: "已入库", n: archived },
    { key: "rest", name: "未入库", n: pending },
  ];
  const gateParts = [
    { key: "ok", name: "已覆盖", n: Number(cov.ok || 0) },
    { key: "empty", name: "无标题", n: Number(cov.empty || 0) },
    { key: "gap", name: "缺测", n: Number(cov.gap || 0) },
    { key: "degraded", name: "降级", n: Number(cov.degraded || 0) },
  ];
  const gateHead = gateParts.slice().sort((a, b) => b.n - a.n)[0];
  return (
    (stop ? `<p class="watching-news-stop">${escapeHtml(stop)}</p>` : "") +
    `<div class="watching-news-board">` +
    `<section class="watching-news-meter">` +
    `<div class="watching-news-meter-head"><span>入库</span><strong>${archived} / ${universe}<small>${escapeHtml(pctLabel(archived, universe))}</small></strong></div>` +
    meterBar(archiveParts) +
    meterLegend(archiveParts) +
    `</section>` +
    `<section class="watching-news-meter">` +
    `<div class="watching-news-meter-head"><span>今日闸</span><strong>${escapeHtml(gateHead && gateHead.n ? `${gateHead.name} ${gateHead.n}` : "—")}</strong></div>` +
    meterBar(gateParts) +
    meterLegend(gateParts) +
    `<p class="watching-news-meter-note">${escapeHtml(late)}</p>` +
    `</section>` +
    `</div>`
  );
}

function fmtRan(iso) {
  const raw = String(iso || "").trim();
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw.replace("T", " ").slice(0, 16);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function progressFacts(progress) {
  const done = Number(progress.n_done || 0);
  const total = Number(progress.n_total || 0);
  const skipped = Number(progress.n_skipped || 0);
  const bits = [
    `成功 ${Number(progress.n_ok || 0)}`,
    `失败 ${Number(progress.n_fail || 0)}`,
  ];
  if (skipped) bits.push(`未请求 ${skipped}`);
  else if (total > done) bits.push(`剩余 ${total - done}`);
  const code = String(progress.code || "").trim();
  if (progress.running && code) bits.push(code);
  return bits;
}

export function newsArchiveProgressHtml(progress, mode) {
  const src = progress || {};
  const total = Number(src.n_total || 0);
  const done = Number(src.n_done || 0);
  const pct = total ? Math.max(0, Math.min(100, (100 * done) / total)) : mode === "run" ? 0 : 100;
  const stopped = String(src.stop_reason || "");
  const phase = mode === "run" ? "更新中" : stopped ? "已停止" : "更新完成";
  const msg = STOP_TEXT[stopped] || (mode === "run" ? "只追加当前页，每只间隔约 1.2 秒" : "整池当前页已写回本地");
  const facts = progressFacts(src)
    .map((bit) => `<span>${escapeHtml(bit)}</span>`)
    .join("");
  return (
    `<div class="quant-bars-progress-head">` +
    `<span class="quant-bars-progress-phase">${escapeHtml(phase)}</span>` +
    `<span class="quant-bars-progress-pct">${Math.round(pct)}%</span>` +
    `</div>` +
    `<div class="quant-bars-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}">` +
    `<div class="quant-bars-progress-fill" style="width:${pct.toFixed(1)}%"></div>` +
    `</div>` +
    (facts ? `<div class="quant-bars-progress-facts">${facts}</div>` : "") +
    `<p class="quant-bars-progress-msg">${escapeHtml(msg)}</p>`
  );
}

export function installNewsArchivePanel() {
  const fold = document.getElementById("watching-news-archive-fold");
  const btn = document.getElementById("watching-news-archive-poll");
  const strip = document.getElementById("watching-news-archive-strip");
  const chip = document.getElementById("watching-news-archive-chip");
  const msg = document.getElementById("watching-news-archive-msg");
  const ran = document.getElementById("watching-news-archive-ran");
  const progressEl = document.getElementById("watching-news-archive-progress");
  const table = document.getElementById("watching-news-archive-table");
  const portrait = document.getElementById("watching-news-archive-portrait");
  const failed = document.getElementById("watching-news-archive-failed");
  const errEl = document.getElementById("watching-news-archive-error");
  const note = document.getElementById("watching-news-archive-note");
  if (!fold || !btn || !table) return;
  let held = null;
  let watchTimer = null;
  let sawRunning = false;

  function showError(message) {
    if (!errEl) return;
    const text = String(message || "").trim();
    errEl.hidden = !text;
    errEl.textContent = text;
  }

  function paintRan(iso) {
    if (!ran) return;
    const text = fmtRan(iso);
    if (!text) {
      ran.textContent = "上次 —";
      ran.title = "尚无更新记录";
      return;
    }
    ran.textContent = `上次 ${text}`;
    ran.title = `上次更新 ${text}`;
  }

  function paintChip(state, label, message) {
    if (strip) strip.classList.toggle("is-busy", state === "busy");
    if (chip) {
      chip.dataset.state = state || "idle";
      chip.textContent = label || "—";
    }
    if (msg) {
      msg.textContent = message || "";
      msg.title = message || "";
    }
  }

  function paintProgress(progress, mode) {
    if (!progressEl) return;
    if (!progress || !mode) {
      progressEl.hidden = true;
      progressEl.innerHTML = "";
      progressEl.classList.remove("is-active", "is-warn", "is-done");
      return;
    }
    progressEl.hidden = false;
    progressEl.classList.add("is-active");
    progressEl.classList.toggle("is-warn", mode === "stop");
    progressEl.classList.toggle("is-done", mode !== "run");
    progressEl.innerHTML = newsArchiveProgressHtml(progress, mode);
  }

  function applyStatus(data, progressMode) {
    const last = (data && data.last) || {};
    const live = data && data.progress;
    const running = !!(live && live.running);
    const shown = running ? live : held && held.progress;
    const mode = running ? "run" : progressMode || (held && held.mode) || "";
    if (running) {
      const done = Number(live.n_done || 0);
      const total = Number(live.n_total || 0);
      paintChip(
        "busy",
        "更新中",
        total ? `${done} / ${total} · 成功 ${Number(live.n_ok || 0)} · 失败 ${Number(live.n_fail || 0)}` : "正在向东财要当前页"
      );
      paintProgress(live, "run");
    } else if (mode === "stop") {
      paintChip("warn", "已停止", newsArchiveSummary(data));
      paintProgress(shown || live, "stop");
    } else if (mode === "done") {
      paintChip("ok", "已更新", newsArchiveSummary(data));
      paintProgress(shown || live, "done");
    } else if (last.ran_at) {
      const stopped = STOP_TEXT[String(last.stop_reason || "")];
      paintChip(stopped ? "warn" : "ok", stopped ? "已停止" : "已更新", newsArchiveSummary(data));
      paintProgress(null, "");
    } else {
      paintChip("idle", "—", "尚无采集");
      paintProgress(null, "");
    }
    if (!running) paintRan(last && last.ran_at);
    btn.disabled = running;
  }

  function paint(data) {
    showError("");
    stockCatalog = Array.isArray(data && data.sample_stocks) ? data.sample_stocks : [];
    applyStatus(data);
    if (note && data && data.note) note.textContent = String(data.note);
    table.innerHTML = newsArchiveTableHtml(data);
    if (portrait) {
      portrait.innerHTML =
        newsArchivePortraitHtml(data && data.portrait, data) +
        newsArchiveSamplesHtml(data && data.samples, data);
    }
    const codes = (data && data.failed_codes) || [];
    if (failed) {
      if (codes.length) {
        failed.hidden = false;
        failed.textContent = `失败 ${codes.join(" ")}`;
      } else {
        failed.hidden = true;
        failed.textContent = "";
      }
    }
  }

  let sampleGen = 0;
  let stockCatalog = [];
  let suggestIndex = -1;
  let committing = false;

  function sampleFilters() {
    const dateEl = document.getElementById("watching-news-sample-date");
    const codeEl = document.getElementById("watching-news-sample-name");
    let code = "";
    if (codeEl) {
      const raw = String(codeEl.value || "").trim();
      const committed = String(codeEl.dataset.code || "");
      const label = String(codeEl.dataset.label || "");
      if (!raw) code = "";
      else if (committed && (raw === label || raw === committed)) code = committed;
      else if (document.activeElement === codeEl) code = committed;
      else code = resolveNewsStock(stockCatalog, raw);
    }
    return {
      date: dateEl ? String(dateEl.value || "") : "",
      code,
    };
  }

  function suggestBox() {
    return document.querySelector(".watching-news-sample-suggest");
  }

  function hideSuggest() {
    const box = suggestBox();
    if (!box) return;
    box.hidden = true;
    box.innerHTML = "";
    suggestIndex = -1;
  }

  function renderSuggest(input) {
    const box = suggestBox();
    if (!box || !input) return;
    const raw = String(input.value || "").trim();
    if (!raw) {
      hideSuggest();
      return;
    }
    const hits = matchNewsStocks(stockCatalog, raw);
    suggestIndex = hits.length ? 0 : -1;
    if (!hits.length) {
      box.hidden = false;
      box.innerHTML = `<p class="watching-news-sample-suggest-empty">没有这只股票</p>`;
      return;
    }
    box.hidden = false;
    box.innerHTML = hits
      .map((item, i) => {
        const code = String(item.code || "");
        const name = String(item.name || "").trim() || code;
        return (
          `<button type="button" class="watching-search-item${i === suggestIndex ? " is-active" : ""}" data-news-stock="${escapeHtml(code)}">` +
          `<span class="watching-search-item-name">${escapeHtml(name)}</span>` +
          `<span class="watching-search-item-code">${escapeHtml(code)}</span>` +
          `</button>`
        );
      })
      .join("");
  }

  function moveSuggest(step) {
    const box = suggestBox();
    if (!box || box.hidden) return;
    const buttons = [...box.querySelectorAll("[data-news-stock]")];
    if (!buttons.length) return;
    suggestIndex = (suggestIndex + step + buttons.length) % buttons.length;
    buttons.forEach((btn, i) => btn.classList.toggle("is-active", i === suggestIndex));
    buttons[suggestIndex].scrollIntoView({ block: "nearest" });
  }

  function commitStock(code) {
    if (committing) return;
    committing = true;
    hideSuggest();
    const dateEl = document.getElementById("watching-news-sample-date");
    load({
      date: dateEl ? String(dateEl.value || "") : "",
      code: String(code || ""),
    }).catch((err) => {
      showError(err && err.message ? err.message : String(err));
    }).finally(() => {
      committing = false;
    });
  }

  async function load(filters) {
    const picked = filters || sampleFilters();
    const gen = ++sampleGen;
    const q = new URLSearchParams();
    if (picked.date) q.set("date", picked.date);
    if (picked.code) q.set("code", picked.code);
    const suffix = q.toString() ? `?${q}` : "";
    const res = await fetch(`/api/news-archive/status${suffix}`);
    const data = await res.json().catch(() => ({}));
    if (gen !== sampleGen) return;
    if (!res.ok || data.ok === false) {
      throw new Error(data.detail || data.error || "新闻舆情状态读取失败");
    }
    paint(data);
  }

  if (portrait) {
    portrait.addEventListener("change", (event) => {
      const target = event.target;
      if (!target || target.id !== "watching-news-sample-date") return;
      load(sampleFilters()).catch((err) => {
        showError(err && err.message ? err.message : String(err));
      });
    });
    portrait.addEventListener("input", (event) => {
      const target = event.target;
      if (!target || target.id !== "watching-news-sample-name") return;
      if (!String(target.value || "").trim()) {
        target.dataset.code = "";
        target.dataset.label = "";
        hideSuggest();
        commitStock("");
        return;
      }
      renderSuggest(target);
    });
    portrait.addEventListener("keydown", (event) => {
      const target = event.target;
      if (!target || target.id !== "watching-news-sample-name") return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        const box = suggestBox();
        if (!box || box.hidden) renderSuggest(target);
        else moveSuggest(1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        moveSuggest(-1);
        return;
      }
      if (event.key === "Escape") {
        hideSuggest();
        target.value = String(target.dataset.label || "");
        return;
      }
      if (event.key !== "Enter") return;
      event.preventDefault();
      const box = suggestBox();
      const active = box && box.querySelector(".watching-search-item.is-active");
      if (active && active.dataset.newsStock) {
        commitStock(active.dataset.newsStock);
        return;
      }
      const raw = String(target.value || "").trim();
      if (!raw) {
        commitStock("");
        return;
      }
      const resolved = resolveNewsStock(stockCatalog, raw);
      if (resolved) commitStock(resolved);
      else renderSuggest(target);
    });
    portrait.addEventListener("mousedown", (event) => {
      const btn = event.target && event.target.closest
        ? event.target.closest("[data-news-stock]")
        : null;
      if (!btn || !portrait.contains(btn)) return;
      event.preventDefault();
      commitStock(btn.dataset.newsStock || "");
    });
    portrait.addEventListener("focusout", (event) => {
      const target = event.target;
      if (!target || target.id !== "watching-news-sample-name") return;
      setTimeout(() => {
        if (committing) return;
        const input = document.getElementById("watching-news-sample-name");
        if (!input || document.activeElement === input) return;
        if (document.activeElement && document.activeElement.closest && document.activeElement.closest(".watching-news-sample-suggest")) {
          return;
        }
        hideSuggest();
        const raw = String(input.value || "").trim();
        const label = String(input.dataset.label || "");
        if (!raw) {
          if (input.dataset.code) commitStock("");
          return;
        }
        if (raw === label) return;
        const resolved = resolveNewsStock(stockCatalog, raw);
        if (resolved && resolved !== String(input.dataset.code || "")) commitStock(resolved);
        else input.value = label;
      }, 0);
    });
  }

  function stopWatch() {
    if (watchTimer) {
      clearInterval(watchTimer);
      watchTimer = null;
    }
  }

  async function pullProgress() {
    const res = await fetch("/api/news-archive/poll/progress");
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) return;
    const progress = data.progress || {};
    if (progress.running) {
      sawRunning = true;
      held = { progress, mode: "run" };
      applyStatus({ progress }, "run");
      return;
    }
    if (!(sawRunning && held && held.mode === "run")) return;
    sawRunning = false;
    held = { progress, mode: progress.stop_reason ? "stop" : "done" };
    stopWatch();
    btn.disabled = false;
    await load(sampleFilters());
  }

  function startWatch() {
    if (watchTimer) return;
    watchTimer = setInterval(() => {
      pullProgress().catch(() => {});
    }, 1200);
    pullProgress().catch(() => {});
  }

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    showError("");
    held = { progress: { running: true, n_done: 0, n_total: 0, n_ok: 0, n_fail: 0 }, mode: "run" };
    applyStatus({ last: {}, progress: held.progress }, "run");
    startWatch();
    try {
      const res = await fetch("/api/news-archive/poll", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.detail || data.error || "更新舆情失败");
      }
      if (data.reason === "busy") {
        held = { progress: data.progress || held.progress, mode: "run" };
        startWatch();
        return;
      }
      sawRunning = false;
      const progress = data.progress || {};
      const mode = progress.stop_reason ? "stop" : "done";
      held = { progress, mode };
      stopWatch();
      await load(sampleFilters());
    } catch (err) {
      sawRunning = false;
      held = null;
      stopWatch();
      paintChip("error", "失败", String((err && err.message) || err || "更新舆情失败"));
      paintProgress(null, "");
      showError(err && err.message ? err.message : String(err));
    } finally {
      if (!(held && held.mode === "run")) btn.disabled = false;
    }
  });

  if (document.body && document.body.dataset.page === "watching") {
    load()
      .then(() => pullProgress().then(() => {
        if (held && held.mode === "run") startWatch();
      }))
      .catch((err) => {
        paintChip("error", "失败", "读取失败");
        showError(err && err.message ? err.message : String(err));
      });
  }
}
