"""Prepare text for text-to-speech conversion.

Cleans up raw article/email text to sound natural when read aloud.
"""

import re


def _integrate_footnotes(text: str) -> str:
    """Inline Substack-style trailing numbered footnotes into the body.

    Substack renders footnotes as bare numbered paragraphs at the end of
    a post ("1First footnote text.", "2Second footnote text...") with no
    "Notes" or "Footnotes" header, so generic header-based truncation
    misses them. Without intervention, TTS reads them out as a confusing
    disjointed tail.

    Strategy:
    1. Detect the footnote block — a sequence of >=2 consecutive non-empty
       lines near the end of the text that each start with a number 1, 2,
       3, … in ascending order (allowing some gaps for footnote-less
       articles).
    2. Parse {number: text} from those lines.
    3. Find inline references in the body — sentence-ending periods
       followed by a footnote digit (".7 " or ".10\n"), where the char
       before the period is a letter (to avoid matching decimals like
       "3.5"). Replace each with ". Footnote: <text>."
    4. Strip the trailing footnote block.

    If no plausible footnote block is detected, returns text unchanged.
    """
    if not text:
        return text
    lines = text.split('\n')
    if len(lines) < 6:
        return text

    # Walk backwards from the end of the text, collecting numbered lines.
    # Skip past Substack page-chrome that follows the article ("647", "61",
    # "PreviousNext", "Share", numeric like/restack counts, etc.) until we
    # either hit footnote lines (digit + substantive text) or substantive
    # body text. Stop on the first substantive non-footnote line.
    footnote_pat = re.compile(r'^(\d{1,3})[\s.]?\s*(.{8,})$')
    # Lines we treat as "page chrome" — skip past them when scanning back
    chrome_words = {
        "share", "like", "comment", "restack", "tweet", "save", "bookmark",
        "previousnext", "previous", "next", "subscribe", "subscribed",
    }
    collected: list[tuple[int, int, str]] = []  # (line_idx, number, body)
    halfway = len(lines) // 2

    i = len(lines) - 1
    while i >= halfway:
        raw = lines[i]
        s = raw.strip()
        if not s:
            i -= 1
            continue
        # Substack engagement counts: bare integers (e.g. "647", "61")
        if re.fullmatch(r'\d{1,5}\.?', s):
            i -= 1
            continue
        # Page chrome words
        if s.lower() in chrome_words:
            i -= 1
            continue
        m = footnote_pat.match(s)
        if not m:
            # Once we collected at least one footnote, hitting a real
            # paragraph means we're done. Otherwise, just keep walking
            # back — we haven't entered the footnote zone yet.
            if collected:
                break
            i -= 1
            continue
        try:
            num = int(m.group(1))
        except ValueError:
            i -= 1
            continue
        # Footnote numbers are typically 1-50; bail out on absurd values
        # (year numbers like "2026Trump…" shouldn't qualify).
        if num < 1 or num > 50:
            if collected:
                break
            i -= 1
            continue
        body_text = m.group(2).strip()
        if not body_text or len(body_text) < 8:
            if collected:
                break
            i -= 1
            continue
        collected.append((i, num, body_text))
        i -= 1

    if len(collected) < 2:
        return text

    # collected is in reverse order; flip and check sequence is 1,2,3,...
    collected.reverse()
    expected = 1
    valid = True
    for _, num, _ in collected:
        if num != expected:
            valid = False
            break
        expected += 1
    if not valid:
        return text

    # Build footnotes dict and find the start of the block
    footnotes = {num: body for (_, num, body) in collected}
    block_start = collected[0][0]
    body_text = '\n'.join(lines[:block_start])

    # Now inline each footnote where its reference appears in the body.
    # Match a sentence-ending punctuation + footnote number + boundary,
    # where the char before the punctuation is a letter (to avoid
    # decimals like "3.5" matching as ".5").
    def _replace(m: re.Match) -> str:
        n = int(m.group(2))
        if n in footnotes:
            # Strip trailing punctuation from the footnote body so we don't
            # end up with double periods ("... still survives.. ").
            body_clean = footnotes[n].rstrip(' .!?,;')
            return f"{m.group(1)} Footnote: {body_clean}. "
        return m.group(0)

    # Two patterns:
    #   (a) "letter.N " — the most common Substack-after-extraction form
    #   (b) "letter)N " or "letter!N " — quote-closing followed by ref
    # The lookbehind for a letter prevents matching decimals.
    pattern_a = re.compile(r'(?<=[a-zA-Z\)])([\.\?!])(\d{1,2})(?=\s|$|[A-Z])')
    new_body = pattern_a.sub(_replace, body_text)

    # If any footnotes were never referenced inline, append them at the
    # end so the reader doesn't lose information. (Common for "extra
    # commentary" footnotes that aren't tied to a specific spot.)
    used = set()
    for m in pattern_a.finditer(body_text):
        try: used.add(int(m.group(2)))
        except Exception: pass
    leftover = [(n, t) for n, t in footnotes.items() if n not in used]
    if leftover:
        parts = []
        for n, t in leftover:
            t_clean = t.rstrip(' .!?,;')
            parts.append(f"Footnote {n}: {t_clean}.")
        new_body = new_body.rstrip() + "\n\nAdditional notes from the author. " + " ".join(parts)

    return new_body


