const API = 'http://localhost:8001/api';

const FILTERS = {
    horizon: { opts: [['7', '7D'], ['14', '14D'], ['30', '30D'], ['90', '90D'], ['365', '1Y'], ['', 'ALL']], def: '30' },
    category: { opts: [['', 'ALL TYPES'], ['central_bank', 'CENTRAL BANKS'], ['commercial_bank', 'COMMERCIAL BANKS'], ['fund', 'FUNDS'], ['multilateral', 'MULTILATERAL'], ['academic', 'ACADEMIC']], def: '' },
    source: { opts: [['', 'ALL SRC'], ['ING', 'ING THINK'], ['ECB', 'ECB'], ['Fed', 'FED'], ['Bank of England', 'BOE'], ['Bank of Japan', 'BOJ'], ['Bank of Canada', 'BOC'], ['BIS', 'BIS'], ['IMF', 'IMF'], ['NBER', 'NBER'], ['NEP', 'RePEc/NEP'], ['SSRN', 'SSRN']], def: '' },
    thematic: { opts: [['', 'ALL THEMES'], ['Monetary Policy', 'Monetary'], ['Inflation', 'Inflation'], ['Financial Stability', 'Fin. Stability'], ['Liquidity', 'Liquidity'], ['Labor Market', 'Labor'], ['Fiscal Policy', 'Fiscal'], ['Digital Currency', 'Digital FX'], ['Macro-Finance', 'Macro-Fin']], def: '' },
    region: { opts: [['', 'ALL REGIONS'], ['US', 'USD'], ['EU', 'EUR'], ['UK', 'GBP'], ['Japan', 'JPY'], ['China', 'CNY'], ['Global', 'GLOBAL']], def: '' },
    pair: { opts: [['', 'ALL PAIRS'], ['EUR/USD', 'EUR/USD'], ['USD/JPY', 'USD/JPY'], ['GBP/USD', 'GBP/USD'], ['USD/CHF', 'USD/CHF'], ['USD/CAD', 'USD/CAD'], ['AUD/USD', 'AUD/USD'], ['USD/CNY', 'USD/CNY'], ['EUR/GBP', 'EUR/GBP'], ['EUR/JPY', 'EUR/JPY'], ['GBP/JPY', 'GBP/JPY'], ['DXY', 'DXY']], def: '' },
    sentiment: { opts: [['', 'ALL SENT'], ['positive', '▲ BULLISH'], ['neutral', '■ NEUTRAL'], ['negative', '▼ BEARISH']], def: '' },
};

const BLOC_ORDER = [['US', 'USD'], ['EU', 'EUR'], ['UK', 'GBP'], ['Japan', 'JPY'], ['China', 'CNY'], ['Global', 'GLB']];

const SAVE_KEY = 'mrt_saved';
const VISIT_KEY = 'mrt_last_visit';
const state = {
    papers: [], sort: 'interest_score', savedOnly: false, saved: loadSaved(),
    tab: 'feed', lastVisit: readVisit(), autoTimer: null, catalog: null,
};

const $ = (id) => document.getElementById(id);
function loadSaved() { try { return new Set(JSON.parse(localStorage.getItem(SAVE_KEY) || '[]')); } catch { return new Set(); } }
function persistSaved() {
    localStorage.setItem(SAVE_KEY, JSON.stringify([...state.saved]));
    // mirror server-side so the digest cron can read the watchlist
    fetch(`${API}/watchlist`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [...state.saved] }),
    }).catch(() => {});
}
function readVisit() {
    const prev = parseInt(localStorage.getItem(VISIT_KEY) || '0', 10);
    localStorage.setItem(VISIT_KEY, Date.now().toString());  // stamp for next session
    return prev || Date.now();                               // first visit: nothing is "new"
}
function isNew(p) { return p.created_at && Date.parse(p.created_at + 'Z') > state.lastVisit; }

document.addEventListener('DOMContentLoaded', () => {
    buildFilters();
    wireEvents();
    startClock();
    loadCatalog();
    refreshAll();
    setInterval(loadBoard, 60000);
});

// Provenance catalog: source -> {category, zone, institution, portal, ...}.
// Fetched once; powers source chips and the DESKS map. Mirrors how the
// aggregator knows where every paper truly originates.
async function loadCatalog() {
    try {
        state.catalog = await (await fetch(`${API}/sources`)).json();
    } catch (e) { /* catalog optional */ }
}
function srcMeta(name) {
    if (!state.catalog) return null;
    return state.catalog.sources.find(s => s.name === name) || null;
}

function buildFilters() {
    for (const [key, cfg] of Object.entries(FILTERS)) {
        $(`filter-${key}`).innerHTML = cfg.opts.map(([v, l]) => `<option value="${v}"${v === cfg.def ? ' selected' : ''}>${l}</option>`).join('');
    }
}

