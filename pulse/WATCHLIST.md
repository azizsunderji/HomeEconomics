# Pulse Watchlist

Edit this file to add or remove sources. Changes take effect on the next
pipeline run (after committing and pushing to GitHub).

For quick changes, just edit the relevant section below and push.
For anything more complex, open a Claude Code conversation in this project.


## Twitter Accounts
These people are scraped via Apify. One handle per line.
To add someone: add their handle (without @). To remove: delete the line.

Current list is in: `scripts/config.py` → `TWITTER_ACCOUNTS`

Recently added:
- ezraklein (Ezra Klein)
- jabornesworth (John Burn-Murdoch, FT)
- trq212 (Thariq)
- DKThomp (Derek Thompson) — already tracked


## Headline Sources (RSS only)
Headlines come ONLY from RSS feeds of these publications:
- New York Times (4 feeds: Real Estate, Economy, DealBook, Upshot)
- Financial Times (6 feeds: Property, US Economy, Global Economy, Markets, JBM)
- Bloomberg (7 feeds: Markets, Economics, Industries, Wealth, + columnists)
- Wall Street Journal (2 feeds: US Business, Markets)
- Washington Post (2 feeds: Business, Economy)
- The Economist (3 feeds: Finance & Economics, Leaders, United States)

RSS feeds are defined in: `/Users/azizsunderji/Dropbox/Home Economics/RSSFeeds/HomeEconomicsRSS.opml`
To add a new publication: add its RSS feed URL to the OPML file AND add its
domain to `HEADLINE_DOMAIN_ALLOWLIST` in `scripts/config.py`.


## Newsletter Senders (Gmail → Newsletters section)
These Gmail senders get routed to the Newsletters section (with Sonnet summaries).
Edit `GMAIL_NEWSLETTER_SENDERS` in `scripts/config.py`.

Current:
- Brandon Donnelly (brandondonnelly@newsletter.paragraph.xyz)
- FT newsletters (newsletters.ft.com) — Unhedged/Robert Armstrong
- Bloomberg Opinion (noreply@news.bloomberg) — Conor Sen, etc.


## Institutional Signal Senders (Gmail → Institutional section)
Only these senders appear in Institutional Signal.
Edit `INSTITUTIONAL_SENDER_ALLOWLIST` in `scripts/config.py`.

Current: Thesis Driven, Gothamist, Leonard Steinberg (COMPASS),
THE CITY SCOOP, Dan Rasmussen (Verdad), GS Macro, Torsten Slok (Apollo),
AEI, Daily Shot, ResiClub, Pulsenomics, Zillow Research, Fannie Mae,
Freddie Mac, FHFA, Fed/NY Fed, NBER, Census, BLS, Brookings, Wiley


## Substack Newsletters (RSS)
These are fetched via RSS. Edit the feeds in `scripts/config.py` → substack
section, or add to the OPML file.

Current: Calculated Risk, Kevin Erdmann, Logan Mohtashami, Apricitas Economics,
Construction Physics, Ben Carlson, Matt Yglesias (Slow Boring), Noah Smith,
Lance Lambert (ResiClub), Joe Weisenthal, Kobeissi Letter, Nick Timiraos,
Full Stack Economics, Employ America, Matthew Klein (The Overshoot),
Conor Sen, Odd Lots, Tracy Alloway, Ernie Tedeschi, Jason Furman


## Blocked Sources
- FT Opinion (blocked from headlines — 0% approval in triage)
- HousingWire, Realtor.com, Inman (not in headline allowlist)


## How to Make Changes

**Add a Twitter account:** Add handle to `TWITTER_ACCOUNTS` in config.py
**Add a newsletter sender:** Add email pattern to `GMAIL_NEWSLETTER_SENDERS` in config.py
**Add an institutional sender:** Add name/email pattern to `INSTITUTIONAL_SENDER_ALLOWLIST` in config.py
**Add a headline source:** Add RSS feed to OPML + domain to `HEADLINE_DOMAIN_ALLOWLIST` in config.py
**Block a feed from headlines:** Add feed name pattern to `HEADLINE_FEED_BLOCKLIST` in config.py
