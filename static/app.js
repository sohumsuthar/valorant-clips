// valclips - Frontend

const API = '/api';

// ── State (synced with URL) ──

const state = {
    page: 1, sort: 'score', tag: '', search: '', share: '',
    dateFrom: '', dateTo: '', hideDupes: true,
    scoreMin: '', killsMin: '', highlightType: '', clutchType: '',
    agent: '', weapon: '', map: '', acesOnly: false,
};

// ── URL State Sync ──

function stateToURL() {
    const p = new URLSearchParams();
    if (state.page > 1) p.set('page', state.page);
    if (state.sort !== 'score') p.set('sort', state.sort);
    if (state.search) p.set('q', state.search);
    if (state.share) p.set('share', state.share);
    if (state.tag) p.set('tag', state.tag);
    if (state.dateFrom) p.set('from', state.dateFrom);
    if (state.dateTo) p.set('to', state.dateTo);
    if (!state.hideDupes) p.set('dupes', '1');
    if (state.scoreMin) p.set('score', state.scoreMin);
    if (state.killsMin) p.set('kills', state.killsMin);
    if (state.highlightType) p.set('type', state.highlightType);
    if (state.clutchType) p.set('clutch', state.clutchType);
    if (state.agent) p.set('agent', state.agent);
    if (state.weapon) p.set('weapon', state.weapon);
    if (state.map) p.set('map', state.map);
    if (state.acesOnly) p.set('aces', '1');
    const qs = p.toString();
    const url = qs ? `?${qs}` : window.location.pathname;
    history.replaceState(null, '', url);
}

function stateFromURL() {
    const p = new URLSearchParams(window.location.search);
    state.page = parseInt(p.get('page')) || 1;
    state.sort = p.get('sort') || 'score';
    state.search = p.get('q') || '';
    state.share = p.get('share') || '';
    state.tag = p.get('tag') || '';
    state.dateFrom = p.get('from') || '';
    state.dateTo = p.get('to') || '';
    state.hideDupes = p.get('dupes') !== '1';
    state.scoreMin = p.get('score') || '';
    state.killsMin = p.get('kills') || '';
    state.highlightType = p.get('type') || '';
    state.clutchType = p.get('clutch') || '';
    state.agent = p.get('agent') || '';
    state.weapon = p.get('weapon') || '';
    state.map = p.get('map') || '';
    state.acesOnly = p.get('aces') === '1';
}

// ── Utilities ──

function formatBytes(b) {
    if (!b) return '-';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = b;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(1) + ' ' + u[i];
}

