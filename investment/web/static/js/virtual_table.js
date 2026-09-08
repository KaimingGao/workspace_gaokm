/**
 * 四页主表共享内核：纯 DOM 虚拟滚动。
 * 不依赖 React / TanStack CDN（避免 403 空白表）。
 *
 * R5.1 / 性能预算：目标支撑 ≥500 行交互不掉帧（仅渲染可视区 + overscan）。
 * 观察/持仓岛默认始终虚拟化；HTML 分页表仅作岛失败回退。
 */

export const VIRTUAL_TABLE_ROW_BUDGET = 500;

export function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function truncateName(name, max = 6) {
  const full = String(name || "").trim();
  const chars = Array.from(full);
  if (chars.length <= max) return full;
  return `${chars.slice(0, max).join("")}…`;
}

export function colStyle(col) {
  // 兼容旧调用；行级已改用 grid-template-columns，单元格需可读下限，防窄屏挤成「空」
  if (col.flex) {
    const min = col.flexMin != null ? String(col.flexMin) : "0";
    return `min-width:${min};`;
  }
  if (col.widthPct != null) {
    const min = col.widthMin != null ? String(col.widthMin) : "3.1rem";
    return `min-width:${min};`;
  }
  if (col.width != null) {
    const w = Number(col.width);
    return `min-width:${w}px;width:${w}px;`;
  }
  return "min-width:0;";
}

/** 表头/表体共用同一套轨道，避免 flex + max-width 导致列错位 */
export function gridTemplateColumns(columns, { shrink = false } = {}) {
  return (columns || [])
    .map((col) => {
      if (col.flex) {
        // flexMin：因子+徽章等需要更宽下限，避免被 overflow 裁切
        const min = shrink
          ? "0"
          : col.flexMin != null
            ? String(col.flexMin)
            : "6.5rem";
        const fr =
          col.flexFr != null && Number(col.flexFr) > 0
            ? Number(col.flexFr)
            : 1.35;
        return `minmax(${min}, ${fr}fr)`;
      }
      // widthPct 作相对权重（fr），并设 rem 下限，避免 % 轨在窄容器里塌成 0
      if (col.widthPct != null) {
        const w = Math.max(2, Number(col.widthPct) || 8);
        const min = shrink
          ? "0"
          : col.widthMin != null
            ? String(col.widthMin)
            : "3.1rem";
        return `minmax(${min}, ${w}fr)`;
      }
      if (col.width != null) return `${Number(col.width)}px`;
      return shrink ? "minmax(0, 1fr)" : "minmax(3.1rem, 1fr)";
    })
    .join(" ");
}

function makeEmitter() {
  const map = new Map();
  return {
    on(name, fn) {
      const arr = map.get(name) || [];
      arr.push(fn);
      map.set(name, arr);
    },
    emit(name, payload) {
      (map.get(name) || []).forEach((fn) => {
        try {
          fn(payload);
        } catch (_) {
          /* ignore */
        }
      });
    },
  };
}

function defaultCompare(id, a, b) {
  const av = a?.[id];
  const bv = b?.[id];
  const an = Number(av);
  const bn = Number(bv);
  if (Number.isFinite(an) && Number.isFinite(bn)) return an - bn;
  return String(av ?? "").localeCompare(String(bv ?? ""), "zh-CN", { numeric: true });
}

/**
 * @param {HTMLElement} host
 * @param {{
 *   columns: Array<object>,
 *   emptyText?: string,
 *   rowHeight?: number,
 *   rootClass?: string,
 *   bodyClass?: string,
 *   initialSort?: Array<{column:string, dir:string}>,
 *   compare?: (id:string, a:object, b:object) => number,
 *   rowClass?: (d:object) => string,
 *   rowAttrs?: (d:object) => Record<string, string|number|boolean|null|undefined>,
 *   headHtml?: (col:object, ctx:object) => string,
 *   cellHtml: (col:object, d:object) => string,
 * }} options
 */
