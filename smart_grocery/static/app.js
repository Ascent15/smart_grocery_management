const $ = s => document.querySelector(s);
const inr = n => '₹' + Number(n).toLocaleString('en-IN', {maximumFractionDigits: 0});
let ME = null, SUPPLIERS = [], PRODUCTS = [], editingId = null;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || 'GET',
    headers: {'Content-Type': 'application/json'},
    body: opts.body ? JSON.stringify(opts.body) : undefined
  });
  let data = {};
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401 && ME) { location.reload(); }
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const table = (el, cols, data) => {
  $(el).innerHTML = '<tr>' + cols.map(c => `<th>${c[0]}</th>`).join('') + '</tr>' +
    (data.length ? data.map(r => '<tr>' + cols.map(c => `<td>${c[1](r)}</td>`).join('') + '</tr>').join('')
                 : `<tr><td colspan="${cols.length}" class="muted">Nothing to show</td></tr>`);
};

// ---- login ----
async function doLogin() {
  try {
    ME = await api('/api/login', {method: 'POST', body: {username: $('#lu').value, password: $('#lp').value}});
    start();
  } catch (e) { $('#lerr').textContent = e.message; }
}
$('#lbtn').onclick = doLogin;
$('#lp').onkeydown = e => { if (e.key === 'Enter') doLogin(); };
$('#logout').onclick = async () => { await api('/api/logout', {method: 'POST'}); location.reload(); };

async function start() {
  $('#login').hidden = true; $('#app').hidden = false;
  $('#whoami').textContent = `${ME.user} (${ME.role})`;
  SUPPLIERS = await api('/api/suppliers');
  $('#fSup').innerHTML = '<option value="">— none —</option>' + SUPPLIERS.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  show('dashboard'); refreshBadge();
}
fetch('/api/me').then(r => r.ok ? r.json() : null).then(u => { if (u) { ME = u; start(); } });

// ---- tabs ----
document.querySelectorAll('nav button').forEach(b => b.onclick = () => show(b.dataset.tab));
function show(tab) {
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('main section').forEach(s => s.hidden = s.id !== 'tab-' + tab);
  ({dashboard: loadDashboard, products: loadProducts, stock: loadStock, alerts: loadAlerts, reports: loadReports})[tab]();
}

// ---- charts (plain canvas) ----
function setup(canvas) {
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = +canvas.getAttribute('height');
  canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.height = h + 'px';
  const c = canvas.getContext('2d'); c.scale(dpr, dpr); c.clearRect(0, 0, w, h);
  c.font = '11px system-ui'; return [c, w, h];
}
function barChart(canvas, labels, values, color) {
  const [c, w, h] = setup(canvas), pad = {l: 50, r: 10, t: 10, b: 28};
  const max = Math.max(...values, 1), bw = (w - pad.l - pad.r) / values.length;
  c.strokeStyle = '#e1e6e2'; c.fillStyle = '#6b776f'; c.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (h - pad.t - pad.b) * (1 - i / 4);
    c.beginPath(); c.moveTo(pad.l, y); c.lineTo(w - pad.r, y); c.stroke();
    c.fillText(Math.round(max * i / 4).toLocaleString('en-IN'), pad.l - 6, y + 4);
  }
  values.forEach((v, i) => {
    const bh = (h - pad.t - pad.b) * v / max, x = pad.l + i * bw + bw * 0.15;
    c.fillStyle = color; c.fillRect(x, h - pad.b - bh, bw * 0.7, bh);
    c.fillStyle = '#6b776f'; c.textAlign = 'center';
    c.fillText(labels[i], x + bw * 0.35, h - 10);
  });
}

// ---- dashboard ----
async function loadDashboard() {
  const d = await api('/api/dashboard');
  const stat = (v, l, cls = '') => `<div class="stat ${cls}"><div class="v">${v}</div><div class="l">${l}</div></div>`;
  $('#stats').innerHTML = stat(d.total_products, 'Products') + stat(d.total_units.toLocaleString('en-IN'), 'Units in stock') +
    stat(inr(d.inventory_value), 'Inventory value') + stat(inr(d.revenue_14d), 'Revenue (14 days)') +
    stat(d.low_stock, 'Low / out of stock', d.low_stock ? 'warn' : '') + stat(d.near_expiry, 'Near expiry / expired', d.near_expiry ? 'bad' : '');
  barChart($('#salesChart'), d.sales_series.map(s => s.day.slice(8)), d.sales_series.map(s => s.revenue), '#2e7d4f');
  const cats = Object.entries(d.category_value);
  barChart($('#catChart'), cats.map(c => c[0].slice(0, 8)), cats.map(c => c[1]), '#e0a030');
  renderAlerts('#dashAlerts', await api('/api/alerts'), 5);
}

// ---- alerts ----
function renderAlerts(el, list, limit) {
  const types = {out_of_stock: 'Out of stock', low_stock: 'Low stock', near_expiry: 'Near expiry', expired: 'Expired'};
  $(el).innerHTML = list.slice(0, limit || 999).map(a =>
    `<div class="alert ${a.alert_type === 'expired' || a.alert_type === 'out_of_stock' ? 'bad' : ''}">
       <b>${types[a.alert_type]}</b> — ${esc(a.message)}<small>${esc(a.created_at.replace('T', ' '))}</small></div>`).join('')
    || '<p class="muted">No active alerts 🎉</p>';
  if (limit && list.length > limit) $(el).innerHTML += `<p class="muted small">+${list.length - limit} more in the Alerts tab</p>`;
}
async function loadAlerts() { renderAlerts('#alertList', await api('/api/alerts')); }
async function refreshBadge() {
  const a = await api('/api/alerts'); const b = $('#alertBadge');
  b.hidden = !a.length; b.textContent = a.length;
}