function wireEvents() {
    let t;
    const debounced = () => { clearTimeout(t); t = setTimeout(loadPapers, 250); };
    $('filter-search').addEventListener('input', debounced);
    ['horizon', 'category', 'source', 'thematic', 'region', 'pair', 'sentiment'].forEach(k =>
        $(`filter-${k}`).addEventListener('change', () => { loadPapers(); if (k === 'horizon') loadBoard(); }));

    $('btn-refresh').addEventListener('click', () => refreshAll(true));
    $('btn-analyze').addEventListener('click', runFinbert);
    $('btn-auto').addEventListener('click', toggleAuto);
    document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));
    $('sort-select').addEventListener('change', (e) => { state.sort = e.target.value; renderCards(); });
    $('btn-export').addEventListener('click', exportCsv);
    $('btn-saved').addEventListener('click', () => {
        state.savedOnly = !state.savedOnly;
        $('btn-saved').innerHTML = state.savedOnly ? '★ SAVED' : '☆ SAVED';
        $('btn-saved').classList.toggle('active-btn', state.savedOnly);
        renderCards();
    });

    document.addEventListener('keydown', (e) => {
        const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
        if (e.key === '/' && !typing) { e.preventDefault(); $('filter-search').focus(); }
        else if (e.key === 'Escape') document.activeElement.blur();
        else if (e.key === 'r' && !typing) refreshAll(true);
        else if (!typing && state.tab === 'feed' && ['j', 'k', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
            e.preventDefault(); moveSelection(e.key === 'j' || e.key === 'ArrowDown' ? 1 : -1);
        }
        else if (!typing && state.tab === 'feed' && (e.key === 'Enter' || e.key === 'o')) {
            const p = state.papers.find(x => x.id === state.selectedId);
            if (p && p.source_url) window.open(p.source_url, '_blank');
        }
    });
}

// ---- clock + FX sessions ----
function startClock() {
    const sessions = [['SYD', 22, 7], ['TOK', 0, 9], ['LDN', 8, 17], ['NYC', 13, 22]];
    const tick = () => {
        const now = new Date(), h = now.getUTCHours(), pad = (n) => String(n).padStart(2, '0');
        $('clock').textContent = `${pad(h)}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())} UTC`;
        $('session-badges').innerHTML = sessions.map(([name, o, c]) => {
            const open = o < c ? (h >= o && h < c) : (h >= o || h < c);
            return `<span class="px-1 rounded-sm border ${open ? 'text-brand-up border-brand-up/40 bg-brand-up/10' : 'text-brand-textMuted border-brand-border'}">${name}</span>`;
        }).join('');
    };
    tick(); setInterval(tick, 1000);
}

// ---- loads ----
async function refreshAll(spin) {
    if (spin) $('btn-refresh').classList.add('busy');
    try { await loadPapers(); await loadBoard(); await loadStats(); }
    finally { $('btn-refresh').classList.remove('busy'); }
}

function setStatus(text, cls) { const el = $('status-indicator'); el.textContent = text; el.className = `text-[11px] ${cls}`; }

async function loadStats() {
    try {
        const s = await (await fetch(`${API}/stats`)).json();
        const sent = s.sentiment || {};
        $('stats-line').innerHTML =
            `${s.total} docs · <span class="text-brand-up">▲${sent.positive || 0}</span> <span class="text-brand-textMuted">■${sent.neutral || 0}</span> <span class="text-brand-down">▼${sent.negative || 0}</span>`;
    } catch (e) { /* silent */ }
}

async function loadBoard() {
    const days = $('filter-horizon').value || '30';
    try {
        const b = await (await fetch(`${API}/board?days=${days}`)).json();
        $('board-window').textContent = `window ${b.window_days}d`;
        const active = $('filter-region').value;
        $('board').innerHTML = b.blocs.map(bl => {
            const tot = bl.count || 1, w = (n) => `${(n / tot * 100).toFixed(0)}%`;
            const score = bl.count ? (bl.score >= 0 ? '+' : '') + bl.score.toFixed(2) : '—';
            const sc = bl.count === 0 ? 'score-neu' : bl.score > 0.03 ? 'score-pos' : bl.score < -0.03 ? 'score-neg' : 'score-neu';
            const arrow = bl.count === 0 ? '' : bl.score > 0.03 ? '▲' : bl.score < -0.03 ? '▼' : '■';
            return `<div class="bloc ${bl.region === active ? 'active' : ''}" data-region="${bl.region}">
                <div class="flex items-center justify-between">
                    <span class="text-xs font-bold tracking-wide">${bl.bloc}</span>
                    <span class="text-sm font-bold ${sc} tabular-nums">${arrow} ${score}</span>
                </div>
                <div class="flex items-center justify-between mt-1 mb-1.5">
                    <span class="text-[9px] text-brand-textMuted">${bl.count} docs</span>
                    <span class="text-[9px] text-brand-textMuted tabular-nums"><span class="text-brand-up">${bl.positive}</span>/${bl.neutral}/<span class="text-brand-down">${bl.negative}</span></span>
                </div>
                <div class="bloc-bar">
                    <span style="width:${w(bl.positive)};background:var(--up)"></span>
                    <span style="width:${w(bl.neutral)};background:#2e3037"></span>
                    <span style="width:${w(bl.negative)};background:var(--down)"></span>
                </div></div>`;
        }).join('');
        document.querySelectorAll('.bloc').forEach(el => el.addEventListener('click', () => {
            const sel = $('filter-region'); sel.value = (sel.value === el.dataset.region) ? '' : el.dataset.region;
            loadPapers();
            document.querySelectorAll('.bloc').forEach(b => b.classList.toggle('active', b.dataset.region === sel.value));
        }));
    } catch (e) { /* board optional */ }
}

