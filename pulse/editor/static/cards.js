// "Post to X" panel on /cards/{date} (27 Sep 2026). Owner's rule: nothing posts without
// the "Approve and post" click and its confirm(). State lives in xpost.py (state.json).
(function () {
  const box = document.getElementById('xp');
  if (!box) return;
  const date = box.dataset.date;
  const text = document.getElementById('xpText');
  const count = document.getElementById('xpCount');
  const status = document.getElementById('xpStatus');
  const shot = document.getElementById('xpShot');
  const btnPreview = document.getElementById('xpPreview');
  const btnPost = document.getElementById('xpPost');
  let limit = 280;
  let posted = false;

  // X's own count: each URL is 23; most emoji and CJK count 2 (same rule as xpost.x_length).
  function xLength(t) {
    const urls = t.match(/https?:\/\/\S+/g) || [];
    let n = 23 * urls.length;
    for (const ch of t.replace(/https?:\/\/\S+/g, '')) {
      const o = ch.codePointAt(0);
      const light = o <= 4351 || (o >= 8192 && o <= 8205) || (o >= 8208 && o <= 8223) || (o >= 8242 && o <= 8247);
      n += light ? 1 : 2;
    }
    return n;
  }
  function updateCount() {
    const t = text.value;
    const x = xLength(t);
    count.textContent = [...t].length + ' characters; ' + x + ' of ' + limit + ' as X counts them (each link counts 23)';
    count.className = 'cnt' + (x > limit ? ' bad' : '');
    if (!posted) btnPost.disabled = x > limit || !t.trim();
  }
  function say(msg, err) { status.textContent = msg; status.className = 'st' + (err ? ' err' : ''); }
  function showShot(kind) {
    shot.innerHTML = '<img alt="X screenshot" src="/api/xpost/' + date + '/' + kind + '.png?t=' + Date.now() + '">';
  }
  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined, credentials: 'same-origin' });
    if (r.status === 401) { location.href = '/login'; throw new Error('signed out'); }
    let data = null; try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) throw new Error((data && data.detail) || ('HTTP ' + r.status));
    return data;
  }
  function apply(d) {
    limit = d.limit || 280;
    posted = ['posted', 'posting', 'unconfirmed'].includes(d.status);
    if (posted) {
      text.value = d.text; text.readOnly = true;
      btnPost.disabled = true; btnPreview.disabled = true;
      btnPost.textContent = d.status === 'posted' ? 'Posted' : 'Post clicked';
      if (d.status === 'posted') {
        status.innerHTML = '';
        status.className = 'st';
        status.append('Posted to X' + (d.posted_at ? ' at ' + d.posted_at.slice(0, 16).replace('T', ' ') + ' UTC' : '') + '. ');
        if (d.url) { const a = document.createElement('a'); a.href = d.url; a.target = '_blank'; a.textContent = d.url; status.append(a); }
        else status.append('X did not show the link; the post is on the account.');
      } else {
        say((d.error || 'Post was clicked but X did not confirm it.') + ' Check the account on X before doing anything else.', true);
      }
      if (d.has_shot) showShot('shot');
    } else {
      if (!text.value) text.value = d.text;
      if (d.status === 'error' && d.error) say('Last attempt failed before posting: ' + d.error, true);
      else if (d.preview_at) say('Last composer preview ' + d.preview_at.slice(0, 16).replace('T', ' ') + ' UTC. ' + (d.preview_note || ''));
      if (d.has_preview) showShot('preview');
    }
    updateCount();
  }

  text.addEventListener('input', updateCount);
  btnPreview.addEventListener('click', async () => {
    btnPreview.disabled = true; btnPost.disabled = true;
    say('Opening the X composer in the server browser and attaching the four cards… about 30 seconds. Nothing will be posted.');
    try { const d = await api('POST', '/api/xpost/' + date + '/preview', { text: text.value }); apply(d); say('Composer preview below. Nothing was posted. ' + ((d.result && d.result.discard) || ''), false); showShot('preview'); }
    catch (e) { say('Preview failed: ' + e.message, true); }
    finally { if (!posted) { btnPreview.disabled = false; updateCount(); } }
  });
  btnPost.addEventListener('click', async () => {
    if (!confirm('Post these four cards to X now with this text?\n\n' + text.value)) return;
    btnPreview.disabled = true; btnPost.disabled = true; btnPost.textContent = 'Posting…';
    say('Posting to X… about 45 seconds. Do not close this page.');
    try { apply(await api('POST', '/api/xpost/' + date + '/post', { text: text.value })); }
    catch (e) {
      btnPost.textContent = 'Approve and post';
      say('Post failed: ' + e.message, true);
      try { apply(await api('GET', '/api/xpost/' + date)); } catch (e2) { /* keep the message */ }
      if (!posted) btnPreview.disabled = false;
    }
  });
  api('GET', '/api/xpost/' + date).then(apply).catch(e => say('Could not load the X post state: ' + e.message, true));
})();
