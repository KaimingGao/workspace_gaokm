/** Paper ops report UI helpers (W0.1 extract). */

import { formatOpsReportHtml } from "../shared.js";

export function renderOpsReport(ops, { forceShow = false } = {}) {
  const el = document.getElementById("paper-ops-report");
  const section = document.getElementById("paper-rebalance-section");
  if (!el) return;
  if (!ops || typeof ops !== "object") {
    if (!forceShow) {
      el.hidden = true;
      el.innerHTML = "";
    }
    return;
  }
  el.hidden = false;
  el.innerHTML = formatOpsReportHtml(ops);
  window.__paperLastOpsReport = ops;
  if (section && forceShow) section.hidden = false;
}
