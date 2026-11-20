/* Read-only coverage report. Everything comes from data/latest.json, which the
   admin app on the office PC publishes after each check. No writes happen here. */
(function () {
  const $app = document.getElementById('app');

  document.getElementById('themeBtn').addEventListener('click', () => {
    const d = document.documentElement, next = d.dataset.theme === 'dark' ? 'light' : 'dark';
    d.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch (e) {}
  });

  const P = {
    check: '<path d="M5 12.5 10 17.5 19.5 7"/>',
    x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.5h.01"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 1 1 3.4 2.6c-.6.3-1 .8-1 1.4v.3"/><path d="M12 17h.01"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    external: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  };
  const icon = (n, s = 16) => `<svg class="ico" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n]}</svg>`;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const LABEL = { DELIVERABLE: 'Deliverable', NOT_DELIVERABLE: 'Not deliverable', OUT_OF_STOCK: 'Out of stock', UNKNOWN: 'Unclear', ERROR: 'Check failed' };

  function when(s) {
    if (!s) return '—';
    const [d, t] = s.split(' ');
    const [y, m, day] = d.split('-').map(Number);
    const dt = new Date(y, m - 1, day), today = new Date(); today.setHours(0, 0, 0, 0);
    const diff = Math.round((today - dt) / 864e5);
    const label = diff === 0 ? 'Today' : diff === 1 ? 'Yesterday'
      : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
    return `${label}, ${t.slice(0, 5)}`;
  }
  function shortDelivery(t) {
    if (!t) return '';
    const parts = t.split(','), head = parts[0].trim();
    if (/^(today|tomorrow)$/i.test(head)) return head;
    if (parts.length > 1 && /^[A-Za-z]+day$/.test(head)) return `${head.slice(0, 3)} ${parts[1].trim()}`;
    return t.slice(0, 18);
  }
  function statusCell(c) {
    const [st, text, cod] = c;
    if (st === 'DELIVERABLE')
      return `<span class="st ok">${icon('check', 13)}<span>${esc(shortDelivery(text))}</span></span>` +
        (cod === 0 ? '<span class="tag-mini" title="No cash on delivery">No COD</span>' : '');
    if (st === 'NOT_DELIVERABLE') return `<span class="st no">${icon('x', 13)}<span>No</span></span>`;
    if (st === 'OUT_OF_STOCK') return `<span class="st warn">${icon('alert', 13)}<span>Stock</span></span>`;
    return `<span class="st err">${icon('help', 13)}<span>Error</span></span>`;
  }

  function render(d) {
    const k = d.kpi, n = d.products.length, pins = d.pins;
    document.title = 'Delivery Coverage';
    const stamp = (d.generated_at || '').slice(0, 10);
    const byProduct = [...d.products].sort((a, b) => b.pct - a.pct || a.name.localeCompare(b.name));
    const cMetro = pins.filter(p => p.metro).length, cCap = pins.filter(p => p.capital).length;
    const cDist = pins.filter(p => p.district).length;
    const cOther = pins.filter(p => !p.metro && !p.capital && !p.district).length;

    const head = d.products.map(p => `
      <th scope="col"><div class="th-wrap"><span>
        <a class="th-name" href="${esc(p.url)}" target="_blank" rel="noopener noreferrer" title="Open on Flipkart" style="text-decoration:none">${esc(p.name)}</a>
        <span class="th-sub">${p.ok}/${pins.length} pincodes</span></span></div></th>`).join('');

    const body = pins.map(p => {
      const row = d.cells[p.pin] || {};
      const kind = (p.metro ? 'metro ' : '') + (p.capital ? 'capital ' : '') + (p.district ? 'district ' : '')
        + (!p.metro && !p.capital && !p.district ? 'custom' : '');
      const cells = d.products.map(prod => {
        const c = row[String(prod.id)];
        if (!c) return '<td class="cell pending" title="Not checked yet"><span class="dots" aria-label="Not checked yet"></span></td>';
        const tip = `${prod.name} → ${p.city || p.pin}: ${LABEL[c[0]] || c[0]}${c[1] ? ` (${c[1]})` : ''}${c[3] ? ` · ${c[3]}` : ''} · checked ${when(c[4])}`;
        return `<td class="cell" title="${esc(tip)}">${statusCell(c)}</td>`;
      }).join('');
      return `<tr data-gap="${p.ok < n ? 1 : 0}" data-kind="${kind}" data-key="${esc(`${p.pin} ${p.city} ${p.state}`.toLowerCase())}">
        <th class="sticky-col" scope="row"><div class="th-wrap"><span>
          <span class="loc-city">${esc(p.city || p.pin)}</span>
          <span class="loc-sub"><span class="mono">${p.pin}</span>${p.state ? ` · ${esc(p.state)}` : ''}</span></span></div></th>
        ${cells}
        <td class="num cov"><span class="cov-txt ${p.ok === n ? 'full' : p.ok === 0 ? 'none' : ''}">${p.ok}/${n}</span>
          <span class="cov-bar"><span style="width:${n ? 100 * p.ok / n : 0}%"></span></span></td></tr>`;
    }).join('');

    $app.innerHTML = `
      <header class="page-head">
        <div>
          <p class="eyebrow">Latest results</p>
          <h1>Delivery coverage</h1>
          <p class="sub">${k.fresh_pins ?? ''}${k.fresh_pins != null ? `/${k.n_pins} pincodes checked in the last 7 days · ` : ''}Updated ${when(k.newest)}${k.oldest && k.newest && k.oldest.slice(0, 10) !== k.newest.slice(0, 10) ? ` · oldest result ${when(k.oldest)}` : ''}
            · ${n} products × ${pins.length} pincodes</p>
        </div>
        <div class="head-actions">
          <a class="btn primary" href="data/coverage.xlsx" download="parxen_flipkart_coverage_${stamp}.xlsx">${icon('download', 16)} Download Excel</a>
        </div>
      </header>

      <section class="kpis">
        <article class="card kpi"><p class="kpi-label">Overall coverage</p>
          <p class="kpi-value">${k.coverage}<small>%</small></p>
          <div class="meter" role="img" aria-label="${k.coverage} percent"><span style="width:${k.coverage}%"></span></div>
          <p class="kpi-foot">${k.ok} of ${k.checked} product–pincode checks deliverable</p></article>
        <article class="card kpi"><p class="kpi-label">Fully covered pincodes</p>
          <p class="kpi-value">${k.full_pins}<small> / ${k.n_pins}</small></p><p class="kpi-foot">Every product deliverable</p></article>
        <article class="card kpi"><p class="kpi-label">Pincodes with no coverage</p>
          <p class="kpi-value ${k.zero_pins ? 'danger-text' : ''}">${k.zero_pins}<small> / ${k.n_pins}</small></p><p class="kpi-foot">No product deliverable</p></article>
        <article class="card kpi"><p class="kpi-label">Out of stock</p>
          <p class="kpi-value ${k.oos ? 'warn-text' : ''}">${k.oos}<small> cells</small></p>
          <p class="kpi-foot">${k.issues ? `${k.issues} check${k.issues === 1 ? '' : 's'} failed · ` : ''}${k.missing > 0 ? `${k.missing} not checked yet` : 'all cells checked'}</p></article>
      </section>

      <section class="card panel">
        <div class="panel-head"><div><h2>Coverage by product</h2><p class="sub">Share of checked pincodes where Flipkart offers delivery</p></div></div>
        <ul class="hbars">${byProduct.map(p => `
          <li title="${esc(p.name)}: deliverable to ${p.ok} of ${p.n} pincodes (${p.pct}%)">
            <span class="hb-name">${esc(p.name)}</span>
            <span class="hb-track"><span class="hb-fill" style="width:${p.pct}%"></span></span>
            <span class="hb-val"><b>${p.pct}%</b> <span>${p.ok}/${p.n}</span></span></li>`).join('')}
        </ul>
      </section>

      <section class="card panel grid-panel">
        <div class="panel-head">
          <div><h2>Pincode × product</h2><p class="sub">What Flipkart showed for each listing at each delivery location</p></div>
          <ul class="legend" aria-label="Legend">
            <li><span class="st ok">${icon('check', 13)}</span>Deliverable (by date)</li>
            <li><span class="st no">${icon('x', 13)}</span>Not deliverable</li>
            <li><span class="st warn">${icon('alert', 13)}</span>Out of stock</li>
            <li><span class="st err">${icon('help', 13)}</span>Check failed</li>
          </ul>
        </div>
        <div class="toolbar">
          <div class="seg" role="tablist" aria-label="Filter pincodes">
            <button class="on" data-f="all">All <span>${pins.length}</span></button>
            <button data-f="metro">Metros <span>${cMetro}</span></button>
            <button data-f="capital">State capitals <span>${cCap}</span></button>
            ${cDist ? `<button data-f="district">Districts <span>${cDist}</span></button>` : ''}
            ${cOther ? `<button data-f="custom">Other <span>${cOther}</span></button>` : ''}
          </div>
          <div class="toolbar-right">
            <label class="switch"><input type="checkbox" id="gapsOnly"><span class="sw"></span>Gaps only</label>
            <label class="search">${icon('search', 15)}<input type="search" id="pinFilter" placeholder="City, state or pincode" aria-label="Filter pincodes"></label>
          </div>
        </div>
        <div class="matrix-wrap">
          <table class="matrix">
            <thead><tr><th class="sticky-col" scope="col">Location</th>${head}<th scope="col" class="num">Coverage</th></tr></thead>
            <tbody>${body}</tbody>
          </table>
          <p class="no-rows" hidden>No pincodes match this filter.</p>
        </div>
        <p class="hint">Hover a cell for the exact text, the address Flipkart resolved the pincode to, and when it was checked.</p>
      </section>`;
    wireFilters();
  }

  function wireFilters() {
    const rows = [...document.querySelectorAll('.matrix tbody tr')];
    const gaps = document.getElementById('gapsOnly'), q = document.getElementById('pinFilter');
    const segs = [...document.querySelectorAll('.seg button')], none = document.querySelector('.no-rows');
    let kind = 'all';
    try { kind = sessionStorage.getItem('pinKind') || 'all'; gaps.checked = sessionStorage.getItem('gapsOnly') === '1'; } catch (e) {}
    if (!segs.some(b => b.dataset.f === kind)) kind = 'all';
    function apply() {
      const term = q.value.trim().toLowerCase();
      let shown = 0;
      rows.forEach(tr => {
        const hide = (kind !== 'all' && !tr.dataset.kind.includes(kind)) || (gaps.checked && tr.dataset.gap !== '1')
          || (term && !tr.dataset.key.includes(term));
        tr.hidden = hide; if (!hide) shown++;
      });
      segs.forEach(b => { b.classList.toggle('on', b.dataset.f === kind); b.setAttribute('aria-selected', b.dataset.f === kind); });
      none.hidden = shown > 0;
      try { sessionStorage.setItem('pinKind', kind); sessionStorage.setItem('gapsOnly', gaps.checked ? '1' : '0'); } catch (e) {}
    }
    segs.forEach(b => b.addEventListener('click', () => { kind = b.dataset.f; apply(); }));
    gaps.addEventListener('change', apply);
    q.addEventListener('input', apply);
    apply();
  }

  fetch('data/latest.json', { cache: 'no-store' })
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(render)
    .catch(() => {
      $app.innerHTML = `<section class="empty card load-error"><div class="empty-ico">${icon('alert', 28)}</div>
        <h2>No results published yet</h2><p>The report appears here after the first check is published from the office computer.</p></section>`;
    });
})();
