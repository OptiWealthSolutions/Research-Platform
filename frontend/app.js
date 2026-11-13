const API = 'http://localhost:8001/api';

const FILTERS = {
    horizon: {
        opts: [['7', '7D'], ['14', '14D'], ['30', '30D'], ['90', '90D'], ['365', '1Y'], ['', 'ALL']],
        def: '30',
    },
    category: {
        opts: [
            ['', 'ALL TYPES'],
            ['central_bank', 'CENTRAL BANKS'],
            ['commercial_bank', 'COMMERCIAL BANKS'],
            ['fund', 'FUNDS'],
            ['multilateral', 'MULTILATERAL'],
            ['academic', 'ACADEMIC'],
            ['macro_news', 'MACRO NEWS'],
        ],
        def: '',
    },
    source: {
        opts: [
            ['', 'ALL SRC'],
            ['Fed', 'FED'],
            ['ECB', 'ECB'],
            ['Bank of England', 'BOE'],
            ['Bank of Japan', 'BOJ'],
            ['Bank of Canada', 'BOC'],
            ['SNB', 'SNB'],
            ['Riksbank', 'RIKSBANK'],
            ['RBI', 'RBI'],
            ['BIS', 'BIS'],
            ['BEA', 'BEA'],
            ['Eurostat', 'EUROSTAT'],
            ['ING', 'ING THINK'],
            ['Wells Fargo', 'WELLS FARGO'],
            ['Goldman Sachs', 'GOLDMAN SACHS'],
            ['J.P. Morgan', 'J.P. MORGAN'],
            ['HSBC', 'HSBC'],
            ['Danske Bank', 'DANSKE BANK'],
            ['Man Institute', 'MAN GROUP'],
            ['Robeco', 'ROBECO'],
            ['Amundi', 'AMUNDI'],
            ['NBER', 'NBER'],
            ['NEP', 'RePEc/NEP'],
            ['PIIE', 'PIIE'],
            ['Bruegel', 'BRUEGEL'],
            ['SSRN', 'SSRN'],
            ['CNBC', 'CNBC'],
            ['WSJ', 'WSJ'],
            ['FT', 'FT'],
            ['Financial Times', 'FINANCIAL TIMES'],
            ['MarketWatch', 'MARKETWATCH'],
            ['Seeking Alpha', 'SEEKING ALPHA'],
            ['Investing', 'INVESTING.COM'],
            ['FXStreet', 'FXSTREET'],
            ['ForexLive', 'FOREXLIVE'],
            ['Calculated Risk', 'CALCULATED RISK'],
        ],
        def: '',
    },
    thematic: {
        opts: [
            ['', 'ALL THEMES'],
            ['Monetary Policy', 'Monetary'],
            ['Inflation', 'Inflation'],
            ['Financial Stability', 'Fin. Stability'],
            ['Liquidity', 'Liquidity'],
            ['Labor Market', 'Labor'],
            ['Fiscal Policy', 'Fiscal'],
            ['Digital Currency', 'Digital FX'],
            ['Macro-Finance', 'Macro-Fin'],
        ],
        def: '',
    },
    region: {
        opts: [
            ['', 'ALL REGIONS'],
            ['US', 'USD'],
            ['EU', 'EUR'],
            ['UK', 'GBP'],
            ['Japan', 'JPY'],
            ['China', 'CNY'],
            ['Switzerland', 'CHF'],
            ['Sweden', 'SEK'],
            ['India', 'INR'],
            ['Global', 'GLOBAL'],
        ],
        def: '',
    },
    pair: {
        opts: [
            ['', 'ALL PAIRS'],
            ['EUR/USD', 'EUR/USD'],
            ['USD/JPY', 'USD/JPY'],
            ['GBP/USD', 'GBP/USD'],
            ['USD/CHF', 'USD/CHF'],
            ['USD/CAD', 'USD/CAD'],
            ['AUD/USD', 'AUD/USD'],
            ['USD/CNY', 'USD/CNY'],
            ['EUR/GBP', 'EUR/GBP'],
            ['EUR/JPY', 'EUR/JPY'],
            ['GBP/JPY', 'GBP/JPY'],
            ['DXY', 'DXY'],
        ],
        def: '',
    },
    sentiment: {
        opts: [
            ['', 'ALL SENT'],
            ['positive', 'BULLISH'],
            ['neutral', 'NEUTRAL'],
            ['negative', 'BEARISH'],
        ],
        def: '',
    },
};

const SORT_OPTIONS = [
    ['published_date', 'Date'],
    ['interest_score', 'Interest'],
    ['impact_score', 'Impact'],
    ['sentiment_score', 'Score'],
    ['sentiment_abs', 'Conviction'],
    ['source', 'Source A-Z'],
    ['title', 'Title A-Z'],
];

const BLOC_ORDER = [['US', 'USD'], ['EU', 'EUR'], ['UK', 'GBP'], ['Japan', 'JPY'], ['China', 'CNY'], ['Global', 'GLB']];

const SAVE_KEY = 'mrt_saved';
const VISIT_KEY = 'mrt_last_visit';

const state = {
    papers: [],
    sort: 'published_date',
    savedOnly: false,
    saved: loadSaved(),
    tab: 'feed',
    lastVisit: readVisit(),
    autoTimer: null,
    catalog: null,
    selectedId: null,
    viewMode: 'grid',
    quantMeta: null,
    quantPapers: [],
    quantCat: 'q-fin.TR',
    quantAsset: '',
    quantStrategy: '',
    quantSort: 'submittedDate',
};

const $ = (id) => document.getElementById(id);

function loadSaved() {
    try { return new Set(JSON.parse(localStorage.getItem(SAVE_KEY) || '[]')); }
    catch { return new Set(); }
}