async function loadPapers() {
    skeleton();
    setStatus('● FETCHING', 'text-brand-amber');
    try {
        const u = new URL(`${API}/papers`);
        const sv = $('filter-search').value.trim(); if (sv) u.searchParams.set('search', sv);
        const h = $('filter-horizon').value; if (h) u.searchParams.set('horizon_days', h);
        const cat = $('filter-category').value; if (cat) u.searchParams.set('category', cat);
        const src = $('filter-source').value; if (src) u.searchParams.set('source', src);
        const th = $('filter-thematic').value; if (th) u.searchParams.set('thematic_tags', th);
        const rg = $('filter-region').value; if (rg) u.searchParams.set('country_tags', rg);
        const se = $('filter-sentiment').value; if (se) u.searchParams.set('sentiment', se);
        u.searchParams.set('limit', '150');

        let res;
        try { res = await fetch(u); }
        catch (e) { await new Promise(r => setTimeout(r, 600)); res = await fetch(u); }
        if (!res.ok) throw new Error('bad response');
        state.papers = await res.json();
        renderCards();
        setStatus('● ONLINE', 'text-brand-up');
    } catch (e) {
        console.error(e);
        $('papers-container').innerHTML = '';
        showEmpty('[ERR] API offline. Backend must run on :8001 (./start.sh).', true);
        $('results-count').textContent = 'ERR_CONN';
        setStatus('● SYS_ERR', 'text-brand-down');
    }
}

async function runFinbert() {
    const btn = $('btn-analyze'); btn.classList.add('busy');
    setStatus('● FINBERT', 'text-brand-amber');
    try { await fetch(`${API}/analyze?days=7`, { method: 'POST' }); setTimeout(() => refreshAll(), 5000); }
    catch (e) { console.error(e); }
    finally { setTimeout(() => btn.classList.remove('busy'), 5000); }
}

