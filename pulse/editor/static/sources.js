/* Housing at Noon — LinkedIn sources page. Plain JS, no build step.
   Data from GET /api/sources; each account carries `included`, the posts
   already collected (`corpus`, free) and any Apify `preview` (paid, cached). */
(function () {
  'use strict';
  const $ = (s, el) => (el || document).querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const PAGE = 40;
  const state = { data: null, filter: 'in', q: '', sort: 'interactions', shown: PAGE, busy: new Set() };

  function banner(msg, kind) {
    const b = $('#banner');
    if (!msg) { b.hidden = true; return; }
    b.textContent = msg; b.className = 'banner' + (kind ? ' ' + kind : ''); b.hidden = false;
  }

  async function api(method, url, body) {
    const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {},
                                 body: body ? JSON.stringify(body) : undefined, credentials: 'same-origin' });
    if (r.status === 401) { location.href = '/login'; throw new Error('not signed in'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
    return j;
  }

  // posts to show: collected posts if any, otherwise the Apify preview
  function activity(a) {
    if (a.corpus && a.corpus.count) return { n: a.corpus.count, posts: a.corpus.posts, kind: 'corpus' };
    if (a.preview) return { n: a.preview.posts.length, posts: a.preview.posts, kind: 'preview' };
    return null;
  }

  function visible() {
    const q = state.q.toLowerCase();
    let rows = state.data.accounts.filter(a =>
      (state.filter === 'all' || (state.filter === 'in') === a.included) &&
      (!q || (a.name + ' ' + (a.headline || '') + ' ' + (a.company || '')).toLowerCase().includes(q)));
    const act = a => { const x = activity(a); return x ? x.n : -1; };
    if (state.sort === 'name') rows = rows.slice().sort((a, b) => a.name.localeCompare(b.name));
    else if (state.sort === 'activity') rows = rows.slice().sort((a, b) => act(b) - act(a));
    else rows = rows.slice().sort((a, b) => (b.interactions || 0) - (a.interactions || 0));
    return rows;
  }

  function postHtml(p) {
    const text = (p.text || '').replace(/\s+/g, ' ').slice(0, 260);
    return `<li><span class="muted">${esc(p.date)} · ${p.likes || 0} likes · ${p.comments || 0} comments</span><br>
      <a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(text) || '(no text)'}</a></li>`;
  }

  function cardHtml(a) {
    const x = activity(a);
    let line;
    if (x && x.kind === 'corpus') line = `${x.n} post${x.n === 1 ? '' : 's'} collected in the last ${state.data.activity_days} days`;
    else if (x) line = `${x.n === 5 ? '5 or more' : x.n} post${x.n === 1 ? '' : 's'} in the month before ${esc(a.preview.fetched_at.slice(0, 10))}`;
    else if (a.included) line = `No posts collected in the last ${state.data.activity_days} days`;
    else line = 'Recent posts not loaded';
    const busy = state.busy.has(a.key);
    const canLoad = !(x && x.kind === 'corpus');
    const bits = [];
    if (a.interactions) bits.push(`you interacted ${a.interactions} time${a.interactions === 1 ? '' : 's'}`);
    if (a.company_page) bits.push('company page');
    return `<div class="src ${a.included ? 'in' : ''}" data-key="${esc(a.key)}">
      <div class="src-head">
        <div class="src-name"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.name || a.key)}</a>
          ${a.included ? '<span class="chip sent">In</span>' : ''}</div>
        <button class="btn ${a.included ? '' : 'primary'}" data-act="toggle">${a.included ? 'Remove' : 'Add'}</button>
      </div>
      ${a.headline ? `<div class="muted src-headline">${esc(a.headline)}</div>` : ''}
      <div class="src-meta">${line}${bits.length ? ' · ' + bits.join(' · ') : ''}
        ${canLoad ? `<button class="btn" data-act="load" ${busy ? 'disabled' : ''}>${busy ? 'Loading…' : (a.preview ? 'Reload' : 'Load recent posts')}</button>` : ''}</div>
      ${x && x.posts.length ? `<details><summary>Show posts</summary><ul class="src-posts">${x.posts.map(postHtml).join('')}</ul></details>` : ''}
    </div>`;
  }

  function render() {
    const d = state.data;
    $('#countChip').textContent = `${d.included} of ${d.max_targets} in`;
    $('#btnPublish').disabled = !d.unpublished;
    $('#btnPublish').textContent = d.unpublished ? 'Publish changes' : 'Published';
    const rows = visible();
    $('#list').innerHTML = rows.slice(0, state.shown).map(cardHtml).join('') ||
      '<p class="muted">No accounts match.</p>';
    $('#btnMore').hidden = rows.length <= state.shown;
    $('#btnMore').textContent = `Show more (${rows.length - state.shown} left)`;
    const pending = rows.filter(a => !activity(a) && !a.preview).length;
    $('#btnBatch').disabled = !pending || state.busy.size > 0;
    $('#costNote').textContent = `Loading posts uses Apify: up to $${(d.cost_per_account_max_usd * 25).toFixed(2)} for 25 accounts, ` +
      `up to $${d.cost_per_account_max_usd.toFixed(4)} each. ${pending} account${pending === 1 ? '' : 's'} in this view have nothing loaded.`;
  }

  async function load() {
    state.data = await api('GET', '/api/sources');
    render();
  }

  async function toggle(a) {
    try {
      const r = await api('POST', '/api/sources/include', { key: a.key, include: !a.included });
      a.included = !a.included;
      state.data.included = r.included;
      state.data.unpublished = r.unpublished;
      banner(null);
      render();
    } catch (e) { banner(e.message, 'err'); }
  }

  async function fetchPosts(keys) {
    keys.forEach(k => state.busy.add(k));
    render();
    banner(`Loading recent posts for ${keys.length} account${keys.length === 1 ? '' : 's'} from LinkedIn. This can take a minute.`);
    try {
      const r = await api('POST', '/api/sources/fetch', { keys });
      const byKey = Object.fromEntries(state.data.accounts.map(a => [a.key, a]));
      Object.entries(r.previews || {}).forEach(([k, p]) => { if (byKey[k]) byKey[k].preview = p; });
      banner(`Loaded ${r.posts} posts for ${r.fetched} account${r.fetched === 1 ? '' : 's'} (about $${(r.cost_usd || 0).toFixed(2)}).`, 'ok');
    } catch (e) { banner(e.message, 'err'); }
    keys.forEach(k => state.busy.delete(k));
    render();
  }

  $('#list').addEventListener('click', ev => {
    const btn = ev.target.closest('button[data-act]');
    if (!btn) return;
    const key = btn.closest('.src').dataset.key;
    const a = state.data.accounts.find(x => x.key === key);
    if (btn.dataset.act === 'toggle') toggle(a);
    if (btn.dataset.act === 'load') fetchPosts([a.key]);
  });
  $('#filter').addEventListener('click', ev => {
    const b = ev.target.closest('button[data-f]');
    if (!b) return;
    state.filter = b.dataset.f; state.shown = PAGE;
    document.querySelectorAll('#filter button').forEach(x => x.classList.toggle('on', x === b));
    render();
  });
  $('#q').addEventListener('input', ev => { state.q = ev.target.value; state.shown = PAGE; render(); });
  $('#sort').addEventListener('change', ev => { state.sort = ev.target.value; render(); });
  $('#btnMore').addEventListener('click', () => { state.shown += PAGE; render(); });
  $('#btnBatch').addEventListener('click', () => {
    const keys = visible().filter(a => !activity(a) && !a.preview).slice(0, state.data.fetch_batch_max).map(a => a.key);
    if (keys.length) fetchPosts(keys);
  });
  $('#btnPublish').addEventListener('click', async () => {
    const b = $('#btnPublish');
    b.disabled = true; b.textContent = 'Publishing…';
    try {
      const r = await api('POST', '/api/sources/publish');
      state.data.unpublished = false;
      banner(r.message, 'ok');
    } catch (e) { banner(e.message, 'err'); state.data.unpublished = true; }
    render();
  });

  load().catch(e => banner(e.message, 'err'));
})();