function persistSaved() {
    localStorage.setItem(SAVE_KEY, JSON.stringify([...state.saved]));
    fetch(`${API}/watchlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [...state.saved] }),
    }).catch(() => {});
}

function readVisit() {
    const prev = parseInt(localStorage.getItem(VISIT_KEY) || '0', 10);
    localStorage.setItem(VISIT_KEY, Date.now().toString());
    return prev || Date.now();
}

function isNew(p) {
    return p.created_at && Date.parse(p.created_at) > state.lastVisit;
}

function parseLocalDate(d) {
    if (!d) return null;
    const s = String(d).replace(/Z$/, '');
    const dt = new Date(s);
    return isNaN(dt.getTime()) ? null : dt;
}

function fmtDate(d) {
    const dt = parseLocalDate(d);
    if (!dt) return d ? String(d).slice(0, 10) : 'Unknown';
    return dt.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function fmtTime(d) {
    const dt = parseLocalDate(d);
    if (!dt) return '';
    const h24 = String(dt.getHours()).padStart(2, '0');
    const mStr = String(dt.getMinutes()).padStart(2, '0');
    return `${h24}:${mStr}`;
}

function fmtDateTime(d) {
    const dt = parseLocalDate(d);
    if (!dt) return fmtDate(d);
    return `${fmtDate(d)} ${fmtTime(d)}`;
}

function esc(s) {
    let t = String(s ?? '');
    t = t.replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1F9FF}\u{1FA70}-\u{1FAFF}\u{1F000}-\u{1F02F}\u{1F0A0}-\u{1F0FF}\u{1F100}-\u{1F1FF}]/gu, '');
    t = t.replace(/·\s*[▲■▼▶▪▫●◆◆✓✗✔✕★☆]\s*\d+/g, '');
    t = t.replace(/\s*·\s*/g, ' ').replace(/\s{2,}/g, ' ').trim();
    return t.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function srcMeta(name) {
    if (!state.catalog || !state.catalog.sources) return null;
    return state.catalog.sources.find(s => s.name === name) || null;
}

function categoryLabelOf(p) {
    const meta = srcMeta(p.source);
    if (meta && state.catalog && state.catalog.categories) {
        return state.catalog.categories[meta.category] || meta.category || 'Research';
    }
    if (p.category) return p.category;
    return (p.thematic_tags && p.thematic_tags[0]) || 'Macro Research';
}

function blocOf(p) {
    if (p.area && p.area.length) return p.area.join(' · ');
    const t = p.country_tags || [];
    for (const [r, c] of BLOC_ORDER) if (t.includes(r)) return c;
    return 'GLB';
}

function sentimentChip(p) {
    const lbl = p.sentiment_label;
    if (!lbl) return '';
    const dotCls = lbl === 'positive' ? 'pos' : lbl === 'negative' ? 'neg' : 'neu';
    const sentScore = p.sentiment_score != null
        ? `${p.sentiment_score >= 0 ? '+' : ''}${Number(p.sentiment_score).toFixed(2)}`
        : '';
    const cls = lbl === 'positive' ? 'pos-pill' : lbl === 'negative' ? 'neg-pill' : 'neu-pill';
    const label = lbl === 'positive' ? 'BULLISH' : lbl === 'negative' ? 'BEARISH' : 'NEUTRAL';
    return `<span class="pill ${cls}">${label} ${sentScore}</span>`;
}

function impactChip(p) {
    const impVal = p.impact_score != null ? Number(p.impact_score) : null;
    if (impVal == null) return '';
    const impCls = impVal >= 70 ? 'impact-high' : impVal >= 45 ? 'impact-med' : 'impact-low';
    return `<span class="impact-chip ${impCls}">Impact: ${impVal.toFixed(0)}</span>`;
}

function pairsHtml(p) {
    const pairs = (p.currency_pairs || []).slice(0, 4).map(fx => `<span class="pair-chip">${esc(fx)}</span>`).join('');
    return pairs;
}

function articleCard(p) {
    const { title, published_date, source } = p;
    const cat = categoryLabelOf(p);
    const displayDate = fmtDateTime(published_date);
    const summary = p.summary || p.abstract || '';
    const lbl = p.sentiment_label;
    const dotCls = lbl === 'positive' ? 'pos' : lbl === 'negative' ? 'neg' : 'neu';

    const cover = p.image_url || '';
    const coverHtml = cover
        ? `<div class="table-cover" style="background-image:url('${esc(cover)}')"></div>`
        : `<div class="table-cover table-cover-empty ${dotCls}" data-symbol="${dotCls === 'pos' ? '+' : dotCls === 'neg' ? '-' : 'o'}"></div>`;

    const savedTag = state.saved.has(p.id) ? '[SAVED] ' : '';

    return `
        <div class="table-card" data-id="${esc(p.id)}" data-src="${esc(p.source_url || p.pdf_url || '')}">
            ${coverHtml}
            <div class="table-body">
                <div class="table-head">
                    <span class="table-cat"><span class="dot ${dotCls}"></span>${esc(cat)}</span>
                    <span class="table-src">${esc(savedTag)}${esc(source || 'Source')}</span>
                </div>
                <div class="table-title">${esc(title || '')}</div>
                ${summary ? `<div class="table-summary">${esc(summary)}</div>` : ''}
                <div class="table-meta">
                    <span class="table-date">${esc(displayDate)}</span>
                    ${impactChip(p)}
                    ${sentimentChip(p)}
                </div>
                <div class="table-pairs">${pairsHtml(p)}</div>
            </div>
            <div class="table-sentiment-bar ${dotCls}"></div>
        </div>
    `;
}

function listCard(p) {
    const { title, published_date, source } = p;
    const cat = categoryLabelOf(p);
    const displayDate = fmtDateTime(published_date);
    const desc = p.summary || p.abstract || '';
    const activeCls = p.id === state.selectedId ? ' active' : '';
    const lbl = p.sentiment_label;
    const dotCls = lbl === 'positive' ? 'pos' : lbl === 'negative' ? 'neg' : 'neu';
    const sentScore = p.sentiment_score != null
        ? `${p.sentiment_score >= 0 ? '+' : ''}${Number(p.sentiment_score).toFixed(2)}`
        : '';
    const sentChip = lbl
        ? `<span class="pill ${dotCls === 'pos' ? 'pos-pill' : dotCls === 'neg' ? 'neg-pill' : 'neu-pill'}">${lbl === 'positive' ? 'BULLISH' : lbl === 'negative' ? 'BEARISH' : 'NEUTRAL'} ${sentScore}</span>`
        : '';
    const impVal = p.impact_score != null ? Number(p.impact_score) : null;
    const impCls = impVal >= 70 ? 'impact-high' : impVal >= 45 ? 'impact-med' : 'impact-low';
    const impChip = impVal != null
        ? `<span class="impact-chip ${impCls}">Impact: ${impVal.toFixed(0)}</span>`
        : '';
    const pairs = (p.currency_pairs || []).slice(0, 3).map(fx => `<span class="pair-chip">${esc(fx)}</span>`).join('');
    const thesisBadge = p.thesis ? `<span class="pill pos-pill" title="These liee">${esc(p.thesis)}</span>` : '';
    const savedTag = state.saved.has(p.id) ? '[SAVED] ' : '';

    return `
        <div class="card${activeCls}" data-id="${esc(p.id)}">
            <div class="card-header">
                <span class="cat"><span class="dot ${dotCls}"></span>${esc(cat)}</span>
                <span class="lang">${esc(savedTag)}${esc(source || 'Source')}</span>
            </div>
            <div class="card-title">${esc(title || '')}</div>
            ${desc ? `<div class="card-desc">${esc(desc)}</div>` : ''}
            <div class="card-meta">
                <span>${esc(displayDate)}</span>
                ${impChip}
                ${sentChip}
                ${pairs}
                ${thesisBadge}
            </div>
        </div>
    `;
}

function buildFilters() {
    for (const [key, cfg] of Object.entries(FILTERS)) {
        const el = $(`filter-${key}`);
        if (el) {
            el.innerHTML = cfg.opts
                .map(([v, l]) => `<option value="${v}"${v === cfg.def ? ' selected' : ''}>${l}</option>`)
                .join('');
        }
    }
    const sortEl = $('sort-select');
    if (sortEl && !sortEl.options.length) {
        sortEl.innerHTML = SORT_OPTIONS
            .map(([v, l]) => `<option value="${v}"${v === state.sort ? ' selected' : ''}>${l}</option>`)
            .join('');
    }
}

function wireEvents() {
    let t;
    const debounced = () => {
        clearTimeout(t);
        t = setTimeout(() => {
            if (state.tab === 'quant') loadQuantPapers();
            else loadPapers();
        }, 250);
    };

    if ($('filter-search')) $('filter-search').addEventListener('input', debounced);

    ['horizon', 'category', 'source', 'thematic', 'region', 'pair', 'sentiment'].forEach(k => {
        const el = $(`filter-${k}`);
        if (el) {
            el.addEventListener('change', () => {
                if (state.tab === 'quant') loadQuantPapers();
                else {
                    loadPapers();
                    if (k === 'horizon') loadBoard();
                }
            });
        }
    });

    if ($('filter-author')) $('filter-author').addEventListener('input', debounced);
    if ($('filter-doi')) $('filter-doi').addEventListener('input', debounced);
    if ($('filter-domain')) $('filter-domain').addEventListener('input', debounced);
    if ($('filter-date-from')) $('filter-date-from').addEventListener('change', debounced);
    if ($('filter-date-to')) $('filter-date-to').addEventListener('change', debounced);

    if ($('btn-refresh')) $('btn-refresh').addEventListener('click', () => syncAndRefresh());
    if ($('btn-analyze')) $('btn-analyze').addEventListener('click', runFinbert);
    if ($('btn-auto')) $('btn-auto').addEventListener('click', toggleAuto);
    if ($('btn-compare-run')) $('btn-compare-run').addEventListener('click', loadCompare);
    if ($('btn-reader-close')) $('btn-reader-close').addEventListener('click', () => {
        $('reader-modal').classList.add('hidden');
        $('reader-iframe').src = '';
    });
    if ($('reader-backdrop')) $('reader-backdrop').addEventListener('click', () => {
        $('reader-modal').classList.add('hidden');
        $('reader-iframe').src = '';
    });

    if ($('btn-view-toggle')) {
        $('btn-view-toggle').addEventListener('click', () => {
            state.viewMode = state.viewMode === 'grid' ? 'preview' : 'grid';
            $('btn-view-toggle').textContent = state.viewMode === 'grid' ? 'GRID' : 'LISTE';
            renderCards();
        });
    }

    if ($('btn-health-close')) $('btn-health-close').addEventListener('click', () => {
        $('health-modal').classList.add('hidden');
    });
    if ($('health-backdrop')) $('health-backdrop').addEventListener('click', () => {
        $('health-modal').classList.add('hidden');
    });
    if ($('btn-health-dismiss')) $('btn-health-dismiss').addEventListener('click', () => {
        $('health-modal').classList.add('hidden');
    });

    document.querySelectorAll('.tab').forEach(tabEl => {
        tabEl.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab(tabEl.dataset.tab);
        });
    });

    if ($('sort-select')) {
        $('sort-select').addEventListener('change', (e) => {
            state.sort = e.target.value;
            if (state.tab === 'quant') loadQuantPapers();
            else renderCards();
        });
    }

    if ($('btn-export')) $('btn-export').addEventListener('click', exportCsv);

    if ($('btn-saved')) {
        $('btn-saved').addEventListener('click', () => {
            state.savedOnly = !state.savedOnly;
            $('btn-saved').textContent = 'SAVED';
            $('btn-saved').classList.toggle('primary', state.savedOnly);
            renderCards();
        });
    }

    if ($('btn-close-preview')) {
        $('btn-close-preview').addEventListener('click', () => {
            state.selectedId = null;
            document.querySelectorAll('#feed-list .card').forEach(c => c.classList.remove('active'));
            const pv = $('feed-preview');
            if (pv) pv.innerHTML = `<div class="small" style="padding: 24px 0;">Sélectionnez un article dans la liste à gauche pour afficher son aperçu complet.</div>`;
        });
    }

    document.addEventListener('keydown', (e) => {
        const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
        if (e.key === '/' && !typing && $('filter-search')) {
            e.preventDefault();
            $('filter-search').focus();
        } else if (e.key === 'Escape') {
            document.activeElement.blur();
            $('reader-modal').classList.add('hidden');
            $('reader-iframe').src = '';
            $('health-modal').classList.add('hidden');
        } else if (e.key === 'r' && !typing) {
            refreshAll(true);
        }
    });
}

function startClock() {
    const clockEl = $('clock');
    const badgesEl = $('session-badges');
    if (!clockEl && !badgesEl) return;
    const sessions = [['SYD', 22, 7], ['TOK', 0, 9], ['LDN', 8, 17], ['NYC', 13, 22]];
    const tick = () => {
        const now = new Date(), h = now.getUTCHours(), pad = (n) => String(n).padStart(2, '0');
        if (clockEl) clockEl.textContent = `${pad(h)}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())} UTC`;
        if (badgesEl) {
            badgesEl.innerHTML = sessions.map(([name, o, c]) => {
                const open = o < c ? (h >= o && h < c) : (h >= o || h < c);
                return `<span class="px-1 rounded-sm border ${open ? 'text-brand-up border-brand-up/40 bg-brand-up/10' : 'text-brand-textMuted border-brand-border'}">${name}</span>`;
            }).join('');
        }
    };
    tick();
    setInterval(tick, 1000);
}

async function refreshAll(spin) {
    const btn = $('btn-refresh');
    if (spin && btn) btn.classList.add('busy');
    try {
        if (state.tab === 'quant') await loadQuantPapers();
        else await loadPapers();
        await loadBoard();
        await loadStats();
        await loadHealth();
    } finally {
        if (btn) btn.classList.remove('busy');
    }
}

async function syncAndRefresh() {
    const btn = $('btn-refresh');
    if (btn) {
        btn.classList.add('busy');
        btn.textContent = 'SYNCING...';
    }
    try {
        await fetch(`${API}/ingest?analyze=true`, { method: 'POST' }).catch(() => {});
        await refreshAll(false);
    } finally {
        if (btn) {
            btn.classList.remove('busy');
            btn.textContent = 'SYNC';
        }
    }
}

function setStatus(text, cls) {
    const el = $('status-indicator');
    if (!el) return;
    el.textContent = text;
    el.className = `text-[11px] ${cls}`;
}

async function loadStats() {
    const el = $('stats-line');
    try {
        const s = await (await fetch(`${API}/stats`)).json();
        if (el) {
            el.textContent = `${s.total} docs`;
        }
        const existingKpi = document.getElementById('header-stats-kpi');
        if (existingKpi) existingKpi.remove();
    } catch (e) { /* silent */ }
}

async function loadBoard() {
    const boardEl = $('board');
    if (!boardEl) return;
    const days = ($('filter-horizon') && $('filter-horizon').value) || '30';
    try {
        const b = await (await fetch(`${API}/board?days=${days}`)).json();
        if ($('board-window')) $('board-window').textContent = `window ${b.window_days}d`;
        const active = $('filter-region') ? $('filter-region').value : '';
        boardEl.innerHTML = b.blocs.map(bl => {
            const tot = bl.count || 1, w = (n) => `${(n / tot * 100).toFixed(0)}%`;
            const score = bl.count ? (bl.score >= 0 ? '+' : '') + bl.score.toFixed(2) : '-';
            const sc = bl.count === 0 ? 'score-neu' : bl.score > 0.03 ? 'score-pos' : bl.score < -0.03 ? 'score-neg' : 'score-neu';
            return `<div class="bloc ${bl.region === active ? 'active' : ''}" data-region="${bl.region}">
                <div class="flex items-center justify-between">
                    <span class="text-xs font-bold tracking-wide">${bl.bloc}</span>
                    <span class="text-sm font-bold ${sc} tabular-nums">${score}</span>
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
            const sel = $('filter-region');
            if (!sel) return;
            sel.value = (sel.value === el.dataset.region) ? '' : el.dataset.region;
            loadPapers();
            document.querySelectorAll('.bloc').forEach(x => x.classList.toggle('active', x.dataset.region === sel.value));
        }));
    } catch (e) { /* board optional */ }
}

