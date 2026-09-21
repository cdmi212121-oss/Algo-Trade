async function loadProfile() {
  const p = await getJSON("/api/profile");
  document.getElementById("p-client").textContent = p.client_code;
  document.getElementById("p-capital").value = p.starting_capital;
  document.getElementById("p-tradable").value = p.tradable_capital_pct;
  document.getElementById("p-risk").value = p.risk_per_trade_pct;
  document.getElementById("p-maxloss").value = p.max_daily_loss_pct;
  document.getElementById("p-maxtrades").value = p.max_trades_per_day;
  document.getElementById("p-maxpos").value = p.max_concurrent_positions;
  document.getElementById("p-hours").textContent = `${p.market_open} – ${p.market_close}`;

  const checksEl = document.getElementById("p-symbols-checks");
  checksEl.innerHTML = p.tradable_symbols.map((sym) => `
    <label style="display:flex; align-items:center; gap:6px; font-weight:600; font-size:13px;">
      <input type="checkbox" value="${sym}" ${p.symbols.includes(sym) ? "checked" : ""}> ${sym}
    </label>`).join("");
}

async function saveProfile() {
  const payload = {
    starting_capital: parseFloat(document.getElementById("p-capital").value),
    tradable_capital_pct: parseFloat(document.getElementById("p-tradable").value),
    risk_per_trade_pct: parseFloat(document.getElementById("p-risk").value),
    max_daily_loss_pct: parseFloat(document.getElementById("p-maxloss").value),
    max_trades_per_day: parseInt(document.getElementById("p-maxtrades").value, 10),
    max_concurrent_positions: parseInt(document.getElementById("p-maxpos").value, 10),
  };
  const result = await postJSON("/api/profile", payload);
  const msg = document.getElementById("p-message");
  msg.textContent = result.ok ? "Saved." : "Failed to save.";
  msg.style.color = result.ok ? "var(--good)" : "var(--critical)";
  loadProfile();
}

async function saveSymbols() {
  const checked = [...document.querySelectorAll("#p-symbols-checks input:checked")].map((el) => el.value);
  const msg = document.getElementById("p-symbols-message");
  if (!checked.length) {
    msg.textContent = "Pick at least one instrument.";
    msg.style.color = "var(--critical)";
    return;
  }
  const result = await postJSON("/api/profile", { symbols: checked });
  msg.textContent = result.ok ? "Saved." : "Failed to save.";
  msg.style.color = result.ok ? "var(--good)" : "var(--critical)";
}

document.getElementById("p-save").addEventListener("click", saveProfile);
document.getElementById("p-save-symbols").addEventListener("click", saveSymbols);
loadProfile();
