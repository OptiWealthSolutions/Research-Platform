const API = 'http://localhost:8001/api';

const FILTERS = {
    horizon: { opts: [['7', '7D'], ['14', '14D'], ['30', '30D'], ['90', '90D'], ['365', '1Y'], ['', 'ALL']], def: '30' },
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
    tab: 'feed', lastVisit: readVisit(), autoTimer: null,
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
    refreshAll();
    setInterval(loadBoard, 60000);
});

function buildFilters() {
    for (const [key, cfg] of Object.entries(FILTERS)) {
        $(`filter-${key}`).innerHTML = cfg.opts.map(([v, l]) => `<option value="${v}"${v === cfg.def ? ' selected' : ''}>${l}</option>`).join('');
    }
}

function wireEvents() {
    let t;
    const debounced = () => { clearTimeout(t); t = setTimeout(loadPapers, 250); };
    $('filter-search').addEventListener('input', debounced);
    ['horizon', 'source', 'thematic', 'region', 'pair', 'sentiment'].forEach(k =>
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
            `${s.total} docs · ${s.analyzed} scored · 7d:${s.last_week}<br>` +
            `<span class="text-brand-up">▲${sent.positive || 0}</span> <span class="text-brand-textMuted">■${sent.neutral || 0}</span> <span class="text-brand-down">▼${sent.negative || 0}</span>`;
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
                    <span style="width:${w(bl.neutral)};background:#33405c"></span>
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

function renderCards() {
    const list = currentList();
    const c = $('papers-container');
    if (!list.length) {
        c.innerHTML = '';
        showEmpty(state.savedOnly ? 'No saved research yet. Click ☆ on a card to save it.' : 'No matching research. Widen the horizon or run SYNC.');
        $('results-count').textContent = '0 records';
        return;
    }
    $('empty-state').classList.add('hidden');
    $('results-count').textContent = `${list.length} record(s)`;
    updateNewCount();

    c.innerHTML = list.map(p => {
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
        const newTag = isNew(p) ? '<span class="new-tag">NEW</span>' : '';
        const pairs = (p.currency_pairs || []).map(fx => `<span class="pair-chip">${fx}</span>`).join('');

        return `<article class="card" data-id="${p.id}">
            <div class="flex justify-between gap-4">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center flex-wrap gap-2.5 mb-1.5">
                        ${newTag}
                        <span class="text-[11px] text-brand-textMuted tabular-nums">${fmtDate(p.published_date)}</span>
                        <span class="text-[11px] text-brand-blue uppercase tracking-wider">${esc(p.source)}</span>
                        <span class="reg-chip">${blocOf(p)}</span>
                        ${pairs}
                        ${badge}
                        ${interest}
                    </div>
                    <h3 class="card-title text-[16px] font-semibold leading-snug mb-1">${esc(p.title)}</h3>
                    <p class="card-auth text-[12px] mb-2.5">${esc(p.authors || 'Unknown')}</p>
                    ${tags ? `<div class="mb-2">${tags}</div>` : ''}
                    ${kws ? `<div class="mb-2.5">${kws}</div>` : ''}
                    ${detStr ? `<div class="text-[10px] text-brand-textMuted tabular-nums mb-2">FinBERT · ${detStr}</div>` : ''}
                    <div class="flex flex-wrap gap-x-5 gap-y-1">
                        ${p.summary ? `<button class="toggle text-[11px] text-brand-amber/90 hover:text-brand-amber" data-tgt="sum">[+] Compte rendu</button>` : ''}
                        ${p.abstract ? `<button class="toggle text-[11px] text-brand-textSecondary hover:text-brand-textPrimary" data-tgt="abs">[+] Abstract</button>` : ''}
                    </div>
                    ${p.summary ? `<div class="fold sum-c detail-text" style="border-left:2px solid rgba(245,158,11,.4);padding-left:12px;margin-top:8px">${esc(p.summary)}</div>` : ''}
                    ${p.abstract ? `<div class="fold abs-c detail-text" style="border-left:2px solid var(--border);padding-left:12px;margin-top:8px">${esc(p.abstract)}</div>` : ''}
                </div>
                <div class="shrink-0 w-36 flex flex-col gap-2 items-stretch">
                    <button class="act save ${saved ? 'saved' : ''}">${saved ? '★ SAVED' : '☆ SAVE'}</button>
                    <a href="${API}/papers/${p.id}/report" target="_blank" class="act primary">▤ REPORT PDF</a>
                    <a href="${API}/papers/${p.id}/download" target="_blank" class="act ${p.pdf_url ? '' : 'disabled'}">⤓ SOURCE PDF</a>
                    <a href="${esc(p.source_url) || '#'}" target="_blank" class="act ${p.source_url ? '' : 'disabled'}">↗ ARTICLE</a>
                    ${p.analyzed_at ? '' : `<button class="act analyze">⚡ ANALYZE</button>`}
                </div>
            </div>
        </article>`;
    }).join('');

    bindCards();
}

function bindCards() {
    document.querySelectorAll('.card .toggle').forEach(btn => btn.addEventListener('click', (e) => {
        const card = e.target.closest('.card');
        const cls = e.target.dataset.tgt === 'sum' ? '.sum-c' : '.abs-c';
        const el = card.querySelector(cls);
        const open = el.classList.toggle('open');
        const label = e.target.dataset.tgt === 'sum' ? 'Compte rendu' : 'Abstract';
        e.target.textContent = (open ? '[-] ' : '[+] ') + label;
    }));
    document.querySelectorAll('.card .save').forEach(btn => btn.addEventListener('click', (e) => {
        const id = e.target.closest('.card').dataset.id;
        if (state.saved.has(id)) state.saved.delete(id); else state.saved.add(id);
        persistSaved();
        const on = state.saved.has(id);
        e.target.textContent = on ? '★ SAVED' : '☆ SAVE';
        e.target.classList.toggle('saved', on);
        if (state.savedOnly && !on) renderCards();
    }));
    document.querySelectorAll('.card .analyze').forEach(btn => btn.addEventListener('click', async (e) => {
        const card = e.target.closest('.card'); const id = card.dataset.id;
        e.target.textContent = '⏳ …'; e.target.classList.add('disabled');
        try {
            const full = await (await fetch(`${API}/papers/${id}/analyze`, { method: 'POST' })).json();
            const i = state.papers.findIndex(p => p.id === id);
            if (i >= 0) state.papers[i] = full;
            renderCards(); loadBoard(); loadStats();
        } catch (err) { e.target.textContent = '⚡ ANALYZE'; e.target.classList.remove('disabled'); }
    }));
}

function updateNewCount() {
    const n = state.papers.filter(isNew).length;
    const el = $('new-count');
    if (n > 0) { el.textContent = `${n} NEW`; el.classList.remove('hidden'); }
    else el.classList.add('hidden');
}

// ---- tabs ----
function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('tab-active', t.dataset.tab === tab));
    $('papers-container').classList.toggle('hidden', tab !== 'feed');
    $('empty-state').classList.add('hidden');
    $('feed-controls').style.visibility = (tab === 'feed') ? 'visible' : 'hidden';
    $('calendar-panel').classList.toggle('hidden', tab !== 'calendar');
    $('digest-panel').classList.toggle('hidden', tab !== 'digest');
    if (tab === 'feed') { $('results-count').textContent = `${currentList().length} record(s)`; renderCards(); }
    else if (tab === 'calendar') loadEvents();
    else if (tab === 'digest') loadDigest();
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
