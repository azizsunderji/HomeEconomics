/* Housing at Noon: the social cards page (/cards/{date}). Plain JS, no build step.
   Each card has a text panel: the title (one line), the body (paragraphs), a character
   counter against the card's budget, Re-render and Reset to generated. Edits are saved as
   overrides (PUT /api/cards/{date}/{pos}) and used by every later render, including the
   one after the noon send. Owner's request, 29 Sep 2026: "I want a way to edit the text,
   some kind of editor, like the main one". Editing never calls Claude. */
(function () {
  'use strict';
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const date = decodeURIComponent(location.pathname.split('/')[2] || '');
  const state = { data: null, busy: false, pending: new Map(), timers: new Map() };
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const escAttr = s => esc(s).replace(/"/g, '&quot;');
  document.execCommand('defaultParagraphSeparator', false, 'p');

  const banner = $('#banner');
  function say(msg, kind) {
    banner.textContent = msg; banner.className = 'banner' + (kind ? ' ' + kind : ''); banner.hidden = !msg;
    if (kind === 'ok') setTimeout(() => { if (banner.textContent === msg) banner.hidden = true; }, 5000);
  }
  function chip(text, cls) { const c = $('#saveChip'); c.textContent = text; c.className = 'chip' + (cls ? ' ' + cls : ''); }
  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' },
                                  body: body === undefined ? undefined : JSON.stringify(body) });
    if (r.status === 401) { location.href = '/login'; throw new Error('signed out'); }
    let data = null; try { data = await r.json(); } catch (_) { /* no body */ }
    if (!r.ok) { const e = new Error((data && data.detail) || ('HTTP ' + r.status)); e.status = r.status; throw e; }
    return data;
  }

  // ── body text <-> contenteditable ──────────────────────────────────
  function bodyHtml(text) {
    const paras = String(text || '').replace(/\r/g, '').split(/\n\s*\n/).map(p => p.replace(/\s+/g, ' ').trim()).filter(Boolean);
    return paras.length ? paras.map(p => '<p>' + esc(p) + '</p>').join('') : '<p><br></p>';
  }
  function bodyText(root) {
    const blocks = []; let cur = '';
    const flush = () => { blocks.push(cur); cur = ''; };
    (function walk(node) {
      for (const n of node.childNodes) {
        if (n.nodeType === 3) { cur += n.nodeValue; continue; }
        if (n.nodeType !== 1) continue;
        if (n.tagName === 'BR') { cur += '\n'; continue; }
        if (/^(P|DIV|LI|H[1-6]|BLOCKQUOTE)$/.test(n.tagName)) { flush(); walk(n); flush(); } else walk(n);
      }
    })(root);
    flush();
    // a blank line (or a new block) starts a paragraph; a single line break is a space
    return blocks.join('\n\n').split(/\n\s*\n/).map(p => p.replace(/\s+/g, ' ').trim()).filter(Boolean).join('\n\n');
  }

  // ── one-line title check (same font, width and tracking as the card) ──
  const canvas = document.createElement('canvas').getContext('2d');
  function titlePx(t) {
    const L = (state.data && state.data.title_limits) || { max_px: 54, min_px: 40, width_px: 920, tracking_em: -0.03 };
    canvas.font = '500 40px "Card Oracle", "Helvetica Neue", Arial, sans-serif';
    const w40 = canvas.measureText(t).width + L.tracking_em * 40 * [...t].length;
    for (let px = L.max_px; px >= L.min_px; px -= 2) if (w40 * px / 40 <= L.width_px - 2) return px;
    return null;
  }
  function fitTitle(t) { t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; }
  window.addEventListener('resize', () => $$('textarea').forEach(fitTitle));

  // ── rendering the page ─────────────────────────────────────────────
  function cardRow(c) {
    return '<div class="entry crow" data-pos="' + c.pos + '">' +
      '<figure class="cimg"><a href="' + escAttr(c.image) + '" download><img src="' + escAttr(c.image) + '" alt="Card ' + c.n + '"></a>' +
      '<figcaption>Card ' + c.n + ' · <a href="' + escAttr(c.image) + '" download>save</a></figcaption></figure>' +
      '<div class="cpanel">' +
        '<div class="clabel"><span class="num">' + c.pos + '</span><span class="cstate"></span></div>' +
        '<textarea class="title" rows="1" placeholder="Title" enterkeyhint="done">' + esc(c.title) + '</textarea>' +
        '<div class="tmeta"></div>' +
        '<div class="rich cbody" contenteditable="true" spellcheck="true">' + bodyHtml(c.body) + '</div>' +
        '<div class="cmeta"><span class="counter"></span><span class="cnote"></span></div>' +
        '<div class="tools"><button class="btn primary" data-act="render">Re-render</button>' +
        '<a href="#" class="reset" data-act="reset">Reset to generated</a></div>' +
      '</div></div>';
  }
  function ctaRow(c) {
    return '<div class="entry crow" data-pos="0">' +
      '<figure class="cimg"><a href="' + escAttr(c.image) + '" download><img src="' + escAttr(c.image) + '" alt="Card ' + c.n + '"></a>' +
      '<figcaption>Card ' + c.n + ' (sign-up card) · <a href="' + escAttr(c.image) + '" download>save</a></figcaption></figure>' +
      '<div class="cpanel"><div class="clabel"><span>Sign-up card: description line</span><span class="cstate"></span></div>' +
        '<textarea class="title desc" rows="1" placeholder="' + escAttr(c.default_desc) + '">' + esc(c.desc) + '</textarea>' +
        '<div class="cmeta"><span class="counter"></span></div>' +
        '<div class="tools"><button class="btn primary" data-act="render">Re-render</button>' +
        '<a href="#" class="reset" data-act="reset">Reset to generated</a></div>' +
      '</div></div>';
  }
  function cardOf(pos) { return pos === 0 ? state.data.cta : state.data.cards.find(c => c.pos === pos); }
  function refreshRow(row) {
    const pos = +row.dataset.pos, c = cardOf(pos);
    const edited = state.pending.has(pos) || c.has_override;
    $('.cstate', row).textContent = state.pending.has(pos) ? 'Unsaved' : c.has_override ? 'Your text' : 'Generated';
    $('.cstate', row).className = 'cstate' + (edited ? ' edited' : '');
    $('.reset', row).hidden = !edited;
    if (pos === 0) { $('.counter', row).textContent = $('textarea', row).value.length + ' characters'; return; }
    const t = $('textarea.title', row).value.replace(/\s+/g, ' ').trim();
    const px = titlePx(t);
    const tm = $('.tmeta', row);
    tm.textContent = !t ? '' : px ? 'Title: one line at ' + px + ' px' : 'Title too long for one line even at 40 px: shorten it';
    tm.classList.toggle('over', !!t && !px);
    $('textarea.title', row).classList.toggle('over', !!t && !px);
    const n = bodyText($('.cbody', row)).length;
    const cn = $('.counter', row);
    cn.textContent = n + ' / ' + c.budget + ' characters';
    cn.classList.toggle('over', n > c.budget);
    const note = $('.cnote', row);
    if (c.cut) { note.textContent = 'Too long for the card at 36 px: the image leaves off the last sentence(s). Shorten the text and re-render.'; note.className = 'cnote over'; }
    else if (c.title_wrap) { note.textContent = 'The title wraps to two lines on the image.'; note.className = 'cnote over'; }
    else if (c.missing && c.missing.length && !c.has_override) { note.textContent = 'Generated text is missing: ' + c.missing.join(', '); note.className = 'cnote'; }
    else { note.textContent = ''; note.className = 'cnote'; }
  }
  function draw() {
    const d = state.data;
    $('#dateLabel').textContent = 'Cards · ' + d.date_label;
    $('#pdfLink').href = d.pdf; $('#pdfLink').textContent = 'Download the carousel PDF (' + (d.cards.length + 1) + ' pages)';
    $('#editorLink').href = '/?d=' + encodeURIComponent(d.date);
    $('#cards').innerHTML = d.cards.map(cardRow).join('') + ctaRow(d.cta);
    $$('.crow').forEach(row => { wire(row); refreshRow(row); });
    $$('textarea').forEach(fitTitle);
    chip('Saved');
  }

  // ── saving ─────────────────────────────────────────────────────────
  function collect(row) {
    const pos = +row.dataset.pos;
    if (pos === 0) return { title: '', body: $('textarea', row).value.replace(/\s+/g, ' ').trim() };
    return { title: $('textarea.title', row).value.replace(/\s+/g, ' ').trim(), body: bodyText($('.cbody', row)) };
  }
  function touch(row) {
    const pos = +row.dataset.pos;
    state.pending.set(pos, true); chip('Unsaved', 'dirty'); refreshRow(row);
    clearTimeout(state.timers.get(pos));
    state.timers.set(pos, setTimeout(() => saveRow(row), 1200));
  }
  async function saveRow(row) {
    const pos = +row.dataset.pos;
    if (!state.pending.has(pos)) return;
    clearTimeout(state.timers.get(pos));
    state.pending.delete(pos);
    chip('Saving…');
    try {
      const r = await api('PUT', '/api/cards/' + date + '/' + pos, collect(row));
      cardOf(pos).has_override = r.has_override;
      if (!state.pending.size) chip('Saved');
    } catch (e) { state.pending.set(pos, true); chip('Save failed', 'err'); say('Save failed: ' + e.message, 'err'); }
    refreshRow(row);
  }
  async function saveAll() { for (const row of $$('.crow')) if (state.pending.has(+row.dataset.pos)) await saveRow(row); }
  window.addEventListener('beforeunload', ev => { if (state.pending.size) { saveAll(); ev.preventDefault(); ev.returnValue = ''; } });
  document.addEventListener('visibilitychange', () => { if (document.hidden && state.pending.size) saveAll(); });

  // ── re-render ──────────────────────────────────────────────────────
  function setBusy(on) {
    state.busy = on;
    $$('[data-act=render]').forEach(b => { b.disabled = on; b.textContent = on ? 'Rendering…' : 'Re-render'; });
    $$('.cbody').forEach(b => b.setAttribute('contenteditable', on ? 'false' : 'true'));
    $$('.crow textarea').forEach(t => { t.readOnly = on; });
  }
  async function rerender() {
    if (state.busy) return;
    await saveAll();
    if (state.pending.size) return;
    setBusy(true); chip('Rendering…');
    const t0 = Date.now();
    try {
      state.data = await api('POST', '/api/cards/' + date + '/render');
      draw();
      say('Cards and PDF re-rendered (' + ((Date.now() - t0) / 1000).toFixed(1) + ' s).', 'ok');
    } catch (e) { chip('Render failed', 'err'); say('Render failed: ' + e.message, 'err'); }
    finally { setBusy(false); }
  }

  function wire(row) {
    const pos = +row.dataset.pos;
    $$('textarea', row).forEach(t => {
      t.addEventListener('input', () => { fitTitle(t); touch(row); });
      // one line: Enter does not add a line break
      t.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); t.blur(); } });
      t.addEventListener('blur', () => saveRow(row));
    });
    const body = $('.cbody', row);
    if (body) {
      body.addEventListener('input', () => touch(row));
      body.addEventListener('blur', () => saveRow(row));
      // plain text only: the card has no links or styling
      body.addEventListener('paste', ev => {
        ev.preventDefault();
        const text = (ev.clipboardData || window.clipboardData).getData('text/plain');
        document.execCommand('insertText', false, text);
      });
    }
    $('[data-act=render]', row).addEventListener('click', rerender);
    $('[data-act=reset]', row).addEventListener('click', async ev => {
      ev.preventDefault();
      if (state.busy) return;
      clearTimeout(state.timers.get(pos)); state.pending.delete(pos);
      try { await api('DELETE', '/api/cards/' + date + '/' + pos); } catch (e) { say('Reset failed: ' + e.message, 'err'); return; }
      await rerender();
    });
  }

  async function boot() {
    try {
      if (document.fonts && document.fonts.load) { try { await document.fonts.load('500 40px "Card Oracle"'); } catch (_) { /* fallback font */ } }
      state.data = await api('GET', '/api/cards/' + date);
      draw();
    } catch (e) { chip('Error', 'err'); say('Could not load the cards: ' + e.message, 'err'); }
  }
  boot();
})();
