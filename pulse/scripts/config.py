"""Pulse configuration — topics, keywords, sources, thresholds."""

from __future__ import annotations
import os

# ── Topic taxonomy (20 topics) ────────────────────────────────────────────────
# Each item gets one or more topic tags from Haiku classification.
TOPICS = {
    "mortgage_rates": {
        "label": "Mortgage Rates",
        "keywords": ["mortgage rate", "30-year", "15-year", "fed rate", "interest rate",
                      "rate lock", "buy down", "points", "ARM", "fixed rate"],
    },
    "home_prices": {
        "label": "Home Prices",
        "keywords": ["home price", "house price", "median price", "price cut",
                      "price growth", "appreciation", "HPI", "ZHVI", "case-shiller"],
    },
    "inventory_supply": {
        "label": "Inventory & Supply",
        "keywords": ["inventory", "listings", "new listings", "active listings",
                      "months supply", "housing supply", "days on market", "DOM"],
    },
    "affordability": {
        "label": "Affordability",
        "keywords": ["affordability", "price to income", "housing cost burden",
                      "rent burden", "cost of living", "first time buyer",
                      "priced out", "unaffordable"],
    },
    "rent_market": {
        "label": "Rental Market",
        "keywords": ["rent", "rental", "asking rent", "rent growth", "landlord",
                      "tenant", "eviction", "lease", "multifamily", "apartment"],
    },
    "construction": {
        "label": "Construction & New Build",
        "keywords": ["housing starts", "permits", "new construction", "builder",
                      "homebuilder", "new home sales", "completions", "construction cost"],
    },
    "migration_population": {
        "label": "Migration & Population",
        "keywords": ["migration", "moving", "population growth", "domestic migration",
                      "net migration", "inflow", "outflow", "sunbelt", "moving to",
                      "leaving", "exodus", "remote work migration"],
    },
    "federal_reserve": {
        "label": "Fed & Monetary Policy",
        "keywords": ["federal reserve", "fed", "FOMC", "rate cut", "rate hike",
                      "powell", "monetary policy", "QT", "quantitative tightening",
                      "inflation target"],
    },
    "inflation_cpi": {
        "label": "Inflation & CPI",
        "keywords": ["inflation", "CPI", "PCE", "shelter inflation", "OER",
                      "owners equivalent rent", "core inflation", "disinflation"],
    },
    "employment_labor": {
        "label": "Employment & Labor",
        "keywords": ["jobs report", "unemployment", "payroll", "labor market",
                      "hiring", "layoff", "BLS", "employment", "wage growth",
                      "average hourly earnings"],
    },
    "recession_economy": {
        "label": "Recession & GDP",
        "keywords": ["recession", "GDP", "economic growth", "slowdown", "soft landing",
                      "hard landing", "contraction", "expansion", "GDI"],
    },
    "housing_policy": {
        "label": "Housing Policy",
        "keywords": ["zoning", "YIMBY", "NIMBY", "housing policy", "rent control",
                      "section 8", "public housing", "LIHTC", "housing voucher",
                      "inclusionary", "ADU", "upzoning"],
    },
    "commercial_real_estate": {
        "label": "Commercial Real Estate",
        "keywords": ["commercial real estate", "CRE", "office vacancy", "retail space",
                      "industrial", "CMBS", "cap rate", "office to residential",
                      "return to office"],
    },
    "fintech_proptech": {
        "label": "Fintech & Proptech",
        "keywords": ["proptech", "iBuyer", "Opendoor", "Offerpad", "Zillow offers",
                      "real estate tech", "fintech", "digital mortgage", "AI real estate"],
    },
    "demographics": {
        "label": "Demographics",
        "keywords": ["millennial", "gen z", "boomer", "household formation",
                      "birth rate", "aging", "generational wealth", "inheritance",
                      "first time homebuyer age"],
    },
    "wealth_inequality": {
        "label": "Wealth & Inequality",
        "keywords": ["wealth gap", "inequality", "housing wealth", "home equity",
                      "net worth", "racial wealth gap", "intergenerational",
                      "housing as investment"],
    },
    "regional_markets": {
        "label": "Regional Markets",
        "keywords": ["austin", "boise", "phoenix", "tampa", "miami", "nashville",
                      "denver", "seattle", "san francisco", "new york",
                      "housing market", "local market", "metro area"],
    },
    "mortgage_industry": {
        "label": "Mortgage Industry",
        "keywords": ["origination", "refinance", "refi", "mortgage application",
                      "MBA", "Fannie Mae", "Freddie Mac", "GSE", "FHA", "VA loan",
                      "non-QM", "mortgage servicing"],
    },
    "climate_insurance": {
        "label": "Climate & Insurance",
        "keywords": ["insurance crisis", "home insurance", "flood insurance",
                      "wildfire", "climate risk", "natural disaster", "hurricane",
                      "FEMA", "insurance premium", "uninsurable"],
    },
    "consumer_sentiment": {
        "label": "Consumer Sentiment",
        "keywords": ["consumer confidence", "sentiment", "housing sentiment",
                      "good time to buy", "Fannie Mae survey", "Michigan survey",
                      "buyer sentiment", "seller sentiment"],
    },
}

