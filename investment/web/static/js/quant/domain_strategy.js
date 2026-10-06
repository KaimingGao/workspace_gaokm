import { mergeScoringFloors } from "./scoring.js";
import { runMarketContextIngest } from "../macro_context_ui.js";

/** Quant domain: strategy */
export function installStrategy(q) {
  const { els, state, escapeHtml, apiFetch } = q;
  const { factorMetaByName, ensureFactorMeta } = q;

  async function loadSignalConfigPanel() {
    try {
      await ensureFactorMeta();
      renderFactorDict();
      await loadMarketContextPanel();
      await loadMarketPriorForm();
      return null;
    } catch (_) {
      return null;
    }
  }

  function regimeLabel(code) {
    const m = {
      bear: "熊市",
      weak: "弱势",
      neutral: "中性",
      strong: "强势",
      bull: "牛市",
    };
    return m[String(code || "").toLowerCase()] || code || "—";
  }

  function regimeShortLabel(code) {
    const m = { bear: "熊", weak: "弱", neutral: "中", strong: "强", bull: "牛" };
    return m[String(code || "").toLowerCase()] || "—";
  }

  function regimeBenchmarkLabel(code) {
    const raw = String(code || "").toLowerCase();
    if (!raw) return "沪深300";
    if (raw.includes("300") || raw === "hs300" || raw === "csi300") return "沪深300";
    return String(code);
  }

  function regimePositionScale(code, apply) {
    if (apply === false) return 1;
    const c = String(code || "").toLowerCase();
    if (c === "bear" || c === "weak" || c === "weak_trend" || c === "chop") return 0.8;
    if (c === "neutral") return 0.9;
    if (c === "strong" || c === "bull") return 1;
    return 1;
  }

  function regimeFactorHint(code) {
    const c = String(code || "").toLowerCase();
    if (c === "bear" || c === "weak") return "防守权 · 关动量/量价";
    if (c === "bull" || c === "strong") return "趋势权 · 关估值/质量";
    return "全因子环境";
  }

  function regimeAxisPct(roc) {
    const lo = -10;
    const hi = 10;
    if (roc == null || !Number.isFinite(Number(roc))) return 50;
    const t = (Number(roc) - lo) / (hi - lo);
    return Math.max(3, Math.min(97, t * 100));
  }

  function renderRegimeBoard(reg) {
    const body = document.getElementById("strategy-regime-body");
    const pill = document.getElementById("strategy-mctx-regime-pill");
    if (!body) return;
    const r = reg && typeof reg === "object" ? reg : {};
    const code = String(r.regime || "").toLowerCase();
    const lab = regimeLabel(code);
    const days = Number(r.window_days);
    const windowDays = Number.isFinite(days) && days > 0 ? days : 20;
    const applyScale = r.apply_position_scale !== false;
    const scale = regimePositionScale(code, applyScale);
    const roc = r.index_return_pct;
    const rocTone = toneClass(roc);
    const bench = regimeBenchmarkLabel(r.benchmark);
    const ticks = [
      { k: "bear", lab: "熊" },
      { k: "weak", lab: "弱" },
      { k: "neutral", lab: "中" },
      { k: "strong", lab: "强" },
      { k: "bull", lab: "牛" },
    ];
    const overlay = r.macro_overlay_deferred
      ? "overlay → M"
      : r.macro_overlay_applied
        ? "overlay on"
        : "仓位闸";
    const overlayTip = r.macro_overlay_deferred
      ? "科技拖累已交给跨市场 prior，避免双重惩罚"
      : r.macro_overlay_applied
        ? "Regime 叠加了海外科技拖累"
        : "弱/熊压单票与行业上限；不进 ŷ";
    const reason = r.reason ? String(r.reason) : "";
    const vol =
      r.volatility_pct != null && Number.isFinite(Number(r.volatility_pct))
        ? Number(r.volatility_pct)
        : null;
    const penalty =
      r.score_penalty != null && Number(r.score_penalty) > 0
        ? Number(r.score_penalty)
        : null;
    const known = !!code;
    const envHint = regimeFactorHint(code);
    const envMetric = envHint.split(" · ")[0] || "—";
    const envSub = [
      !applyScale ? "仓位缩放已关" : null,
      penalty != null && penalty >= 0.05 ? `启发式罚 ${penalty.toFixed(1)}` : null,
      vol != null ? `近5日波动 ${vol.toFixed(2)}%` : null,
      r.blend_blended ? "边界软插值" : null,
    ]
      .filter(Boolean)
      .join(" · ");
    if (pill) {
      pill.textContent = known
        ? `Regime · ${regimeShortLabel(code)} · ${bench} ${windowDays}D`
        : "Regime —";
    }
    const axisPct = regimeAxisPct(roc);
    const rocText = fmtPct(roc);
    const needleTitle = roc == null ? "无基准收益" : `${windowDays}D ROC ${rocText}`;
    const needleSide = axisPct < 18 ? "is-start" : axisPct > 82 ? "is-end" : "";
    body.innerHTML =
      `<div class="strategy-regime-axis" data-regime="${escapeHtml(code || "na")}" role="img" aria-label="${escapeHtml(
        needleTitle
      )}">` +
      `<div class="strategy-regime-axis-track">` +
      ticks
        .map(
          (t) =>
            `<span class="strategy-regime-seg ${escapeHtml(t.k)}${
              t.k === code ? " is-current" : ""
            }"></span>`
        )
        .join("") +
      `<span class="strategy-regime-needle ${needleSide}" style="left:${axisPct.toFixed(
        2
      )}%" title="${escapeHtml(needleTitle)}">` +
      `<span class="strategy-regime-needle-val ${rocTone}">${escapeHtml(rocText)}</span>` +
      `</span>` +
      `</div>` +
      `<ol class="strategy-regime-spectrum" aria-label="Regime 五档">` +
      ticks
        .map((t) => {
          const on = t.k === code;
          return (
            `<li class="${escapeHtml(t.k)}${on ? " is-current" : ""}"` +
            (on ? ` aria-current="true"` : "") +
            `>${escapeHtml(t.lab)}</li>`
          );
        })
        .join("") +
      `</ol>` +
      `<div class="strategy-regime-scale" aria-hidden="true">` +
      `<span>−10%</span><span>0</span><span>+10%</span>` +
      `</div>` +
      `</div>` +
      `<p class="strategy-regime-reason${reason ? "" : " is-muted"}" title="${escapeHtml(
        reason || `现价相对 ${windowDays} 日前收盘，不是均线多空`
      )}">${escapeHtml(reason || `现价相对 ${windowDays} 日前收盘，不是均线多空`)}</p>` +
      `<div class="strategy-mctx-strip strategy-regime-metrics">` +
      `<div class="strategy-mctx-cell ${known ? `is-${code}` : ""}">` +
      `<div class="strategy-mctx-cell-top">` +
      `<span class="strategy-mctx-label">状态</span>` +
      `<span class="strategy-mctx-badge ${
        code === "bear" || code === "weak" ? "is-fire" : known ? "is-armed" : "is-idle"
      }">${escapeHtml(known ? lab : "未评估")}</span>` +
      `</div>` +
      `<span class="strategy-mctx-metric">${escapeHtml(known ? lab : "—")}</span>` +
      `<span class="strategy-mctx-sub" title="${escapeHtml(overlayTip)}">${escapeHtml(
        [code || "—", overlay].filter(Boolean).join(" · ")
      )}</span>` +
      `</div>` +
      `<div class="strategy-mctx-cell">` +
      `<div class="strategy-mctx-cell-top">` +
      `<span class="strategy-mctx-label">${windowDays}D ROC</span>` +
      `</div>` +
      `<span class="strategy-mctx-metric ${rocTone}">${escapeHtml(rocText)}</span>` +
      `<span class="strategy-mctx-sub">相对${windowDays}日前收盘 · ${escapeHtml(bench)}</span>` +
      `</div>` +
      `<div class="strategy-mctx-cell">` +
      `<div class="strategy-mctx-cell-top">` +
      `<span class="strategy-mctx-label">仓位上限</span>` +
      `</div>` +
      `<span class="strategy-mctx-metric">×${scale.toFixed(2)}</span>` +
      `<span class="strategy-mctx-sub">${applyScale ? "单票/行业同乘" : "缩放关闭"} · 不进 ŷ</span>` +
      `</div>` +
      `<div class="strategy-mctx-cell">` +
      `<div class="strategy-mctx-cell-top">` +
      `<span class="strategy-mctx-label">因子环境</span>` +
      `</div>` +
      `<span class="strategy-mctx-metric">${escapeHtml(envMetric)}</span>` +
      `<span class="strategy-mctx-sub" title="${escapeHtml(envHint)}">${escapeHtml(
        envSub || "查表，非模型"
      )}</span>` +
      `</div>` +
      `</div>`;
  }

  function fmtPct(v) {
    if (v == null || !Number.isFinite(Number(v))) return "—";
    const n = Number(v);
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  }

  function toneClass(v) {
    if (v == null || !Number.isFinite(Number(v))) return "";
    const n = Number(v);
    if (n > 0.05) return "is-up";
    if (n < -0.05) return "is-down";
    return "is-flat";
  }

  function ageLabel(parts) {
    const ages = ["macro", "market_sentiment", "announcement"]
      .map((k) => (parts && parts[k] && parts[k].age_hours != null ? Number(parts[k].age_hours) : null))
      .filter((x) => x != null && Number.isFinite(x));
    if (!ages.length) return null;
    const h = Math.min(...ages);
    if (h < 1) return `${Math.round(h * 60)}m`;
    if (h < 24) return `${h.toFixed(1)}h`;
    return `${(h / 24).toFixed(1)}d`;
  }

  function paintMctxSpark(canvas, data) {
    if (!canvas || !data || data.length < 2) {
      if (canvas) canvas.hidden = true;
      return;
    }
    const sctx = canvas.getContext("2d");
    if (!sctx) return;
    canvas.hidden = false;
    const w = canvas.width;
    const h = canvas.height;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const step = w / (data.length - 1);
    const last = data[data.length - 1];
    const root = document.documentElement;
    const up = getComputedStyle(root).getPropertyValue("--color-up").trim() || "#f5222d";
    const down = getComputedStyle(root).getPropertyValue("--color-down").trim() || "#52c41a";
    sctx.clearRect(0, 0, w, h);
    sctx.beginPath();
    data.forEach((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      if (i === 0) sctx.moveTo(x, y);
      else sctx.lineTo(x, y);
    });
    sctx.strokeStyle = last >= 0 ? up : down;
    sctx.lineWidth = 1.4;
    sctx.stroke();
  }

  function paintMctxBox(c) {
    const unit = document.querySelector(`.strategy-mctx-unit[data-k="${c.k}"]`);
    if (!unit) return;
    unit.classList.toggle("is-on", !!c.on);
    unit.classList.toggle("is-armed", !c.on && !!c.neutral);
    if (c.tip) unit.setAttribute("title", c.tip);
    const note = unit.querySelector(".strategy-mctx-block-note");
    if (!note) return;
    const live = c.on ? `触发 · ${c.metric}` : c.metric || "—";
    note.textContent = c.sub && c.sub !== "—" ? `${live} · ${c.sub}` : live;
    note.classList.remove("is-up", "is-down", "is-flat");
    if (c.metricTone) note.classList.add(c.metricTone);
  }

  function paintMctxEmpty(msg) {
    document.querySelectorAll(".strategy-mctx-unit").forEach((unit) => {
      unit.classList.remove("is-on", "is-armed");
      const note = unit.querySelector(".strategy-mctx-block-note");
      if (!note) return;
      note.textContent = msg || "—";
      note.classList.remove("is-up", "is-down", "is-flat");
    });
  }

  function setMctxHeadStatus(text, { busy = false, warn = false } = {}) {
    const head = document.getElementById("strategy-mctx-head-status");
    if (!head) return;
    head.textContent = text || "";
    head.classList.toggle("is-busy", !!busy);
    head.classList.toggle("is-warn", !!warn && !busy);
    if (busy) head.setAttribute("aria-busy", "true");
    else head.removeAttribute("aria-busy");
  }

  function setMctxMeta(chips) {
    const st = document.getElementById("strategy-mctx-status");
    if (!st) return;
    const list = (chips || []).filter((c) => c && c.t);
    if (!list.length) {
      st.hidden = true;
      st.classList.remove("is-busy");
      st.innerHTML = "";
      return;
    }
    st.hidden = false;
    st.innerHTML = list
      .map(
        (c) =>
          `<span class="strategy-mctx-chip ${c.cls || ""}">${escapeHtml(c.t)}</span>`
      )
      .join("");
  }

  async function loadMarketContextPanel() {
    if (!document.querySelector(".strategy-mctx-unit")) return;
    const spark = document.getElementById("strategy-mctx-spark");
    try {
      const [ctxRes, priorRes] = await Promise.all([
        apiFetch("/api/dashboard/market-context"),
        fetch("/api/signal/config/market-prior").then(async (res) => {
          const data = await res.json().catch(() => ({}));
          return res.ok ? data : {};
        }),
      ]);
      const ctx = ctxRes.ok !== false && ctxRes.data ? ctxRes.data : null;
      if (!ctx || ctx.ok === false) {
        paintMctxEmpty("盘前上下文未就绪");
        renderRegimeBoard({});
        setMctxMeta([]);
        setMctxHeadStatus("无快照", { warn: true });
        if (spark) spark.hidden = true;
        return;
      }
      const mp = priorRes.market_prior || {};
      const cmCfg = mp.cross_market || {};
      const mspCfg = mp.market_sentiment_prior || {};
      const regCfg = mp.regulatory_prior || {};
      const ipoCfg = mp.ipo_drain_prior || {};
      const flags = ctx.prior_flags || {};
      const reg = ctx.regime || {};
      const macro = ctx.macro || {};
      const sent = ctx.market_sentiment || {};
      const fresh = ctx.freshness || {};
      const macroErrHint = (ctx.macro_errors || macro.errors || []).slice(0, 3).join(" · ");
      const macroBlocking =
        !!ctx.macro_degraded || macro.overseas_tech_1d_pct == null;

      const activeCount = [
        flags.cross_market,
        flags.market_sentiment,
        flags.regulatory,
        flags.ipo_drain,
      ].filter(Boolean).length;
      const bits = [];
      const age = ageLabel(fresh.parts);

      if (fresh.needs_ingest || ctx.macro_degraded) {
        bits.push(ctx.macro_degraded ? "macro 降级" : "需刷新");
      } else if (ctx.data_ready === false) {
        bits.push("部分空");
      } else {
        bits.push(age ? `新鲜 ${age}` : "新鲜");
      }
      if (macroBlocking) bits.push("跨市场空");
      if (activeCount > 0) bits.push(`触发 ${activeCount}/4`);
      setMctxHeadStatus(bits.join(" · "), {
        warn: !!(fresh.needs_ingest || ctx.macro_degraded || macroBlocking),
      });

      paintMctxSpark(spark, ctx.macro_sparkline || []);
      renderRegimeBoard(reg);

      const cards = [
        {
          k: "cross_market",
          label: "跨市场",
          on: flags.cross_market,
          neutral: !flags.cross_market && String(cmCfg.mode || "off") !== "off",
          tip:
            "海外科技/A50 等隔夜冲击。触发后由 cross_market prior 在调仓时缩仓或警告；不改 ŷ。overlay→M 时科技拖累优先走此路。",
          metric:
            macro.overseas_tech_1d_pct != null
              ? fmtPct(macro.overseas_tech_1d_pct)
              : "—",
          metricTone: toneClass(macro.overseas_tech_1d_pct),
          sub:
            macro.a50_1d_pct != null
              ? `A50 ${fmtPct(macro.a50_1d_pct)}`
              : macroBlocking
                ? macroErrHint || "macro 未就绪"
                : "海外科技 1D",
        },
        {
          k: "sentiment",
          label: "情绪",
          on: flags.market_sentiment,
          neutral: !flags.market_sentiment && String(mspCfg.mode || "off") !== "off",
          tip:
            "炸板率 = 涨停后开板的比例。偏高说明跟风盘脆弱，market_sentiment_prior 可在调仓缩仓；不改 ŷ。",
          metric:
            sent.broken_limit_rate != null
              ? `${(Number(sent.broken_limit_rate) * 100).toFixed(0)}%`
              : sent.sentiment_cycle_score != null
                ? String(sent.sentiment_cycle_score)
                : "—",
          metricTone: "",
          sub:
            sent.broken_limit_rate != null
              ? flags.market_sentiment
                ? "涨停后开板 · 偏高"
                : "涨停后开板占比"
              : sent.sentiment_cycle_score != null
                ? "周期分"
                : "—",
        },
        {
          k: "regulatory",
          label: "监管",
          on: flags.regulatory,
          neutral: !flags.regulatory && String(regCfg.mode || "off") !== "off",
          tip:
            "监管/处罚公告与概念映射。命中时 regulatory_prior 可警告或约束开仓；不改 ŷ。",
          metric: flags.regulatory ? "命中" : "清静",
          metricTone: flags.regulatory ? "is-down" : "",
          sub:
            (ctx.announcement && ctx.announcement.penalty_concepts || [])
              .slice(0, 2)
              .join(" · ") || "无处罚概念",
        },
        {
          k: "ipo",
          label: "IPO",
          on: flags.ipo_drain,
          neutral: !flags.ipo_drain && String(ipoCfg.mode || "off") !== "off",
          tip:
            "新股对流动性的虹吸（比值/只数）。极端日由 ipo_drain_prior 参与调仓缩仓；不改 ŷ。",
          metric:
            ctx.announcement &&
            ctx.announcement.ipo &&
            ctx.announcement.ipo.liquidity_drain_ratio != null
              ? `${Number(ctx.announcement.ipo.liquidity_drain_ratio).toFixed(1)}×`
              : ctx.announcement &&
                  ctx.announcement.ipo &&
                  ctx.announcement.ipo.ipo_today_count != null
                ? `${ctx.announcement.ipo.ipo_today_count}`
                : "—",
          metricTone: "",
          sub:
            ctx.announcement &&
            ctx.announcement.ipo &&
            ctx.announcement.ipo.liquidity_drain_ratio != null
              ? "虹吸比"
              : ctx.announcement &&
                  ctx.announcement.ipo &&
                  ctx.announcement.ipo.ipo_today_count != null
                ? "今日新股"
                : "—",
        },
      ];
      cards.forEach(paintMctxBox);

      const chips = [];
      if (reg.macro_overlay_deferred) chips.push({ t: "overlay→跨市场" });
      setMctxMeta(chips);
    } catch (err) {
      paintMctxEmpty(String(err.message || err));
      renderRegimeBoard({});
      setMctxMeta([]);
      setMctxHeadStatus(String(err.message || err), { warn: true });
    }
  }

  async function refreshMarketContextPanel() {
    const btn = document.getElementById("strategy-mctx-refresh");
    const head = document.getElementById("strategy-mctx-head-status");
    if (btn) btn.disabled = true;
    setMctxHeadStatus((head && head.textContent) || "", { busy: true });
    try {
      const res = await runMarketContextIngest(apiFetch, { backfill: true });
      if (res && res.ok === false) {
        setMctxHeadStatus(res.error || "ingest 失败", { warn: true });
        return;
      }
      await loadMarketContextPanel();
    } catch (err) {
      setMctxHeadStatus(String(err.message || err), { warn: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function selectMode(id, fallback) {
    const el = document.getElementById(id);
    const v = el && el.value != null ? String(el.value) : "";
    return v || fallback || "off";
  }

  function setSelectMode(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = String(value || "off");
  }

  function syncGateOpts(optsId, modeId) {
    const opts = document.getElementById(optsId);
    if (opts) opts.hidden = selectMode(modeId) !== "gate";
  }

  function syncMctxGateOptsVisibility() {
    syncGateOpts("strategy-mctx-gate-opts", "strategy-mctx-mode");
  }

  function syncMspGateOptsVisibility() {
    syncGateOpts("strategy-msp-gate-opts", "strategy-msp-mode");
  }

  function syncRegGateOptsVisibility() {
    syncGateOpts("strategy-reg-gate-opts", "strategy-reg-mode");
  }

  function syncIpoGateOptsVisibility() {
    syncGateOpts("strategy-ipo-gate-opts", "strategy-ipo-mode");
  }

  function fillMarketPriorForm(mp) {
    const cm = (mp && mp.cross_market) || {};
    const msp = (mp && mp.market_sentiment_prior) || {};
    const reg = (mp && mp.regulatory_prior) || {};
    const ipo = (mp && mp.ipo_drain_prior) || {};
    const policy = (mp && mp.market_prior_policy) || {};
    setSelectMode("strategy-mctx-mode", cm.mode || "off");
    setSelectMode("strategy-msp-mode", msp.mode || "off");
    setSelectMode("strategy-reg-mode", reg.mode || "off");
    setSelectMode("strategy-ipo-mode", ipo.mode || "off");
    const trig = document.getElementById("strategy-mctx-tech-trigger");
    if (trig && cm.tech_drag_trigger_pct != null) {
      trig.value = String(cm.tech_drag_trigger_pct);
    }
    const scaleEl = document.getElementById("strategy-mctx-scale");
    if (scaleEl && cm.scale_buy_pct != null) {
      scaleEl.value = String(Math.round(Number(cm.scale_buy_pct) * 100));
    }
    const holdsEl = document.getElementById("strategy-mctx-scale-holds");
    if (holdsEl) holdsEl.checked = !!cm.scale_holds;
    const mspScaleEl = document.getElementById("strategy-msp-scale");
    if (mspScaleEl && msp.scale_buy_pct != null) {
      mspScaleEl.value = String(Math.round(Number(msp.scale_buy_pct) * 100));
    }
    const mspHoldsEl = document.getElementById("strategy-msp-scale-holds");
    if (mspHoldsEl) mspHoldsEl.checked = !!msp.scale_holds;
    const regScaleEl = document.getElementById("strategy-reg-scale");
    if (regScaleEl && reg.scale_buy_pct != null) {
      regScaleEl.value = String(Math.round(Number(reg.scale_buy_pct) * 100));
    }
    const ipoScaleEl = document.getElementById("strategy-ipo-scale");
    if (ipoScaleEl && ipo.scale_buy_pct != null) {
      ipoScaleEl.value = String(Math.round(Number(ipo.scale_buy_pct) * 100));
    }
    const ipoRatioEl = document.getElementById("strategy-ipo-drain-ratio");
    if (ipoRatioEl && ipo.drain_ratio_high != null) {
      ipoRatioEl.value = String(ipo.drain_ratio_high);
    }
    const mergeEl = document.getElementById("strategy-mctx-merge");
    if (mergeEl) mergeEl.value = String(policy.merge_mode || "min_scale");
    syncMctxGateOptsVisibility();
    syncMspGateOptsVisibility();
    syncRegGateOptsVisibility();
    syncIpoGateOptsVisibility();
  }

  async function loadMarketPriorForm() {
    const st = document.getElementById("strategy-mctx-save-status");
    try {
      const res = await fetch("/api/signal/config/market-prior");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      fillMarketPriorForm(data.market_prior || {});
    } catch (err) {
      if (st) st.textContent = String(err.message || err);
    }
  }

  async function saveStrategyMarketPrior() {
    const st = document.getElementById("strategy-mctx-save-status");
    const mode = selectMode("strategy-mctx-mode");
    const mspMode = selectMode("strategy-msp-mode");
    const trigEl = document.getElementById("strategy-mctx-tech-trigger");
    const scaleEl = document.getElementById("strategy-mctx-scale");
    const holdsEl = document.getElementById("strategy-mctx-scale-holds");
    const mergeEl = document.getElementById("strategy-mctx-merge");
    const mspScaleEl = document.getElementById("strategy-msp-scale");
    const mspHoldsEl = document.getElementById("strategy-msp-scale-holds");
    const regMode = selectMode("strategy-reg-mode");
    const ipoMode = selectMode("strategy-ipo-mode");
    const regScaleEl = document.getElementById("strategy-reg-scale");
    const ipoScaleEl = document.getElementById("strategy-ipo-scale");
    const ipoRatioEl = document.getElementById("strategy-ipo-drain-ratio");
    let scalePct = scaleEl && scaleEl.value !== "" ? Number(scaleEl.value) : 50;
    if (!Number.isFinite(scalePct)) scalePct = 50;
    const scale = Math.max(0, Math.min(scalePct, 100)) / 100;
    let techTrig =
      trigEl && trigEl.value !== "" ? Number(trigEl.value) : -1.5;
    if (!Number.isFinite(techTrig)) techTrig = -1.5;
    const mergeMode = mergeEl ? String(mergeEl.value || "min_scale") : "min_scale";
    let mspScalePct =
      mspScaleEl && mspScaleEl.value !== "" ? Number(mspScaleEl.value) : 55;
    if (!Number.isFinite(mspScalePct)) mspScalePct = 55;
    const mspScale = Math.max(0, Math.min(mspScalePct, 100)) / 100;
    let regScalePct =
      regScaleEl && regScaleEl.value !== "" ? Number(regScaleEl.value) : 50;
    if (!Number.isFinite(regScalePct)) regScalePct = 50;
    const regScale = Math.max(0, Math.min(regScalePct, 100)) / 100;
    let ipoScalePct =
      ipoScaleEl && ipoScaleEl.value !== "" ? Number(ipoScaleEl.value) : 45;
    if (!Number.isFinite(ipoScalePct)) ipoScalePct = 45;
    const ipoScale = Math.max(0, Math.min(ipoScalePct, 100)) / 100;
    let ipoRatio =
      ipoRatioEl && ipoRatioEl.value !== "" ? Number(ipoRatioEl.value) : 3.0;
    if (!Number.isFinite(ipoRatio)) ipoRatio = 3.0;
    if (
      !window.confirm(
        [
          "保存 M 层 prior（4 路）？",
          "",
          `· 跨市场：${priorModeLabel(mode)}（${mode}）`,
          mode === "gate"
            ? `· 科技拖累 ≤ ${techTrig}% · 缩仓 ${Math.round(scale * 100)}%`
            : "",
          `· 情绪周期：${priorModeLabel(mspMode)}（${mspMode}）`,
          mspMode === "gate"
            ? `· 缩仓 ${Math.round(mspScale * 100)}%${
                mspHoldsEl && mspHoldsEl.checked ? " · 持仓同步" : ""
              }`
            : "",
          `· 监管：${priorModeLabel(regMode)}（${regMode}）`,
          regMode === "gate" ? `· 缩仓 ${Math.round(regScale * 100)}%` : "",
          `· IPO：${priorModeLabel(ipoMode)}（${ipoMode}）`,
          ipoMode === "gate"
            ? `· 虹吸 ≥ ${ipoRatio}x · 缩仓 ${Math.round(ipoScale * 100)}%`
            : "",
          `· 合并：${mergeMode}`,
          "",
          "契约：不改 predicted_score（ŷ）；不改 weights。",
        ]
          .filter(Boolean)
          .join("\n")
      )
    ) {
      return;
    }
    if (st) st.textContent = "正在保存…";
    try {
      const body = {
        cross_market_mode: mode,
        market_sentiment_mode: mspMode,
        regulatory_mode: regMode,
        ipo_drain_mode: ipoMode,
        merge_mode: mergeMode,
        note: "策略中心人审·市场 prior",
      };
      if (mode === "gate") {
        body.tech_drag_trigger_pct = techTrig;
        body.scale_buy_pct = scale;
        body.scale_holds = !!(holdsEl && holdsEl.checked);
      }
      if (mspMode === "gate") {
        body.market_sentiment_scale_buy_pct = mspScale;
        body.market_sentiment_scale_holds = !!(mspHoldsEl && mspHoldsEl.checked);
      }
      if (regMode === "gate") {
        body.regulatory_scale_buy_pct = regScale;
      }
      if (ipoMode === "gate") {
        body.ipo_drain_scale_buy_pct = ipoScale;
        body.ipo_drain_ratio_high = ipoRatio;
      }
      const res = await fetch("/api/signal/config/market-prior", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || data.success === false) {
        if (st) st.textContent = data.detail || data.error || "保存失败";
        return;
      }
      fillMarketPriorForm(data.market_prior || {});
      if (st) {
        st.textContent = "已保存";
      }
      loadMarketContextPanel().catch(() => {});
      loadStrategyList().catch(() => {});
    } catch (err) {
      if (st) st.textContent = String(err.message || err);
    }
  }

  function priorModeLabel(mode) {
    if (mode === "risk") return "仅警告";
    if (mode === "gate") return "约束开仓";
    return "关闭";
  }

  async function loadStrategyList() {
    try {
      const res = await fetch("/api/quant/strategies");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderStrategyList(data);
    } catch (err) {
      const st = document.getElementById("strategy-meta");
      if (st) st.textContent = `策略配置加载失败：${err.message || err}`;
    }
  }

  function renderFactorDict() {
    const list = document.getElementById("strategy-factor-dict-list");
    if (!list) return;
    const factors = Object.values(factorMetaByName || {});
    if (!factors.length) {
      list.innerHTML = `<p class="results-empty">暂无因子元数据</p>`;
      return;
    }
    list.innerHTML = factors
      .map((f) => {
        const name = f.name || "";
        const label = f.label || name;
        const tip = f.description || label;
        return (
          `<button type="button" class="strategy-factor-chip factor-tip" data-factor="${escapeHtml(name)}" ` +
          `title="${escapeHtml(tip)}">${escapeHtml(label)}</button>`
        );
      })
      .join("");
  }

  function renderStrategyList(data) {
    if (!data || !data.success || !data.strategies) return;
    // 观察页直方图只读滞回门槛（本页不再提供编辑）
    const floors = data.scoring_floors || {};
    if (floors.min_predicted_score != null || floors.min_hold_predicted_score != null) {
      state.quantScoringFloors = mergeScoringFloors(state.quantScoringFloors, floors);
      state._scoringFloorsHydrated = true;
    }
  }

  return {
    loadSignalConfigPanel,
    loadStrategyList,
    renderFactorDict,
    renderStrategyList,
    saveStrategyMarketPrior,
    loadMarketPriorForm,
    loadMarketContextPanel,
    refreshMarketContextPanel,
    syncMctxGateOptsVisibility,
    syncMspGateOptsVisibility,
    syncRegGateOptsVisibility,
    syncIpoGateOptsVisibility,
  };
}
