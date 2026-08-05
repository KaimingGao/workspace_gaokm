/**
 * 研究台小表网格（与数据中心 watching-react-grid 同壳）。
 */
import { escapeHtml } from "../shared.js";
import { colStyle } from "../virtual_table.js";

export function metricCell(text, cls) {
  return `<span class="bt-trade-ret ${cls || ""}">${text}</span>`;
}

/**
 * @param {Array<{id:string,label?:string,title?:string,num?:boolean,center?:boolean,headClass?:string,cellClass?:string,widthPct?:number,flex?:boolean}>} columns
 * @param {Array<object>} rows
 * @param {(col:object, row:object) => string} [cellHtml]
 * @param {{ emptyText?: string, rowClass?: (row:object) => string }} [opts]
 */
export function researchGridHtml(columns, rows, cellHtml, opts = {}) {
  const cols = Array.isArray(columns) ? columns : [];
  const data = Array.isArray(rows) ? rows : [];
  if (!cols.length) return "";
  const emptyText = opts.emptyText || "暂无数据";
  const rowClassFn = typeof opts.rowClass === "function" ? opts.rowClass : null;
  const head =
    `<div class="watching-react-grid-head"><div class="watching-react-grid-row is-head">` +
    cols
      .map((col) => {
        const extra = [
          col.num ? "watching-col-num" : "",
          col.center ? "watching-col-center" : "",
          col.headClass || "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          `<div class="watching-react-grid-cell${extra ? ` ${extra}` : ""}" ` +
          `style="${colStyle(col)}" title="${escapeHtml(col.title || col.label || "")}">` +
          `${escapeHtml(col.label || "")}</div>`
        );
      })
      .join("") +
    `</div></div>`;
  const body =
    data.length === 0
      ? `<p class="watching-table-empty">${escapeHtml(emptyText)}</p>`
      : data
          .map((d) => {
            const rowExtra = rowClassFn ? rowClassFn(d) : "";
            return (
              `<div class="watching-react-grid-row${rowExtra ? ` ${escapeHtml(rowExtra)}` : ""}">` +
              cols
                .map((col) => {
                  const extra = [
                    col.num ? "watching-col-num num" : "",
                    col.center ? "watching-col-center" : "",
                    col.cellClass || "",
                  ]
                    .filter(Boolean)
                    .join(" ");
                  const inner =
                    typeof cellHtml === "function"
                      ? cellHtml(col, d)
                      : escapeHtml(d[col.id] ?? "—");
                  return (
                    `<div class="watching-react-grid-cell${extra ? ` ${extra}` : ""}" ` +
                    `style="${colStyle(col)}">${inner}</div>`
                  );
                })
                .join("") +
              `</div>`
            );
          })
          .join("");
  return (
    `<div class="watching-react-grid quant-research-grid">` +
    head +
    `<div class="watching-react-grid-body quant-research-grid-body">${body}</div>` +
    `</div>`
  );
}
