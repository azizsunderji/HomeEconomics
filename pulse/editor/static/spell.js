/* Spell check for the editor (owner, 28 Oct 2026). The browser's own checker only marks words as you type them,
   so text loaded into the page or typed earlier was never marked. This checks every editable text block itself
   (Hunspell en_US via nspell, plus dict/custom_words.txt for housing terms) and draws a wavy underline with the
   CSS Custom Highlight API, which marks text without changing the page's HTML. Rules: spellrules.js. */
(function () {
  if (!window.CSS || !CSS.highlights || typeof Highlight === 'undefined') return;   // older browsers: no marks
  let spell = null, extra = new Set(), timer = null;
  const BASE = new URL('.', document.currentScript ? document.currentScript.src : location.href);   // .../static/
  const SEL = '.rich[contenteditable="true"]';

  function scan() {
    timer = null;
    if (!spell) return;
    const ranges = [];
    document.querySelectorAll(SEL).forEach(root => {
      // only text the owner can edit: the nearest contenteditable ancestor must be "true" (skips box headers,
      // image controls; includes the pull-quote attribution, which is editable inside a non-editable box)
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: n => {
          const c = n.parentElement && n.parentElement.closest('[contenteditable]');
          return c && c.getAttribute('contenteditable') === 'true' ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
      });
      let n;
      while ((n = walker.nextNode())) {
        for (const m of NoonSpellRules.misspelled(n.nodeValue, spell, extra)) {
          const r = document.createRange(); r.setStart(n, m.start); r.setEnd(n, m.end); ranges.push(r);
        }
      }
    });
    CSS.highlights.set('noon-misspell', new Highlight(...ranges));
  }
  function later() { if (timer) clearTimeout(timer); timer = setTimeout(scan, 400); }

  Promise.all(['dict/en_US.aff', 'dict/en_US.dic', 'dict/custom_words.txt']
    .map(u => fetch(new URL(u, BASE), { cache: 'force-cache' }).then(r => r.text())))
    .then(([aff, dic, words]) => {
      spell = window.nspell(aff, dic);
      extra = new Set(words.split(/\s+/).filter(Boolean).map(w => w.toLowerCase()));
      scan();
    })
    .catch(e => console.warn('spell check unavailable', e));
  new MutationObserver(later).observe(document.body, { subtree: true, childList: true, characterData: true });
  document.addEventListener('input', later, true);
})();
