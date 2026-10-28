/* Housing at Noon editor: which words to flag. Shared by the browser (static/spellrules.js) and the node test.
   Skips capitalised words (names), acronyms, words touching digits, URLs and @handles. Hyphenated words pass when
   every part passes (common prefixes like pre-, non-, mis- allowed). */
(function (root) {
  const WORD = /\p{L}+(?:['’-]\p{L}+)*/gu;
  const PREFIX = new Set('pre post mis tri non co re sub anti multi semi mid ex self cross inter intra over under out de bi mega micro ultra pro quasi neo un up e'.split(' '));
  function okWord(w, spell, extra) {
    const lower = w.toLowerCase();
    if (lower.length < 3 || PREFIX.has(lower)) return true;
    if (extra.has(lower)) return true;
    if (/s$/.test(lower) && (extra.has(lower.slice(0, -1)) || extra.has(lower.slice(0, -2)))) return true;
    return spell.correct(w) || spell.correct(lower);
  }
  function misspelled(text, spell, extra) {
    const out = [];
    let m;
    WORD.lastIndex = 0;
    while ((m = WORD.exec(text))) {
      const tok = m[0], start = m.index, end = start + tok.length;
      if (/^\p{Lu}/u.test(tok)) continue;                              // names, sentence starts
      if (/\p{Lu}/u.test(tok.slice(1))) continue;                       // iPhone, eBay
      if (/[^\x00-\x7F]/.test(tok.replace(/\u2019/g, "'"))) continue;           // café, résumé: accented words are skipped
      const before = text.slice(Math.max(0, start - 1), start), after = text.slice(end, end + 1);
      if (/[0-9$#@_\/]/.test(before) || /[0-9@_\/]/.test(after)) continue;
      if (before === '.' && /\S/.test(text.slice(Math.max(0, start - 2), start - 1))) continue;   // .com
      const s0 = text.lastIndexOf(' ', start) + 1, e0 = text.indexOf(' ', end);
      const chunk = text.slice(s0, e0 < 0 ? text.length : e0);
      if (/:\/\/|www\.|\.(com|org|net|gov|edu)\b|@/.test(chunk)) continue;
      const w = tok.replace(/’/g, "'").replace(/'s$|'$/i, '');
      if (okWord(w, spell, extra)) continue;
      if (w.includes('-') && (w.split(/-/).every(p => okWord(p, spell, extra)) || okWord(w.replace(/-/g, ''), spell, extra))) continue;
      out.push({ start, end, word: tok });
    }
    return out;
  }
  const api = { misspelled };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.NoonSpellRules = api;
})(typeof window !== 'undefined' ? window : globalThis);
