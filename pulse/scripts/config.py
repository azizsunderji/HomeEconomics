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
GOOGLE_NEWS_QUERIES = [
    # Macro topic queries (national scope)
    "federal reserve interest rates",
    "inflation shelter costs",
    "US housing starts",
    "US mortgage rates",
    "national housing market",
    "Case-Shiller home prices",
    "existing home sales national",
    "new home sales national",
    # Publication-specific queries (guarantees national sources)
    "site:wsj.com real estate OR housing OR mortgage",
    "site:nytimes.com real estate OR housing OR rent",
    "site:bloomberg.com housing OR mortgage OR real estate",
    "site:ft.com housing OR property OR mortgage",
    "site:reuters.com housing OR mortgage OR real estate",
    "site:washingtonpost.com housing OR real estate OR rent",
    "site:economist.com housing OR property",
    "site:cnbc.com housing OR mortgage OR real estate",
    "site:fortune.com housing OR real estate",
    "site:vox.com housing OR rent",
    # ALL reporters from press contacts list (PressEmailsOnly.csv) — 100+ journalists
    # WSJ
    '"Will Parker" housing OR real estate',
    '"Veronica Dagher" housing OR real estate',
    '"Sarah Paynter" housing OR real estate',
    '"Rebecca Picciotto" housing OR real estate',
    '"Nancy Keates" housing OR real estate',
    '"Konrad Putzier" housing OR real estate',
    '"Katherine Clarke" housing OR real estate',
    '"Kate King" housing OR real estate',
    '"Nicole Friedman" housing OR real estate',
    '"Eliot Brown" housing OR real estate',
    '"E.B. Solomont" housing OR real estate',
    '"Deborah Acosta" housing OR real estate',
    '"Candace Taylor" housing OR real estate',
    '"Jon Hilsenrath" economy OR fed',
    '"James Mackintosh" economy OR markets',
    '"Aaron Kuriloff" housing OR real estate',
    '"Craig Karmin" housing OR real estate',
    # NYT
    '"Conor Dougherty" housing OR real estate',
    '"Emily Badger" housing OR cities',
    '"Debra Kamin" real estate',
    '"Stefanos Chen" real estate',
    '"Ronda Kaysen" real estate',
    '"Mihir Zaveri" housing OR real estate',
    '"Keiko Morris" housing OR real estate',
    '"Talmon Smith" housing OR economy',
    # Bloomberg
    '"Patrick Clark" housing OR real estate',
    '"Prashant Gopal" housing OR mortgage',
    '"Alexandre Tanzi" economy',
    '"Noah Buhayar" housing OR insurance',
    '"Enda Curran" economy',
    '"Jonathan Levin" economy OR housing',
    '"Natalie Wong" housing OR real estate',
    '"Caleb Zachariahs" housing OR real estate',
    '"Tracy Chui" housing OR real estate',
    '"Jordan Karl" housing OR real estate',
    # Washington Post
    '"Rachel Siegel" economy OR housing',
    '"Marissa Lang" housing OR real estate',
    '"Aaron Wiener" housing OR real estate',
    # FT / Economist
    '"John Burn-Murdoch" economy OR housing',
    '"Archie Hall" housing OR economy',
    # CNBC / Fortune / Axios / Semafor
    '"Lance Lambert" housing OR real estate',
    '"Nick Timiraos" fed OR mortgage OR rates',
    '"Sami Sparber" housing OR real estate',
    '"Jason Capelouto" housing OR real estate',
    '"Priyanka Rao" housing OR real estate',
    # Vox / NY Mag / Newsweek
    '"Kim Velsey" housing OR real estate',
    '"Giulia Carbonaro" housing OR real estate',
    '"Rachel Cohen Booth" housing OR real estate',
    '"Kriston Capps" housing OR real estate',
    # ProPublica / USA Today / MarketWatch
    '"Joel Jacobs" housing OR real estate',
    '"Anny Riquier" housing OR real estate',
    '"Aarthi Swaminathan" housing OR real estate',
    # LA Times / SF Chronicle / Seattle Times / Boston Globe
    '"Jack Flemming" housing OR real estate',
    '"Andrew Khouri" housing OR real estate',
    '"Roland Li" housing OR real estate',
    '"JK Dineen" housing OR real estate',
    '"Hannah Groover" housing OR real estate',
    '"Timothy Logan" housing OR real estate',
    '"Catherine Carlock" housing OR real estate',
    '"Andrew Brinker" housing OR real estate',
    # Crain's / The Real Deal / Bisnow
    '"Julianne Cuba" housing OR real estate',
    '"Eddie Small" housing OR real estate',
    '"Rachel Herzog" housing OR real estate',
    '"Dennis Rodkin" housing OR real estate',
    '"Sasha Jones" housing OR real estate',
    '"Sheridan Wall" real estate',
    '"Abigail Nehring" real estate',
    '"Rich Bockmann" real estate',
    # Other regional / trade
    '"Rebecca Schuetz" housing OR real estate',
    '"Matt Reynolds" housing OR real estate',
    '"Mia Ryder-Marks" housing OR real estate',
    '"Rebecca Liebson" housing OR real estate',
    '"Paul Blennerhassett" housing OR real estate',
    '"Michaelle Bond" housing OR real estate',
    '"Jeff Bach" housing OR real estate',
    '"Olivera Perkins" housing OR real estate',
    # CoStar
    '"Candace Carlisle" housing OR real estate',
    '"Kathleen Brooks" housing OR real estate',
    '"Mark Ritter" housing OR real estate',
    '"Rori Kelner" housing OR real estate',
    # Think tanks / research
    '"Jenny Schuetz" housing OR real estate',
    '"Orphe Divounguy" housing OR real estate',
    '"Daryl Fairweather" housing OR real estate',
    '"Chen Zhao" housing OR real estate',
    '"Jeff Tucker" housing OR real estate',
    # HousingWire / Inman / other industry
    '"Sarah Wheeler" housing OR real estate',
    '"Andrea Brambila" housing OR real estate',
    '"Les Shaver" housing OR real estate',
    '"Rob Binsacca" housing OR real estate',
    '"Lew Sichelman" housing OR real estate',
    '"Byron Lazine" housing OR real estate',
    # Freelance / Gmail journalists
    '"Joyce Cohen" housing OR real estate',
    '"Julie Lasky" housing OR real estate',
    '"Emily Flitter" housing OR real estate',
    # Canadian press
    '"Rachelle Younglai" housing OR real estate',
    '"Carolyn Ireland" housing OR real estate',
    '"Maria Vega" housing OR real estate',
    # Radio / public media
    '"Audrey McGlinchy" housing OR real estate',
    '"David Brand" housing OR real estate',
    # Other
    '"Alcynna Lloyd" housing OR real estate',
    '"Joey Rodriguez" housing OR real estate',
    '"Lauren Hepler" housing OR real estate',
    '"Ben Christopher" housing OR real estate',
    '"Sam Turner" housing OR real estate',
]

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
    ("Nick Timiraos", "https://nicktimiraos.substack.com/feed"),
    ("Full Stack Economics", "https://fullstackeconomics.com/feed"),
    ("Employ America", "https://employamerica.substack.com/feed"),
    ("Matthew Klein - The Overshoot", "https://theovershoot.co/feed"),
    ("Conor Sen", "https://conorsen.substack.com/feed"),
    ("Odd Lots (Bloomberg)", "https://oddlots.substack.com/feed"),
    ("Tracy Alloway", "https://tracyalloway.substack.com/feed"),
    ("Ernie Tedeschi", "https://ernietedeschi.substack.com/feed"),
    ("Jason Furman", "https://jasonfurman.substack.com/feed"),
]

