/* News at Noon editor. Plain JS, no build step.
   Content model: the draft JSON from /api/draft. Summaries are markdown with
   [text](url) links; the contenteditable blocks convert to and from that. */
(function () {
  'use strict';
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));

  const state = { date: null, version: null, status: null, json: null, dirty: false,
                  saving: false, blocked: false, meta: {} };
  const params = new URLSearchParams(location.search);
  const wanted = params.get('d') || 'today';

  // ── markdown <-> html ─────────────────────────────────────────────
  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const escAttr = s => esc(s).replace(/"/g, '&quot;');
  function inlineToHtml(text) {
    const re = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
    let out = '', i = 0, m;
    while ((m = re.exec(text))) {
      out += esc(text.slice(i, m.index));
      out += '<a href="' + escAttr(m[2]) + '">' + esc(m[1]) + '</a>';
      i = m.index + m[0].length;
    }
    return out + esc(text.slice(i));
  }
  function mdToHtml(md) {
    const paras = String(md || '').replace(/\r/g, '').split(/\n{2,}/).map(p => p.trim()).filter(Boolean);
    if (!paras.length) return '<p><br></p>';
    return paras.map(p => '<p>' + inlineToHtml(p).replace(/\n/g, '<br>') + '</p>').join('');
  }
  function htmlToMd(root) {
    const paras = []; let cur = '';
    const flush = () => { const t = cur.replace(/ /g, ' ').replace(/[ \t]+\n/g, '\n').replace(/\n{2,}/g, '\n').trim(); if (t) paras.push(t); cur = ''; };
    (function walk(node) {
      for (const n of node.childNodes) {
        if (n.nodeType === 3) { cur += n.nodeValue; continue; }
        if (n.nodeType !== 1) continue;
        const tag = n.tagName;
        if (tag === 'BR') { cur += '\n'; continue; }
        if (tag === 'A') {
          const href = n.getAttribute('href') || '', t = n.textContent;
          cur += href && t.trim() ? '[' + t + '](' + href + ')' : t; continue;
        }
        if (/^(P|DIV|LI|H[1-6]|BLOCKQUOTE|PRE)$/.test(tag)) { flush(); walk(n); flush(); } else walk(n);
      }
    })(root);
    flush();
    return paras.join('\n\n');
  }

  // ── ui helpers ────────────────────────────────────────────────────
  const banner = $('#banner');
  function say(msg, kind) {
    banner.textContent = msg; banner.className = 'banner' + (kind ? ' ' + kind : ''); banner.hidden = !msg;
    if (kind === 'ok') setTimeout(() => { if (banner.textContent === msg) banner.hidden = true; }, 5000);
  }
  function chip(text, cls) { const c = $('#saveChip'); c.textContent = text; c.className = 'chip' + (cls ? ' ' + cls : ''); }
  function statusChip() {
    const c = $('#statusChip'); c.textContent = state.meta.status_label || '';
    c.className = 'chip ' + (state.status === 'held' ? 'held' : state.status === 'sent' ? 'sent' : '');
    $('#btnHold').textContent = state.status === 'held' ? 'Resume today’s send' : 'Hold today’s send';
    const locked = state.status === 'sent';
    ['#btnHold', '#btnSendNow', '#btnReset'].forEach(s => { $(s).disabled = locked; });
  }
  function counts() {
    const es = state.json.entries || [];
    const shown = es.filter(e => e.tier !== 'premium').length;
    $('#freeShown').textContent = shown; $('#freeTotal').textContent = es.length;
  }
  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' },
                                  body: body === undefined ? undefined : JSON.stringify(body) });
    if (r.status === 401) { location.href = '/login'; throw new Error('signed out'); }
    let data = null; try { data = await r.json(); } catch (_) { /* no body */ }
    if (!r.ok) { const err = new Error((data && data.detail) || ('HTTP ' + r.status)); err.status = r.status; err.data = data; throw err; }
    return data;
  }

  // ── saving ────────────────────────────────────────────────────────
  let saveTimer = null;
  function touch() {
    if (state.blocked) return;
    state.dirty = true; chip('Unsaved', 'dirty');
    clearTimeout(saveTimer); saveTimer = setTimeout(save, 1200);
  }
  async function save() {
    if (!state.dirty || state.saving || state.blocked) return;
    clearTimeout(saveTimer);
    state.saving = true; chip('Saving…');
    (state.json.entries || []).forEach((e, i) => { e.rank = i + 1; });
    try {
      const data = await api('PUT', '/api/draft/' + state.date, { version: state.version, json: state.json });
      state.version = data.version; state.meta = data; state.dirty = false; chip('Saved');
    } catch (e) {
      if (e.status === 409) {
        state.blocked = true; chip('Stale', 'err');
        say('This draft was changed somewhere else (another tab or phone). Reload to pick up the latest version; edits made here since are not saved.', 'err');
      } else { chip('Save failed', 'err'); say('Save failed: ' + e.message, 'err'); }
    } finally { state.saving = false; if (state.dirty && !state.blocked) touch(); }
  }
  async function ensureSaved() { if (state.dirty) await save(); return !state.dirty; }
  window.addEventListener('beforeunload', ev => { if (state.dirty) { save(); ev.preventDefault(); ev.returnValue = ''; } });
  document.addEventListener('visibilitychange', () => { if (document.hidden && state.dirty) save(); });

  // ── selection / links ─────────────────────────────────────────────
  let savedRange = null, activeEditor = null, pending = null;
  function editorOf(node) { const el = node && (node.nodeType === 1 ? node : node.parentElement); return el ? el.closest('.rich') : null; }
  document.addEventListener('selectionchange', () => {
    const sel = document.getSelection(); if (!sel || !sel.rangeCount) return;
    const r = sel.getRangeAt(0); const ed = editorOf(r.startContainer);
    if (!ed) return;
    savedRange = r.cloneRange(); activeEditor = ed;
    $$('.rich a.active').forEach(a => a.classList.remove('active'));
    const a = anchorAt(r); if (a) a.classList.add('active');
  });
  function anchorAt(range) {
    const el = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
    return el ? el.closest('a') : null;
  }
  function restoreRange(range, ed) {
    ed.focus(); const sel = document.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  }
  function unwrap(a) { const p = a.parentNode; while (a.firstChild) p.insertBefore(a.firstChild, a); p.removeChild(a); }
  function toolbarFor(ed) { return ed.parentElement.querySelector('.tools[data-for], .tools'); }
  function linkboxFor(ed) { return ed.parentElement.querySelector('.linkbox'); }
  function keepSelection(btn) { btn.addEventListener('mousedown', ev => ev.preventDefault()); }

  function startLink(ed) {
    const box = linkboxFor(ed), input = $('input', box), selInfo = $('.sel', box);
    const r = savedRange && activeEditor === ed ? savedRange : null;
    const a = r ? anchorAt(r) : null;
    if (a) {
      pending = { ed, anchor: a }; selInfo.textContent = 'Editing link on “' + a.textContent + '”'; input.value = a.getAttribute('href') || '';
    } else if (r && !r.collapsed && r.toString().trim()) {
      pending = { ed, range: r.cloneRange() }; selInfo.textContent = 'Link “' + r.toString().trim() + '” to:'; input.value = '';
    } else { say('Select a word or phrase first, then press Link.', ''); return; }
    box.hidden = false; input.focus();
  }
  function applyLink(ed) {
    const box = linkboxFor(ed), input = $('input', box);
    let url = input.value.trim();
    if (url && !/^https?:\/\//i.test(url)) url = 'https://' + url;
    if (!pending || pending.ed !== ed) { box.hidden = true; return; }
    if (!url) { say('Enter a URL, or Cancel.', ''); return; }
    if (pending.anchor) { pending.anchor.setAttribute('href', url); }
    else {
      restoreRange(pending.range, ed);
      document.execCommand('createLink', false, url);
      // execCommand may add target/rel or leave the new anchor unselected; nothing else to do.
    }
    pending = null; box.hidden = true; onRichInput(ed);
  }
  function removeLink(ed) {
    const r = savedRange && activeEditor === ed ? savedRange : null;
    const a = r ? anchorAt(r) : (pending && pending.anchor);
    if (!a) { say('Put the cursor on a linked word first.', ''); return; }
    unwrap(a); pending = null; linkboxFor(ed).hidden = true; onRichInput(ed);
  }
  function wireTools(section, ed) {
    $$('.tools .btn, .linkbox .btn', section).forEach(keepSelection);
    section.addEventListener('click', ev => {
      const b = ev.target.closest('[data-act]'); if (!b) return;
      const act = b.dataset.act;
      if (act === 'link') startLink(ed);
      else if (act === 'unlink') removeLink(ed);
      else if (act === 'apply') applyLink(ed);
      else if (act === 'cancel') { pending = null; linkboxFor(ed).hidden = true; }
    });
    const input = $('.linkbox input', section);
    input.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); applyLink(ed); } });
    ed.addEventListener('paste', ev => {
      ev.preventDefault(); const t = (ev.clipboardData || window.clipboardData).getData('text/plain');
      document.execCommand('insertText', false, t);
    });
    ed.addEventListener('input', () => onRichInput(ed));
  }
  function onRichInput(ed) {
    const md = htmlToMd(ed);
    const kind = ed.dataset.kind;
    if (kind === 'intro') state.json.intro = md;
    else if (kind === 'summary') state.json.entries[+ed.dataset.idx].summary = md;
    else if (kind === 'paper') state.json.paper_of_the_day.summary = md;
    touch();
  }

  // ── entries ───────────────────────────────────────────────────────
  function toolsHtml() {
    return '<div class="tools">' +
      '<button class="btn" data-act="link">Link</button>' +
      '<button class="btn" data-act="unlink">Unlink</button>' +
      '<span class="spacer"></span>' +
      '<div class="seg"><button data-act="free">Free</button><button data-act="premium">Premium</button></div>' +
      '<button class="btn icon" data-act="up" title="Move up">↑</button>' +
      '<button class="btn icon" data-act="down" title="Move down">↓</button>' +
      '<button class="btn icon danger" data-act="delete" title="Delete">✕</button>' +
      '</div>' +
      '<div class="linkbox" hidden><div class="sel"></div>' +
      '<input type="url" placeholder="https://…" inputmode="url" autocapitalize="off" autocomplete="off">' +
      '<button class="btn primary" data-act="apply">Apply</button><button class="btn" data-act="cancel">Cancel</button></div>';
  }
  function renderEntries() {
    const host = $('#entries'); host.innerHTML = '';
    (state.json.entries || []).forEach((e, i) => {
      const sec = document.createElement('section');
      sec.className = 'entry' + (e.tier === 'premium' ? ' premium' : ''); sec.dataset.idx = i;
      const pills = (e.news_outlets || []).slice(0, 6).join(' · ');
      sec.innerHTML =
        '<div class="head"><span class="num">' + (i + 1) + '</span>' +
        '<input class="title" value="' + escAttr(e.title || '') + '" placeholder="Title"></div>' +
        '<div class="meta">' + esc(e.origin === 'cluster' ? 'From social' : 'From news') + (pills ? ' · ' + esc(pills) : '') + '</div>' +
        '<div class="rich" contenteditable="true" data-kind="summary" data-idx="' + i + '">' + mdToHtml(e.summary) + '</div>' +
        toolsHtml();
      host.appendChild(sec);
      const ed = $('.rich', sec);
      wireTools(sec, ed);
      $('.title', sec).addEventListener('input', ev => { state.json.entries[i].title = ev.target.value; touch(); });
      const seg = $('.seg', sec); seg.children[e.tier === 'premium' ? 1 : 0].classList.add('on', e.tier === 'premium' ? 'premium' : 'free');
      sec.addEventListener('click', ev => {
        const b = ev.target.closest('[data-act]'); if (!b) return;
        const act = b.dataset.act, es = state.json.entries;
        if (act === 'free' || act === 'premium') {
          es[i].tier = act; sec.classList.toggle('premium', act === 'premium');
          Array.from(seg.children).forEach(c => c.className = ''); b.className = 'on ' + act;
          counts(); touch();
        } else if (act === 'up' && i > 0) { [es[i - 1], es[i]] = [es[i], es[i - 1]]; touch(); renderEntries(); }
        else if (act === 'down' && i < es.length - 1) { [es[i + 1], es[i]] = [es[i], es[i + 1]]; touch(); renderEntries(); }
        else if (act === 'delete') {
          const [gone] = es.splice(i, 1); (state.json._deleted_entries = state.json._deleted_entries || []).push(gone);
          touch(); renderEntries();
        }
      });
    });
    renderDeleted(); counts();
  }
  function renderDeleted() {
    const host = $('#deleted'); host.innerHTML = '';
    (state.json._deleted_entries || []).forEach((e, i) => {
      const row = document.createElement('div'); row.className = 'deleted';
      row.innerHTML = '<span class="t">' + esc(e.title || '(untitled)') + '</span><button class="btn">Restore</button>';
      $('button', row).addEventListener('click', () => {
        const [back] = state.json._deleted_entries.splice(i, 1); back.tier = back.tier || 'premium';
        state.json.entries.push(back); touch(); renderEntries();
      });
      host.appendChild(row);
    });
  }

  // ── paper of the day ──────────────────────────────────────────────
  function renderPaper() {
    const host = $('#paper'); host.innerHTML = '';
    const p = state.json.paper_of_the_day;
    if (!p || !p.title) {
      const removed = state.json._removed_paper;
      host.innerHTML = '<p class="muted">No paper today.' + (removed ? ' <button class="btn" id="paperRestore">Restore “' + esc(removed.title) + '”</button>' : '') + '</p>';
      const b = $('#paperRestore'); if (b) b.addEventListener('click', () => { state.json.paper_of_the_day = state.json._removed_paper; delete state.json._removed_paper; touch(); renderPaper(); });
      return;
    }
    const f = (k, label) => '<div class="field"><label>' + label + '</label><input data-k="' + k + '" value="' + escAttr(p[k] || '') + '"></div>';
    host.innerHTML = f('title', 'Title') + f('authors', 'Authors') + f('publication', 'Publication') + f('url', 'URL') +
      '<div class="sub">Summary</div><div class="rich" contenteditable="true" data-kind="paper">' + mdToHtml(p.summary) + '</div>' +
      '<div class="tools"><button class="btn" data-act="link">Link</button><button class="btn" data-act="unlink">Unlink</button>' +
      '<span class="spacer"></span><button class="btn danger" id="paperRemove">Remove paper</button></div>' +
      '<div class="linkbox" hidden><div class="sel"></div><input type="url" placeholder="https://…" inputmode="url" autocapitalize="off" autocomplete="off">' +
      '<button class="btn primary" data-act="apply">Apply</button><button class="btn" data-act="cancel">Cancel</button></div>';
    $$('input[data-k]', host).forEach(inp => inp.addEventListener('input', ev => { p[ev.target.dataset.k] = ev.target.value; touch(); }));
    wireTools(host, $('.rich', host));
    $('#paperRemove').addEventListener('click', () => { state.json._removed_paper = p; state.json.paper_of_the_day = null; touch(); renderPaper(); });
  }

  // ── actions ───────────────────────────────────────────────────────
  function twoTap(btn, label, fn) {
    let armed = null;
    btn.addEventListener('click', async () => {
      if (armed) { clearTimeout(armed); armed = null; btn.textContent = label; await fn(); return; }
      btn.textContent = 'Tap again to confirm'; armed = setTimeout(() => { armed = null; btn.textContent = label; }, 4000);
    });
  }
  async function openPreview(tier) {
    if (!(await ensureSaved())) { say('Could not save before preview.', 'err'); return; }
    window.open('/preview/' + state.date + '?tier=' + tier, '_blank');
  }
  async function sendTest(tier) {
    if (!(await ensureSaved())) { say('Could not save before sending.', 'err'); return; }
    say('Sending the ' + tier + ' edition to you…');
    try { const r = await api('POST', '/api/draft/' + state.date + '/send-test', { tier }); say('Sent the ' + tier + ' edition to ' + r.to + '.', 'ok'); }
    catch (e) { say('Test send failed: ' + e.message, 'err'); }
  }
  function applyMeta(data) {
    state.meta = data; state.status = data.status; state.version = data.version; statusChip();
    $('#footnote').textContent = 'Draft from brief #' + (data.source_id || '?') + ' · version ' + data.version +
      ' · noon send mode: ' + data.send_mode + (data.sent_at ? ' · sent ' + data.sent_at.slice(0, 16).replace('T', ' ') + ' UTC' : '');
  }

  function wireActions() {
    $('#btnPreviewFree').addEventListener('click', () => openPreview('free'));
    $('#btnPreviewPremium').addEventListener('click', () => openPreview('premium'));
    $('#btnPdf').addEventListener('click', async () => { if (!(await ensureSaved())) { say('Could not save before the PDF.', 'err'); return; } window.open('/pdf/' + state.date, '_blank'); });
    $('#btnTestFree').addEventListener('click', () => sendTest('free'));
    $('#btnTestPremium').addEventListener('click', () => sendTest('premium'));
    $('#btnHold').addEventListener('click', async () => {
      const next = state.status === 'held' ? 'draft' : 'held';
      try { applyMeta(await api('POST', '/api/draft/' + state.date + '/status', { status: next })); say(next === 'held' ? 'Held. Today’s edition will not send until you resume it.' : 'Resumed. It sends at 12:15 ET.', 'ok'); }
      catch (e) { say(e.message, 'err'); }
    });
    twoTap($('#btnSendNow'), 'Send now', async () => {
      if (!(await ensureSaved())) { say('Could not save before sending.', 'err'); return; }
      try { applyMeta(await api('POST', '/api/draft/' + state.date + '/send-now', { confirm: true })); say('Sent. ' + (state.meta.send_log || ''), 'ok'); }
      catch (e) { say('Send failed: ' + e.message, 'err'); }
    });
    twoTap($('#btnReset'), 'Discard edits and rebuild', async () => {
      try { const d = await api('POST', '/api/draft/' + state.date + '/reset'); load(d); say('Rebuilt from the stored brief. Your edits were discarded.', 'ok'); }
      catch (e) { say(e.message, 'err'); }
    });
  }

  // ── load ──────────────────────────────────────────────────────────
  function load(data) {
    state.date = data.date; state.json = data.json; state.dirty = false; state.blocked = false;
    applyMeta(data);
    $('#dateLabel').textContent = data.date_label;
    document.title = 'News at Noon — ' + data.date_label;
    const intro = $('#intro'); intro.innerHTML = mdToHtml(state.json.intro || '');
    renderEntries(); renderPaper(); chip('Saved');
  }
  async function boot() {
    wireTools($('.standfirst'), $('#intro'));
    wireActions();
    try { load(await api('GET', '/api/draft?d=' + encodeURIComponent(wanted))); }
    catch (e) {
      chip('No draft', 'err');
      say((e.message || 'No draft.') + ' The pipeline runs at 7:00 ET; the draft appears here a few minutes after. Reload to check again.', 'err');
    }
  }
  boot();
})();