async function loadPapers() {
    skeleton();
    setStatus('FETCHING', 'text-brand-amber');
    try {
        const u = new URL(`${API}/papers`);
        const sv = $('filter-search') ? $('filter-search').value.trim() : '';
        if (sv) u.searchParams.set('search', sv);
        const au = $('filter-author') ? $('filter-author').value.trim() : '';
        if (au) u.searchParams.set('author', au);
        const doi = $('filter-doi') ? $('filter-doi').value.trim() : '';
        if (doi) u.searchParams.set('doi', doi);
        const dom = $('filter-domain') ? $('filter-domain').value.trim() : '';
        if (dom) u.searchParams.set('domain', dom);
        const df = $('filter-date-from') ? $('filter-date-from').value : '';
        if (df) u.searchParams.set('start_date', df);
        const dt = $('filter-date-to') ? $('filter-date-to').value : '';
        if (dt) u.searchParams.set('end_date', dt);
        const h = $('filter-horizon') ? $('filter-horizon').value : '';
        if (h) u.searchParams.set('horizon_days', h);
        const cat = $('filter-category') ? $('filter-category').value : '';
        if (cat) u.searchParams.set('category', cat);
        const src = $('filter-source') ? $('filter-source').value : '';
        if (src) u.searchParams.set('source', src);
        const th = $('filter-thematic') ? $('filter-thematic').value : '';
        if (th) u.searchParams.set('thematic_tags', th);
        const rg = $('filter-region') ? $('filter-region').value : '';
        if (rg) u.searchParams.set('country_tags', rg);
        const pr = $('filter-pair') ? $('filter-pair').value : '';
        if (pr) u.searchParams.set('pair', pr);
        const se = $('filter-sentiment') ? $('filter-sentiment').value : '';
        if (se) u.searchParams.set('sentiment', se);
        u.searchParams.set('limit', '200');

        let res;
        try { res = await fetch(u); }
        catch (e) { await new Promise(r => setTimeout(r, 600)); res = await fetch(u); }
        if (!res.ok) throw new Error('bad response');
        state.papers = await res.json();
        renderCards();
        setStatus('ONLINE', 'text-brand-up');
    } catch (e) {
        console.error(e);
        const gridEl = $('grid-view');
        if (gridEl) gridEl.innerHTML = `<div class="small" style="grid-column: 1 / -1; text-align:center; padding: 40px 0;">[ERR] API hors-ligne. Vérifiez que le backend tourne sur :8001 (./start.sh).</div>`;
        const listEl = $('feed-list');
        if (listEl) listEl.innerHTML = `<div class="small" style="padding:16px;">[ERR] API hors-ligne.</div>`;
        showEmpty('[ERR] API offline. Backend must run on :8001 (./start.sh).', true);
        if ($('results-count')) $('results-count').textContent = 'ERR_CONN';
        setStatus('SYS_ERR', 'text-brand-down');
    }
}

