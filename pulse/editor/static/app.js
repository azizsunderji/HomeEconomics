/* Housing at Noon editor. Plain JS, no build step.
   Content model: the draft JSON from /api/draft. Summaries are markdown with
   [text](url) links; the contenteditable blocks convert to and from that.
   A link the owner wants to stay live in the free edition carries the
   markdown title "free" — [text](url "free") — and data-free="1" on its
   anchor here (see the "Keep in free edition" box).
   An image is a markdown image on a line of its own, ![caption](url); in the
   editor it is a <figure class="img"> (thumbnail, caption field, remove button)
   that is not editable text, so it never counts as a link.
   A Context box is a paragraph group between a line "::: context" and a line ":::";
   in the editor it is a shaded <div class="ctx"> with a CONTEXT label (14 Oct 2026).
   A pull quote is "::: quote", the quote text, an optional "-- Attribution" line and ":::";
   in the editor it is a <div class="pq"> with a quote field and an attribution field (20 Oct 2026). */
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
  // [text](url) with an optional markdown title: [text](url "free") marks a
  // link that stays live in the free edition. Only the title "free" is kept
  // (as data-free="1"); any other title is dropped.
  const FREE_TITLE = 'free';
  function inlineToHtml(text) {
    const re = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)(?:\s+(?:"([^"]*)"|'([^']*)'))?\s*\)/g;
    let out = '', i = 0, m;
    while ((m = re.exec(text))) {
      out += esc(text.slice(i, m.index));
      const title = (m[3] !== undefined ? m[3] : m[4] || '').trim().toLowerCase();
      out += '<a href="' + escAttr(m[2]) + '"' + (title === FREE_TITLE ? ' data-free="1"' : '') + '>' + esc(m[1]) + '</a>';
      i = m.index + m[0].length;
    }
    return out + esc(text.slice(i));
  }
  // ![caption](url) on a line of its own: an image block. The caption is the alt text.
  const IMG_RE = /^!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)$/;
  function figureHtml(caption, url) {
    return '<figure class="img" contenteditable="false" data-url="' + escAttr(url) + '">' +
      '<img src="' + escAttr(url) + '" alt="">' +
      '<div class="caprow"><input class="cap" type="text" placeholder="Caption (optional)" value="' + escAttr(caption) + '">' +
      '<button type="button" class="ctxmv" data-act="imgup" title="Move the image up one paragraph (Alt+↑ in the caption)">↑</button>' +
      '<button type="button" class="ctxmv" data-act="imgdown" title="Move the image down one paragraph (Alt+↓ in the caption)">↓</button>' +
      '<button type="button" class="btn icon rm" data-act="rmimg" title="Remove image">✕</button></div></figure>';
  }
  // Context box (owner, 14 Oct 2026): background for readers, set apart as a shaded block.
  // Markdown: a paragraph group between a line "::: context" and a line ":::".
  // In the editor: <div class="ctx"> holding a non-editable label row (CONTEXT and a
  // remove button) and editable paragraphs. An empty box is dropped by htmlToMd.
  const CTX_RE = /^[ \t]*:::[ \t]*context[ \t]*$([\s\S]*?)^[ \t]*:::[ \t]*$/gim;
  const CTX_HEAD = '<div class="ctxhead" contenteditable="false"><span class="ctxlabel">Context</span>' +
    '<span class="ctxbtns">' +
    '<button type="button" class="ctxmv" data-act="ctxup" title="Move the box up one paragraph (Alt+↑)">↑</button>' +
    '<button type="button" class="ctxmv" data-act="ctxdown" title="Move the box down one paragraph (Alt+↓)">↓</button>' +
    '<button type="button" class="ctxrm" data-act="rmctx" title="Remove context box">✕</button></span></div>';
  function parasHtml(md) {
    const paras = String(md || '').split(/\n{2,}/).map(p => p.trim()).filter(Boolean);
    return paras.map(p => { const m = IMG_RE.exec(p); return m ? figureHtml(m[1].trim(), m[2]) : '<p>' + inlineToHtml(p).replace(/\n/g, '<br>') + '</p>'; });
  }
  function ctxHtml(innerMd) {
    const inner = parasHtml(innerMd);
    return '<div class="ctx">' + CTX_HEAD + (inner.length ? inner.join('') : '<p><br></p>') + '</div>';
  }
  // Pull quote (owner, 20 Oct 2026, style B): a named person's verbatim words.
  // Markdown: "::: quote", the quote text, an optional last line "-- Attribution", ":::".
  // In the editor: <div class="pq"> (not editable text, like an image figure) holding a
  // label row (QUOTE, up/down, remove), a textarea for the quote and an input for the
  // attribution. An empty quote is dropped by htmlToMd.
  const FENCE_RE = /^[ \t]*:::[ \t]*(context|quote)[ \t]*$([\s\S]*?)^[ \t]*:::[ \t]*$/gim;
  const PQ_HEAD = '<div class="ctxhead pqhead"><span class="ctxlabel">Quote</span>' +
    '<span class="ctxbtns">' +
    '<button type="button" class="ctxmv" data-act="pqup" title="Move the quote up one paragraph (Alt+↑)">↑</button>' +
    '<button type="button" class="ctxmv" data-act="pqdown" title="Move the quote down one paragraph (Alt+↓)">↓</button>' +
    '<button type="button" class="ctxrm" data-act="rmpq" title="Remove quote">✕</button></span></div>';
  function quoteParts(inner) {
    const lines = String(inner || '').split('\n').map(l => l.trim()).filter(Boolean);
    let who = '';
    const m = lines.length ? /^(?:--|—)\s*(.+)$/.exec(lines[lines.length - 1]) : null;
    if (m) { who = m[1].trim(); lines.pop(); }
    return { text: lines.join(' '), who };
  }
  function pqHtml(text, who) {
    return '<div class="pq" contenteditable="false">' + PQ_HEAD +
      '<div class="pqrow"><span class="pqmark" aria-hidden="true">“</span><div class="pqfields">' +
      '<textarea class="pqtext" rows="1" placeholder="The quote, word for word (no quotation marks)">' + esc(text || '') + '</textarea>' +
      '<div class="pqwhorow"><span class="pqdash">—</span><input class="pqwho" type="text" placeholder="Name, organisation (optional)" value="' + escAttr(who || '') + '"></div>' +
      '</div></div></div>';
  }
  function mdToHtml(md) {
    md = String(md || '').replace(/\r/g, '');
    const out = []; let i = 0, m;
    FENCE_RE.lastIndex = 0;
    while ((m = FENCE_RE.exec(md))) {
      const before = parasHtml(md.slice(i, m.index));
      out.push(...before);
      if (m[1].toLowerCase() === 'quote') { const q = quoteParts(m[2]); out.push(pqHtml(q.text, q.who)); }
      else out.push(ctxHtml(m[2]));
      i = m.index + m[0].length;
    }
    const rest = parasHtml(md.slice(i));
    out.push(...rest);
    if (!out.length) return '<p><br></p>';
    // an image, a context box or a quote at either end gets an empty paragraph beside it so
    // the caret has somewhere to go; empty paragraphs are dropped again by htmlToMd
    const blockish = s => s.startsWith('<figure') || s.startsWith('<div class="ctx">') || s.startsWith('<div class="pq"');
    if (blockish(out[0])) out.unshift('<p><br></p>');
    if (blockish(out[out.length - 1])) out.push('<p><br></p>');
    return out.join('');
  }
  // Clipboard HTML -> text plus <a href> only (data-free kept). Returns '' when there is
  // no http(s) link, so the caller falls back to plain text. Several blocks become <p>s.
  function pastedLinksHtml(html) {
    if (!html || !/<a\s[^>]*href/i.test(html)) return '';
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const BLOCK = /^(P|DIV|LI|UL|OL|H[1-6]|BLOCKQUOTE|TR|TABLE|SECTION|ARTICLE|FIGURE)$/;
    const blocks = []; let cur = '', links = 0;
    const flush = () => { if (cur.replace(/<br>/g, '').trim()) blocks.push(cur.replace(/^(<br>)+|(<br>)+$/g, '')); cur = ''; };
    (function walk(node) {
      for (const n of node.childNodes) {
        if (n.nodeType === 3) { cur += esc(n.nodeValue.replace(/\s+/g, ' ')); continue; }
        if (n.nodeType !== 1 || /^(SCRIPT|STYLE|META|TITLE|BUTTON|INPUT|TEXTAREA)$/.test(n.tagName) || n.classList.contains('ctxhead')) continue;
        if (n.tagName === 'BR') { cur += '<br>'; continue; }
        if (n.tagName === 'A' && /^https?:\/\//i.test(n.getAttribute('href') || '') && n.textContent.trim()) {
          links++;
          cur += '<a href="' + escAttr(n.getAttribute('href')) + '"' + (n.hasAttribute('data-free') ? ' data-free="1"' : '') + '>' +
            esc(n.textContent.replace(/\s+/g, ' ')) + '</a>';
          continue;
        }
        const block = BLOCK.test(n.tagName);
        if (block) flush();
        walk(n);
        if (block) flush();
      }
    })(doc.body);
    flush();
    if (!links) return '';
    return blocks.length === 1 ? blocks[0] : blocks.map(b => '<p>' + b + '</p>').join('');
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
        if (n.classList.contains('ctxhead')) continue;
        if (tag === 'DIV' && n.classList.contains('ctx')) {
          // a context box: its own paragraphs between the fence lines; an empty box is dropped
          flush();
          const inner = htmlToMd(n);
          if (inner.trim()) paras.push('::: context\n' + inner + '\n:::');
          continue;
        }
        if (tag === 'DIV' && n.classList.contains('pq')) {
          // a pull quote: the text on one line, then "-- attribution" when there is one
          flush();
          const tEl = n.querySelector('textarea.pqtext'), wEl = n.querySelector('input.pqwho');
          const text = (tEl ? tEl.value : '').replace(/\s+/g, ' ').trim();
          const who = (wEl ? wEl.value : '').replace(/\s+/g, ' ').replace(/^(?:--|—)\s*/, '').trim();
          if (text) paras.push('::: quote\n' + text + (who ? '\n-- ' + who : '') + '\n:::');
          continue;
        }
        if (tag === 'FIGURE' && n.classList.contains('img')) {
          flush();
          const capEl = n.querySelector('input.cap');
          const cap = (capEl ? capEl.value : '').replace(/[\[\]]/g, '').replace(/\s+/g, ' ').trim();
          const url = n.getAttribute('data-url') || '';
          if (url) paras.push('![' + cap + '](' + url + ')');
          continue;
        }
        if (tag === 'A') {
          const href = n.getAttribute('href') || '', t = n.textContent;
          const title = n.hasAttribute('data-free') ? ' "' + FREE_TITLE + '"' : '';
          cur += href && t.trim() ? '[' + t + '](' + href + title + ')' : t; continue;
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
    // after the send: link to the cards page (four PNGs for X, carousel PDF for LinkedIn; 28 Sep 2026)
    $('#sentLine').hidden = !locked;
    if (state.date) $('#lnkCards').href = '/cards/' + state.date;
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
    if (!ed) { showOpenPill(null); return; }
    savedRange = r.cloneRange(); activeEditor = ed;
    $$('.rich a.active').forEach(a => a.classList.remove('active'));
    const a = anchorAt(r); if (a) a.classList.add('active');
    showOpenPill(a);
  });
  // ── open the underlying source ────────────────────────────────────
  // A tap on a linked word places the caret so the word can be edited; the
  // pill offers the link itself. On a desktop, Cmd/Ctrl-click opens it too.
  const openPill = document.createElement('a');
  openPill.className = 'openpill'; openPill.target = '_blank'; openPill.rel = 'noopener'; openPill.hidden = true;
  document.body.appendChild(openPill);
  function showOpenPill(a) {
    const href = a && a.getAttribute('href');
    if (!href) { openPill.hidden = true; return; }
    let host = href; try { host = new URL(href).hostname.replace(/^www\./, ''); } catch (e) { /* keep raw */ }
    openPill.href = href;
    openPill.textContent = 'Open “' + a.textContent.trim() + '” → ' + host + ' ↗';
    const bar = document.querySelector('.actions');
    openPill.style.bottom = ((bar ? bar.offsetHeight : 60) + 10) + 'px';
    openPill.hidden = false;
  }
  document.addEventListener('click', ev => {
    const a = ev.target.closest('.rich a');
    if (a && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); window.open(a.getAttribute('href'), '_blank', 'noopener'); }
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
  function freeCheckFor(ed) { return $('input[data-free-check]', linkboxFor(ed)); }
  function setFree(a, on) { if (on) a.setAttribute('data-free', '1'); else a.removeAttribute('data-free'); }
  function keepSelection(btn) { btn.addEventListener('mousedown', ev => ev.preventDefault()); }

  function startLink(ed) {
    const box = linkboxFor(ed), input = $('.url', box), selInfo = $('.sel', box), free = freeCheckFor(ed);
    const r = savedRange && activeEditor === ed ? savedRange : null;
    const a = r ? anchorAt(r) : null;
    if (a) {
      pending = { ed, anchor: a }; selInfo.textContent = 'Editing link on “' + a.textContent + '”'; input.value = a.getAttribute('href') || '';
      free.checked = a.hasAttribute('data-free');
    } else if (r && !r.collapsed && r.toString().trim()) {
      pending = { ed, range: r.cloneRange() }; selInfo.textContent = 'Link “' + r.toString().trim() + '” to:'; input.value = '';
      free.checked = false;
    } else { say('Select a word or phrase first, then press Link.', ''); return; }
    box.hidden = false; fitTitle(input); input.focus();
  }
  function applyLink(ed) {
    const box = linkboxFor(ed), input = $('.url', box), keepFree = freeCheckFor(ed).checked;
    let url = input.value.trim();
    if (url && !/^https?:\/\//i.test(url)) url = 'https://' + url;
    if (!pending || pending.ed !== ed) { box.hidden = true; return; }
    if (!url) { say('Enter a URL, or Cancel.', ''); return; }
    if (pending.anchor) { pending.anchor.setAttribute('href', url); setFree(pending.anchor, keepFree); }
    else {
      const before = new Set($$('a', ed));
      restoreRange(pending.range, ed);
      document.execCommand('createLink', false, url);
      // execCommand may add target/rel or leave the new anchor unselected. Find
      // what it created (any anchor not there before) and set the free flag on it.
      let made = $$('a', ed).filter(a => !before.has(a));
      if (!made.length) { const sel = document.getSelection(); const a = sel && sel.rangeCount ? anchorAt(sel.getRangeAt(0)) : null; if (a) made = [a]; }
      made.forEach(a => setFree(a, keepFree));
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
    // "Keep in free edition" box: added here so every link box (entries,
    // paper, standfirst) gets it without touching the page markup.
    const box = $('.linkbox', section);
    if (box && !$('input[data-free-check]', box)) {
      const lab = document.createElement('label'); lab.className = 'freeopt';
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.setAttribute('data-free-check', '');
      lab.appendChild(cb); lab.appendChild(document.createTextNode(' Keep in free edition'));
      box.insertBefore(lab, $('[data-act="apply"]', box));
    }
    $$('.tools .btn, .linkbox .btn', section).forEach(keepSelection);
    section.addEventListener('click', ev => {
      const b = ev.target.closest('[data-act]'); if (!b) return;
      const act = b.dataset.act;
      if (act === 'link') startLink(ed);
      else if (act === 'unlink') removeLink(ed);
      else if (act === 'image') startImage(ed);
      else if (act === 'rmimg') { const f = b.closest('figure.img'); if (f) { f.remove(); onRichInput(ed); } }
      else if (act === 'context') insertContext(ed);
      else if (act === 'quote') insertQuote(ed);
      else if (act === 'rmpq') { const q = b.closest('.pq'); if (q) removeContext(ed, q); }
      else if (act === 'pqup' || act === 'pqdown') { const q = b.closest('.pq'); if (q) moveContext(ed, q, act === 'pqup' ? -1 : 1); }
      else if (act === 'rmctx') { const c = b.closest('.ctx'); if (c) removeContext(ed, c); }
      else if (act === 'ctxup' || act === 'ctxdown') { const c = b.closest('.ctx'); if (c) moveContext(ed, c, act === 'ctxup' ? -1 : 1); }
      else if (act === 'imgup' || act === 'imgdown') { const f = b.closest('figure.img'); if (f) moveContext(ed, f, act === 'imgup' ? -1 : 1); }  // owner, 16 Oct 2026: images move like boxes
      else if (act === 'apply') applyLink(ed);
      else if (act === 'cancel') { pending = null; linkboxFor(ed).hidden = true; }
    });
    const input = $('.linkbox .url', section);
    input.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); applyLink(ed); } });
    input.addEventListener('input', () => { if (/[\r\n]/.test(input.value)) input.value = input.value.replace(/[\r\n]+/g, ' '); fitTitle(input); });
    ed.addEventListener('paste', ev => {
      // a quote's fields are plain form fields: the browser's own paste (plain text) is right there
      if (ev.target && ev.target.closest && ev.target.closest('.pq')) return;
      ev.preventDefault(); const cd = ev.clipboardData || window.clipboardData;
      // Cut and paste inside the brief must not lose links: when the clipboard HTML has
      // http(s) links, insert its text with those links and nothing else.
      const linked = pastedLinksHtml(cd.getData('text/html'));
      if (linked) document.execCommand('insertHTML', false, linked);
      else document.execCommand('insertText', false, cd.getData('text/plain'));
    });
    ed.addEventListener('input', ev => {
      if (ev.target && ev.target.matches && ev.target.matches('textarea.pqtext')) {
        // the quote stays one paragraph; the field grows with its text
        if (/[\r\n]/.test(ev.target.value)) ev.target.value = ev.target.value.replace(/[\r\n]+/g, ' ');
        fitTitle(ev.target);
      }
      onRichInput(ed);
    });
    $$('textarea.pqtext', ed).forEach(fitTitle);
    ed.addEventListener('keydown', ev => contextKeydown(ed, ev));
    // the move arrows must not take the caret away from where it is
    ed.addEventListener('mousedown', ev => { if (ev.target.closest('.ctxmv')) ev.preventDefault(); });
  }
  // ── context boxes ─────────────────────────────────────────────────
  // Context button: with whole paragraphs selected, wraps them in a box; otherwise
  // inserts an empty box at the caret (above the paragraph when the caret is at its
  // start, below it at its end, splitting it in the middle). Boxes never nest.
  function caretPlace(node, atEnd) {
    const sel = document.getSelection(); const r = document.createRange();
    r.selectNodeContents(node); r.collapse(!atEnd);
    sel.removeAllRanges(); sel.addRange(r); savedRange = r.cloneRange();
  }
  function newContext() {
    const tmp = document.createElement('div'); tmp.innerHTML = ctxHtml(''); return tmp.firstChild;
  }
  function insertContext(ed) {
    const r = savedRange && activeEditor === ed && ed.contains(savedRange.startContainer) ? savedRange.cloneRange() : null;
    const startEl = r ? (r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement) : null;
    if (startEl && startEl.closest('.ctx')) { say('The cursor is already inside a Context box.', ''); return; }
    let box;
    const a = blockOf(r ? r.startContainer : ed, ed), z = r ? blockOf(r.endContainer, ed) : null;
    if (r && !r.collapsed && a && z && a.tagName === 'P' && z.tagName === 'P' && r.toString().trim()) {
      // wrap every paragraph the selection touches (a..z)
      const blocks = []; for (let n = a; n; n = n.nextSibling) { blocks.push(n); if (n === z) break; }
      if (blocks.some(n => n.nodeType === 1 && n.classList.contains('ctx'))) { say('That selection already includes a Context box.', ''); return; }
      box = newContext(); box.lastChild.remove();  // drop the empty paragraph
      a.before(box); blocks.forEach(n => box.appendChild(n));
    } else {
      box = newContext();
      const blk = r ? blockOf(r.startContainer, ed) : null;
      if (!blk) ed.appendChild(box);
      else if (blk.tagName !== 'P') blk.after(box);
      else {
        const anc = anchorAt(r); if (anc) { r.setStartAfter(anc); r.collapse(true); }  // never split a link
        const before = document.createRange(); before.selectNodeContents(blk); before.setEnd(r.startContainer, r.startOffset);
        const rest = document.createRange(); rest.selectNodeContents(blk); rest.setStart(r.startContainer, r.startOffset);
        if (!before.toString().trim() && blk.textContent.trim()) blk.before(box);
        else if (!rest.toString().trim()) blk.after(box);
        else { const p = document.createElement('p'); p.appendChild(rest.extractContents()); blk.after(box); box.after(p); }
      }
    }
    // keep a paragraph on each side so the caret can always leave the box
    if (!box.previousSibling) { const p = document.createElement('p'); p.innerHTML = '<br>'; box.before(p); }
    if (!box.nextSibling) { const p = document.createElement('p'); p.innerHTML = '<br>'; box.after(p); }
    activeEditor = ed; ed.focus();
    caretPlace(box.lastChild, true);
    onRichInput(ed);
  }
  // Quote button (owner, 20 Oct 2026): with whole paragraphs selected, turns them into a
  // pull quote (their text becomes the quote; quotation marks around it are dropped);
  // otherwise inserts an empty quote at the caret, placed as a Context box would be.
  // Never inside a Context box (the email would not render it there).
  function newQuote(text) {
    const tmp = document.createElement('div'); tmp.innerHTML = pqHtml(text || '', ''); return tmp.firstChild;
  }
  function insertQuote(ed) {
    const r = savedRange && activeEditor === ed && ed.contains(savedRange.startContainer) ? savedRange.cloneRange() : null;
    const startEl = r ? (r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement) : null;
    if (startEl && startEl.closest('.ctx')) { say('A quote cannot go inside a Context box. Put the cursor in a paragraph outside it.', ''); return; }
    let q;
    const a = blockOf(r ? r.startContainer : ed, ed), z = r ? blockOf(r.endContainer, ed) : null;
    if (r && !r.collapsed && a && z && a.tagName === 'P' && z.tagName === 'P' && r.toString().trim()) {
      const blocks = []; for (let n = a; n; n = n.nextSibling) { blocks.push(n); if (n === z) break; }
      if (blocks.some(n => n.nodeType === 1 && !(n.tagName === 'P'))) { say('Select whole paragraphs of text only (no image, box or quote) to turn them into a quote.', ''); return; }
      const holder = document.createElement('div'); blocks.forEach(n => holder.appendChild(n.cloneNode(true)));
      let text = htmlToMd(holder).replace(/\s+/g, ' ').trim();
      if (text.length > 1 && /^["“]/.test(text) && /["”]$/.test(text)) text = text.slice(1, -1).trim();
      q = newQuote(text);
      a.before(q); blocks.forEach(n => n.remove());
    } else {
      q = newQuote('');
      const blk = r ? blockOf(r.startContainer, ed) : null;
      if (!blk) ed.appendChild(q);
      else if (blk.tagName !== 'P') blk.after(q);
      else {
        const anc = anchorAt(r); if (anc) { r.setStartAfter(anc); r.collapse(true); }  // never split a link
        const before = document.createRange(); before.selectNodeContents(blk); before.setEnd(r.startContainer, r.startOffset);
        const rest = document.createRange(); rest.selectNodeContents(blk); rest.setStart(r.startContainer, r.startOffset);
        if (!before.toString().trim() && blk.textContent.trim()) blk.before(q);
        else if (!rest.toString().trim()) blk.after(q);
        else { const p = document.createElement('p'); p.appendChild(rest.extractContents()); blk.after(q); q.after(p); }
      }
    }
    // keep a paragraph on each side so the caret can always leave the quote
    if (!q.previousSibling) q.before(emptyP());
    if (!q.nextSibling) q.after(emptyP());
    activeEditor = ed;
    const ta = $('textarea.pqtext', q); fitTitle(ta); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
    onRichInput(ed);
  }
  function removeContext(ed, box) {
    const next = box.nextSibling;
    box.remove();
    if (next && next.nodeType === 1) { ed.focus(); caretPlace(next, false); }
    onRichInput(ed);
  }
  // Move a box (or, since 16 Oct 2026, an image figure) one block up (dir -1) or down (+1) among the editor's top-level blocks
  // (paragraphs, images, other boxes; owner, 14 Oct 2026). Empty spacer paragraphs are
  // stepped over because they do not exist in the markdown. At either end it does
  // nothing. The caret stays where it was, and the page does not scroll.
  function isSpacer(n) {
    return n.nodeType === 3 ? !n.nodeValue.trim() :
      n.nodeType === 1 && n.tagName === 'P' && !n.textContent.trim() && !n.querySelector('img,figure');
  }
  function emptyP() { const p = document.createElement('p'); p.innerHTML = '<br>'; return p; }
  function moveContext(ed, box, dir) {
    let t = dir < 0 ? box.previousSibling : box.nextSibling;
    while (t && isSpacer(t)) t = dir < 0 ? t.previousSibling : t.nextSibling;
    if (!t) return false;
    const sx = window.scrollX, sy = window.scrollY;
    const sel = document.getSelection();
    const r = sel && sel.rangeCount && ed.contains(sel.getRangeAt(0).commonAncestorContainer) ? sel.getRangeAt(0) : null;
    const keep = r ? [r.startContainer, r.startOffset, r.endContainer, r.endOffset] : null;
    const oldPrev = box.previousSibling, oldNext = box.nextSibling;
    if (dir < 0) t.before(box); else t.after(box);
    // the gap the box left: two spacer paragraphs side by side become one
    if (oldPrev && oldNext && oldPrev.parentNode === box.parentNode && oldPrev.nextSibling === oldNext && isSpacer(oldPrev) && isSpacer(oldNext)) oldNext.remove();
    // keep a paragraph on each side so the caret can always leave the box
    if (!box.previousSibling) box.before(emptyP());
    if (!box.nextSibling) box.after(emptyP());
    if (keep && ed.contains(keep[0]) && ed.contains(keep[2])) {
      try {
        const nr = document.createRange(); nr.setStart(keep[0], keep[1]); nr.setEnd(keep[2], keep[3]);
        sel.removeAllRanges(); sel.addRange(nr); savedRange = nr.cloneRange(); activeEditor = ed;
      } catch (e) { /* the range no longer fits; leave the selection to the browser */ }
    }
    window.scrollTo(sx, sy);
    onRichInput(ed);
    return true;
  }
  // Backspace in an empty box removes the box; Backspace at the very start of a box's
  // first paragraph does nothing (it would otherwise pull the text out of the box).
  // Alt+↑ / Alt+↓ with the caret inside a box move the box.
  function contextKeydown(ed, ev) {
    const isMove = ev.altKey && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey && (ev.key === 'ArrowUp' || ev.key === 'ArrowDown');
    // In a quote's fields (owner, 20 Oct 2026): Alt+↑/↓ moves the quote, Enter in the quote
    // moves to the attribution (and leaves the attribution), Backspace with both fields
    // empty removes the quote. Everything else is ordinary typing in a form field.
    const pq = ev.target && ev.target.closest ? ev.target.closest('.pq') : null;
    if (pq && ed.contains(pq)) {
      if (isMove) { ev.preventDefault(); moveContext(ed, pq, ev.key === 'ArrowUp' ? -1 : 1); return; }
      if (ev.key === 'Enter') {
        ev.preventDefault();
        if (ev.target.matches('textarea.pqtext')) $('input.pqwho', pq).focus();
        else { const nx = pq.nextSibling; if (nx && nx.nodeType === 1) { ed.focus(); caretPlace(nx, false); } }
        return;
      }
      if (ev.key === 'Backspace' || ev.key === 'Delete') {
        const t = $('textarea.pqtext', pq).value.trim(), w = $('input.pqwho', pq).value.trim();
        if (!t && !w) { ev.preventDefault(); removeContext(ed, pq); return; }
        if (ev.key === 'Backspace' && ev.target.matches('input.pqwho') && !ev.target.value) {
          ev.preventDefault(); const ta = $('textarea.pqtext', pq); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
        }
      }
      return;
    }
    if (!isMove && ev.key !== 'Backspace' && ev.key !== 'Delete') return;
    // Alt+arrows typed in an image's caption field move the image (owner, 16 Oct 2026)
    const fig = ev.target && ev.target.closest ? ev.target.closest('figure.img') : null;
    if (fig && ed.contains(fig)) { if (isMove) { ev.preventDefault(); moveContext(ed, fig, ev.key === 'ArrowUp' ? -1 : 1); } return; }
    const sel = document.getSelection(); if (!sel || !sel.rangeCount) return;
    const r = sel.getRangeAt(0);
    const el = r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement;
    const box = el && el.closest('.ctx'); if (!box || !ed.contains(box)) return;
    if (isMove) { ev.preventDefault(); moveContext(ed, box, ev.key === 'ArrowUp' ? -1 : 1); return; }
    const body = Array.from(box.childNodes).filter(n => !(n.nodeType === 1 && n.classList.contains('ctxhead')));
    const text = body.map(n => n.textContent).join('').replace(/ /g, ' ').trim();
    const hasFig = body.some(n => n.nodeType === 1 && n.querySelector && (n.matches('figure') || n.querySelector('figure')));
    if (!text && !hasFig) { ev.preventDefault(); removeContext(ed, box); return; }
    if (ev.key === 'Backspace' && r.collapsed && body.length) {
      const first = body[0];
      const pre = document.createRange(); pre.selectNodeContents(first); pre.setEnd(r.startContainer, r.startOffset);
      if (first.contains(r.startContainer) && !pre.toString().length) ev.preventDefault();
    }
  }
  // ── images ────────────────────────────────────────────────────────
  // Image button -> the hidden file input (camera roll / files on a phone) ->
  // POST /api/upload -> a <figure> at the caret, splitting the paragraph when
  // the caret is inside one. The caret is remembered before the picker opens
  // because the selection is lost while it is up.
  const fileInput = $('#imgFile');
  let imgTarget = null;
  function startImage(ed) {
    imgTarget = { ed, range: savedRange && activeEditor === ed ? savedRange.cloneRange() : null };
    fileInput.value = ''; fileInput.click();
  }
  function blockOf(node, ed) {
    let el = node.nodeType === 1 ? node : node.parentElement;
    while (el && el.parentElement !== ed) el = el.parentElement;
    return el;
  }
  function insertImage(ed, url, caption, range) {
    const tmp = document.createElement('div'); tmp.innerHTML = figureHtml(caption || '', url);
    const figure = tmp.firstChild;
    const r = range && ed.contains(range.startContainer) ? range.cloneRange() : null;
    const a = r ? anchorAt(r) : null;
    if (a) { r.setStartAfter(a); r.collapse(true); }  // never split a link in two
    const blk = r ? blockOf(r.startContainer, ed) : null;
    if (!blk) ed.appendChild(figure);
    else if (blk.tagName !== 'P') blk.after(figure);
    else {
      const before = document.createRange(); before.selectNodeContents(blk); before.setEnd(r.startContainer, r.startOffset);
      const rest = document.createRange(); rest.selectNodeContents(blk); rest.setStart(r.startContainer, r.startOffset);
      if (!before.toString().trim()) blk.before(figure);          // caret at the start: image above this paragraph
      else if (!rest.toString().trim()) blk.after(figure);        // caret at the end: image below it
      else { const p = document.createElement('p'); p.appendChild(rest.extractContents()); blk.after(figure); figure.after(p); }
    }
    if (!figure.nextSibling) { const p = document.createElement('p'); p.innerHTML = '<br>'; figure.after(p); }
    const after = figure.nextSibling;
    const sel = document.getSelection(); const nr = document.createRange(); nr.setStart(after, 0); nr.collapse(true);
    sel.removeAllRanges(); sel.addRange(nr); savedRange = nr.cloneRange(); activeEditor = ed;
    onRichInput(ed);
    const cap = $('input.cap', figure); if (cap) cap.focus();
    return figure;
  }
  async function uploadImage(file) {
    const fd = new FormData(); fd.append('file', file, file.name || 'image');
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    if (r.status === 401) { location.href = '/login'; throw new Error('signed out'); }
    let data = null; try { data = await r.json(); } catch (_) { /* no body */ }
    if (!r.ok) throw new Error((data && data.detail) || ('HTTP ' + r.status));
    return data;
  }
  fileInput.addEventListener('change', async () => {
    const f = fileInput.files && fileInput.files[0]; const t = imgTarget; imgTarget = null;
    if (!f || !t) return;
    // Any image type: the server converts HEIC/WebP/GIF/TIFF to PNG and refuses non-images.
    if (f.size > 8 * 1024 * 1024) { say('That image is over 8 MB.', 'err'); fileInput.value = ''; return; }
    say('Uploading ' + (f.name || 'image') + '…');
    try {
      const data = await uploadImage(f);
      insertImage(t.ed, data.url, '', t.range);
      say('Image added. Type a caption under it, or leave it blank.', 'ok');
    } catch (e) { say('Upload failed: ' + e.message, 'err'); }
    fileInput.value = '';
  });
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
      '<button class="btn" data-act="image">Image</button>' +
      '<button class="btn" data-act="context" title="Add a shaded Context box at the cursor (or around the selected paragraphs)">Context</button>' +
      '<button class="btn" data-act="quote" title="Add a pull quote at the cursor (or turn the selected paragraph into one)">Quote</button>' +
      '<span class="spacer"></span>' +
      '<div class="seg"><button data-act="free">Free</button><button data-act="premium">Premium</button></div>' +
      '<span class="move">' +
      '<button class="btn icon" data-act="up" title="Move up">↑</button>' +
      '<button class="btn icon" data-act="down" title="Move down">↓</button>' +
      '<button class="btn icon danger" data-act="delete" title="Delete">✕</button>' +
      '</span></div>' +
      '<div class="linkbox" hidden><div class="sel"></div>' +
      '<textarea class="url" rows="1" placeholder="https://…" inputmode="url" autocapitalize="off" autocomplete="off"></textarea>' +
      '<button class="btn primary" data-act="apply">Apply</button><button class="btn" data-act="cancel">Cancel</button></div>';
  }
  // Titles, link-box URLs and paper fields are one-row textareas so long text wraps on a phone.
  // Height follows the text; the stored value stays single-line (Enter is handled, newlines stripped).
  function fitTitle(t) { t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; }
  window.addEventListener('resize', () => $$('.entry .title, .linkbox .url, .field textarea, .rich .pqtext').forEach(fitTitle));
  // "Move to" dropdown in each theme's header (owner, 2 Oct 2026): lists every slot as
  // "N · current title" with the theme's own slot selected; picking a slot removes the theme
  // and inserts it there, so the others shift. Rebuilt on every render, so titles stay current.
  function moveLabel(j, title) {
    let t = String(title || '(untitled)').replace(/\s+/g, ' ').trim();
    if (t.length > 60) t = t.slice(0, 59).trimEnd() + '…';
    return (j + 1) + ' · ' + t;
  }
  function moveToHtml(i) {
    return '<select class="moveto" title="Move to…" aria-label="Move to slot">' +
      (state.json.entries || []).map((e, j) =>
        '<option value="' + j + '"' + (j === i ? ' selected' : '') + '>' + esc(moveLabel(j, e.title)) + '</option>').join('') +
      '</select>';
  }
  function moveEntry(from, to) {
    const es = state.json.entries;
    if (from === to || to < 0 || to >= es.length) return;
    const [it] = es.splice(from, 1); es.splice(to, 0, it);
    // the screen stays where it is (owner, 2 Oct 2026): no jump to the moved theme
    const y = window.scrollY;
    touch(); renderEntries();
    window.scrollTo(0, y);
  }
  function renderEntries() {
    const host = $('#entries'); host.innerHTML = '';
    (state.json.entries || []).forEach((e, i) => {
      const sec = document.createElement('section');
      sec.className = 'entry' + (e.tier === 'premium' ? ' premium' : ''); sec.dataset.idx = i;
      const pills = (e.news_outlets || []).slice(0, 6).join(' · ');
      sec.innerHTML =
        '<div class="head"><span class="num">' + (i + 1) + '</span>' +
        '<textarea class="title" rows="1" placeholder="Title">' + esc(e.title || '') + '</textarea>' +
        moveToHtml(i) + '</div>' +
        '<div class="meta">' + esc(e.origin === 'cluster' ? 'From social' : 'From news') + (pills ? ' · ' + esc(pills) : '') + '</div>' +
        '<div class="rich" contenteditable="true" data-kind="summary" data-idx="' + i + '">' + mdToHtml(e.summary) + '</div>' +
        toolsHtml();
      host.appendChild(sec);
      const ed = $('.rich', sec);
      wireTools(sec, ed);
      const title = $('.title', sec); fitTitle(title);
      title.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); title.blur(); } });
      title.addEventListener('input', () => {
        if (/[\r\n]/.test(title.value)) title.value = title.value.replace(/[\r\n]+/g, ' ');
        fitTitle(title); state.json.entries[i].title = title.value; touch();
        $$('#entries .moveto').forEach(s => { if (s.options[i]) s.options[i].textContent = moveLabel(i, title.value); });
      });
      $('.moveto', sec).addEventListener('change', ev => moveEntry(i, +ev.target.value));
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
    const f = (k, label) => '<div class="field"><label>' + label + '</label><textarea data-k="' + k + '" rows="1"' + (k === 'url' ? ' inputmode="url" autocapitalize="off" autocomplete="off"' : '') + '>' + esc(p[k] || '') + '</textarea></div>';
    host.innerHTML = f('title', 'Title') + f('authors', 'Authors') + f('publication', 'Publication') + f('url', 'URL') +
      '<div class="sub">Summary</div><div class="rich" contenteditable="true" data-kind="paper">' + mdToHtml(p.summary) + '</div>' +
      '<div class="tools"><button class="btn" data-act="link">Link</button><button class="btn" data-act="unlink">Unlink</button>' +
      '<button class="btn" data-act="image">Image</button>' +
      '<button class="btn" data-act="context" title="Add a shaded Context box at the cursor (or around the selected paragraphs)">Context</button>' +
      '<button class="btn" data-act="quote" title="Add a pull quote at the cursor (or turn the selected paragraph into one)">Quote</button>' +
      '<span class="spacer"></span><button class="btn danger" id="paperRemove">Remove paper</button></div>' +
      '<div class="linkbox" hidden><div class="sel"></div><textarea class="url" rows="1" placeholder="https://…" inputmode="url" autocapitalize="off" autocomplete="off"></textarea>' +
      '<button class="btn primary" data-act="apply">Apply</button><button class="btn" data-act="cancel">Cancel</button></div>';
    $$('textarea[data-k]', host).forEach(t => {
      fitTitle(t);
      t.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); t.blur(); } });
      t.addEventListener('input', () => {
        if (/[\r\n]/.test(t.value)) t.value = t.value.replace(/[\r\n]+/g, ' ');
        fitTitle(t); p[t.dataset.k] = t.value; touch();
      });
    });
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
    $('#btnCards').addEventListener('click', async () => {
      if (!(await ensureSaved())) { say('Could not save before the cards.', 'err'); return; }
      say('Rendering cards… this takes about half a minute.');
      window.open('/cards/' + state.date, '_blank');
    });
    $('#btnTestFree').addEventListener('click', () => sendTest('free'));
    $('#btnTestPremium').addEventListener('click', () => sendTest('premium'));
    $('#btnHold').addEventListener('click', async () => {
      const next = state.status === 'held' ? 'draft' : 'held';
      try { applyMeta(await api('POST', '/api/draft/' + state.date + '/status', { status: next })); say(next === 'held' ? 'Held. Today’s edition will not send until you resume it.' : 'Resumed. It sends at noon ET.', 'ok'); }
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
    document.title = 'Housing at Noon — ' + data.date_label;
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