function formatDuration(s) {
    if (!s) return '-';
    s = Math.floor(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    return `${m}:${String(sec).padStart(2,'0')}`;
}

function formatClipName(fn) {
    const dvr = fn.match(/Valorant\s+(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(\d{2})\.(\d{2})\.\d+\.\d+\.DVR\.mp4/i);
    if (dvr) { const [,y,mo,d,h,mi] = dvr; return `DVR ${y}-${mo}-${d} ${h}:${mi}`; }
    const rep = fn.match(/VALORANT_replay_(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.mp4/i);
    if (rep) { const [,y,mo,d,h,mi] = rep; return `Replay ${y}-${mo}-${d} ${h}:${mi}`; }
    return fn.replace(/\.mp4$/i, '');
}

function formatStaticElements() {
    document.querySelectorAll('[data-bytes]').forEach(el => el.textContent = formatBytes(parseFloat(el.dataset.bytes)));
    document.querySelectorAll('[data-seconds]').forEach(el => el.textContent = formatDuration(parseFloat(el.dataset.seconds)));
}

// ── Loading Skeleton ──

function showSkeletons(grid, count = 12) {
    grid.innerHTML = '';
    for (let i = 0; i < count; i++) {
        const el = document.createElement('div');
        el.className = 'skeleton-card';
        el.innerHTML = '<div class="skeleton-thumb"></div><div class="skeleton-body"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>';
        grid.appendChild(el);
    }
}

// ── Gallery ──

let loading = false;

async function loadClips() {
    const grid = document.getElementById('clip-grid');
    if (!grid || loading) return;
    loading = true;

    showSkeletons(grid);
    stateToURL();

    const p = new URLSearchParams({ page: state.page, sort: state.sort, limit: 24 });
    if (state.tag) p.set('tag', state.tag);
    if (state.search) p.set('search', state.search);
    if (state.share) p.set('share', state.share);
    if (state.dateFrom) p.set('date_from', state.dateFrom);
    if (state.dateTo) p.set('date_to', state.dateTo);
    if (state.hideDupes) p.set('hide_dupes', 'true');
    if (state.scoreMin) p.set('score_min', state.scoreMin);
    if (state.killsMin) p.set('kills_min', state.killsMin);
    if (state.highlightType) p.set('highlight_type', state.highlightType);
    if (state.clutchType) p.set('clutch_type', state.clutchType);
    if (state.agent) p.set('player_agent', state.agent);
    if (state.weapon) p.set('weapon', state.weapon);
    if (state.map) p.set('map_name', state.map);
    if (state.acesOnly) p.set('aces_only', 'true');

    try {
        const resp = await fetch(`${API}/clips?${p}`);
        const data = await resp.json();
        grid.innerHTML = '';

        // Result count
        const countEl = document.getElementById('result-count');
        if (countEl) {
            const total = data.total || 0;
            countEl.textContent = `${total.toLocaleString()} clip${total !== 1 ? 's' : ''}`;
        }

        if (!data.clips || data.clips.length === 0) {
            grid.innerHTML = '<div class="empty-state"><p>No clips found</p><p>Try adjusting your filters</p></div>';
            document.getElementById('pagination').innerHTML = '';
            loading = false;
            return;
        }

        const tpl = document.getElementById('clip-card-template');
        for (const clip of data.clips) {
            const card = tpl.content.cloneNode(true);
            const article = card.querySelector('.clip-card');
            const link = card.querySelector('a');
            const img = card.querySelector('img');
            const dur = card.querySelector('.duration-badge');
            const dupe = card.querySelector('.dupe-badge');
            const score = card.querySelector('.card-score');
            const name = card.querySelector('.clip-name');
            const meta = card.querySelector('.clip-meta');
            const date = card.querySelector('.clip-date');

            article.dataset.id = clip.id;
            link.href = `/clips/${clip.id}`;

            if (clip.thumbnail_path) {
                const thumb = clip.thumbnail_path.split('/').pop().split('\\').pop();
                img.src = `/thumbnails/${thumb}`;
            } else {
                img.remove();
                const ph = document.createElement('div');
                ph.className = 'no-thumb';
                ph.innerHTML = `<span class="no-thumb-icon">&#9654;</span><span class="no-thumb-id">#${clip.id}</span>`;
                card.querySelector('.thumb-wrap').prepend(ph);
            }

            if (clip.duplicate_of) dupe.style.display = '';

            if (clip.ai_score) {
                score.textContent = `${clip.ai_score}/10`;
                score.className = `card-score score-${clip.ai_score}`;
                score.style.display = '';
            }

            dur.textContent = formatDuration(clip.duration_seconds);
            name.textContent = formatClipName(clip.filename);
            date.textContent = clip.recorded_at ? new Date(clip.recorded_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';

            // Metadata pills
            meta.innerHTML = '';
            const pills = document.createElement('div');
            pills.className = 'card-pills';
            const pill = (text, cls) => { const s = document.createElement('span'); s.className = `card-pill ${cls}`; s.textContent = text; pills.appendChild(s); };

            if (clip.ai_kills) pill(`${clip.ai_kills}K`, 'kills');
            if (clip.ai_is_ace) pill('ACE', 'ace');
            if (clip.ai_clutch_type) pill(clip.ai_clutch_type, 'clutch');
            if (clip.ai_highlight_type && clip.ai_highlight_type !== 'regular-round' && clip.ai_highlight_type !== 'non-gameplay') pill(clip.ai_highlight_type, 'type');
            if (clip.ai_player_agent) pill(clip.ai_player_agent, 'agent');
            if (clip.ai_map) pill(clip.ai_map, 'map');
            if (clip.ai_weapon) pill(clip.ai_weapon, 'weapon');

            if (pills.children.length) {
                meta.appendChild(pills);
            } else {
                const parts = [];
                if (clip.width && clip.height) parts.push(`${clip.width}x${clip.height}`);
                if (clip.file_size_bytes) parts.push(formatBytes(clip.file_size_bytes));
                meta.textContent = parts.join(' | ');
            }

            grid.appendChild(card);
        }

        renderPagination(data.page, data.pages);
    } catch (e) {
        grid.innerHTML = '<div class="empty-state"><p>Failed to load clips</p></div>';
    }
    loading = false;
}

function renderPagination(current, total) {
    const wrap = document.getElementById('pagination');
    if (!wrap) return;
    wrap.innerHTML = '';
    if (total <= 1) return;

    const btn = (label, page, active = false) => {
        const b = document.createElement('button');
        b.textContent = label;
        if (active) { b.className = 'active'; b.disabled = true; }
        else b.onclick = () => { state.page = page; loadClips(); window.scrollTo({ top: 0, behavior: 'smooth' }); };
        wrap.appendChild(b);
    };

    if (current > 1) btn('\u2039', current - 1);

    const start = Math.max(1, current - 3);
    const end = Math.min(total, current + 3);
    if (start > 1) { btn('1', 1); if (start > 2) { const d = document.createElement('span'); d.className = 'page-info'; d.textContent = '...'; wrap.appendChild(d); } }
    for (let i = start; i <= end; i++) btn(i, i, i === current);
    if (end < total) { if (end < total - 1) { const d = document.createElement('span'); d.className = 'page-info'; d.textContent = '...'; wrap.appendChild(d); } btn(total, total); }

    if (current < total) btn('\u203A', current + 1);

    // Page indicator
    const info = document.createElement('span');
    info.className = 'page-info';
    info.textContent = `${current} / ${total}`;
    wrap.appendChild(info);
}

// ── Active Filter Chips ──

function renderFilterChips() {
    const wrap = document.getElementById('active-filters');
    if (!wrap) return;
    wrap.innerHTML = '';

    const chip = (label, clearFn) => {
        const el = document.createElement('span');
        el.className = 'filter-chip';
        el.innerHTML = `${label} <span class="chip-x">&times;</span>`;
        el.onclick = () => { clearFn(); renderFilterChips(); state.page = 1; loadClips(); syncFiltersToUI(); };
        wrap.appendChild(el);
    };

    if (state.scoreMin) chip(`Score ${state.scoreMin}+`, () => state.scoreMin = '');
    if (state.killsMin) chip(`Kills ${state.killsMin}+`, () => state.killsMin = '');
    if (state.highlightType) chip(`Type: ${state.highlightType}`, () => state.highlightType = '');
    if (state.agent) chip(`Agent: ${state.agent}`, () => state.agent = '');
    if (state.map) chip(`Map: ${state.map}`, () => state.map = '');
    if (state.weapon) chip(`Weapon: ${state.weapon}`, () => state.weapon = '');
    if (state.clutchType) chip(`Clutch: ${state.clutchType}`, () => state.clutchType = '');
    if (state.acesOnly) chip('Aces only', () => state.acesOnly = false);
    if (state.search) chip(`"${state.search}"`, () => { state.search = ''; const el = document.getElementById('search-input'); if (el) el.value = ''; });
    if (state.share) chip(`Source: ${state.share}`, () => state.share = '');
    if (state.tag) chip(`Tag: ${state.tag}`, () => state.tag = '');
    if (state.dateFrom || state.dateTo) chip(`Date range`, () => { state.dateFrom = ''; state.dateTo = ''; document.querySelectorAll('.date-btn').forEach(b => b.classList.remove('active')); document.querySelector('.date-btn[data-range="all"]')?.classList.add('active'); });

    // Update filter toggle button
    const toggleBtn = document.getElementById('filter-toggle');
    const hasFilters = state.scoreMin || state.killsMin || state.highlightType || state.clutchType || state.agent || state.weapon || state.map || state.acesOnly;
    if (toggleBtn) toggleBtn.classList.toggle('has-filters', !!hasFilters);

    // Clear all button
    const clearBtn = document.getElementById('clear-filters');
    if (clearBtn) clearBtn.style.display = hasFilters ? '' : 'none';
}

function syncFiltersToUI() {
    const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
    setVal('score-filter', state.scoreMin);
    setVal('kills-filter', state.killsMin);
    setVal('type-filter', state.highlightType);
    setVal('clutch-filter', state.clutchType);
    setVal('agent-filter', state.agent);
    setVal('weapon-filter', state.weapon);
    setVal('map-filter', state.map);
    setVal('sort-select', state.sort);
    setVal('share-select', state.share);
    const aces = document.getElementById('aces-only');
    if (aces) aces.checked = state.acesOnly;
    const dupes = document.getElementById('hide-dupes');
    if (dupes) dupes.checked = state.hideDupes;
    const search = document.getElementById('search-input');
    if (search) search.value = state.search;
}

// ── Filter Events ──

function setupGalleryEvents() {
    const search = document.getElementById('search-input');
    if (search) {
        let t;
        search.addEventListener('input', () => {
            clearTimeout(t);
            t = setTimeout(() => { state.search = search.value.trim(); state.page = 1; renderFilterChips(); loadClips(); }, 300);
        });
    }

    const sort = document.getElementById('sort-select');
    if (sort) sort.addEventListener('change', () => { state.sort = sort.value; state.page = 1; loadClips(); });

    const share = document.getElementById('share-select');
    if (share) share.addEventListener('change', () => { state.share = share.value; state.page = 1; renderFilterChips(); loadClips(); });

    const dupes = document.getElementById('hide-dupes');
    if (dupes) dupes.addEventListener('change', () => { state.hideDupes = dupes.checked; state.page = 1; loadClips(); });

    // Filter toggle
    const toggleBtn = document.getElementById('filter-toggle');
    const panel = document.getElementById('filter-panel');
    if (toggleBtn && panel) {
        toggleBtn.addEventListener('click', () => panel.classList.toggle('open'));
    }

    // Advanced filters
    const filters = {
        'score-filter': v => state.scoreMin = v,
        'kills-filter': v => state.killsMin = v,
        'type-filter': v => state.highlightType = v,
        'clutch-filter': v => state.clutchType = v,
        'agent-filter': v => state.agent = v,
        'weapon-filter': v => state.weapon = v,
        'map-filter': v => state.map = v,
    };

    for (const [id, setter] of Object.entries(filters)) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => { setter(el.value); state.page = 1; renderFilterChips(); loadClips(); });
    }

    const aces = document.getElementById('aces-only');
    if (aces) aces.addEventListener('change', () => { state.acesOnly = aces.checked; state.page = 1; renderFilterChips(); loadClips(); });

    const clearBtn = document.getElementById('clear-filters');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            state.scoreMin = ''; state.killsMin = ''; state.highlightType = '';
            state.clutchType = ''; state.agent = ''; state.weapon = '';
            state.map = ''; state.acesOnly = false;
            state.page = 1;
            syncFiltersToUI();
            renderFilterChips();
            loadClips();
        });
    }
}

