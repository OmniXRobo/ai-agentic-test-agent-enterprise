// Deterministic invoice data shared by the two test applications (finance system A and portal B).
function invoices() {
  const out = [];
  for (let i = 1; i <= 250; i++) {
    const amount = ((i * 7919) % 100000) / 100 + 100;  // 100.00 .. 1099.99
    const status = i % 5 === 0 ? 'Pending' : 'Paid';
    const day = (i % 28) + 1;
    out.push({ id: 'INV-' + String(i).padStart(4, '0'), customer: 'Customer ' + (i % 17), amount, status,
               day, month: (i % 12) + 1 });
  }
  return out;
}
function fmt(n) { return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function pager(rows, size, render) {
  let page = 1; const pages = Math.ceil(rows.length / size);
  const show = () => {
    const slice = rows.slice((page - 1) * size, page * size);
    render(slice);
    document.getElementById('info').textContent = 'Showing ' + ((page - 1) * size + 1) + ' to ' +
      Math.min(page * size, rows.length) + ' of ' + rows.length + ' entries';
    document.getElementById('next').disabled = page >= pages;
    document.getElementById('prev').disabled = page <= 1;
  };
  document.getElementById('next').onclick = () => { if (page < pages) { page++; setTimeout(show, 80); } };
  document.getElementById('prev').onclick = () => { if (page > 1) { page--; show(); } };
  show();
}
function login(onOk) {
  document.getElementById('login').onsubmit = (e) => {
    e.preventDefault();
    if (['tester', 'tester@corp.local'].includes(document.getElementById('user').value) && document.getElementById('pass').value === 'Cmp-Pass-1') {
      document.getElementById('login').remove(); document.getElementById('app').hidden = false; onOk();
    } else { document.getElementById('err').textContent = 'Invalid credentials'; }
  };
}
