// Valorant Clips - Frontend JS

const API = '/api';
let currentPage = 1;
let currentSort = 'date';
let currentTag = null;
let currentSearch = '';
let currentShare = '';
let currentDateFrom = '';
let currentDateTo = '';
let hideDupes = true;
let currentScoreMin = '';
let currentKillsMin = '';
let currentHighlightType = '';
let currentClutchType = '';
let currentAgent = '';
let currentWeapon = '';
let currentMap = '';
let acesOnly = false;

// ---- Utilities ----

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '-';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let val = bytes;
    while (val >= 1024 && i < units.length - 1) {
        val /= 1024;
        i++;
    }
    return val.toFixed(1) + ' ' + units[i];
}

function formatDuration(seconds) {
    if (!seconds || seconds === 0) return '-';
    const s = Math.floor(seconds);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    return `${m}:${String(sec).padStart(2,'0')}`;
}

function formatClipName(filename) {
    // "Valorant YYYY.MM.DD - HH.MM.SS.NN.DVR.mp4" -> "DVR 2025-07-11 20:33"
    const dvrMatch = filename.match(/Valorant\s+(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(\d{2})\.(\d{2})\.(\d{2})\.\d+\.DVR\.mp4/i);
    if (dvrMatch) {
        const [, y, mo, d, h, mi] = dvrMatch;
        return `DVR ${y}-${mo}-${d} ${h}:${mi}`;
    }
    // "VALORANT_replay_YYYY.MM.DD-HH.MM.mp4" -> "Replay 2025-07-11 20:33"
    const replayMatch = filename.match(/VALORANT_replay_(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.mp4/i);
    if (replayMatch) {
        const [, y, mo, d, h, mi] = replayMatch;
        return `Replay ${y}-${mo}-${d} ${h}:${mi}`;
    }
    // Strip .mp4 extension for cleaner display
    return filename.replace(/\.mp4$/i, '');
}

// ---- Format static elements ----

function formatStaticElements() {
    document.querySelectorAll('[data-bytes]').forEach(el => {
        el.textContent = formatBytes(parseFloat(el.dataset.bytes));
    });
    document.querySelectorAll('[data-seconds]').forEach(el => {
        el.textContent = formatDuration(parseFloat(el.dataset.seconds));
    });
}

// ---- Gallery (index page) ----

async function loadClips() {
    const grid = document.getElementById('clip-grid');
    if (!grid) return;

    const params = new URLSearchParams({
        page: currentPage,
        sort: currentSort,
        limit: 24,
    });
    if (currentTag) params.set('tag', currentTag);
    if (currentSearch) params.set('search', currentSearch);
    if (currentShare) params.set('share', currentShare);
    if (currentDateFrom) params.set('date_from', currentDateFrom);
    if (currentDateTo) params.set('date_to', currentDateTo);
    if (hideDupes) params.set('hide_dupes', 'true');
    if (currentScoreMin) params.set('score_min', currentScoreMin);
    if (currentKillsMin) params.set('kills_min', currentKillsMin);
    if (currentHighlightType) params.set('highlight_type', currentHighlightType);
    if (currentClutchType) params.set('clutch_type', currentClutchType);
    if (currentAgent) params.set('player_agent', currentAgent);
    if (currentWeapon) params.set('weapon', currentWeapon);
    if (currentMap) params.set('map_name', currentMap);
    if (acesOnly) params.set('aces_only', 'true');

    const resp = await fetch(`${API}/clips?${params}`);
    const data = await resp.json();

    grid.innerHTML = '';

    if (data.clips.length === 0) {
        grid.innerHTML = '<div class="empty-state"><p>No clips found.</p><p>Run <code>valclips scan</code> to index clips.</p></div>';
        document.getElementById('pagination').innerHTML = '';
        return;
    }

    const template = document.getElementById('clip-card-template');

    for (const clip of data.clips) {
        const card = template.content.cloneNode(true);
        const article = card.querySelector('.clip-card');
        const link = card.querySelector('a');
        const img = card.querySelector('img');
        const duration = card.querySelector('.duration-badge');
        const dupeBadge = card.querySelector('.dupe-badge');
        const scoreBadge = card.querySelector('.card-score');
        const date = card.querySelector('.clip-date');
        const name = card.querySelector('.clip-name');
        const meta = card.querySelector('.clip-meta');

        article.dataset.id = clip.id;
        link.href = `/clips/${clip.id}`;

        if (clip.thumbnail_path) {
            const thumbName = clip.thumbnail_path.split('/').pop().split('\\').pop();
            img.src = `/thumbnails/${thumbName}`;
        } else {
            img.remove();
            const placeholder = document.createElement('div');
            placeholder.className = 'no-thumb';
            placeholder.innerHTML = `<span class="no-thumb-icon">&#9654;</span><span class="no-thumb-id">#${clip.id}</span>`;
            card.querySelector('.thumb-wrap').prepend(placeholder);
        }

        if (clip.duplicate_of) {
            dupeBadge.style.display = '';
        }

        if (clip.ai_score) {
            scoreBadge.textContent = `${clip.ai_score}/10`;
            scoreBadge.className = `card-score score-${clip.ai_score}`;
            scoreBadge.style.display = '';
        }

        duration.textContent = formatDuration(clip.duration_seconds);
        date.textContent = clip.recorded_at
            ? new Date(clip.recorded_at).toLocaleDateString()
            : '-';
        name.textContent = formatClipName(clip.filename);

        // Build colored pill badges for AI data
        meta.innerHTML = '';
        const pills = document.createElement('div');
        pills.className = 'card-pills';
        const addPill = (text, cls) => {
            const pill = document.createElement('span');
            pill.className = `card-pill ${cls}`;
            pill.textContent = text;
            pills.appendChild(pill);
        };

        if (clip.ai_kills) addPill(`${clip.ai_kills}K`, 'kills');
        if (clip.ai_is_ace) addPill('ACE', 'ace');
        if (clip.ai_clutch_type) addPill(clip.ai_clutch_type, 'clutch');
        if (clip.ai_highlight_type && clip.ai_highlight_type !== 'regular-round' &&
            clip.ai_highlight_type !== 'non-gameplay')
            addPill(clip.ai_highlight_type, 'type');
        if (clip.ai_player_agent) addPill(clip.ai_player_agent, 'agent');
        if (clip.ai_map) addPill(clip.ai_map, 'map');
        if (clip.ai_weapon) addPill(clip.ai_weapon, 'weapon');

        if (pills.children.length > 0) {
            meta.appendChild(pills);
        } else {
            // Fallback: show resolution + size
            const parts = [];
            if (clip.width && clip.height) parts.push(`${clip.width}x${clip.height}`);
            if (clip.file_size_bytes) parts.push(formatBytes(clip.file_size_bytes));
            meta.textContent = parts.join(' | ');
        }

        grid.appendChild(card);
    }

    renderPagination(data.page, data.pages);
}

function renderPagination(current, total) {
    const wrap = document.getElementById('pagination');
    if (!wrap) return;
    wrap.innerHTML = '';

    if (total <= 1) return;

    const addBtn = (label, page, active = false) => {
        const btn = document.createElement('button');
        btn.textContent = label;
        btn.className = active ? 'active' : '';
        btn.disabled = active;
        btn.onclick = () => { currentPage = page; loadClips(); };
        wrap.appendChild(btn);
    };

    if (current > 1) addBtn('<', current - 1);

    const start = Math.max(1, current - 3);
    const end = Math.min(total, current + 3);
    for (let i = start; i <= end; i++) {
        addBtn(i, i, i === current);
    }

    if (current < total) addBtn('>', current + 1);
}

// ---- Event Listeners ----

function setupGalleryEvents() {
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        let debounce;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => {
                currentSearch = searchInput.value.trim();
                currentPage = 1;
                loadClips();
            }, 300);
        });
    }

    const sortSelect = document.getElementById('sort-select');
    if (sortSelect) {
        sortSelect.addEventListener('change', () => {
            currentSort = sortSelect.value;
            currentPage = 1;
            loadClips();
        });
    }

    const shareSelect = document.getElementById('share-select');
    if (shareSelect) {
        shareSelect.addEventListener('change', () => {
            currentShare = shareSelect.value;
            currentPage = 1;
            loadClips();
        });
    }

    const dupeCheckbox = document.getElementById('hide-dupes');
    if (dupeCheckbox) {
        dupeCheckbox.addEventListener('change', () => {
            hideDupes = dupeCheckbox.checked;
            currentPage = 1;
            loadClips();
        });
    }

    document.querySelectorAll('.tag-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const tag = link.dataset.tag;
            if (currentTag === tag) {
                currentTag = null;
                link.classList.remove('active');
            } else {
                document.querySelectorAll('.tag-link').forEach(l => l.classList.remove('active'));
                currentTag = tag;
                link.classList.add('active');
            }
            currentPage = 1;
            loadClips();
        });
    });
}