# ── Google News search queries ────────────────────────────────────────────────
# Dropped: site: queries duplicated OPML RSS feeds; reporter queries duplicated
# Google Alert RSS feeds already in OPML (30 alerts, "Alert: Name Pub" feeds).
# Both returned mostly stale articles (99%+ older than 48h). Google Alerts RSS
# in OPML provides the same reporter coverage with same-day freshness.
GOOGLE_NEWS_QUERIES: list = []

# ── Competitor Substacks ──────────────────────────────────────────────────────
COMPETITOR_SUBSTACKS = [
    # Housing/real estate focused
    ("Calculated Risk", "https://calculatedrisk.substack.com/feed"),
    ("Kevin Erdmann", "https://kevinerdmann.substack.com/feed"),
    ("Logan Mohtashami", "https://loganmohtashami.substack.com/feed"),
    ("Apricitas Economics", "https://www.apricitas.io/feed"),
    ("Construction Physics", "https://www.constructionphysics.com/feed"),
    ("Ben Carlson", "https://awealthofcommonsense.substack.com/feed"),
    ("Matthew Yglesias - Slow Boring", "https://www.slowboring.com/feed"),
    ("Noah Smith - Noahpinion", "https://www.noahpinion.blog/feed"),
    ("Lance Lambert - ResiClub", "https://www.resiclub.com/feed"),
    ("Joe Weisenthal - TheStalwart", "https://www.thestalwart.com/feed"),
    ("The Kobeissi Letter", "https://thekobeissiletter.substack.com/feed"),
    ("Full Stack Economics", "https://fullstackeconomics.com/feed"),
    ("Employ America", "https://employamerica.substack.com/feed"),
    ("Matthew Klein - The Overshoot", "https://theovershoot.co/feed"),
    ("Conor Sen", "https://conorsen.substack.com/feed"),
    ("Odd Lots (Bloomberg)", "https://oddlots.substack.com/feed"),
    ("Tracy Alloway", "https://tracyalloway.substack.com/feed"),
    ("Ernie Tedeschi", "https://ernietedeschi.substack.com/feed"),
    ("Jason Furman", "https://jasonfurman.substack.com/feed"),
    ("Ethan Mollick - One Useful Thing", "https://www.oneusefulthing.org/feed"),
    # Housing/Urbanism
    ("Jerusalem Demsas", "https://www.theatlantic.com/feed/author/jerusalem-demsas/"),
    # AI
    ("Ben Thompson - Stratechery", "https://stratechery.com/feed"),
    ("Zvi Mowshowitz", "https://thezvi.substack.com/feed"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    # Economics/Demographics
    ("Matt Clancy - New Things Under the Sun", "https://mattsclancy.substack.com/feed"),
    ("Lyman Stone", "https://lymanstone.substack.com/feed"),
    ("Derek Thompson", "https://derekthompson.substack.com/feed"),
    ("Ezra Klein", "https://www.nytimes.com/svc/collections/v1/publish/www.nytimes.com/column/ezra-klein/rss.xml"),
    ("Lenny Rachitsky", "https://www.lennysnewsletter.com/feed"),
    ("Tangle", "https://www.readtangle.com/feed"),
    ("Paul Goldsmith-Pinkham", "https://paulgp.substack.com/feed"),
    ("Heather Cox Richardson", "https://heathercoxrichardson.substack.com/feed"),
    ("Cameron Murray", "https://fresheconomicthinking.substack.com/feed"),
    ("Not Boring", "https://www.notboring.co/feed"),
    ("David Pierce", "https://www.theverge.com/authors/david-pierce/rss"),
    ("Ryan Avent", "https://ryanavent.substack.com/feed"),
    ("Mike DelPrete", "https://mikedp.substack.com/feed"),
    ("Alexander Kustov", "https://alexanderkustov.substack.com/feed"),
    ("Sarah O'Connor - FT", "https://www.ft.com/sarah-o-connor?format=rss"),
    ("Shadow Price Macro (Robin Brooks)", "https://robinjbrooks.substack.com/feed"),
    ("Maximum New York", "https://maximumnewyork.substack.com/feed"),
    ("Nominal News", "https://nominalnews.substack.com/feed"),
    ("L.A. Reported", "https://lareported.substack.com/feed"),
    ("Casey Newton (Platformer)", "https://www.platformer.news/rss/"),
    ("Paul Krugman", "https://paulkrugman.substack.com/feed"),
    ("Jonathan Miller - Miller Samuel", "https://www.millersamuel.com/feed/"),
    ("The Argument", "https://www.theargumentmag.com/feed"),
    ("Understanding AI (Tim Lee)", "https://www.understandingai.org/feed"),
    ("SemiAnalysis (Dylan Patel)", "https://www.semianalysis.com/feed"),
    ("Dwarkesh Patel", "https://www.dwarkesh.com/feed"),
    ("Jack Clark - Import AI", "https://importai.substack.com/feed"),
    ("Hugh Clarke - A Thread of Order", "https://hughclarke.substack.com/feed"),
]

# ── Bluesky configuration ────────────────────────────────────────────────────
# Primary strategy: follow specific housing/econ accounts (no auth needed)
# Only accounts verified as active on Bluesky with recent posts
BLUESKY_ACCOUNTS = [
    # Also tracked on Twitter (cross-platform convergence)
    "calculatedrisk.bsky.social",       # Bill McBride — housing data
    "loganmohtashami.bsky.social",      # Logan Mohtashami — housing wire
    "conorsen.bsky.social",             # Conor Sen — housing/macro
    "mattyglesias.bsky.social",         # Matt Yglesias — housing policy
    "jasonfurman.bsky.social",          # Jason Furman — macro/policy
    "apricitas.bsky.social",            # Joseph Politano — econ data
    # Bluesky-primary voices (NOT on Twitter list — unique signal)
    "jennyschuetz.bsky.social",         # Jenny Schuetz — Arnold Ventures VP of Housing
    "markzandi.bsky.social",            # Mark Zandi — Moody's chief economist
    "deanbaker13.bsky.social",          # Dean Baker — CEPR, called the housing bubble
    "jerusalem.bsky.social",            # Jerusalem Demsas — The Atlantic, housing/urbanism
    "mikesimonsen.bsky.social",         # Mike Simonsen — Altos Research/Compass
    "mnolangray.bsky.social",           # M. Nolan Gray — California YIMBY
    "econcunningham.bsky.social",       # Chris Cunningham — former Fed, housing/urban econ
    "econberger.bsky.social",           # Guy Berger — labor markets, ex-LinkedIn economist
    "dismalscientist86.bsky.social",    # Dani Sandler — Census Bureau, housing/eviction
    "resi-analyst.bsky.social",         # Neal Hudson — UK housing market analyst
    "cwhitzman.bsky.social",            # Carolyn Whitzman — U of Toronto housing researcher
    "claesbackman.bsky.social",         # Claes Bäckman — housing/mortgage economist
    "ternerhousing.bsky.social",        # Terner Center — UC Berkeley housing research
]

# Secondary: search terms (only works with auth)
BLUESKY_SEARCH_TERMS = []

BLUESKY_MAX_PER_QUERY = 30

# ── Hacker News configuration ────────────────────────────────────────────────
HN_MIN_SCORE = 20  # Higher threshold — HN is lower relevance
HN_KEYWORDS = [
    "housing", "mortgage", "rent", "real estate", "home price",
    "affordability", "zoning", "NIMBY", "YIMBY", "migration",
    "inflation", "CPI", "federal reserve", "interest rate",
    "recession", "economy", "employment", "labor market",
]

# ── Twitter/Apify configuration ──────────────────────────────────────────────
TWITTER_SEARCH_QUERIES = []  # No discovery queries — account tracking provides full coverage

TWITTER_ACCOUNTS = [
    # Housing analysts & journalists
    "_brianpotter", "aaronAcarr", "abcampbell", "AlecStapp", "amandafung",
    "americanhousing", "arpitrage", "BillMcBride4", "BobKnakal",
    "brendanwhitsitt", "ByKyleCampbell", "calculatedrisk", "CandaceETaylor",
    "CarolWalshReal1", "commobserver",
    "ConorSen", "cremieuxrecueil", "DavidFBrand", "DKThomp",
    "dmlevitt", "donweinland",
    "FullStackEcon", "gandhisahil",
    "HomeLoanBill", "jasonfurman", "jburnmurdoch",
    "JedKolko", "jonathanmiller", "JosephPolitano",
    "keegan_tweets", "LanceRLambert",
    "LoganMohtashami", "MarcGoldwein",
    "mattkahn1966", "mattyglesias",
    "ModeledBehavior", "moorehn", "MoreBirths",
    "MTabarrok", "NateSilver538", "nberpubs",
    "nfergus", "Noahpinion",
    "palladiummag", "profstonge",
    "R2Rsquared", "RickPalaciosJr", "rileymeik", "robin_j_brooks",
    "robinhanson", "S_Stantcheva",
    "slatestarcodex", "stevecuozzo",
    "TenantBloc", "TheStalwart", "trdny", "trq212",
    "UrbanDigs", "urbanistvc",
    "xurbanxcowboyx",
    "YIMBYLAND",
    # Individual voices requested
    "ezraklein", "jabornesworth",
    # VIP accounts (also in TWITTER_VIP_ACCOUNTS)
    "phfloor",
    "scottlincicome",
    "aarmlovi",
    "michael_wiebe",
    "kaerdmann",
    "mnolangray",
    "jayparsons",
    "mikefellman",
    "josephpolitano",
    "pyradius",
    "TheStalwart",
    "ConorSen",
    "producercities",
    # AI Roundup accounts (also in AI_ROUNDUP_ACCOUNTS below)
    "claudeai", "felixrieseberg", "bcherny", "CaseyNewton", "kevinroose",
]

# AI Roundup accounts: shown in a separate "AI Roundup" section in the email.
# These accounts are EXCLUDED from the main Twitter Roundup to avoid duplication.
AI_ROUNDUP_ACCOUNTS = [
    "trq212",          # Thariq — Claude/Anthropic
    "claudeai",        # Claude AI official
    "felixrieseberg",  # Felix Rieseberg — Anthropic
    "bcherny",         # Boris Cherny — Anthropic
    "emollick",        # Ethan Mollick — AI + work
    "CaseyNewton",     # Casey Newton — Platformer, AI/tech
    "kevinroose",      # Kevin Roose — NYT AI/tech
]

# Twitter handle → real name map. Used to prevent Sonnet from inventing
# wrong names (e.g. calling @aarmlovi "Lubock"). If a handle isn't in
# this map, use the @handle directly in the summary.
TWITTER_REAL_NAMES = {
    "arpitrage": "Arpit Gupta",
    "emollick": "Ethan Mollick",
    "jasonfurman": "Jason Furman",
    "cremieuxrecueil": "Cremieux",
    "phfloor": "Pierre",
    "trq212": "Thariq",
    "scottlincicome": "Scott Lincicome",
    "aarmlovi": "Alex Armlovich",
    "lymanstoneky": "Lyman Stone",
    "greg_ip": "Greg Ip",
    "michael_wiebe": "Michael Wiebe",
    "kaerdmann": "Kevin Erdmann",
    "mnolangray": "M Nolan Gray",
    "jayparsons": "Jay Parsons",
    "mikefellman": "Mike Fellman",
    "alecstapp": "Alec Stapp",
    "josephpolitano": "Joseph Politano",
    "thestalwart": "Joe Weisenthal",
    "conorsen": "Conor Sen",
    "producercities": "Producer Cities",
    "claudeai": "Claude",
    "felixrieseberg": "Felix Rieseberg",
    "bcherny": "Boris Cherny",
    "caseynewton": "Casey Newton",
    "kevinroose": "Kevin Roose",
    "_brianpotter": "Brian Potter",
    "abcampbell": "AB Campbell",
    "amandafung": "Amanda Fung",
    "americanhousing": "American Housing",
    "billmcbride4": "Bill McBride (Calculated Risk)",
    "bobknakal": "Bob Knakal",
    "bykylecampbell": "Kyle Campbell",
    "calculatedrisk": "Calculated Risk",
    "candaceetaylor": "Candace Taylor",
    "carolwalshreal1": "Carol Walsh",
    "conorsen": "Conor Sen",
    "davidfbrand": "David Brand",
    "dkthomp": "Derek Thompson",
    "donweinland": "Don Weinland",
    "fullstackecon": "Full Stack Economics",
    "gandhisahil": "Sahil Gandhi",
    "homeloanbill": "Bill (HomeLoanBill)",
    "jburnmurdoch": "John Burn-Murdoch",
    "jedkolko": "Jed Kolko",
    "jonathanmiller": "Jonathan Miller",
    "loganmohtashami": "Logan Mohtashami",
    "marcgoldwein": "Marc Goldwein",
    "mattkahn1966": "Matt Kahn",
    "mattyglesias": "Matt Yglesias",
    "modeledbehavior": "Adam Ozimek",
    "moorehn": "Heidi Moore",
    "morebirths": "More Births",
    "mtabarrok": "Maxwell Tabarrok",
    "natesilver538": "Nate Silver",
    "nfergus": "Niall Ferguson",
    "noahpinion": "Noah Smith",
    "palladiummag": "Palladium Magazine",
    "profstonge": "Prof Stonge",
    "rickpalaciosjr": "Rick Palacios Jr",
    "rileymeik": "Riley Meik",
    "robin_j_brooks": "Robin Brooks",
    "robinhanson": "Robin Hanson",
    "s_stantcheva": "Stefanie Stantcheva",
    "slatestarcodex": "Scott Alexander",
    "stevecuozzo": "Steve Cuozzo",
    "tenantbloc": "Tenant Bloc",
    "thestalwart": "Joe Weisenthal",
    "trdny": "The Real Deal",
    "urbandigs": "UrbanDigs",
    "urbanistvc": "Urbanist VC",
    "xurbanxcowboyx": "Urban Cowboy",
    "yimbyland": "YIMBYland",
    "ezraklein": "Ezra Klein",
    "jabornesworth": "Jabor Nesworth",
}

TWITTER_MIN_LIKES = 5    # Low threshold — these are curated voices, not keyword search
TWITTER_MAX_PER_QUERY = 60  # Per batch; more results = better coverage of quiet accounts
TWITTER_DAILY_BUDGET_CENTS = 200  # $2/day max Apify spend (1 batch, no sweep)

TWITTER_VIP_ACCOUNTS = []  # Removed — all accounts earn placement on merit

# ── Gmail configuration ──────────────────────────────────────────────────────
GMAIL_SENDER_WHITELIST = []  # No whitelist — let Haiku classify everything in the inbox

GMAIL_LABELS = ["INBOX"]
GMAIL_MAX_RESULTS = 50

# Junk sender patterns for institutional signal filtering (blocklist approach)
GMAIL_JUNK_SENDER_PATTERNS = [
    "stripe.com", "github.com", "statuspage.io", "apify.com",
    "calendar-notification", "google.com/calendar", "calendly.com",
    "noreply", "no-reply", "donotreply", "notifications@",
    "buildinglink.com", "notify@",
    "theneurondaily.com", "joinsuperhuman.ai", "harkaudio.com",
    "theverge.com", "thecity.nyc",
    "amazon.com", "uber.com", "doordash.com",
    "linkedin.com", "facebook.com",
]

GMAIL_JUNK_TITLE_PATTERNS = [
    "invitation:", "accepted:", "updated invitation", "tentative accepted:",
    "daily agenda", "password", "payment receipt",
    "payout for", "run failed:", "new subscriber", "new paid subscriber",
    "new free subscriber", "unsubscription", "meeting today",
    "re: founders", "gemini-notes",
]

# Headlines: strict domain allowlist
HEADLINE_DOMAIN_ALLOWLIST = {
    "nytimes.com": "New York Times",
    "ft.com": "Financial Times",
    "bloomberg.com": "Bloomberg",
    "wsj.com": "Wall Street Journal",
    "washingtonpost.com": "Washington Post",
    "economist.com": "The Economist",
}

# No longer used for headlines (RSS-only now), but kept for reference
HEADLINE_AUTHOR_ALLOWLIST = {}

HEADLINE_FEED_BLOCKLIST = [
    "ft opinion",
]

# Curated feeds that skip the relevance filter for headlines — these are
# editorially curated by the publication, so everything in them is relevant
HEADLINE_CURATED_FEEDS = [
    "nyt dealbook", "nyt economy", "nyt the upshot", "nyt real estate",
    "nyt > business > dealbook", "nyt > business > economy", "nyt > real estate",
    "nyt calculator", "calculator",
]

# Gmail senders that should route to Newsletters section (not institutional signal)
# These are individual writers/columnists whose emails read like newsletter posts.
GMAIL_NEWSLETTER_SENDERS = [
    "brandondonnelly",          # Brandon Donnelly (Paragraph)
    "newsletters.ft.com",       # FT newsletters (Unhedged/Robert Armstrong, etc.)
    "noreply@news.bloomberg",   # Bloomberg Opinion newsletters (Conor Sen, etc.)
]

# Gmail senders that should route to Headlines section
GMAIL_HEADLINE_SENDERS = [
    # Conor Sen's Bloomberg column should be in headlines, not institutional
]

# Institutional signal: only these senders qualify.
# Built from triage votes. Everything else from Gmail is excluded.
INSTITUTIONAL_SENDER_ALLOWLIST = [
    "thesis driven", "thesisdriven",
    "gothamist",
    "leonard steinberg", "ls@compass.com",
    "the city scoop", "thecity",
    "dan rasmussen", "verdadcap",
    "gs macro", "goldman",
    "torsten slok", "apollo",
    "aei", "edward pinto",
    "daily shot",
    "resiclub", "lance lambert",
    "pulsenomics",
    "zillow research",
    "fannie mae", "freddie mac",
    "fhfa",
    "fed", "newyorkfed", "ny.frb.org",
    "nber",
    "census.gov", "bls.gov",
    "brookings",
    "atlantafed", "atlanta fed",
    "prakash loungani",
    "crain",
    # Added 2026-04-10
    "capital economics",
    "missing middle", "missingmiddle",
    "hbr.org", "harvard business review",
    "jay parsons", "jayparsons",
    "calculatedrisk", "calculated risk",
    "jchs.harvard", "joint center for housing",
    "urban.org", "urban institute",
]

# Gmail senders that should route to the unified AI section
GMAIL_AI_HEADLINE_SENDERS = [
    "superhuman",
    "theneurondaily", "the neuron",
    "platformer",
    "john burn-murdoch", "the ai shift",
]

# Substack authors whose posts should route to the unified AI section
# (matched against the 'author' field in substacker_takes)
AI_SUBSTACK_AUTHORS = [
    "understanding ai", "tim lee",
    "ethan mollick", "one useful thing",
    "ben thompson", "stratechery",
    "zvi",
    "simon willison",
    "casey newton", "platformer",
    "semianalysis", "dylan patel",
    "dwarkesh",
    "jack clark", "import ai",
]

JOURNAL_FEED_PATTERNS = [
    "sciencedirect", "journal of", "housing studies", "real estate economics",
    "cornell real estate", "nber", "wiley", "taylor & francis",
    "journal of urban economics", "journal of housing economics", "cities",
]

# ── Classification thresholds ─────────────────────────────────────────────────
RELEVANCE_THRESHOLD_INCLUDE = 30  # Below this, skip entirely
RELEVANCE_THRESHOLD_HIGHLIGHT = 70  # Above this, feature in briefing
CONVERGENCE_ALERT_THRESHOLD = 4  # Platforms required for push alert

# ── Delivery ──────────────────────────────────────────────────────────────────
EMAIL_TO = "aziz@home-economics.us"
EMAIL_FROM = "Pulse <onboarding@resend.dev>"

# ── Source weights (conversation pivot) ───────────────────────────────────────
# Higher weight = more prominent in briefing. Conversation sources dominate.
SOURCE_WEIGHTS = {
    "twitter": 4,
    "hackernews": 4,
    "bluesky": 3,
    "substack": 3,
    "gmail": 2,       # institutional research via email
    "google_news": 1,
    "rss": 1,
}

MIN_COMMENTS_FOR_CONVERSATION = 10  # Below this, a post is a link share, not a conversation

# ── Data lake path (for crosswalk) ────────────────────────────────────────────
DATA_LAKE_PATH = os.environ.get("DATA_LAKE_PATH", "/Users/azizsunderji/Dropbox/Home Economics/Data")
DATA_LAKE_CATALOG_PATH = os.environ.get("DATA_LAKE_CATALOG_PATH", "/Users/azizsunderji/Dropbox/Home Economics/Reference/data_lake_catalog.md")