async function runFinbert() {
    const btn = $('btn-analyze');
    if (btn) btn.classList.add('busy');
    setStatus('FINBERT', 'text-brand-amber');
    try {
        await fetch(`${API}/analyze?days=7`, { method: 'POST' });
        setTimeout(() => refreshAll(), 4000);
    } catch (e) {
        console.error(e);
    } finally {
        if (btn) setTimeout(() => btn.classList.remove('busy'), 4000);
    }
}

function skeleton() {
    const emptyEl = $('empty-state');
    if (emptyEl) emptyEl.classList.add('hidden');
    const gridEl = $('grid-view');
    if (gridEl) {
        gridEl.innerHTML = `<div class="small" style="grid-column: 1 / -1; text-align:center; padding: 40px 0;">Chargement des flux macro…</div>`;
    }
    const listEl = $('feed-list');
    if (listEl) {
        listEl.innerHTML = `<div class="small" style="padding:12px;">Chargement des flux macro…</div>`;
    }
    if ($('results-count')) $('results-count').textContent = 'querying…';
}

function showEmpty(msg, isErr) {
    const el = $('empty-state');
    if (!el) return;
    el.textContent = msg;
    el.className = `text-xs p-10 text-center ${isErr ? 'text-brand-down' : 'text-brand-textMuted'}`;
    el.classList.remove('hidden');
}