// ---- helpers ----
function blocOf(p) { const t = p.country_tags || []; for (const [r, c] of BLOC_ORDER) if (t.includes(r)) return c; return 'GLB'; }
function esc(s) { return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function fmtDate(d) { return d ? new Date(d).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : 'Unknown'; }

function currentList() {
    let list = state.papers;
    if (state.savedOnly) list = list.filter(p => state.saved.has(p.id));
    const k = state.sort;
    const dateOf = (p) => p.published_date ? Date.parse(p.published_date) : 0;
    const val = (p) => k === 'sentiment_abs' ? Math.abs(p.sentiment_score ?? 0)
        : k === 'sentiment_score' ? (p.sentiment_score ?? -2)
            : k === 'interest_score' ? (p.interest_score ?? 0)
                : k === 'published_date' ? dateOf(p)
                    : (p[k] || '').toString().toLowerCase();
    const asc = (k === 'source' || k === 'title');
    return [...list].sort((a, b) => {
        const x = val(a), y = val(b);
        if (x !== y) return (x < y ? -1 : 1) * (asc ? 1 : -1);
        return dateOf(b) - dateOf(a);  // tiebreak: newest first
    });
}

// ---- render ----
function skeleton() {
    $('empty-state').classList.add('hidden');
    $('papers-container').innerHTML = Array.from({ length: 6 }).map(() =>
        `<div class="skeleton-card border border-brand-border/50 rounded p-5 space-y-3">
            <div class="h-3 bg-brand-border/60 rounded w-1/3"></div>
            <div class="h-4 bg-brand-border/60 rounded w-3/4"></div>
            <div class="h-3 bg-brand-border/40 rounded w-1/2"></div>
        </div>`).join('');
    $('results-count').textContent = 'querying…';
}

function showEmpty(msg, isErr) {
    const el = $('empty-state');
    el.textContent = msg;
    el.className = `text-xs p-10 text-center ${isErr ? 'text-brand-down' : 'text-brand-textMuted'}`;
    el.classList.remove('hidden');
}

// ---- feed: master list (left) + article preview (right) ----
function renderCards() {
    const list = currentList();
    const c = $('papers-container');
    if (!list.length) {
        c.innerHTML = '';
        showEmpty(state.savedOnly ? 'No saved research yet. Click SAVE on an article.' : 'No matching research. Widen the horizon or run SYNC.');
        $('results-count').textContent = '0 records';
        return;
    }
    $('empty-state').classList.add('hidden');
    $('results-count').textContent = `${list.length} record(s)`;
    updateNewCount();

    if (!state.selectedId || !list.find(p => p.id === state.selectedId)) state.selectedId = list[0].id;

    c.innerHTML = `<div class="feed-split">
        <div class="feed-list" id="feed-list">${list.map(feedRow).join('')}</div>
        <div class="feed-preview" id="feed-preview"></div>
    </div>`;

    $('feed-list').querySelectorAll('.feed-row').forEach(r =>
        r.addEventListener('click', () => selectPaper(r.dataset.id)));
    renderPreview(state.selectedId);
}

function feedRow(p) {
    const lbl = p.sentiment_label;
    const dot = lbl === 'positive' ? 'var(--up)' : lbl === 'negative' ? 'var(--down)' : 'var(--muted)';
    const newTag = isNew(p) ? '<span class="new-tag">NEW</span>' : '';
    const iv = p.interest_score || 0;
    const stars = iv ? `<span class="fr-int">${'★'.repeat(iv)}</span>` : '';
    const sel = p.id === state.selectedId ? ' active' : '';
    return `<div class="feed-row${sel}" data-id="${p.id}">
        <span class="fr-dot" style="background:${dot}"></span>
        <div class="fr-body">
            <div class="fr-meta"><span class="fr-date tabular-nums">${fmtDate(p.published_date)}</span><span class="fr-src">${esc(p.source)}</span>${newTag}</div>
            <div class="fr-title">${esc(p.title)}</div>
        </div>${stars}
    </div>`;
}

function selectPaper(id) {
    state.selectedId = id;
    document.querySelectorAll('.feed-row').forEach(r => r.classList.toggle('active', r.dataset.id === id));
    renderPreview(id);
}

function renderPreview(id) {
    const p = state.papers.find(x => x.id === id);
    const pv = $('feed-preview');
    if (!p || !pv) return;
    const lbl = p.sentiment_label;
    const badge = lbl
        ? `<span class="pill ${lbl === 'positive' ? 'pos' : lbl === 'negative' ? 'neg' : 'neu'}">${lbl === 'positive' ? '▲ BULLISH' : lbl === 'negative' ? '▼ BEARISH' : '■ NEUTRAL'} ${p.sentiment_score >= 0 ? '+' : ''}${Number(p.sentiment_score).toFixed(2)}</span>`
        : `<span class="pill neu">UNSCORED</span>`;
    const tags = [...(p.thematic_tags || []), ...(p.country_tags || [])].map(t => `<span class="theme-chip">${esc(t)}</span>`).join('');
    const kws = (p.keywords || []).map(k => `<span class="kw">${esc(k)}</span>`).join('');
    const det = p.sentiment_detail || {};
    const detStr = Object.keys(det).length ? Object.entries(det).map(([k, v]) => `${k.slice(0, 3)} ${(v * 100).toFixed(0)}%`).join(' · ') : '';
    const saved = state.saved.has(p.id);
    const iv = p.interest_score || 0;
    const interest = iv ? `<span class="interest" title="Trader interest ${iv}/5"><span class="i-on">${'★'.repeat(iv)}</span><span class="i-off">${'★'.repeat(5 - iv)}</span></span>` : '';
    const pairs = (p.currency_pairs || []).map(fx => `<span class="pair-chip">${fx}</span>`).join('');

    pv.innerHTML = `<div class="pv-scroll">
        <div class="pv-meta">
            <span class="fr-date tabular-nums">${fmtDate(p.published_date)}</span>
            <span class="pv-src">${esc(p.source)}</span>
            <span class="reg-chip">${blocOf(p)}</span>
            ${pairs}${badge}${interest}
        </div>
        <h2 class="pv-title">${esc(p.title)}</h2>
        <div class="pv-auth">${esc(p.authors || 'Unknown')}</div>
        <div class="pv-actions">
            <button class="act save ${saved ? 'saved' : ''}">${saved ? '★ SAVED' : '☆ SAVE'}</button>
            <a href="${esc(p.source_url) || '#'}" target="_blank" class="act primary ${p.source_url ? '' : 'disabled'}">↗ OPEN</a>
            <a href="${API}/papers/${p.id}/pdf" target="_blank" class="act">⤓ PDF</a>
            <a href="${API}/papers/${p.id}/report" target="_blank" class="act">▤ REPORT</a>
            ${p.analyzed_at ? '' : `<button class="act analyze">⚡ ANALYZE</button>`}
        </div>
        ${tags ? `<div class="pv-chips">${tags}</div>` : ''}
        ${detStr ? `<div class="pv-finbert tabular-nums">FinBERT · ${detStr}</div>` : ''}
        <div class="pv-body" id="pv-body"><div class="pv-note">loading article…</div></div>
    </div>`;
    fillPreviewBody(p);

    pv.querySelector('.save').addEventListener('click', (e) => {
        if (state.saved.has(p.id)) state.saved.delete(p.id); else state.saved.add(p.id);
        persistSaved();
        const on = state.saved.has(p.id);
        e.target.textContent = on ? '★ SAVED' : '☆ SAVE';
        e.target.classList.toggle('saved', on);
        if (state.savedOnly && !on) renderCards();
    });
    const az = pv.querySelector('.analyze');
    if (az) az.addEventListener('click', async (e) => {
        e.target.textContent = '⏳ …'; e.target.classList.add('disabled');
        try {
            const full = await (await fetch(`${API}/papers/${p.id}/analyze`, { method: 'POST' })).json();
            const i = state.papers.findIndex(x => x.id === p.id);
            if (i >= 0) state.papers[i] = full;
            renderPreview(p.id); renderListRowDot(p.id); loadBoard(); loadStats();
        } catch (err) { e.target.textContent = '⚡ ANALYZE'; e.target.classList.remove('disabled'); }
    });
}

// Preview body: full bank PDF if the site exposes one, else compte rendu +
// abstract, else the auto-generated FinBERT report. Async so selection stays snappy.
async function fillPreviewBody(p) {
    const el = () => (state.selectedId === p.id ? document.getElementById('pv-body') : null);
    try {
        const info = await (await fetch(`${API}/papers/${p.id}/pdfinfo`)).json();
        const b = el();
        if (!b) return;
        if (info.pdf) {
            b.innerHTML = `<div class="pv-label">Full article · PDF</div>
                <iframe class="report-frame" src="${API}/papers/${p.id}/pdf" title="article"></iframe>`;
            return;
        }
    } catch (e) { /* fall through */ }
    const b = el();
    if (!b) return;
    if (p.summary || p.abstract) {
        b.innerHTML = `${p.summary ? `<div class="pv-label">Compte rendu · FinBERT</div><div class="pv-sum">${esc(p.summary)}</div>` : ''}
            ${p.abstract ? `<div class="pv-label">Abstract</div><div class="pv-abs">${esc(p.abstract)}</div>` : ''}`;
    } else {
        b.innerHTML = `<div class="pv-label">FinBERT report</div>
            <iframe class="report-frame" src="${API}/papers/${p.id}/report" title="report"></iframe>`;
    }
}

function moveSelection(dir) {
    const list = currentList();
    if (!list.length) return;
    let i = list.findIndex(p => p.id === state.selectedId);
    i = Math.max(0, Math.min(list.length - 1, (i < 0 ? 0 : i + dir)));
    selectPaper(list[i].id);
    const row = document.querySelector(`.feed-row[data-id="${list[i].id}"]`);
    if (row) row.scrollIntoView({ block: 'nearest' });
}

function renderListRowDot(id) {
    const p = state.papers.find(x => x.id === id);
    const row = document.querySelector(`.feed-row[data-id="${id}"] .fr-dot`);
    if (p && row) row.style.background = p.sentiment_label === 'positive' ? 'var(--up)' : p.sentiment_label === 'negative' ? 'var(--down)' : 'var(--muted)';
}

function updateNewCount() {
    const n = state.papers.filter(isNew).length;
    const el = $('new-count');
    if (n > 0) { el.textContent = `${n} NEW`; el.classList.remove('hidden'); }
    else el.classList.add('hidden');
}

// ---- tabs ----
const PANELS = {
    feed: 'papers-container', centralbanks: 'cb-panel', banks: 'banks-panel',
    funds: 'funds-panel', transactions: 'tx-panel', desks: 'desks-panel',
    calendar: 'calendar-panel', digest: 'digest-panel',
};
function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('tab-active', t.dataset.tab === tab));
    $('empty-state').classList.add('hidden');
    $('feed-controls').style.visibility = (tab === 'feed') ? 'visible' : 'hidden';
    for (const [t, id] of Object.entries(PANELS)) $(id).classList.toggle('hidden', t !== tab);
    if (tab === 'feed') { $('results-count').textContent = `${currentList().length} record(s)`; renderCards(); }
    else if (tab === 'centralbanks') loadCentralBanks();
    else if (tab === 'banks') loadInstitutions('commercial_bank', 'banks-panel', 'commercial banks');
    else if (tab === 'funds') loadInstitutions('fund', 'funds-panel', 'funds');
    else if (tab === 'transactions') loadTransactions();
    else if (tab === 'desks') loadDesks();
    else if (tab === 'calendar') loadEvents();
    else if (tab === 'digest') loadDigest();
}

