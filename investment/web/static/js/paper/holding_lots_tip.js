/**
 * 持仓股数 · T+1 批次悬浮说明（交易执行持仓表）。
 */

function _num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function _boughtLabel(lot) {
  const raw = lot && (lot.bought_at || lot.bought_date);
  if (!raw) return "—";
  const s = String(raw).trim().replace("Z", "");
  if (s.length >= 19) return s.slice(0, 16).replace("T", " ");
  if (s.length >= 10) return s.slice(0, 10);
  return s;
}

/**
 * @param {object} h 持仓行（summary.holdings 单项）
 * @returns {string} 供 title 使用的多行说明；无批次时返回空串
 */
export function buildHoldingSharesTip(h) {
  if (!h || typeof h !== "object") return "";
  const total = _num(h.shares);
  const sellable = _num(h.sellable_shares);
  const locked = _num(h.locked_shares);
  const lots = Array.isArray(h.lots) ? h.lots.filter((x) => x && _num(x.shares) > 0) : [];
  const lines = [];

  if (total != null) {
    if (locked != null && locked > 0 && sellable != null) {
      lines.push(`总 ${total} 股 · 可卖 ${sellable} · 锁定 ${locked}`);
    } else {
      lines.push(`持仓 ${total} 股`);
    }
  }

  if (lots.length > 0) {
    lines.push("买入批次（FIFO）：");
    for (const lot of lots) {
      const sh = _num(lot.shares);
      if (sh == null || sh <= 0) continue;
      const when = _boughtLabel(lot);
      const status = lot.sellable ? "可卖" : "T+1锁定";
      lines.push(`${sh} 股 · ${when} · ${status}`);
    }
  } else if (h.bought_at || h.bought_date) {
    lines.push(`最早买入 ${_boughtLabel(h)}`);
  }

  if (lines.length <= 1 && !lots.length) return "";
  return lines.join("\n");
}
