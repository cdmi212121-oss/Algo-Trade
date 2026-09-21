const PAGE_SIZE = 10;
let allTrades = [];
let currentPage = 1;

function renderPage() {
  const wrap = document.getElementById("trades-table-wrap");
  const empty = document.getElementById("trades-empty");

  if (!allTrades.length) {
    wrap.hidden = true;
    empty.hidden = false;
    document.getElementById("total-arrivals").textContent = "";
    document.getElementById("page-buttons").innerHTML = "";
    return;
  }
  wrap.hidden = false;
  empty.hidden = true;

  const totalPages = Math.ceil(allTrades.length / PAGE_SIZE);
  currentPage = Math.min(currentPage, totalPages);
  const start = (currentPage - 1) * PAGE_SIZE;
  const pageTrades = allTrades.slice(start, start + PAGE_SIZE);

  const tbody = document.querySelector("#trades-table tbody");
  tbody.innerHTML = pageTrades.map((t) => {
    const pnl = parseFloat(t.pnl);
    const win = pnl >= 0;
    return `<tr>
      <td>${t.trade_id || ""}</td>
      <td>${t.symbol || ""}</td>
      <td>${(t.exit_time || "").split(" ")[0] || (t.exit_time || "").split("T")[0] || ""}</td>
      <td>${t.entry_price || ""}</td>
      <td>${t.exit_price || ""}</td>
      <td><span class="badge ${win ? "badge-good" : "badge-critical"}">${win ? "Win" : "Loss"}</span></td>
      <td class="${win ? "pnl-up" : "pnl-down"}">${fmtNumber(pnl)}</td>
      <td><span class="badge badge-good">Compliant</span></td>
      <td class="muted">-</td>
    </tr>`;
  }).join("");

  document.getElementById("total-arrivals").textContent = `Total Arrivals: ${allTrades.length}`;

  const buttons = [];
  buttons.push(`<button class="btn btn-sm" id="page-prev" ${currentPage === 1 ? "disabled" : ""}>← Previous</button>`);
  for (let p = 1; p <= totalPages; p++) {
    if (p === 1 || p === totalPages || Math.abs(p - currentPage) <= 1) {
      buttons.push(`<button class="btn btn-sm ${p === currentPage ? "btn-primary" : ""}" data-page="${p}">${p}</button>`);
    } else if (p === 2 || p === totalPages - 1) {
      buttons.push(`<span class="muted" style="padding:5px 4px;">&hellip;</span>`);
    }
  }
  buttons.push(`<button class="btn btn-sm" id="page-next" ${currentPage === totalPages ? "disabled" : ""}>Next →</button>`);
  document.getElementById("page-buttons").innerHTML = buttons.join("");

  const prev = document.getElementById("page-prev");
  const next = document.getElementById("page-next");
  if (prev) prev.addEventListener("click", () => { currentPage--; renderPage(); });
  if (next) next.addEventListener("click", () => { currentPage++; renderPage(); });
  document.querySelectorAll("#page-buttons button[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => { currentPage = parseInt(btn.dataset.page, 10); renderPage(); });
  });
}

async function loadTrades() {
  const data = await getJSON("/api/trades");
  allTrades = data.trades;
  renderPage();
}

loadTrades();
setInterval(loadTrades, 15000);
