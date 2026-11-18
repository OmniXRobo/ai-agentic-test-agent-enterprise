// Filtered invoice pages for the 23.9.0 filter tests: the page filters its own data when Apply is clicked.
window.REGION = {"Dubai": "North", "Sharjah": "North", "Abu Dhabi": "South", "Ajman": "South"};
function renderTable(rows, page, size, host, pager, onPage) {
  const slice = rows.slice(page * size, page * size + size);
  host.innerHTML = "<table id='inv'><thead><tr><th>Invoice No</th><th>City</th><th>Status</th><th>Date</th><th>Amount</th></tr></thead><tbody>" +
    slice.map(r => `<tr><td>${r.id}</td><td>${r.city}</td><td>${r.status}</td><td>${r.date}</td><td>${fmt(r.amount)}</td></tr>`).join("") + "</tbody></table>";
  const last = (page + 1) * size >= rows.length;
  pager.innerHTML = `Showing ${rows.length ? page * size + 1 : 0} to ${Math.min(rows.length, page * size + size)} of ${rows.length} entries
    <button id="next" ${last ? "disabled" : ""}>Next</button>`;
  pager.querySelector("#next").onclick = () => setTimeout(() => onPage(page + 1), 250);
}