// ---- Score Distribution Chart ----

function renderScoreChart(distribution) {
    const chart = document.getElementById('score-chart');
    if (!chart || !distribution.length) return;

    // Build full 1-10 range
    const counts = {};
    for (const d of distribution) counts[d.score] = d.count;
    const maxCount = Math.max(...distribution.map(d => d.count), 1);

    chart.innerHTML = '';
    const colors = {
        1: '#555', 2: '#555', 3: '#555', 4: '#555',
        5: '#3b82f6', 6: '#3b82f6',
        7: '#c49b33', 8: '#c49b33',
        9: '#ff4655', 10: '#ff4655',
    };

    for (let score = 1; score <= 10; score++) {
        const count = counts[score] || 0;
        const height = count > 0 ? Math.max(4, (count / maxCount) * 100) : 0;

        const wrap = document.createElement('div');
        wrap.className = 'score-bar-wrap';

        const bar = document.createElement('div');
        bar.className = 'score-bar';
        bar.style.height = `${height}%`;
        bar.style.background = colors[score];

        const countLabel = document.createElement('span');
        countLabel.className = 'score-bar-count';
        countLabel.textContent = count;
        bar.appendChild(countLabel);

        // Click to filter by this score
        bar.addEventListener('click', () => {
            const scoreFilter = document.getElementById('score-filter');
            if (scoreFilter) {
                // Find the closest option
                const minScore = score >= 9 ? '9' : score >= 7 ? '7' : score >= 5 ? '5' : score >= 3 ? '3' : '';
                scoreFilter.value = minScore;
                currentScoreMin = minScore;
                currentPage = 1;
                const clearBtn = document.getElementById('clear-filters');
                if (clearBtn) clearBtn.style.display = minScore ? '' : 'none';
                loadClips();
            }
        });

        const label = document.createElement('span');
        label.className = 'score-bar-label';
        label.textContent = score;

        wrap.appendChild(bar);
        wrap.appendChild(label);
        chart.appendChild(wrap);
    }
}