function renderCards() {
    const list = currentList();
    const gridEl = $('grid-view');
    const listEl = $('feed-list');
    const previewEl = $('feed-preview');

    if (gridEl) gridEl.classList.add('hidden');
    const previewView = $('preview-view');
    if (previewView) previewView.classList.add('hidden');

    if (!list.length) {
        if (gridEl) {
            gridEl.classList.remove('hidden');
            gridEl.innerHTML = `<div class="small" style="grid-column: 1 / -1; text-align:center; padding: 40px 0;">${
                state.savedOnly
                    ? 'Aucun article sauvegardé.'
                    : 'Aucun article trouvé pour ces filtres. Élargissez la période (ALL / 90D) ou cliquez sur SYNC.'
            }</div>`;
        }
        if (listEl) {
            listEl.parentElement.classList.remove('hidden');
            listEl.innerHTML = `<div class="small" style="padding:16px;">Aucun article trouvé.</div>`;
        }
        if (previewEl) previewEl.innerHTML = `<div class="small" style="padding: 24px 0;">Aucun article sélectionné.</div>`;
        return;
    }

    if (!state.selectedId || !list.find(p => p.id === state.selectedId)) {
        state.selectedId = list[0].id;
    }

    if (state.viewMode === 'grid') {
        gridEl.classList.remove('hidden');
        gridEl.innerHTML = list.map(articleCard).join('');
        gridEl.querySelectorAll('.table-card').forEach(card => {
            card.addEventListener('click', () => {
                const id = card.dataset.id;
                const p = state.papers.find(x => x.id === id);
                if (p) openReader(p);
            });
        });
    } else {
        previewView.classList.remove('hidden');
        listEl.innerHTML = list.map(listCard).join('');
        listEl.querySelectorAll('.card').forEach(card => {
            card.addEventListener('click', () => selectPaper(card.dataset.id));
        });
        renderPreview(state.selectedId);
    }

    updateNewCount();
}

function selectPaper(id) {
    state.selectedId = id;
    document.querySelectorAll('#feed-list .card').forEach(c => {
        c.classList.toggle('active', c.dataset.id === id);
    });
    renderPreview(id);
}