// ---- transactions (FX calls extracted from bank/fund research) ----
async function loadTransactions() {
    const panel = $('tx-panel');
    const days = $('filter-horizon').value || '120';
    $('results-count').textContent = 'transactions';
    panel.innerHTML = '<div class="text-brand-textMuted text-xs">loading FX calls…</div>';
    try {
        const d = await (await fetch(`${API}/transactions?days=${days}&limit=200`)).json();
        const head = `<div class="flex items-center justify-between mb-3">
            <span class="text-[10px] text-brand-textMuted uppercase tracking-widest">FX bias · from research</span>
            <span class="text-[10px] text-brand-textMuted">${d.count} · ${d.window_days}d</span></div>`;
        if (!d.calls.length) { panel.innerHTML = head + '<div class="text-brand-textMuted text-xs p-6">No FX calls in window.</div>'; return; }
        const rows = d.calls.map(c => {
            const bc = c.bias === 'bullish' ? 'var(--up)' : c.bias === 'bearish' ? 'var(--down)' : 'var(--txt2)';
            const arrow = c.bias === 'bullish' ? '▲' : c.bias === 'bearish' ? '▼' : '■';
            const href = c.url || '#';
            return `<tr class="tx-row" data-id="${c.paper_id}">
                <td class="tx-date tabular-nums">${fmtDate(c.date)}</td>
                <td class="tx-inst">${esc(c.institution)}</td>
                <td><span class="tx-pair">${esc(c.pair)}</span></td>
                <td class="tx-bias" style="color:${bc}">${arrow} ${c.bias.toUpperCase()}</td>
                <td class="tx-thesis"><a href="${esc(href)}" target="_blank">${esc(c.thesis)}</a></td>
            </tr>`;
        }).join('');
        panel.innerHTML = head + `<table class="tx-table"><thead><tr>
            <th>Date</th><th>Institution</th><th>Pair</th><th>Bias</th><th>Thesis</th></tr></thead>
            <tbody>${rows}</tbody></table>
            <div class="text-[10px] text-brand-textMuted mt-4 leading-relaxed">Broker tickets (entry · take-profit · stop) are not public — they sit in <span class="text-brand-textSecondary">eFXplus / eFXdata</span> (paid, aggregates sell-side desk orders) or behind desk logins. Shown here: directional bias read from each note. Plug an eFXplus key to add real levels.</div>`;
        panel.querySelectorAll('.tx-row').forEach(r => r.addEventListener('click', (e) => {
            if (e.target.tagName === 'A') return;
            const p = state.papers.find(x => x.id === r.dataset.id);
            if (p) { switchTab('feed'); selectPaper(p.id); }
            else { $('filter-search').value = ''; }
        }));
    } catch (e) {
        panel.innerHTML = '<div class="text-brand-down text-xs">Could not load transactions.</div>';
    }
}