// ---- Advanced Filters ----

async function loadFilterOptions() {
    const typeSelect = document.getElementById('type-filter');
    const mapSelect = document.getElementById('map-filter');
    const scoreChart = document.getElementById('score-chart');
    if (!typeSelect && !mapSelect && !scoreChart) return;

    const resp = await fetch(`${API}/filters`);
    const data = await resp.json();

    // Populate dynamic filter dropdowns
    const populateSelect = (id, items) => {
        const el = document.getElementById(id);
        if (!el || !items) return;
        for (const item of items) {
            const opt = document.createElement('option');
            opt.value = item.name;
            opt.textContent = `${item.name} (${item.count})`;
            el.appendChild(opt);
        }
    };

    populateSelect('type-filter', data.highlight_types);
    populateSelect('map-filter', data.maps);
    populateSelect('clutch-filter', data.clutch_types);
    populateSelect('weapon-filter', data.weapons);
    populateSelect('agent-filter', data.agents);

    if (data.score_distribution) {
        renderScoreChart(data.score_distribution);
    }
}

function setupFilterEvents() {
    const filters = {
        'score-filter': v => { currentScoreMin = v; },
        'kills-filter': v => { currentKillsMin = v; },
        'type-filter': v => { currentHighlightType = v; },
        'clutch-filter': v => { currentClutchType = v; },
        'agent-filter': v => { currentAgent = v; },
        'weapon-filter': v => { currentWeapon = v; },
        'map-filter': v => { currentMap = v; },
    };
    const clearBtn = document.getElementById('clear-filters');
    const acesCheckbox = document.getElementById('aces-only');

    function hasActiveFilters() {
        return currentScoreMin || currentKillsMin || currentHighlightType ||
               currentClutchType || currentAgent || currentWeapon || currentMap || acesOnly;
    }

    function updateClearBtn() {
        if (clearBtn) clearBtn.style.display = hasActiveFilters() ? '' : 'none';
    }

    for (const [id, setter] of Object.entries(filters)) {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', () => {
                setter(el.value);
                currentPage = 1;
                updateClearBtn();
                loadClips();
            });
        }
    }

    if (acesCheckbox) {
        acesCheckbox.addEventListener('change', () => {
            acesOnly = acesCheckbox.checked;
            currentPage = 1;
            updateClearBtn();
            loadClips();
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            currentScoreMin = '';
            currentKillsMin = '';
            currentHighlightType = '';
            currentClutchType = '';
            currentAgent = '';
            currentWeapon = '';
            currentMap = '';
            acesOnly = false;
            for (const id of Object.keys(filters)) {
                const el = document.getElementById(id);
                if (el) el.value = '';
            }
            if (acesCheckbox) acesCheckbox.checked = false;
            currentPage = 1;
            updateClearBtn();
            loadClips();
        });
    }
}