// ---- products ----
const stockPill = p => p.quantity === 0 ? '<span class="pill bad">Out</span>' :
  p.quantity <= p.reorder_level ? '<span class="pill low">Low</span>' : '<span class="pill ok">OK</span>';
const expiryCell = p => {
  if (!p.expiry_date) return '—';
  const d = Math.round((new Date(p.expiry_date) - new Date(new Date().toDateString())) / 864e5);
  const cls = d < 0 ? 'bad' : d <= 7 ? 'low' : 'ok';
  return `${p.expiry_date} <span class="pill ${cls}">${d < 0 ? 'expired' : d + 'd'}</span>`;
};
async function loadProducts() {
  const q = encodeURIComponent($('#search').value), cat = encodeURIComponent($('#catFilter').value);
  PRODUCTS = await api(`/api/products?q=${q}&category=${cat}`);
  if ($('#catFilter').options.length === 1) {
    const all = await api('/api/products'); const cats = [...new Set(all.map(p => p.category))].sort();
    $('#catFilter').innerHTML += cats.map(c => `<option>${esc(c)}</option>`).join('');
    $('#catList').innerHTML = cats.map(c => `<option value="${esc(c)}">`).join('');
  }
  table('#prodTable', [
    ['SKU', p => p.sku], ['Name', p => esc(p.name)], ['Category', p => esc(p.category)], ['Price', p => inr(p.price)],
    ['Qty', p => p.quantity], ['Reorder', p => p.reorder_level], ['Status', stockPill], ['Expiry', expiryCell],
    ['Supplier', p => esc(p.supplier || '—')],
    ['', p => `<button class="btn small ghost" onclick="openModal(${p.id})">Edit</button>` +
      (ME.role === 'owner' ? ` <button class="btn small danger" onclick="delProduct(${p.id})">Delete</button>` : '')]
  ], PRODUCTS);
}
$('#search').oninput = loadProducts; $('#catFilter').onchange = loadProducts;
$('#addProdBtn').onclick = () => openModal(null);

window.openModal = id => {
  editingId = id; const p = PRODUCTS.find(x => x.id === id) || {};
  $('#mTitle').textContent = id ? 'Edit product' : 'Add product'; $('#mErr').textContent = '';
  $('#fSku').value = p.sku || ''; $('#fName').value = p.name || ''; $('#fCat').value = p.category || '';
  $('#fPrice').value = p.price ?? ''; $('#fQty').value = p.quantity ?? 0; $('#fRe').value = p.reorder_level ?? 10;
  $('#fExp').value = p.expiry_date || ''; $('#fSup').value = p.supplier_id || '';
  $('#modal').hidden = false;
};
$('#mCancel').onclick = () => $('#modal').hidden = true;
$('#mSave').onclick = async () => {
  const body = {sku: $('#fSku').value, name: $('#fName').value, category: $('#fCat').value, price: $('#fPrice').value,
    quantity: $('#fQty').value, reorder_level: $('#fRe').value, expiry_date: $('#fExp').value, supplier_id: $('#fSup').value};
  try {
    await api(editingId ? `/api/products/${editingId}` : '/api/products', {method: editingId ? 'PUT' : 'POST', body});
    $('#modal').hidden = true; loadProducts(); refreshBadge();
  } catch (e) { $('#mErr').textContent = e.message; }
};
window.delProduct = async id => {
  if (!confirm('Delete this product and its history?')) return;
  try { await api(`/api/products/${id}`, {method: 'DELETE'}); loadProducts(); refreshBadge(); } catch (e) { alert(e.message); }
};

// ---- stock & sales ----
$('#tType').onchange = () => $('#expRow').hidden = $('#tType').value !== 'purchase';
async function loadStock() {
  PRODUCTS = await api('/api/products');
  $('#tProduct').innerHTML = PRODUCTS.map(p => `<option value="${p.id}">${esc(p.name)} (stock ${p.quantity})</option>`).join('');
  const t = await api('/api/transactions?limit=30');
  table('#txnTable', [['Date', r => r.txn_date], ['Product', r => esc(r.product)], ['Type', r => r.txn_type],
    ['Qty', r => r.quantity], ['By', r => esc(r.user || '')]], t);
}
$('#tBtn').onclick = async () => {
  const m = $('#tMsg');
  try {
    const r = await api('/api/transactions', {method: 'POST', body: {
      product_id: $('#tProduct').value, txn_type: $('#tType').value, quantity: $('#tQty').value,
      expiry_date: $('#tExp').value, note: $('#tNote').value}});
    m.className = 'ok-msg'; m.textContent = `Recorded. New stock: ${r.new_quantity}`;
    loadStock(); refreshBadge();
  } catch (e) { m.className = 'err'; m.textContent = e.message; }
};

// ---- reports ----
async function loadReports() {
  const a = await api('/api/analytics');
  $('#modelNote').textContent = `Forecast model: ${a.forecast_model}. Suggestions cover 14 days of forecast demand.`;
  table('#reorderTable', [['Product', r => esc(r.name)], ['Supplier', r => esc(r.supplier || '—')], ['In stock', r => r.quantity],
    ['Sells/day', r => r.daily_velocity], ['Forecast 7d', r => r.forecast_7d], ['Days left', r => r.days_of_stock ?? '∞'],
    ['Suggested order', r => `<b>${r.suggested_reorder}</b>`]], a.reorder);
  const cols = [['Product', r => esc(r.name)], ['Sold (30d)', r => r.units_30d], ['Sells/day', r => r.daily_velocity], ['In stock', r => r.quantity]];
  table('#fastTable', cols, a.fast); table('#slowTable', cols, a.slow);
}
