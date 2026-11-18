// 23.9.0 live-capture test data: 25 invoices, the same in every page of this folder.
window.INVOICES = Array.from({length: 25}, (_, i) => {
  const n = i + 1, cities = ["Dubai", "Sharjah", "Abu Dhabi", "Ajman"], status = ["Paid", "Pending", "Cancelled"];
  return {id: "INV-" + String(n).padStart(3, "0"), customer: "Customer " + n, city: cities[i % 4],
          status: status[i % 3], amount: (n * 137.5).toFixed(2), date: "2026-0" + (1 + (i % 9)) + "-" + String(1 + (i % 28)).padStart(2, "0")};
});
window.fmt = (v) => Number(v).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