export function mountVirtualTable(host, options = {}) {
  if (!host) throw new Error("virtual table host missing");
  const columns = Array.isArray(options.columns) ? options.columns : [];
  if (!columns.length) throw new Error("virtual table columns required");
  if (typeof options.cellHtml !== "function") throw new Error("cellHtml required");

  const emptyText = options.emptyText || "暂无数据";
  const rowHeight = Number(options.rowHeight) > 0 ? Number(options.rowHeight) : 40;
  const overscan = 8;
  const compareFn = typeof options.compare === "function" ? options.compare : defaultCompare;
  const emitter = makeEmitter();
  const rowMap = new Map();
  let rowOrder = [];
  let sortState = [];
  const init = Array.isArray(options.initialSort) ? options.initialSort : [];
  if (init.length && init[0].column) {
    sortState = [{ id: init[0].column, desc: init[0].dir !== "asc" }];
  }

  const rootClass = options.rootClass || "watching-react-grid";
  const bodyClass = options.bodyClass
    ? `watching-react-grid-body ${options.bodyClass}`
    : "watching-react-grid-body";
  const fitHost = options.fit === "host";
  const gridCols = gridTemplateColumns(columns, { shrink: fitHost });
  const rowTrackStyle = fitHost
    ? `display:grid;grid-template-columns:${gridCols};align-items:center;width:100%;min-width:0;box-sizing:border-box;`
    : `display:grid;grid-template-columns:${gridCols};align-items:center;width:max-content;min-width:100%;box-sizing:border-box;`;

  host.innerHTML =
    `<div class="${escapeHtml(rootClass)}">` +
    `<div class="watching-react-grid-head"></div>` +
    `<div class="${escapeHtml(bodyClass)}"></div>` +
    `</div>`;
  const headEl = host.querySelector(".watching-react-grid-head");
  const bodyEl = host.querySelector(".watching-react-grid-body");

  function snapshot() {
    return {
      rows: rowOrder.map((code) => rowMap.get(code)).filter(Boolean),
      sortState,
    };
  }

  function sortedRows() {
    const rows = snapshot().rows;
    if (!sortState.length) return rows.slice();
    const s = sortState[0];
    const dir = s.desc ? -1 : 1;
    const out = rows.slice();
    out.sort((a, b) => {
      let cmp = compareFn(s.id, a, b);
      if (!Number.isFinite(cmp)) cmp = 0;
      if (cmp !== 0) return cmp * dir;
      return String(a.code || "").localeCompare(String(b.code || ""), "zh-CN", {
        numeric: true,
      });
    });
    return out;
  }

  function syncHeadScrollbarGutter() {
    // 表体出现纵向滚动条时，给表头补同等右内边距，避免列与表头错位
    const sb = Math.max(0, (bodyEl.offsetWidth || 0) - (bodyEl.clientWidth || 0));
    headEl.style.paddingRight = sb > 0 ? `${sb}px` : "";
  }

  function paintHead() {
    const s = sortState[0];
    const ctx = { sortState, rows: snapshot().rows };
    headEl.innerHTML =
      `<div class="watching-react-grid-row is-head" style="${rowTrackStyle}">` +
      columns
        .map((col) => {
          const sorted = s && s.id === col.id;
          const arrow = sorted ? (s.desc ? " ↓" : " ↑") : "";
          const extraCls = [
            `watching-col-${col.id}`,
            col.num ? "watching-col-num" : "",
            col.sortable ? "is-sortable" : "",
            col.headClass || "",
            col.flex ? "paper-wl-name-cell" : "",
          ]
            .filter(Boolean)
            .join(" ");
          const inner =
            typeof options.headHtml === "function"
              ? options.headHtml(col, ctx)
              : `${escapeHtml(col.label || "")}${arrow}`;
          const tipParts = [];
          if (col.title) tipParts.push(String(col.title));
          else if (col.label) tipParts.push(String(col.label));
          if (col.sortable) tipParts.push("点击排序");
          const tip = tipParts.join(" · ");
          return (
            `<div class="watching-react-grid-cell${extraCls ? ` ${extraCls}` : ""}" ` +
            `style="${colStyle(col)}" ` +
            (col.sortable
              ? `data-sort="${escapeHtml(col.id)}" role="button" tabindex="0"`
              : "") +
            (tip ? ` title="${escapeHtml(tip)}"` : "") +
            `>${inner}</div>`
          );
        })
        .join("") +
      `</div>`;
    syncHeadScrollbarGutter();
  }

  function attrsHtml(d) {
    const attrs =
      typeof options.rowAttrs === "function" ? options.rowAttrs(d) || {} : { "data-code": d.code };
    return Object.entries(attrs)
      .filter(([, v]) => v != null && v !== false)
      .map(([k, v]) =>
        v === true ? escapeHtml(k) : `${escapeHtml(k)}="${escapeHtml(String(v))}"`
      )
      .join(" ");
  }

  function paintBody() {
    const rows = sortedRows();
    if (!rows.length) {
      bodyEl.innerHTML = `<p class="watching-table-empty">${escapeHtml(emptyText)}</p>`;
      return;
    }
    const total = rows.length * rowHeight;
    const scrollTop = bodyEl.scrollTop || 0;
    const viewH = bodyEl.clientHeight || 400;
    const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
    const end = Math.min(rows.length, Math.ceil((scrollTop + viewH) / rowHeight) + overscan);
    let html = `<div style="height:${total}px;position:relative">`;
    for (let i = start; i < end; i++) {
      const d = rows[i];
      const extra =
        typeof options.rowClass === "function" ? String(options.rowClass(d) || "").trim() : "";
      const cls = ["watching-react-grid-row", extra].filter(Boolean).join(" ");
      html +=
        `<div ${attrsHtml(d)} class="${escapeHtml(cls)}" ` +
        `style="position:absolute;top:0;left:0;transform:translateY(${
          i * rowHeight
        }px);height:${rowHeight}px;${rowTrackStyle}">` +
        columns
          .map((col) => {
            const extraCls = [
              `watching-col-${col.id}`,
              col.num ? "watching-col-num num" : "",
              col.cellClass || "",
              col.flex ? "paper-wl-name-cell" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              `<div class="watching-react-grid-cell${extraCls ? ` ${extraCls}` : ""}" ` +
              `style="${colStyle(col)}">${options.cellHtml(col, d)}</div>`
            );
          })
          .join("") +
        `</div>`;
    }
    html += `</div>`;
    bodyEl.innerHTML = html;
    bodyEl.scrollTop = scrollTop;
    syncHeadScrollbarGutter();
  }

  function publish(kind) {
    paintHead();
    paintBody();
    if (kind === "sort") {
      const s = sortState[0];
      emitter.emit("sortChanged", s ? [{ field: s.id, dir: s.desc ? "desc" : "asc" }] : []);
    } else if (kind === "data") {
      emitter.emit("dataChanged", null);
    }
  }

  function setRows(rows) {
    rowMap.clear();
    rowOrder = [];
    (rows || []).forEach((row) => {
      const code = String(row.code || "").trim();
      if (!code) return;
      rowMap.set(code, { ...row, code });
      rowOrder.push(code);
    });
    publish("data");
  }

  function updateRow(code, patch) {
    const c = String(code || "").trim();
    if (!c || !rowMap.has(c)) return;
    rowMap.set(c, { ...rowMap.get(c), ...patch });
    publish("data");
  }

  /** 批量补丁：只重绘一次，避免 insights 逐行 update 卡顿/闪空 */
  function patchRows(patches) {
    if (!patches || typeof patches !== "object") return;
    let n = 0;
    Object.keys(patches).forEach((code) => {
      const c = String(code || "").trim();
      if (!c || !rowMap.has(c)) return;
      const patch = patches[c];
      if (!patch || typeof patch !== "object") return;
      rowMap.set(c, { ...rowMap.get(c), ...patch });
      n += 1;
    });
    if (n) publish("data");
  }

  headEl.addEventListener("click", (e) => {
    const th = e.target.closest("[data-sort]");
    if (!th || !headEl.contains(th)) return;
    if (e.target.closest("input,button,a,label")) return;
    e.preventDefault();
    const id = th.getAttribute("data-sort");
    const col = columns.find((c) => c.id === id);
    if (!col || !col.sortable) return;
    const cur = sortState[0];
    if (cur && cur.id === id) sortState = [{ id, desc: !cur.desc }];
    else sortState = [{ id, desc: id !== "code" && id !== "name" }];
    publish("sort");
  });

  bodyEl.addEventListener("scroll", () => paintBody(), { passive: true });

  let resizeObs = null;
  if (typeof ResizeObserver !== "undefined") {
    resizeObs = new ResizeObserver(() => syncHeadScrollbarGutter());
    resizeObs.observe(bodyEl);
  } else {
    window.addEventListener("resize", syncHeadScrollbarGutter);
  }

  bodyEl.addEventListener("click", (e) => {
    const row = e.target.closest(".watching-react-grid-row[data-code]");
    if (!row || !bodyEl.contains(row)) return;
    emitter.emit("rowClick", { code: row.dataset.code, event: e, row });
  });

  paintHead();
  paintBody();
  setTimeout(() => emitter.emit("tableBuilt", null), 0);

  return {
    on: emitter.on,
    getData() {
      return snapshot().rows;
    },
    getRow(code) {
      const c = String(code || "").trim();
      if (!c || !rowMap.has(c)) return null;
      return {
        update(patch) {
          updateRow(c, patch);
        },
        getData() {
          return rowMap.get(c) || null;
        },
      };
    },
    setRows,
    patchRows,
    setSort(sorters) {
      const s = Array.isArray(sorters) && sorters.length ? sorters[0] : null;
      sortState = !s || !s.column ? [] : [{ id: s.column, desc: s.dir !== "asc" }];
      publish("sort");
    },
    clearSort() {
      sortState = [];
      publish("sort");
    },
    destroy() {
      if (resizeObs) {
        try {
          resizeObs.disconnect();
        } catch (_) {
          /* ignore */
        }
        resizeObs = null;
      } else {
        window.removeEventListener("resize", syncHeadScrollbarGutter);
      }
      host.innerHTML = "";
    },
    /** 暴露以便页内做全选等需要重绘表头的操作 */
    repaint() {
      publish("data");
    },
  };
}