// ── Score Chart ──

function renderScoreChart(dist) {
    const chart = document.getElementById('score-chart');
    if (!chart || !dist.length) return;

    const counts = {};
    for (const d of dist) counts[d.score] = d.count;
    const max = Math.max(...dist.map(d => d.count), 1);
    const colors = { 1:'#555566', 2:'#555566', 3:'#555566', 4:'#555566', 5:'#4a9eff', 6:'#4a9eff', 7:'#f5a623', 8:'#f5a623', 9:'#ff4655', 10:'#ff4655' };

    chart.innerHTML = '';
    for (let s = 1; s <= 10; s++) {
        const c = counts[s] || 0;
        const h = c > 0 ? Math.max(4, (c / max) * 100) : 0;
        const w = document.createElement('div'); w.className = 'score-bar-wrap';
        const bar = document.createElement('div'); bar.className = 'score-bar'; bar.style.height = `${h}%`; bar.style.background = colors[s];
        const cnt = document.createElement('span'); cnt.className = 'score-bar-count'; cnt.textContent = c; bar.appendChild(cnt);
        bar.addEventListener('click', () => {
            const min = s >= 9 ? '9' : s >= 7 ? '7' : s >= 5 ? '5' : s >= 3 ? '3' : '';
            state.scoreMin = min; state.page = 1;
            syncFiltersToUI(); renderFilterChips(); loadClips();
        });
        const lbl = document.createElement('span'); lbl.className = 'score-bar-label'; lbl.textContent = s;
        w.appendChild(bar); w.appendChild(lbl); chart.appendChild(w);
    }
}