// ---- banks / funds (articles grouped by institution, by date) ----
async function loadInstitutions(category, panelId, label) {
    const panel = $(panelId);
    const days = $('filter-horizon').value || '180';
    $('results-count').textContent = label;
    panel.innerHTML = `<div class="text-brand-textMuted text-xs">loading ${esc(label)}…</div>`;
    if (!state.catalog) await loadCatalog();
    try {
        const d = await (await fetch(`${API}/by-institution?category=${category}&days=${days}&per=8`)).json();
        // index API groups (institutions that actually have articles) by name
        const byInst = {};
        d.groups.forEach(g => { byInst[g.institution] = g; });
        // full institution list from the catalog, so EVERY desk shows up
        const cat = state.catalog ? state.catalog.sources.filter(s => s.category === category) : [];
        const zones = (state.catalog && state.catalog.zones) || {};
        const insts = [];
        const seen = new Set();
        cat.forEach(s => {
            if (seen.has(s.institution)) return;
            seen.add(s.institution);
            insts.push({ institution: s.institution, zone: s.zone, kind: s.kind, portal: s.portal, group: byInst[s.institution] || null });
        });
        // covered (with articles) first, by article count desc; then portals A–Z
        insts.sort((a, b) => {
            const ca = a.group ? a.group.count : -1, cb = b.group ? b.group.count : -1;
            if (ca !== cb) return cb - ca;
            return a.institution.localeCompare(b.institution);
        });
        const covered = insts.filter(i => i.group).length;
        const totalArts = d.groups.reduce((a, g) => a + g.count, 0);
        const head = `<div class="flex items-center justify-between mb-3">
            <span class="text-[10px] text-brand-textMuted uppercase tracking-widest">${totalArts} articles · ${covered} live desks</span>
            <span class="text-[10px] text-brand-textMuted">${d.window_days}d</span></div>`;
        const cards = insts.map(it => {
            const g = it.group;
            if (!g) {
                // portal-only desk: no public feed -> navigation card
                return `<a href="${esc(it.portal || '#')}" target="_blank" class="cb-card inst-portal">
                    <div class="cb-head">
                        <div class="min-w-0"><div class="cb-zone">${esc(it.institution)}</div>
                        <div class="cb-label">${it.zone}</div></div>
                        <span class="kind-badge kind-portal">PORTAL ↗</span>
                    </div>
                    <div class="inst-empty">Open portal ↗</div></a>`;
            }
            const sc = g.net_score == null ? 'score-neu' : g.net_score > 0.03 ? 'score-pos' : g.net_score < -0.03 ? 'score-neg' : 'score-neu';
            const arrow = g.net_score == null ? '' : g.net_score > 0.03 ? '▲' : g.net_score < -0.03 ? '▼' : '■';
            const scoreTxt = g.net_score == null ? '' : (g.net_score >= 0 ? '+' : '') + g.net_score.toFixed(2);
            const kindB = g.kind === 'rss' ? '<span class="kind-badge kind-rss">RSS</span>' : '<span class="kind-badge kind-scrape">SCRAPED</span>';
            const items = g.latest.map(p => {
                const dot = p.sentiment_label === 'positive' ? 'var(--up)' : p.sentiment_label === 'negative' ? 'var(--down)' : '#2e3037';
                const href = p.source_url || p.pdf_url || '#';
                return `<a href="${esc(href)}" target="_blank" class="cb-item">
                    <span class="cb-dot" style="background:${dot}"></span>
                    <span class="cb-body">
                        <span class="cb-meta"><span class="cb-date tabular-nums">${fmtDate(p.published_date)}</span></span>
                        <span class="cb-title">${esc(p.title)}</span>
                    </span></a>`;
            }).join('');
            return `<div class="cb-card">
                <div class="cb-head">
                    <div class="min-w-0"><div class="cb-zone">${esc(it.institution)}</div>
                    <div class="cb-label">${it.zone} · ${g.count}</div></div>
                    <div class="flex items-center gap-2">${kindB}${scoreTxt ? `<span class="cb-net ${sc} tabular-nums">${arrow} ${scoreTxt}</span>` : ''}</div>
                </div>
                <div class="cb-list">${items}</div>
            </div>`;
        }).join('');
        panel.innerHTML = head + `<div class="cb-grid">${cards}</div>`;
    } catch (e) {
        panel.innerHTML = `<div class="text-brand-down text-xs">Could not load ${esc(label)}.</div>`;
    }
}