function renderPreview(id) {
    const p = state.papers.find(x => x.id === id);
    const pv = $('feed-preview');
    if (!p || !pv) return;

    const lang = 'fr';
    const L = {
        fr: {
            date: 'Publié le',
            author: 'Auteurs',
            sentiment: 'Sentiment FinBERT',
            impact: 'Impact Macro',
            interest: 'Intérêt Trader',
            area: 'Zones / Devises',
            pairs: 'Paires FX',
            summary: 'Compte rendu & Synthèse',
            abstract: 'Résumé / Abstract original',
            keywords: 'Mots-clés & Thématiques',
            openArticle: 'Ouvrir l’article source',
            openReport: 'Compte-rendu PDF',
            openPdf: 'PDF Original',
            save: 'Sauvegarder',
            saved: 'Sauvegardé',
            analyze: 'Analyser avec FinBERT',
            thesisTitle: 'LIER À UNE THÈSE MACRO DU HUB',
            thesisPlaceholder: 'Ex: Long USD / Divergence Fed-BCE, Short JPY…',
            thesisSave: 'Enregistrer la thèse',
            thesisSaved: 'Thèse enregistrée sur cet article',
        },
    }[lang];

    const lbl = p.sentiment_label;
    const dotCls = lbl === 'positive' ? 'pos' : lbl === 'negative' ? 'neg' : 'neu';
    const sentScore = p.sentiment_score != null
        ? `${p.sentiment_score >= 0 ? '+' : ''}${Number(p.sentiment_score).toFixed(2)}`
        : 'N/A';
    const sentBadge = lbl
        ? `<span class="pill ${dotCls === 'pos' ? 'pos-pill' : dotCls === 'neg' ? 'neg-pill' : 'neu-pill'}">${lbl === 'positive' ? 'BULLISH' : lbl === 'negative' ? 'BEARISH' : 'NEUTRAL'} (${sentScore})</span>`
        : `<span class="pill neu-pill">UNSCORED</span>`;

    const impVal = p.impact_score != null ? `${Number(p.impact_score).toFixed(0)} / 100` : '-';
    const iv = p.interest_score || 0;
    const interestTxt = iv ? `${iv} / 5` : '-';
    const pairs = (p.currency_pairs || []).map(fx => `<span class="pair-chip">${esc(fx)}</span>`).join(' ') || '-';
    const tags = [...(p.thematic_tags || []), ...(p.country_tags || []), ...(p.keywords || [])]
        .map(t => `<span class="tag">${esc(t)}</span>`).join('');
    const isSaved = state.saved.has(p.id);

    const det = p.sentiment_detail || {};
    const detStr = Object.keys(det).length
        ? Object.entries(det).map(([k, v]) => `${k}: ${(v * 100).toFixed(0)}%`).join(' · ')
        : '';

    const timePart = fmtTime(p.published_date);

    pv.innerHTML = `
        <div class="source-bar">
            <span class="source">${esc(p.source)} · ${esc(categoryLabelOf(p))}</span>
            <span class="date">${esc(fmtDate(p.published_date))}${timePart ? ` ${esc(timePart)}` : ''}</span>
            ${sentBadge}
        </div>
        <h2>${esc(p.title)}</h2>
        <div class="meta-line">
            <div class="author">${L.author} : <strong>${esc(p.authors || p.source || 'Unknown')}</strong></div>
        </div>
        <div class="kpi-row">
            <div class="kpi"><span class="label">${L.impact}</span><span class="value">${esc(impVal)}</span></div>
            <div class="kpi"><span class="label">${L.interest}</span><span class="value">${esc(interestTxt)}</span></div>
            <div class="kpi"><span class="label">${L.area}</span><span class="value">${esc(blocOf(p))}</span></div>
            <div class="kpi"><span class="label">${L.pairs}</span><span class="value">${pairs}</span></div>
            ${detStr ? `<div class="kpi"><span class="label">FinBERT</span><span class="value">${esc(detStr)}</span></div>` : ''}
        </div>
        <div class="actions-row">
            <button id="pv-btn-save" class="f-btn ${isSaved ? 'primary' : ''}">${isSaved ? L.saved : L.save}</button>
            ${p.source_url ? `<a href="${esc(p.source_url)}" target="_blank" rel="noopener" class="f-btn primary">${L.openArticle}</a>` : ''}
            <a href="${API}/papers/${encodeURIComponent(p.id)}/report?lang=${lang}" target="_blank" rel="noopener" class="f-btn">${L.openReport}</a>
            ${p.pdf_url ? `<a href="${API}/papers/${encodeURIComponent(p.id)}/download" target="_blank" rel="noopener" class="f-btn">${L.openPdf}</a>` : ''}
            <button id="pv-btn-analyze" class="f-btn">${L.analyze}</button>
        </div>

        ${p.summary ? `
            <div class="section-label">${L.summary}</div>
            <p>${esc(p.summary)}</p>
        ` : ''}

        ${p.abstract && p.abstract !== p.summary ? `
            <div class="section-label">${L.abstract}</div>
            <p>${esc(p.abstract)}</p>
        ` : ''}

        ${!p.summary && !p.abstract ? `<p class="small">Aucun résumé texte fourni par le flux RSS. Cliquez sur "${L.openArticle}" ou "${L.analyze}" pour extraire le contenu.</p>` : ''}

        ${tags ? `<div class="tags">${tags}</div>` : ''}

        <div class="thesis-section">
            <div class="section-label">${L.thesisTitle}</div>
            <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                <input type="text" id="pv-thesis-input" class="thesis-input" style="margin-bottom:0;"
                    placeholder="${esc(L.thesisPlaceholder)}" value="${esc(p.thesis || '')}" />
                <button id="pv-thesis-btn" class="thesis-btn">${L.thesisSave}</button>
            </div>
            <div id="pv-thesis-hint" class="thesis-hint hidden">${L.thesisSaved}</div>
        </div>
    `;

    const saveBtn = document.getElementById('pv-btn-save');
    if (saveBtn) {
        saveBtn.addEventListener('click', () => {
            if (state.saved.has(p.id)) state.saved.delete(p.id);
            else state.saved.add(p.id);
            persistSaved();
            renderCards();
        });
    }

    const analyzeBtn = document.getElementById('pv-btn-analyze');
    if (analyzeBtn) {
        analyzeBtn.addEventListener('click', async () => {
            analyzeBtn.textContent = 'FinBERT...';
            analyzeBtn.disabled = true;
            try {
                const updated = await (await fetch(`${API}/papers/${encodeURIComponent(p.id)}/analyze`, { method: 'POST' })).json();
                const idx = state.papers.findIndex(x => x.id === p.id);
                if (idx >= 0) state.papers[idx] = updated;
                renderCards();
                loadStats();
            } catch (e) {
                analyzeBtn.textContent = L.analyze;
                analyzeBtn.disabled = false;
            }
        });
    }

    const thesisBtn = document.getElementById('pv-thesis-btn');
    if (thesisBtn) {
        thesisBtn.addEventListener('click', async () => {
            const val = (document.getElementById('pv-thesis-input').value || '').trim();
            try {
                const updated = await (await fetch(`${API}/papers/${encodeURIComponent(p.id)}/thesis`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ thesis: val || null, thesis_tags: val ? [val] : [] }),
                })).json();
                const idx = state.papers.findIndex(x => x.id === p.id);
                if (idx >= 0) state.papers[idx] = updated;
                renderCards();
                renderPreview(p.id);
                const hint = document.getElementById('pv-thesis-hint');
                if (hint) hint.classList.remove('hidden');
            } catch (e) {
                console.error(e);
            }
        });
    }
}

function updateNewCount() {
    const el = $('new-count');
    if (!el) return;
    const n = state.papers.filter(isNew).length;
    if (n > 0) { el.textContent = `${n} NEW`; el.classList.remove('hidden'); }
    else el.classList.add('hidden');
}

function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('tab-active', t.dataset.tab === tab));

    const secTitle = document.querySelector('.section-title');
    const filterBar = $('filter-bar');
    const gridEl = $('grid-view');
    const previewView = $('preview-view');
    const compareSec = $('compare-section');

    if (filterBar) filterBar.classList.add('hidden');
    if (gridEl) gridEl.classList.add('hidden');
    if (previewView) previewView.classList.add('hidden');
    if (compareSec) compareSec.classList.add('hidden');

    if (tab === 'quant') {
        if (secTitle) secTitle.textContent = 'QUANT / ALGO RESEARCH (arXiv q-fin)';
        setupQuantFilters();
        loadQuantPapers();
        if (filterBar) filterBar.classList.remove('hidden');
        if (gridEl) gridEl.classList.remove('hidden');
        return;
    }

    if (tab === 'compare') {
        if (secTitle) secTitle.textContent = 'COMPARATEUR DE SOURCES';
        if (compareSec) compareSec.classList.remove('hidden');
        loadCompare();
        return;
    }

    if (secTitle) secTitle.textContent = 'FLUX';
    if (filterBar) filterBar.classList.remove('hidden');
    if (state.viewMode === 'grid') {
        if (gridEl) gridEl.classList.remove('hidden');
    } else {
        if (previewView) previewView.classList.remove('hidden');
    }
    buildFilters();
    renderCards();
}