def prepare_for_tts(text: str) -> str:
    """Clean and format text for natural TTS output."""

    # Remove URLs entirely
    text = re.sub(r'https?://\S+', '', text)

    # Remove CSS/HTML code blocks that leaked through HTML stripping
    text = re.sub(r'\{[^}]*(?:padding|margin|border|font|display|width|height|color|background|text-decoration|outline|overflow|position|float|clear|visibility|opacity|cursor|z-index|content|table-layout|vertical-align|line-height|letter-spacing|word-spacing|text-align|text-transform|text-indent|white-space|list-style|mso-|webkit-|-ms-|interpolation)[^}]*\}', '', text, flags=re.IGNORECASE)
    # Remove @media queries and similar CSS at-rules
    text = re.sub(r'@media[^{]*\{[^}]*\}', '', text, flags=re.IGNORECASE)
    text = re.sub(r'@font-face[^{]*\{[^}]*\}', '', text, flags=re.IGNORECASE)
    # Remove remaining CSS-like selectors
    text = re.sub(r'\.[a-z_-]+\s*\{[^}]*\}', '', text, flags=re.IGNORECASE)
    # Remove inline style remnants like "body { ... }"
    text = re.sub(r'\b(?:body|table|td|th|img|div|span|p|a)\s*\{[^}]*\}', '', text, flags=re.IGNORECASE)

    # Remove HTML entities (&#NNN; and &#xNNN;) — these are leftovers from email HTML
    text = re.sub(r'&#x?[0-9a-fA-F]+;', ' ', text)
    # Remove zero-width and invisible Unicode characters
    text = re.sub(r'[\u00ad\u034f\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]', '', text)

    # Remove citation brackets like [1], [2], [source], [citation needed]
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'\[citation[^\]]*\]', '', re.IGNORECASE and text)
    text = re.sub(r'\[source[^\]]*\]', '', text)

    # Keep content in brackets if it's actual words (not just numbers/citations)
    # Convert [important note here] → important note here
    text = re.sub(r'\[([A-Za-z][^\]]{2,})\]', r'\1', text)
    # Remove any remaining empty or number-only brackets
    text = re.sub(r'\[\s*\]', '', text)

    # Remove parentheses but keep content: (important stuff) → important stuff
    # But remove if it's just a number or short code like (fig. 1) or (p. 23)
    text = re.sub(r'\((?:fig\.|p\.|pp\.|ibid|op\.? cit)\s*[^)]*\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\((\d{1,3})\)', '', text)  # Remove (1), (23), etc.
    text = re.sub(r'\(([^)]+)\)', r', \1,', text)  # Keep content, replace parens with commas

    # Remove common email/web junk
    junk_patterns = [
        r'view this post on the web.*',
        r'view in browser.*',
        r'unsubscribe.*',
        r'manage your subscription.*',
        r'click here to.*',
        r'share this post.*',
        r'forward this email.*',
        r'was this forwarded to you\?.*',
        r'sign up for.*newsletter.*',
        r'open in app.*',
        r'like comment restack.*',
        r'©\s*\d{4}.*',
        r'all rights reserved.*',
        r'print pdf.*',
    ]
    for pattern in junk_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.MULTILINE)

    # Remove standalone social buttons and repeated action words
    text = re.sub(r'^\s*(Share|Like|Comment|Restack|Tweet|Save|Bookmark)\s*$', '', text, flags=re.MULTILINE | re.IGNORECASE)
    # Remove repeated "Save this story" / "Share this story" etc.
    text = re.sub(r'(Save this story\s*)+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(Share this story\s*)+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(Save\s+){2,}', '', text, flags=re.IGNORECASE)
    # Remove social share buttons and navigation junk
    text = re.sub(r'on x\s*,\s*opens in a new window\s*,?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'on facebook\s*,\s*opens in a new window\s*,?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'on linkedin\s*,\s*opens in a new window\s*,?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Pro Features Configuration.*?\n', '', text)
    text = re.sub(r'current progress \d+%', '', text)
    text = re.sub(r'Print this page', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Published\s*\d+\s*HOURS?\s*AGO', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Published\s*\d+\s*DAYS?\s*AGO', '', text, flags=re.IGNORECASE)
    text = re.sub(r'©[^.\n]*\.?', '', text)
    text = re.sub(r'^\d+\.\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'SKIP ADVERTISEMENT', '', text, flags=re.IGNORECASE)
    text = re.sub(r'ADVERTISEMENT', '', text, flags=re.IGNORECASE)

    # Format section headings for audio — add a pause marker
    # Lines that look like headings (short, possibly bold/caps)
    lines = text.split('\n')
    formatted = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            formatted.append(line)
            continue

        # Detect headings: short lines (under 80 chars) followed by longer text,
        # or lines that are ALL CAPS, or lines that start with # markdown
        is_heading = False
        if stripped.startswith('#'):
            stripped = stripped.lstrip('#').strip()
            is_heading = True
        elif (len(stripped) < 80 and
              stripped == stripped.title() and
              i + 1 < len(lines) and
              len(lines[i + 1].strip()) > len(stripped)):
            is_heading = True
        elif stripped.isupper() and len(stripped) < 80 and len(stripped.split()) <= 8:
            is_heading = True

        if is_heading and stripped:
            formatted.append(f"\n\n{stripped}.\n")
        else:
            formatted.append(line)

    text = '\n'.join(formatted)

    # Clean up excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'  +', ' ', text)

    # Remove image alt text, captions, and photo credits
    text = re.sub(r'\[image[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Getty Images.*?\n', '', text)
    text = re.sub(r'Photo:.*?\n', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Photo [Cc]redit:.*?\n', '', text)
    text = re.sub(r'Credit:.*?\n', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Illustration:.*?\n', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Image:.*?\n', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Source:.*?Getty.*?\n', '', text)
    text = re.sub(r'Source:.*?Reuters.*?\n', '', text)
    text = re.sub(r'Source:.*?AP Photo.*?\n', '', text)
    text = re.sub(r'Photographer:.*?\n', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\(Photo.*?\)', '', text)
    text = re.sub(r'\(Image.*?\)', '', text)

    # Truncate at reference/notes sections (bibliography, endnotes, etc.)
    # These often appear at the end of academic/newsletter articles
    lines_list = text.split('\n')
    for i, line in enumerate(lines_list):
        ll = line.strip().lower()
        # Detect standalone "Notes" or "References" headers
        if ll in ('notes', 'references', 'bibliography', 'sources', 'endnotes', 'footnotes') and i > len(lines_list) * 0.5:
            text = '\n'.join(lines_list[:i])
            break
        # Detect "Notes:" at end of article
        if ll.startswith('notes:') and i > len(lines_list) * 0.7:
            text = '\n'.join(lines_list[:i])
            break

    # Integrate Substack-style trailing numbered footnotes back into the
    # body where they're referenced. Substack writes footnotes as bare
    # numbered paragraphs at the end of the post ("1Some text.", "2Other
    # text…"), with no "Notes" header, so the truncation pass above
    # misses them — and the audio ended up reading them as a confusing
    # tail of disjointed numbered fragments.
    text = _integrate_footnotes(text)

    # Remove subscribe/signup junk that might appear mid-article
    sub_patterns = [
        r'Subscribe to .*?\n',
        r'Sign up for .*?\n',
        r'Get the full .*? experience\n',
        r'Become a .*? subscriber.*?\n',
        r'This post is for paid subscribers.*?\n',
        r'Already a subscriber\?.*?\n',
        r'Read more from .*?\n',
        r'I write about .*? at .*?\n',
    ]
    for pat in sub_patterns:
        text = re.sub(pat, '', text, flags=re.IGNORECASE)

    return text.strip()