// ── Filter Options ──

async function loadFilterOptions() {
    const chart = document.getElementById('score-chart');
    if (!chart) return;

    try {
        const resp = await fetch(`${API}/filters`);
        const data = await resp.json();

        const fill = (id, items) => {
            const el = document.getElementById(id);
            if (!el || !items) return;
            for (const item of items) {
                const opt = document.createElement('option');
                opt.value = item.name;
                opt.textContent = `${item.name} (${item.count})`;
                el.appendChild(opt);
            }
        };

        fill('type-filter', data.highlight_types);
        fill('map-filter', data.maps);
        fill('clutch-filter', data.clutch_types);
        fill('weapon-filter', data.weapons);
        fill('agent-filter', data.agents);

        if (data.score_distribution) renderScoreChart(data.score_distribution);

        // Sync URL state to UI after options are loaded
        syncFiltersToUI();
        renderFilterChips();

        // Auto-open filter panel if filters are active from URL
        const hasFilters = state.scoreMin || state.killsMin || state.highlightType || state.clutchType || state.agent || state.weapon || state.map || state.acesOnly;
        if (hasFilters) document.getElementById('filter-panel')?.classList.add('open');
    } catch {}
}

// ── Timeline ──

async function loadTimeline() {
    const chart = document.getElementById('timeline-chart');
    if (!chart) return;

    try {
        const resp = await fetch(`${API}/timeline`);
        const data = await resp.json();
        if (!data.length) return;

        const max = Math.max(...data.map(d => d.count));
        chart.innerHTML = '';
        for (const item of data) {
            const bar = document.createElement('div'); bar.className = 'timeline-bar';
            bar.style.height = `${Math.max(4, (item.count / max) * 100)}%`;
            const tip = document.createElement('span'); tip.className = 'tooltip';
            tip.textContent = `${item.month}: ${item.count}`;
            bar.appendChild(tip);
            bar.addEventListener('click', () => {
                const [y, m] = item.month.split('-');
                state.dateFrom = `${item.month}-01`;
                state.dateTo = `${item.month}-${new Date(y, m, 0).getDate()}`;
                state.page = 1;
                document.querySelectorAll('.date-btn').forEach(b => b.classList.remove('active'));
                renderFilterChips(); loadClips();
            });
            chart.appendChild(bar);
        }
    } catch {}
}