async function setupQuantFilters() {
    if (!state.quantMeta) {
        try {
            state.quantMeta = await (await fetch(`${API}/quant/meta`)).json();
        } catch (e) {
            state.quantMeta = { categories: { 'q-fin.TR': 'Trading & Microstructure' }, assets: [], strategies: [] };
        }
    }
    const m = state.quantMeta;
    if ($('filter-category')) {
        const catOpts = [['', 'ALL q-fin'], ...Object.entries(m.categories || {}).map(([k, v]) => [k, `${v} (${k})`])];
        $('filter-category').innerHTML = catOpts.map(([v, l]) => `<option value="${v}"${v === state.quantCat ? ' selected' : ''}>${l}</option>`).join('');
    }
    if ($('filter-region')) {
        const assetOpts = [['', 'ALL ASSETS'], ...(m.assets || []).map(a => [a, a.toUpperCase()])];
        $('filter-region').innerHTML = assetOpts.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
    }
    if ($('filter-thematic')) {
        const stratOpts = [['', 'ALL STRATEGIES'], ...(m.strategies || []).map(s => [s, s])];
        $('filter-thematic').innerHTML = stratOpts.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
    }
}

async function loadQuantPapers() {
    const gridEl = $('grid-view');
    if (gridEl) gridEl.innerHTML = `<div class="small" style="grid-column: 1 / -1; text-align:center; padding:12px;">Recherche de papiers Quant/Algo sur arXiv q-fin…</div>`;
    try {
        const u = new URL(`${API}/quant/search`);
        const q = $('filter-search') ? $('filter-search').value.trim() : '';
        if (q) u.searchParams.set('q', q);
        const cat = $('filter-category') ? $('filter-category').value : state.quantCat;
        if (cat) u.searchParams.append('cats', cat);
        const asset = $('filter-region') ? $('filter-region').value : '';
        if (asset) u.searchParams.append('assets', asset);
        const strat = $('filter-thematic') ? $('filter-thematic').value : '';
        if (strat) u.searchParams.append('strategies', strat);
        u.searchParams.set('limit', '40');

        const d = await (await fetch(u)).json();
        state.quantPapers = d.papers || [];
        renderQuantCards();
    } catch (e) {
        if ($('grid-view')) $('grid-view').innerHTML = `<div class="small" style="grid-column: 1 / -1; text-align:center; color:var(--down);padding:12px;">Erreur lors du chargement arXiv q-fin.</div>`;
    }
}

function renderQuantCards() {
    const list = state.quantPapers || [];
    const gridEl = $('grid-view');
    if (!gridEl) return;
    if (!list.length) {
        gridEl.innerHTML = `<div class="small" style="grid-column: 1 / -1; text-align:center; padding: 40px 0;">Aucun papier arXiv q-fin pour ces critères.</div>`;
        return;
    }
    gridEl.innerHTML = list.map(p => {
        const chips = [...(p.assets || []), ...(p.strategies || []), ...(p.methods || [])]
            .slice(0, 4)
            .map(c => `<span class="pair-chip">${esc(c)}</span>`)
            .join('');
        const providerLabel = (p.primary_cat_label || p.primary_cat || 'arXiv');
        const providerCode = providerLabel.toUpperCase().replace(/[^A-Z0-9]/g, '');
        return `
            <div class="table-card" data-id="${esc(p.id)}" data-src="${esc(p.abs_url || p.pdf_url || '')}">
                <div class="provider-logo" title="${esc(providerLabel)}">${providerCode || 'ARXIV'}</div>
                <div class="table-body">
                    <div class="table-head">
                        <span class="table-cat"><span class="dot neu"></span>${esc(providerLabel)}</span>
                        <span class="table-src">arXiv</span>
                    </div>
                    <div class="table-title">${esc(p.title || '')}</div>
                    <div class="table-summary">${esc(p.abstract || '')}</div>
                    <div class="table-meta">
                        <span class="table-date">${esc(p.published || '')}</span>
                        ${chips}
                    </div>
                    <div class="table-pairs"></div>
                </div>
                <div class="table-sentiment-bar neu"></div>
            </div>
        `;
    }).join('');
    gridEl.querySelectorAll('.table-card').forEach(c => {
        c.addEventListener('click', () => {
            const p = state.quantPapers.find(x => x.id === c.dataset.id);
            if (p) openReader(p);
        });
    });
}

async function loadCompare() {
    const el = $('compare-results');
    const daysEl = $('compare-days');
    const zonesEl = $('compare-zones');
    if (!el) return;
    el.innerHTML = '<div class="small">Chargement du comparateur…</div>';
    try {
        const days = daysEl ? daysEl.value : '30';
        const zones = zonesEl ? zonesEl.value : 'US,EU';
        const res = await fetch(`${API}/compare?days=${encodeURIComponent(days)}&zones=${encodeURIComponent(zones)}`);
        if (!res.ok) throw new Error('bad response');
        const data = await res.json();
        renderCompare(data);
    } catch (e) {
        console.error(e);
        el.innerHTML = `<div class="small" style="color:var(--down);">Erreur chargement comparateur.</div>`;
    }
}

function renderCompare(data) {
    const el = $('compare-results');
    if (!el) return;
    if (!data.sources || !data.sources.length) {
        el.innerHTML = '<div class="small">Aucune donnée de comparaison pour ces zones/période.</div>';
        return;
    }
    el.innerHTML = data.sources.map(s => {
        const pct = Math.round(((s.avg_sentiment + 1) / 2) * 100);
        const color = s.net_score > 0.03 ? 'var(--up)' : s.net_score < -0.03 ? 'var(--down)' : '#6b7280';
        return `
            <div class="compare-card">
                <div class="source-name">${esc(s.source)}</div>
                <div class="source-inst">${esc(s.institution)} · ${esc(s.zone)}</div>
                <div class="compare-bar"><div class="compare-bar-fill" style="width:${pct}%;background:${color}"></div></div>
                <div class="compare-meta">
                    <span>n=${s.count} | analysés=${s.analyzed}</span>
                    <span>net=${s.net_score > 0 ? '+' : ''}${s.net_score}</span>
                </div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:6px;">
                    + ${s.positive} · - ${s.negative} · o ${s.neutral}
                </div>
            </div>
        `;
    }).join('');
}

