async function loadViolations() {
  const violations = await getJSON("/api/violations");
  const wrap = document.getElementById("violations-table-wrap");
  const empty = document.getElementById("violations-empty");

  if (!violations.length) {
    wrap.hidden = true;
    empty.hidden = false;
    return;
  }
  wrap.hidden = false;
  empty.hidden = true;

  const tbody = document.querySelector("#violations-table tbody");
  tbody.innerHTML = violations.slice().reverse().map((v) => `<tr>
    <td>${v.time}</td>
    <td>${v.symbol}</td>
    <td>${v.reason}</td>
  </tr>`).join("");
}

loadViolations();
setInterval(loadViolations, 10000);