// ── Date Range ──

function setupDateRangeButtons() {
    document.querySelectorAll('.date-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.date-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const range = btn.dataset.range;
            const now = new Date();
            if (range === 'all') { state.dateFrom = ''; state.dateTo = ''; }
            else {
                state.dateTo = now.toISOString().slice(0, 10);
                const from = new Date(now);
                if (range === 'week') from.setDate(from.getDate() - 7);
                else if (range === 'month') from.setMonth(from.getMonth() - 1);
                else if (range === '3mo') from.setMonth(from.getMonth() - 3);
                else if (range === '6mo') from.setMonth(from.getMonth() - 6);
                else if (range === 'year') from.setFullYear(from.getFullYear() - 1);
                state.dateFrom = from.toISOString().slice(0, 10);
            }
            state.page = 1;
            renderFilterChips(); loadClips();
        });
    });
}

// ── Keyboard Shortcuts ──

function setupKeyboard() {
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (e.key === '/') { e.preventDefault(); document.getElementById('search-input')?.focus(); }
        else if (e.key === 'ArrowLeft' && state.page > 1) { state.page--; loadClips(); }
        else if (e.key === 'ArrowRight') { const n = document.querySelector('.pagination button:last-of-type:not(.active)'); if (n) n.click(); }
    });
}

// ── Hover Previews ──

function setupHoverPreviews() {
    const grid = document.getElementById('clip-grid');
    if (!grid) return;
    const cache = {};

    grid.addEventListener('mouseenter', async (e) => {
        const card = e.target.closest('.clip-card');
        if (!card) return;
        const img = card.querySelector('.thumb-wrap img');
        if (!img || !img.src) return;
        const orig = img.dataset.orig || img.src;
        img.dataset.orig = orig;
        const preview = orig.replace(/\.jpg$/, '_preview.webp');
        if (cache[preview] === false) return;
        if (cache[preview]) { img.src = preview; return; }
        try {
            const r = await fetch(preview, { method: 'HEAD' });
            cache[preview] = r.ok;
            if (r.ok && card.matches(':hover')) img.src = preview;
        } catch { cache[preview] = false; }
    }, true);

    grid.addEventListener('mouseleave', (e) => {
        const card = e.target.closest('.clip-card');
        if (!card) return;
        const img = card.querySelector('.thumb-wrap img');
        if (img?.dataset.orig) img.src = img.dataset.orig;
    }, true);
}

// ── Clip Detail ──

function setupClipDetail() {
    const tagList = document.getElementById('tag-list');
    if (!tagList) return;
    const clipId = tagList.dataset.clipId;

    tagList.addEventListener('click', async (e) => {
        const btn = e.target.closest('.tag-remove');
        if (!btn) return;
        await fetch(`${API}/clips/${clipId}/tags/${encodeURIComponent(btn.dataset.tag)}`, { method: 'DELETE' });
        btn.parentElement.remove();
    });

    const form = document.getElementById('add-tag-form');
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const input = document.getElementById('new-tag');
            const name = input.value.trim().toLowerCase();
            if (!name) return;
            const r = await fetch(`${API}/clips/${clipId}/tags`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
            if (r.ok) {
                const badge = document.createElement('span');
                badge.className = 'tag-badge';
                badge.innerHTML = `${name} <button class="tag-remove" data-tag="${name}">&times;</button>`;
                tagList.appendChild(badge);
                input.value = '';
            }
        });
    }
}

// ── Init ──

document.addEventListener('DOMContentLoaded', () => {
    formatStaticElements();
    stateFromURL();
    setupGalleryEvents();
    setupDateRangeButtons();
    setupKeyboard();
    setupClipDetail();
    setupHoverPreviews();
    loadTimeline();
    loadFilterOptions();
    loadClips();
});