function openReader(p) {
    const modal = $('reader-modal');
    const iframe = $('reader-iframe');
    const fallback = $('reader-fallback');
    const title = $('reader-title');
    const openLink = $('reader-open');
    if (!modal) return;
    title.textContent = esc(p.title || 'Lecture directe');
    const url = p.source_url || p.pdf_url || '';
    openLink.href = url;
    if (!url) {
        modal.classList.remove('hidden');
        iframe.classList.add('hidden');
        fallback.classList.remove('hidden');
        return;
    }
    iframe.src = url;
    modal.classList.remove('hidden');
    iframe.classList.remove('hidden');
    fallback.classList.add('hidden');
    setTimeout(() => {
        try {
            const doc = iframe.contentDocument || iframe.contentWindow.document;
            if (!doc || !(doc.body && doc.body.innerText.trim().length > 10)) {
                iframe.classList.add('hidden');
                fallback.classList.remove('hidden');
            }
        } catch (e) {
            iframe.classList.add('hidden');
            fallback.classList.remove('hidden');
        }
    }, 1200);
}

function toggleAuto() {
    const btn = $('btn-auto');
    if (state.autoTimer) {
        clearInterval(state.autoTimer);
        state.autoTimer = null;
        if (btn) btn.classList.remove('primary');
    } else {
        state.autoTimer = setInterval(() => { refreshAll(false); }, 300000);
        if (btn) btn.classList.add('primary');
    }
}

function exportCsv() {
    const list = currentList();
    if (!list.length) return;
    const head = ['date', 'time', 'source', 'region', 'sentiment', 'score', 'impact', 'title', 'authors', 'url'];
    const cell = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const rows = list.map(p => [
        p.published_date ? p.published_date.slice(0, 10) : '',
        fmtTime(p.published_date),
        p.source,
        blocOf(p),
        p.sentiment_label || '',
        p.sentiment_score ?? '',
        p.impact_score ?? '',
        p.title,
        p.authors || '',
        p.source_url || p.pdf_url || '',
    ].map(cell).join(','));
    const blob = new Blob([head.join(',') + '\n' + rows.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `macro-survey-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
}

function currentList() {
    let list = state.papers || [];
    if (state.savedOnly) list = list.filter(p => state.saved.has(p.id));
    const k = state.sort;
    const dateOf = (p) => {
        const dt = parseLocalDate(p.published_date);
        return dt ? dt.getTime() : 0;
    };
    const val = (p) => k === 'sentiment_abs' ? Math.abs(p.sentiment_score ?? 0)
        : k === 'sentiment_score' ? (p.sentiment_score ?? -2)
        : k === 'impact_score' ? (p.impact_score ?? 0)
        : k === 'interest_score' ? (p.interest_score ?? 0)
        : k === 'published_date' ? dateOf(p)
        : (p[k] || '').toString().toLowerCase();
    const asc = (k === 'source' || k === 'title');
    return [...list].sort((a, b) => {
        const x = val(a), y = val(b);
        if (x !== y) return (x < y ? -1 : 1) * (asc ? 1 : -1);
        return dateOf(b) - dateOf(a);
    });
}

async function loadCatalog() {
    try {
        state.catalog = await (await fetch(`${API}/sources`)).json();
    } catch (e) { /* catalog optional */ }
}

async function loadHealth() {
    const dot = $('health-dot');
    const text = $('health-text');
    if (!dot || !text) return;
    try {
        const res = await fetch(`${API}/health`);
        if (!res.ok) throw new Error('bad response');
        const data = await res.json();
        dot.className = `health-dot ${data.overall}`;
        text.textContent = data.overall.toUpperCase();
        text.style.color = data.overall === 'healthy' ? 'var(--up)' : data.overall === 'degraded' ? 'var(--amber)' : 'var(--down)';

        // Afficher le dialog d'erreur si des sources sont en panne
        if (data.overall === 'error') {
            const errorSources = data.sources.filter(s => s.status === 'error');
            const warningSources = data.sources.filter(s => s.status === 'warning');
            const modal = $('health-modal');
            const title = $('health-modal-title');
            const message = $('health-modal-message');
            const list = $('health-modal-list');

            let html = '';
            if (errorSources.length > 0) {
                html += `<div style="margin-bottom:8px; color:var(--down); font-weight:600;">${errorSources.length} source(s) sans aucun article :</div>`;
                html += `<ul style="margin-left:16px; margin-bottom:16px;">`;
                for (const s of errorSources.slice(0, 15)) {
                    html += `<li>${esc(s.name)} (${s.kind}, zone ${s.zone}) — 0 documents</li>`;
                }
                if (errorSources.length > 15) html += `<li>… et ${errorSources.length - 15} autres</li>`;
                html += `</ul>`;
            }
            if (warningSources.length > 0) {
                html += `<div style="color:var(--amber); font-weight:600;">${warningSources.length} source(s) sans article récent (&gt;30 jours) :</div>`;
                html += `<ul style="margin-left:16px;">`;
                for (const s of warningSources.slice(0, 10)) {
                    const days = s.days_since_last != null ? s.days_since_last : '?';
                    html += `<li>${esc(s.name)} — dernière activité il y a ${days} jours</li>`;
                }
                if (warningSources.length > 10) html += `<li>… et ${warningSources.length - 10} autres</li>`;
                html += `</ul>`;
            }
            if (!html) html = `<div>Aucune source en erreur détectée.</div>`;

            title.textContent = `Santé des sources RSS — ${data.sources.length} sources surveillées`;
            message.innerHTML = `<div style="font-weight:600; margin-bottom:8px;">Statut global : <span style="color:${text.style.color}">${data.overall.toUpperCase()}</span></div>`;
            list.innerHTML = html;
            modal.classList.remove('hidden');
        }
    } catch (e) {
        dot.className = 'health-dot error';
        text.textContent = 'ERROR';
        text.style.color = 'var(--down)';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    buildFilters();
    wireEvents();
    startClock();
    loadCatalog();
    loadHealth();
    refreshAll();
});
