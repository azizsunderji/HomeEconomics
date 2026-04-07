"""Prepare text for text-to-speech conversion.

Cleans up raw article/email text to sound natural when read aloud.
"""

import re


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