// ---- Hover Preview ----

function setupHoverPreviews() {
    const grid = document.getElementById('clip-grid');
    if (!grid) return;

    // Cache which previews exist to avoid repeated 404s
    const previewCache = {};

    grid.addEventListener('mouseenter', async (e) => {
        const card = e.target.closest('.clip-card');
        if (!card) return;
        const img = card.querySelector('.thumb-wrap img');
        if (!img || !img.src) return;

        const originalSrc = img.dataset.originalSrc || img.src;
        img.dataset.originalSrc = originalSrc;

        // Derive preview URL from thumbnail URL
        // thumbnail: /thumbnails/abc123.jpg -> preview: /thumbnails/abc123_preview.webp
        const previewSrc = originalSrc.replace(/\.jpg$/, '_preview.webp');

        if (previewCache[previewSrc] === false) return; // Known missing
        if (previewCache[previewSrc] === true) {
            img.src = previewSrc;
            return;
        }

        // Check if preview exists
        try {
            const resp = await fetch(previewSrc, { method: 'HEAD' });
            if (resp.ok) {
                previewCache[previewSrc] = true;
                // Only swap if still hovering this card
                if (card.matches(':hover')) {
                    img.src = previewSrc;
                }
            } else {
                previewCache[previewSrc] = false;
            }
        } catch {
            previewCache[previewSrc] = false;
        }
    }, true);

    grid.addEventListener('mouseleave', (e) => {
        const card = e.target.closest('.clip-card');
        if (!card) return;
        const img = card.querySelector('.thumb-wrap img');
        if (img && img.dataset.originalSrc) {
            img.src = img.dataset.originalSrc;
        }
    }, true);
}

// ---- Clip Detail Page ----

