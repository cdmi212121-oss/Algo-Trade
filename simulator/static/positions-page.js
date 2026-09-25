function resultBadge(p) {
  if (p.status === "PENDING_ORDER") return `<span class="badge badge-warning">Pending Order</span>`;
  if (p.unrealized_pnl === null || p.unrealized_pnl === undefined) return `<span class="badge badge-neutral">—</span>`;
  if (p.unrealized_pnl < 0) return `<span class="badge badge-critical">At Risk</span>`;
  return `<span class="badge badge-neutral">Neutral</span>`;
}

function actionButton(p) {
  if (p.status === "PENDING_ORDER" && (p.unrealized_pnl === null || p.unrealized_pnl === undefined) && !p.ltp) {
    return `<button class="btn btn-sm btn-outline-critical" data-cancel="${p.symbol}">Cancel Order</button>`;
  }
  if (p.status === "PENDING_ORDER") {
    return `<button class="btn btn-sm btn-outline-critical" data-cancel="${p.symbol}">Cancel Order</button>`;
  }
  return `<button class="btn btn-sm btn-primary" data-exit="${p.symbol}">Exit at Market</button>`;
}

async function loadPositions() {
  const positions = await getJSONWithRetry("/api/positions");
  const wrap = document.getElementById("positions-table-wrap");
  const empty = document.getElementById("positions-empty");

  if (!positions.length) {
    wrap.hidden = true;
    empty.hidden = false;
  } else {
    wrap.hidden = false;
    empty.hidden = true;
  }

  const tbody = document.querySelector("#positions-table tbody");
  tbody.innerHTML = positions.map((p) => {
    const pnlCls = (p.unrealized_pnl ?? 0) >= 0 ? "pnl-up" : "pnl-down";
    return `<tr>
      <td>${p.trade_id}</td>
      <td>${p.symbol}</td>
      <td>${new Date().toLocaleDateString()}</td>
      <td>${p.qty}</td>
      <td>${fmtNumber(p.entry_price)}</td>
      <td>${fmtNumber(p.stop_loss)}</td>
      <td>${fmtNumber(p.target)}</td>
      <td>${fmtNumber(p.ltp)}</td>
      <td>${resultBadge(p)}</td>
      <td class="${p.status === 'PENDING_ORDER' ? '' : pnlCls}">${p.status === 'PENDING_ORDER' ? '00' : fmtNumber(p.unrealized_pnl)}</td>
      <td>${actionButton(p)}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll("button[data-exit]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await postJSON("/api/close_position", { symbol: btn.dataset.exit });
      loadPositions();
    });
  });
  tbody.querySelectorAll("button[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await postJSON("/api/cancel_order", { symbol: btn.dataset.cancel });
      loadPositions();
    });
  });

  const dayPnl = positions.reduce((sum, p) => sum + (p.unrealized_pnl || 0), 0);
  const pill = document.getElementById("day-pnl-pill");
  pill.textContent = `Day P&L ${dayPnl >= 0 ? "+" : ""}${fmtNumber(dayPnl)}`;
  pill.className = "pill " + (dayPnl >= 0 ? "pill-good" : "pill-critical");
}

async function loadOrbStatus() {
  const pill = document.getElementById("orb-status-pill");
  const wrap = document.getElementById("orb-table-wrap");
  const empty = document.getElementById("orb-no-position");
  let data;
  try {
    data = await getJSON("/api/orb_status");
  } catch (e) {
    pill.textContent = "Unavailable";
    pill.className = "pill pill-critical";
    return;
  }

  if (!data.exists) {
    pill.textContent = "No trades today yet";
    pill.className = "pill badge-neutral";
    wrap.hidden = true;
    empty.hidden = false;
    return;
  }

  const p = data.open_position;
  if (!p) {
    pill.textContent = "Flat";
    pill.className = "pill badge-neutral";
    wrap.hidden = true;
    empty.hidden = false;
    return;
  }

  pill.textContent = `Open: ${p.side}`;
  pill.className = "pill pill-good";
  wrap.hidden = false;
  empty.hidden = true;
  document.querySelector("#orb-table tbody").innerHTML = `<tr>
    <td>${p.side}</td><td>${p.symbol}</td><td>${p.qty}</td>
    <td>${fmtNumber(p.entry_price)}</td><td>${p.entry_time}</td>
    <td>${fmtNumber(p.current_sl)}</td><td>${p.target ? fmtNumber(p.target) : "trailing"}</td>
  </tr>`;
}

loadPositions();
loadOrbStatus();
setInterval(loadPositions, 4000);
setInterval(loadOrbStatus, 4000);