# ── Bluesky configuration ────────────────────────────────────────────────────
# Primary strategy: follow specific housing/econ accounts (no auth needed)
# Only accounts verified as active on Bluesky with recent posts
BLUESKY_ACCOUNTS = [
    # Also tracked on Twitter (cross-platform convergence)
    "calculatedrisk.bsky.social",       # Bill McBride — housing data
    "loganmohtashami.bsky.social",      # Logan Mohtashami — housing wire
    "conorsen.bsky.social",             # Conor Sen — housing/macro
    "nicktimiraos.bsky.social",         # Nick Timiraos — WSJ Fed/rates
    "mattyglesias.bsky.social",         # Matt Yglesias — housing policy
    "jasonfurman.bsky.social",          # Jason Furman — macro/policy
    "apricitas.bsky.social",            # Joseph Politano — econ data
    # Bluesky-primary voices (NOT on Twitter list — unique signal)
    "jennyschuetz.bsky.social",         # Jenny Schuetz — Arnold Ventures VP of Housing
    "markzandi.bsky.social",            # Mark Zandi — Moody's chief economist
    "hatethegamebook.com",              # Daryl Fairweather — Redfin chief economist
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
BLUESKY_SEARCH_TERMS = [
    "housing market",
    "mortgage rates",
    "home prices",
    "rent crisis",
    "housing affordability",
    "housing bubble",
    "#housingmarket",
]

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
    # Full follow list — economists, housing analysts, journalists, policy voices
    "_brianpotter", "aaronAcarr", "aaronsojourner", "abcampbell",
    "AFineBlogger", "AGampel1", "AlecStapp", "amandafung",
    "americanhousing", "andrewrsorkin", "annastansbury", "arindube",
    "arpitrage", "ArthurKolchak", "BasilHalperin",
    "bcheungz", "ben_moll", "besttrousers", "biancoresearch",
    "BillMcBride4", "Birdyword", "bobbyfijan", "BobKnakal",
    "boes_", "brendanwhitsitt", "Brian_J_Berger", "BruceEHansen",
    "ByKyleCampbell", "calculatedrisk", "CandaceETaylor",
    "CarolWalshReal1", "CathieDWood", "cepr_org", "ChrisANeilson",
    "Claudia_Sahm", "ClausVistesen", "commobserver",
    "ConorSen", "cremieuxrecueil", "dandolfa", "darioperkins",
    "DavidBeckworth", "DavidFBrand", "DiMartinoBooth", "DKThomp",
    "dmlevitt", "DominicFrisby", "donweinland", "DrTCJ",
    "DSORennie", "economeager", "Economesteter",
    "economics", "EconTalker", "EconUCL", "EdwardGLuce",
    "elerianm", "eraborsky",
    "ErnestTedeschi", "fed_speak", "FedericoHuneeus", "florianederer",
    "FlorinBilbiie", "FullStackEcon", "fwred", "gandhisahil",
    "Geiger_Capital", "GRechMacro", "HannoLustig",
    "hiromichimizuno", "hitsamty", "HomeLoanBill", "instrumenthull",
    "IvanWerning", "jasaborsky", "jasonfurman", "jburnmurdoch",
    "JedKolko", "jenniferdoleac", "JFischetti", "jfjimenoserrano",
    "jmwooldridge", "JoeMLyman", "johanknorberg", "JohnHCochrane",
    "jonathanmiller", "jondr44", "JonSteinsson", "JosephPolitano",
    "JustinWolfers", "keegan_tweets", "KiraboJackson", "LanceRLambert",
    "LawrenceLepard", "leadlagreport", "LevyAntoine", "Lingling_Wei",
    "LizAnnSonders", "LoganMohtashami", "loud_socialist", "LucaFornaro3",
    "LynAldenContact", "M_McDonough", "MacroAlf", "MarcGoldwein",
    "mark_dow", "markets", "MarkusEconomist", "mateosfo",
    "matthewclifford", "mattkahn1966", "mattyglesias", "maxdubler",
    "MichaelMOTTCM", "ModeledBehavior", "moorehn", "MoreBirths",
    "MTabarrok", "NateSilver538", "nberpubs", "NewsLambert",
    "nfergus", "NickTimiraos", "Noahpinion", "ojblanchard1",
    "p_ferragu", "palladiummag", "pedrohcgs", "PeterSchiff",
    "Ph_Aghion", "PradyuPrasad", "ProfJAParker", "profstonge",
    "R2Rsquared", "RaoulGMI", "RayDalio", "RenMacLLC",
    "RickPalaciosJr", "RickRieder", "rileymeik", "robin_j_brooks",
    "robinhanson", "rvitoria", "RyanLEllis", "S_Stantcheva",
    "saumjha", "shehzadhqazi", "skorusARK",
    "slatestarcodex", "stat_sherpa", "stevecuozzo", "SteveRattner",
    "suani_pilau", "TashaARK", "tashecon", "TaviCosta",
    "TenantBloc", "TheStalwart", "trdny", "trq212",
    "UrbanDigs", "urbanistvc",
    "wintonARK", "wwwojtekk", "xurbanxcowboyx", "YaleEconomics",
    "YIMBYLAND", "ZouhairABH",
    # Individual voices requested
    "ezraklein", "jabornesworth",
]

TWITTER_MIN_LIKES = 5    # Low threshold — these are curated voices, not keyword search
TWITTER_MAX_PER_QUERY = 60  # Per batch; more results = better coverage of quiet accounts
TWITTER_DAILY_BUDGET_CENTS = 400  # $4/day max Apify spend (6 batches + sweep × $0.16 × 2 runs/day)

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