function setupClipDetailEvents() {
    const tagList = document.getElementById('tag-list');
    if (!tagList) return;
    const clipId = tagList.dataset.clipId;

    // Remove tag buttons
    tagList.addEventListener('click', async (e) => {
        const btn = e.target.closest('.tag-remove');
        if (!btn) return;
        const tag = btn.dataset.tag;
        await fetch(`${API}/clips/${clipId}/tags/${encodeURIComponent(tag)}`, { method: 'DELETE' });
        btn.parentElement.remove();
    });

    // Add tag form
    const form = document.getElementById('add-tag-form');
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const input = document.getElementById('new-tag');
            const name = input.value.trim().toLowerCase();
            if (!name) return;

            const resp = await fetch(`${API}/clips/${clipId}/tags`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });

            if (resp.ok) {
                const badge = document.createElement('span');
                badge.className = 'tag-badge';
                badge.innerHTML = `${name} <button class="tag-remove" data-tag="${name}">&times;</button>`;
                tagList.appendChild(badge);
                input.value = '';
            }
        });
    }
}

// ---- Timeline ----

async function loadTimeline() {
    const chart = document.getElementById('timeline-chart');
    if (!chart) return;

    const resp = await fetch(`${API}/timeline`);
    const data = await resp.json();
    if (!data.length) return;

    const maxCount = Math.max(...data.map(d => d.count));

    chart.innerHTML = '';
    for (const item of data) {
        const bar = document.createElement('div');
        bar.className = 'timeline-bar';
        const height = Math.max(4, (item.count / maxCount) * 100);
        bar.style.height = `${height}%`;

        const tooltip = document.createElement('span');
        tooltip.className = 'tooltip';
        tooltip.textContent = `${item.month}: ${item.count} clips`;
        bar.appendChild(tooltip);

        // Click a bar to filter to that month
        bar.addEventListener('click', () => {
            const [year, month] = item.month.split('-');
            const daysInMonth = new Date(year, month, 0).getDate();
            currentDateFrom = `${item.month}-01`;
            currentDateTo = `${item.month}-${daysInMonth}`;
            currentPage = 1;
            // Clear date range button active states
            document.querySelectorAll('.date-btn').forEach(b => b.classList.remove('active'));
            loadClips();
        });

        chart.appendChild(bar);
    }
}

// ---- Date Range Buttons ----

function setupDateRangeButtons() {
    document.querySelectorAll('.date-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.date-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            const range = btn.dataset.range;
            const now = new Date();

            if (range === 'all') {
                currentDateFrom = '';
                currentDateTo = '';
            } else {
                currentDateTo = now.toISOString().slice(0, 10);
                const from = new Date(now);
                switch (range) {
                    case 'week': from.setDate(from.getDate() - 7); break;
                    case 'month': from.setMonth(from.getMonth() - 1); break;
                    case '3mo': from.setMonth(from.getMonth() - 3); break;
                    case '6mo': from.setMonth(from.getMonth() - 6); break;
                    case 'year': from.setFullYear(from.getFullYear() - 1); break;
                }
                currentDateFrom = from.toISOString().slice(0, 10);
            }

            currentPage = 1;
            loadClips();
        });
    });
}

// ---- Keyboard Shortcuts (gallery) ----

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        if (e.key === '/') {
            e.preventDefault();
            const search = document.getElementById('search-input');
            if (search) search.focus();
        } else if (e.key === 'ArrowLeft') {
            // Previous page
            if (currentPage > 1) {
                currentPage--;
                loadClips();
            }
        } else if (e.key === 'ArrowRight') {
            // Next page (handled by pagination)
            const nextBtn = document.querySelector('.pagination button:last-child:not(.active)');
            if (nextBtn) nextBtn.click();
        }
    });
}

// ---- Init ----

document.addEventListener('DOMContentLoaded', () => {
    formatStaticElements();
    setupGalleryEvents();
    setupFilterEvents();
    setupDateRangeButtons();
    setupKeyboardShortcuts();
    setupClipDetailEvents();
    setupHoverPreviews();
    loadTimeline();
    loadFilterOptions();
    loadClips();
});