// ---- central banks (by monetary zone) ----
async function loadCentralBanks() {
    const panel = $('cb-panel');
    const days = $('filter-horizon').value || '90';
    $('results-count').textContent = 'central banks';
    panel.innerHTML = '<div class="text-brand-textMuted text-xs">loading central-bank output…</div>';
    try {
        const d = await (await fetch(`${API}/central-banks?days=${days}&per_zone=6`)).json();
        const head = `<div class="flex items-center justify-between mb-3">
            <span class="text-[10px] text-brand-textMuted uppercase tracking-widest">By monetary zone</span>
            <span class="text-[10px] text-brand-textMuted">${d.window_days}d</span></div>`;
        const cards = d.zones.map(z => {
            const sc = z.net_score == null ? 'score-neu' : z.net_score > 0.03 ? 'score-pos' : z.net_score < -0.03 ? 'score-neg' : 'score-neu';
            const arrow = z.net_score == null ? '·' : z.net_score > 0.03 ? '▲' : z.net_score < -0.03 ? '▼' : '■';
            const scoreTxt = z.net_score == null ? 'n/a' : (z.net_score >= 0 ? '+' : '') + z.net_score.toFixed(2);
            const items = z.latest.length ? z.latest.map(p => {
                const dot = p.sentiment_label === 'positive' ? 'var(--up)' : p.sentiment_label === 'negative' ? 'var(--down)' : '#2e3037';
                const href = p.source_url || p.pdf_url || '#';
                return `<a href="${esc(href)}" target="_blank" class="cb-item">
                    <span class="cb-dot" style="background:${dot}"></span>
                    <span class="cb-body">
                        <span class="cb-meta"><span class="cb-date tabular-nums">${fmtDate(p.published_date)}</span><span class="cb-src">${esc(p.source)}</span></span>
                        <span class="cb-title">${esc(p.title)}</span>
                    </span></a>`;
            }).join('') : '<div class="text-[11px] text-brand-textMuted px-1 py-2">No output in window.</div>';
            return `<div class="cb-card">
                <div class="cb-head" data-zone="${z.zone}" title="Filter feed → ${z.ccy}">
                    <div class="min-w-0">
                        <div class="cb-zone">${z.ccy} <span class="cb-auth">${esc(z.authority)}</span></div>
                        <div class="cb-label">${z.count} docs</div>
                    </div>
                    <span class="cb-net ${sc} tabular-nums">${arrow} ${scoreTxt}</span>
                </div>
                <div class="cb-list">${items}</div>
            </div>`;
        }).join('');
        panel.innerHTML = head + `<div class="cb-grid">${cards}</div>`;
        panel.querySelectorAll('.cb-head').forEach(el => el.addEventListener('click', () => {
            $('filter-category').value = 'central_bank';
            $('filter-region').value = '';
            $('filter-source').value = '';
            switchTab('feed');
            // zone -> region tag for the feed filter where one exists
            const z = el.dataset.zone;
            const map = { USD: 'US', EUR: 'EU', GBP: 'UK', JPY: 'Japan' };
            if (map[z]) $('filter-region').value = map[z];
            loadPapers();
        }));
    } catch (e) {
        panel.innerHTML = '<div class="text-brand-down text-xs">Could not load central-bank data.</div>';
    }
}

// ---- desks (provenance map) ----
async function loadDesks() {
    const panel = $('desks-panel');
    $('results-count').textContent = 'source map';
    if (!state.catalog) await loadCatalog();
    if (!state.catalog) { panel.innerHTML = '<div class="text-brand-down text-xs">Could not load source map.</div>'; return; }
    const { categories, zones, sources } = state.catalog;
    const order = ['central_bank', 'commercial_bank', 'fund', 'multilateral', 'academic'];
    const kindBadge = (k) => `<span class="kind-badge kind-${k === 'article_scrape' ? 'scrape' : k}">${k === 'rss' ? 'LIVE RSS' : (k === 'scrape' || k === 'article_scrape') ? 'SCRAPED' : 'PORTAL'}</span>`;
    const total = sources.reduce((a, s) => a + (s.count || 0), 0);
    const head = `<div class="flex items-center justify-between mb-3">
        <span class="text-[10px] text-brand-textMuted uppercase tracking-widest">Source map · ${sources.length} desks · ${total} docs</span></div>`;
    const sections = order.filter(c => categories[c]).map(cat => {
        const rows = sources.filter(s => s.category === cat)
            .sort((a, b) => (b.count - a.count) || a.name.localeCompare(b.name))
            .map(s => {
                const href = s.portal || s.feed || '#';
                return `<a href="${esc(href)}" target="_blank" class="desk-row">
                    <span class="desk-zone">${s.zone}</span>
                    <span class="desk-name">${esc(s.institution)}<span class="desk-sub">${esc(s.name)} · ${esc(s.domain)}</span></span>
                    ${kindBadge(s.kind)}
                    <span class="desk-count tabular-nums">${s.count || '—'}</span>
                    <span class="desk-go">↗</span>
                </a>`;
            }).join('');
        const n = sources.filter(s => s.category === cat).length;
        return `<div class="desk-section">
            <div class="desk-cat">${esc(categories[cat])} <span class="desk-cat-n">${n}</span></div>
            ${rows}</div>`;
    }).join('');
    panel.innerHTML = head + sections +
        `<div class="text-[10px] text-brand-textMuted mt-5 leading-relaxed">
            <span class="kind-badge kind-rss">LIVE RSS</span> auto-ingested every sync ·
            <span class="kind-badge kind-scrape">SCRAPED</span> best-effort listing scrape ·
            <span class="kind-badge kind-portal">PORTAL</span> JS/login-gated desk — linked for navigation, not auto-pulled.</div>`;
}

// ---- auto-refresh ----
function toggleAuto() {
    const btn = $('btn-auto');
    if (state.autoTimer) {
        clearInterval(state.autoTimer); state.autoTimer = null;
        btn.classList.remove('active-btn');
    } else {
        state.autoTimer = setInterval(() => { loadPapers(); loadBoard(); loadStats(); }, 300000);
        btn.classList.add('active-btn');
    }
}

// ---- calendar ----
async function loadEvents() {
    const panel = $('calendar-panel');
    $('results-count').textContent = 'macro calendar';
    panel.innerHTML = '<div class="text-brand-textMuted text-xs">loading events…</div>';
    try {
        const events = await (await fetch(`${API}/events?limit=16`)).json();
        panel.innerHTML = `<div class="text-[10px] text-brand-textMuted uppercase tracking-widest mb-3">Upcoming macro events · scheduled releases</div>` +
            events.map(e => {
                const cd = e.days_until === 0 ? 'TODAY' : `${e.days_until}d`;
                const imp = e.importance === 'high' ? 'color:var(--down)' : 'color:var(--txt2)';
                return `<div class="evt">
                    <div class="evt-cd"><div class="d">${e.days_until}</div><div class="u">${e.days_until === 1 ? 'day' : 'days'}</div></div>
                    <div>
                        <div class="evt-name">${esc(e.name)}</div>
                        <div class="evt-meta"><span style="${imp}">●</span> ${e.bloc} · ${esc(e.theme)} · ${e.date}</div>
                    </div>
                    <div class="evt-rel" data-region="${e.region}" data-theme="${e.theme}">${e.related_count} related ↗</div>
                </div>`;
            }).join('');
        panel.querySelectorAll('.evt-rel').forEach(el => el.addEventListener('click', () => {
            $('filter-region').value = el.dataset.region;
            $('filter-thematic').value = el.dataset.theme;
            switchTab('feed'); loadPapers();
        }));
    } catch (e) {
        panel.innerHTML = '<div class="text-brand-down text-xs">Could not load events.</div>';
    }
}

// ---- digest ----
async function loadDigest() {
    const panel = $('digest-panel');
    $('results-count').textContent = 'watchlist digest';
    const ids = [...state.saved];
    if (!ids.length) {
        panel.innerHTML = `<div class="dg-hero text-center"><div class="text-brand-textSecondary text-sm">Your watchlist is empty.</div>
            <div class="text-brand-textMuted text-xs mt-2">Save papers with ☆ in the feed to build a digest of their net sentiment.</div></div>`;
        return;
    }
    panel.innerHTML = '<div class="text-brand-textMuted text-xs">building digest…</div>';
    try {
        const d = await (await fetch(`${API}/digest`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        })).json();
        const col = d.net_label === 'bullish' ? 'var(--up)' : d.net_label === 'bearish' ? 'var(--down)' : 'var(--txt2)';
        const arrow = d.net_label === 'bullish' ? '▲' : d.net_label === 'bearish' ? '▼' : '■';
        const blocRows = d.by_bloc.map(b => {
            const bc = b.score > 0.03 ? 'var(--up)' : b.score < -0.03 ? 'var(--down)' : 'var(--txt2)';
            return `<div class="dg-row"><span class="t">${b.bloc}</span><span style="color:${bc}" class="tabular-nums">${b.score >= 0 ? '+' : ''}${b.score.toFixed(2)} · ${b.count} docs</span></div>`;
        }).join('') || '<div class="text-brand-textMuted text-xs">no bloc data</div>';
        const movers = (arr, c) => arr.map(p => `<div class="dg-row"><span class="t">${esc(p.title)}</span><span style="color:${c}" class="tabular-nums">${p.score >= 0 ? '+' : ''}${Number(p.score).toFixed(2)}</span></div>`).join('') || '<div class="text-brand-textMuted text-xs">—</div>';
        panel.innerHTML = `
            <div class="dg-hero">
                <div class="text-[10px] text-brand-textMuted uppercase tracking-widest mb-2">Watchlist net bias · ${d.analyzed}/${d.count} scored</div>
                <div class="flex items-end gap-4">
                    <div class="dg-net" style="color:${col}">${arrow} ${d.net_score >= 0 ? '+' : ''}${d.net_score.toFixed(2)}</div>
                    <div class="text-sm uppercase tracking-wider pb-1" style="color:${col}">${d.net_label}</div>
                </div>
                <div class="text-xs text-brand-textMuted mt-2"><span class="text-brand-up">▲${d.positive} bullish</span> · ■${d.neutral} neutral · <span class="text-brand-down">▼${d.negative} bearish</span></div>
            </div>
            <div class="dg-section-label">By currency bloc</div>${blocRows}
            <div class="dg-section-label">Most bullish</div>${movers(d.top_bullish, 'var(--up)')}
            <div class="dg-section-label">Most bearish</div>${movers(d.top_bearish, 'var(--down)')}
            <div class="text-[10px] text-brand-textMuted mt-5">Daily email: set SMTP env vars + cron <span class="text-brand-textSecondary">scripts/digest.py</span> (see README).</div>`;
    } catch (e) {
        panel.innerHTML = '<div class="text-brand-down text-xs">Could not build digest.</div>';
    }
}

function exportCsv() {
    const list = currentList();
    if (!list.length) return;
    const head = ['date', 'source', 'region', 'sentiment', 'score', 'title', 'authors', 'url'];
    const cell = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const rows = list.map(p => [
        p.published_date ? p.published_date.slice(0, 10) : '', p.source, blocOf(p),
        p.sentiment_label || '', p.sentiment_score ?? '', p.title, p.authors || '',
        p.source_url || p.pdf_url || '',
    ].map(cell).join(','));
    const blob = new Blob([head.join(',') + '\n' + rows.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `macro-research-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
}
