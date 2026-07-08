"""
Internal Ticket Price Lookup Tool
----------------------------------
Pick a home team and away team from dropdowns, the fixture URL is built
automatically (per-team URL rules), and you get either a single filtered
search, a full price list, or a WhatsApp-style customer message.

Each home team has its OWN category map, since every stadium has
different stand names/IDs. See TEAM_CATEGORY_MAPS below to add more clubs.

HOW TO RUN:
1. pip install flask requests beautifulsoup4
2. python app.py
3. Open your browser to http://127.0.0.1:5000
"""

from flask import Flask, request, render_template_string, session, redirect
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode
import re
import unicodedata
import datetime
import secrets
import time
import concurrent.futures

app = Flask(__name__)
# A NEW random secret key every time the server starts. Flask session cookies
# are stored in your browser, not the server -- so without this, restarting
# the app would still show your last search (e.g. "Arsenal vs Coventry")
# because the browser's old cookie would still be valid. Generating a fresh
# key each run invalidates any old cookie, so the app always starts blank.
app.secret_key = secrets.token_hex(32)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TEAMS = [
    "Arsenal",
    "Aston Villa",
    "Bournemouth",
    "Brentford",
    "Brighton and Hove Albion",
    "Chelsea",
    "Coventry City",
    "Crystal Palace",
    "Everton",
    "Fulham",
    "Hull City",
    "Ipswich Town",
    "Leeds United",
    "Liverpool",
    "Manchester City",
    "Manchester United",
    "Newcastle United",
    "Nottingham Forest",
    "Sunderland",
    "Tottenham Hotspur",
]

# Home Team dropdown is limited to clubs we actually have a category map
# for (see TEAM_CATEGORY_MAPS below). Away Team keeps the full list above,
# since any club can be the away side.
HOME_TEAMS = [
    "Arsenal",
    "Chelsea",
    "Liverpool",
    "Tottenham Hotspur",
    "Manchester United",
    "Manchester City",
]

LA_LIGA_HOME_TEAMS = [
    "Real Madrid",
    "FC Barcelona",
]

LA_LIGA_AWAY_TEAMS = [
    "Alavés",
    "Athletic Club",
    "Atlético de Madrid",
    "FC Barcelona",
    "Celta Vigo",
    "Deportivo La Coruña",
    "Elche",
    "Espanyol",
    "Getafe",
    "Levante",
    "Málaga",
    "Osasuna",
    "Racing Santander",
    "Rayo Vallecano",
    "Real Betis",
    "Real Madrid",
    "Real Sociedad",
    "Sevilla",
    "Valencia",
    "Villarreal",
]

# Quantity and "seated together" are no longer user-editable -- every search
# always uses these fixed values.
DEFAULT_QTY = "2"
DEFAULT_SEATED_TOGETHER = True

# ---------------------------------------------------------------------------
# URL-building rules (per team quirks discovered from the live site)
# ---------------------------------------------------------------------------

BASE_DOMAIN = "https://www.livefootballtickets.com/us/fixtures/"

# Some team names don't slugify cleanly. Confirmed against the live site
# where possible (Atlético de Madrid, Athletic Club, Levante); the rest of
# the Spanish teams use best-effort accent-stripped slugs -- if any of
# those turn out wrong, tell me the correct slug and I'll add it here.
SLUG_OVERRIDES = {
    "Brighton and Hove Albion": "brighton-hove-albion",
    "Atlético de Madrid": "atletico-madrid",
    "Athletic Club": "athletic-de-bilbao",
    "Levante": "levante-ud",
}


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def slugify(team_name):
    slug = strip_accents(team_name).lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


def team_slug(team_name):
    return SLUG_OVERRIDES.get(team_name, slugify(team_name))


def build_fixture_url_with_connector(home_team, away_team, connector, url_suffix):
    home_slug = team_slug(home_team)
    away_slug = team_slug(away_team)
    return f"{BASE_DOMAIN}{home_slug}-{connector}-{away_slug}-tickets-{url_suffix}.html"


def find_fixture_url(home_team, away_team, url_suffix):
    """
    Check both '-v-' and '-vs-' fixture URLs IN PARALLEL (instead of trying
    one, waiting, then trying the other) and return whichever works,
    preferring '-v-' if both do. Returns None if neither resolves.
    """
    candidates = {
        connector: build_fixture_url_with_connector(home_team, away_team, connector, url_suffix)
        for connector in ("v", "vs")
    }

    def check(connector):
        url = candidates[connector]
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            return url if resp.status_code == 200 else None
        except requests.RequestException:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = {connector: future for connector, future in
                   ((c, executor.submit(check, c)) for c in ("v", "vs"))}
        v_result = results["v"].result()
        if v_result:
            return v_result
        return results["vs"].result()


# ---------------------------------------------------------------------------
# League definitions -- the single source of truth for which home/away
# teams show up, which URL suffix to use, and which currency is "base"
# for that league (GBP for Premier League, EUR for La Liga). Note: the
# site's currency dropdown is client-side only and doesn't change what our
# scraper reads (verified), so "EUR" for La Liga means the same scraped
# number, just labeled EUR instead of GBP -- see apply_currency_to_results.
# ---------------------------------------------------------------------------

LEAGUES = {
    "premier_league": {
        "label": "Premier League",
        "home_teams": HOME_TEAMS,
        "away_teams": TEAMS,
        "url_suffix": "english-premier-league",
        "base_currency": "GBP",
        "base_symbol": "£",
        "other_rate": 5,
    },
    "la_liga": {
        "label": "La Liga",
        "home_teams": LA_LIGA_HOME_TEAMS,
        "away_teams": LA_LIGA_AWAY_TEAMS,
        "url_suffix": "spanish-la-liga",
        "base_currency": "EUR",
        "base_symbol": "€",
        # AED/SAR = raw scraped GBP price x 5, THEN converted to EUR via
        # the live rate. Since multiplication is commutative, that's
        # mathematically identical to (GBP x live_rate) x 5 -- which is
        # exactly what our code path already computes when other_rate=5.
        "other_rate": 5,
    },
}

DEFAULT_LEAGUE = "premier_league"

# ---------------------------------------------------------------------------
# Live GBP -> EUR exchange rate (for La Liga). The site's own currency
# dropdown is client-side only (verified: ?currency=eur has no effect on
# the server response), so every price we scrape is always in GBP
# regardless of league. For La Liga, we convert that GBP price to a real
# EUR amount ourselves using a live rate, instead of just relabeling the
# GBP number as EUR.
#
# Source: Frankfurter API (api.frankfurter.dev) -- free, no API key,
# tracks official European Central Bank reference rates.
# ---------------------------------------------------------------------------

GBP_EUR_FALLBACK_RATE = 1.17  # used only if the live rate fetch fails and we have no cached rate yet
RATE_CACHE_TTL_SECONDS = 6 * 60 * 60  # refresh every 6 hours

_gbp_eur_rate_cache = {"rate": None, "fetched_at": 0}


def get_gbp_to_eur_rate():
    """Return a live GBP->EUR rate, cached for a few hours. Falls back to
    the last known good rate (or a hardcoded estimate) if the API is
    unreachable, so a network hiccup here never breaks a search."""
    now = time.time()
    cached_rate = _gbp_eur_rate_cache["rate"]
    if cached_rate is not None and (now - _gbp_eur_rate_cache["fetched_at"]) < RATE_CACHE_TTL_SECONDS:
        return cached_rate

    try:
        resp = requests.get(
            "https://api.frankfurter.dev/v1/latest?base=GBP&symbols=EUR",
            timeout=8,
        )
        resp.raise_for_status()
        rate = resp.json()["rates"]["EUR"]
        _gbp_eur_rate_cache["rate"] = rate
        _gbp_eur_rate_cache["fetched_at"] = now
        return rate
    except (requests.RequestException, KeyError, ValueError):
        return cached_rate if cached_rate is not None else GBP_EUR_FALLBACK_RATE


def convert_scraped_price_to_league_base(amount, league_key):
    """The site always returns GBP. Convert that into the given league's
    own base currency: unchanged for Premier League (GBP), or a real
    live-rate conversion to EUR for La Liga."""
    if amount is None:
        return None
    if league_key == "la_liga":
        return amount * get_gbp_to_eur_rate()
    return amount

# ---------------------------------------------------------------------------
# Category maps — ONE PER HOME TEAM (each stadium has different stand IDs)
#
# Format per row: (label, [category_ids] OR None, formula OR None)
#   - [category_ids]: fetch each ID, take the cheapest non-junior price across all of them
#   - formula: (base_label, multiplier) -> computed price, e.g. ("A", 1.3) = 1.3x price of A
# ---------------------------------------------------------------------------

TEAM_CATEGORY_MAPS = {
    "Arsenal": [
        ("D",    [4],  None),
        ("D+",   [3],  None),
        ("C",    [6],  None),
        ("B",    [3],  None),
        ("A",    [5],  None),
        ("A+",   None, ("A", 1.3)),
        ("VIP",  [10], None),
        ("VIP+", [9],  None),
    ],
    "Chelsea": [
        ("D",    [28, 25, 22], None),
        ("D+",   [29, 26, 23], None),
        ("C",    [34, 31, 37], None),
        ("B",    None, ("C", 1.3)),
        ("A",    [35, 32], None),
        ("A+",   None, ("A", 1.3)),
        ("VIP",  [43], None),
    ],
    "Manchester United": [
        ("D",    [74, 72, 77], None),
        ("D+",   [75, 71, 78], None),
        ("C",    [85, 92], None),
        ("B",    [84, 91], None),
        ("A",    [83, 90, 93], None),
        ("A+",   None, ("A", 1.3)),
        ("VIP",  [98, 96], None),
    ],
    "Manchester City": [
        ("D",    [143, 115, 116, 139, 140], None),
        ("D+",   [142, 114, 138], None),
        ("C",    [136, 132, 120], None),
        ("B",    None, ("C", 1.3)),
        ("A",    [134, 130, 118], None),
        ("A+",   None, ("A", 1.3)),
        ("VIP",  [128], None),
        ("VIP+", [127], None),
        ("E",    [121, 122, 123, 124], None),
    ],
    "Liverpool": [
        ("D",    [48, 49], None),
        ("D+",   [51], None),
        ("C",    [63, 56, 60], None),
        ("B",    None, ("C", 1.3)),
        ("A",    [57, 59, 62], None),
        ("A+",   None, ("A", 1.3)),
        ("VIP+", [2676, 625, 2674, 2672, 952, 66, 67, 65], None),
        ("E",    [64], None),
    ],
    "Tottenham Hotspur": [
        ("D",    [146, 145, 148], None),
        ("D+",   [147], None),
        ("C",    [150], None),
        ("B",    None, ("C", 1.3)),
        ("A",    [151], None),
        ("A+",   None, ("A", 1.3)),
        ("VIP+", [154, 155], None),
    ],
    "Real Madrid": [
        ("D",    [12715], None),
        ("D+",   [13576, 12713], None),
        ("C",    [12711], None),
        ("B",    [12716], None),
        ("A",    [12710], None),
        ("A+",   [12712, 13575], None),
        # VIP & Hospitality (ID 12706) is priced "On Request" on the site --
        # no live numeric price to fetch, so it's intentionally left out
        # of automated pricing here.
    ],
}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ---------------------------------------------------------------------------
# Real stand/category names per team, keyed by category ID.
# Used on the "Full Price List" page so it shows actual stand names
# (e.g. "Shortside Upper Tier") instead of internal D/C/B/A labels.
# ---------------------------------------------------------------------------

TEAM_STAND_NAMES = {
    "Arsenal": {
        4: "Shortside Upper Tier",
        3: "Shortside Lower Tier",
        6: "Longside Upper Tier",
        5: "Longside Lower Tier",
        10: "Club Level Shortside",
        9: "Club Level Longside",
    },
    "Chelsea": {
        34: "Westview",
        35: "West Stand Lower",
        28: "Shortside Upper",
        29: "Shortside Lower",
        25: "Shed End Upper",
        26: "Shed End Lower",
        22: "Matthew Harding Upper",
        23: "Matthew Harding Lower",
        31: "Longside Upper",
        32: "Longside Lower",
        37: "East Stand Upper",
        43: "Hospitality East Stand Middle Tier",
    },
    "Manchester United": {
        77: "West Stand Upper",
        78: "West Stand Lower",
        96: "Sir Bobby Charlton Stand Executive",
        93: "Sir Bobby Charlton Stand",
        92: "Sir Alex Ferguson Stand 3rd Tier",
        91: "Sir Alex Ferguson Stand 2nd Tier",
        90: "Sir Alex Ferguson Stand 1st Tier",
        72: "Shortside 2nd Tier",
        71: "Shortside 1st Tier",
        85: "Longside 3rd Tier",
        84: "Longside 2nd Tier",
        83: "Longside 1st Tier",
        98: "Manchester Executive Suite",
        74: "East Stand Upper",
        75: "East Stand Lower",
    },
    "Manchester City": {
        121: "Away Section",
        122: "Away Section Lower Tier",
        123: "Away Section Middle Tier",
        124: "Away Section Upper Tier",
        134: "Colin Bell Stand Lower Tier",
        135: "Colin Bell Stand Middle Tier",
        136: "Colin Bell Stand Upper Tier",
        130: "East Stand Lower Tier",
        131: "East Stand Middle Tier",
        132: "East Stand Upper Tier",
        142: "Family Stand Lower Tier",
        143: "Family Stand Middle Tier",
        13573: "Hospitality Boxes",
        118: "Longside Lower Tier",
        119: "Longside Middle Tier",
        120: "Longside Upper Tier",
        13574: "Manager's Corner",
        114: "Shortside Lower Tier",
        115: "Shortside Middle Tier",
        116: "Shortside Upper Tier",
        138: "South Stand Lower Tier",
        139: "South Stand Middle Tier",
        140: "South Stand Upper Tier",
        128: "The 93:20 Lounge",
        2240: "Vermillion Off Site Hospitality",
        127: "VIP Longside",
        126: "VIP Shortside",
    },
    "Liverpool": {
        49: "Anfield Road Lower",
        69: "Anfield Road Middle Tier Hospitality",
        48: "Anfield Road Upper",
        64: "Away Section",
        2676: "Beat Lounge Hospitality",
        625: "Beautiful Game Hospitality",
        11436: "Brodies Lounge",
        2674: "Chemistry Lounge Hospitality",
        63: "Kenny Dalglish Upper Tier",
        62: "Kenny Dalglish Stand Lower Tier",
        65: "Longside Hospitality",
        57: "Longside Lower",
        56: "Longside Upper",
        67: "Main Stand Executive",
        59: "Main Stand Lower Tier",
        60: "Main Stand Upper Tier",
        66: "Premier Club Executive",
        2672: "The Dugout Hospitality",
        51: "The Kop",
        952: "Village Offsite Hospitality",
    },
    "Tottenham Hotspur": {
        151: "Longside Lower Tier",
        150: "Longside Upper Tier",
        147: "North Stand Lower Tier",
        146: "North Stand Middle Tier",
        145: "North Stand Upper Tier",
        155: "Premium Seats Lower Tier",
        154: "Premium Seats Middle Tier",
        153: "Premium Seats Upper Tier",
        148: "South Stand",
    },
    "Real Madrid": {
        12710: "Category 1",
        12716: "Category 1 Alta",
        12712: "Category 1 Premium",
        13575: "Category 1 Premium Lower",
        12713: "Category 2 Fondo",
        13576: "Category 2 Fondo Lower",
        12711: "Category 2 Lateral",
        12715: "Category 3",
        12706: "VIP & Hospitality",
    },
}


def build_url(base_url, qty, seated_together, category):
    params = {}
    if qty:
        params["qty"] = qty
    if seated_together:
        params["seatedTogether"] = "true"
    if category:
        params["category"] = category

    if not params:
        return base_url

    separator = "&" if "?" in base_url else "?"
    return base_url + separator + urlencode(params)


def parse_listings(html):
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    for card in soup.select('div[data-testid="ticket"]'):
        section_el = card.select_one('[data-ab-test="section"]')
        section = section_el.get_text(strip=True) if section_el else ""

        row_el = card.select_one('[data-ab-test="row"]')
        row_info = row_el.get_text(strip=True) if row_el else ""

        allocation_el = card.select_one('[data-ab-test="allocation"]')
        allocation = allocation_el.get_text(strip=True) if allocation_el else ""

        price_container = card.select_one('[data-ab-test="price-per-ticket"]')
        price_raw = ""
        if price_container:
            price_div = price_container.find("div")
            if price_div:
                price_raw = price_div.get_text(strip=True)

        currency = "GBP" if "£" in price_raw else ("AED" if "AED" in price_raw else "")
        price = "".join(c for c in price_raw if c.isdigit() or c == ".")

        restriction_els = card.select('[data-ab-test="restriction"]')
        restrictions = [r.get_text(strip=True) for r in restriction_els]
        restriction = "; ".join(restrictions)

        badge_els = card.select('[data-ab-test="badge"]')
        badges = "; ".join(b.get_text(strip=True) for b in badge_els)

        listings.append({
            "section": section,
            "row_info": row_info,
            "allocation": allocation,
            "price": price,
            "currency": currency,
            "badges": badges,
            "restriction": restriction,
            "is_junior": "junior" in restriction.lower() or "junior" in allocation.lower(),
        })

    return listings


def get_cheapest_price(base_url, qty, seated_together, category_ids):
    """
    Fetch listings across one or more category IDs and return the lowest
    non-junior price found among all of them (as (amount, currency)).
    """
    all_prices = []

    for cat_id in category_ids:
        full_url = build_url(base_url, qty, seated_together, cat_id)
        try:
            resp = requests.get(full_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            continue

        listings = parse_listings(resp.text)
        for l in listings:
            if l.get("is_junior"):
                continue
            try:
                all_prices.append((float(l["price"]), l["currency"]))
            except (ValueError, TypeError):
                continue

    if not all_prices:
        return None, None

    all_prices.sort(key=lambda p: p[0])
    return all_prices[0]  # (amount, currency)


def fetch_raw_prices(fixture_url, qty, seated_together, category_map):
    """
    Fetch the price for every UNIQUE category ID referenced anywhere in a
    team's category map -- exactly ONE network request per ID, regardless
    of how many D/C/B/A labels or stand-list rows reuse that ID.

    All requests fire IN PARALLEL (thread pool) instead of one-at-a-time,
    since each category ID is an independent HTTP request -- this is the
    main speed win for a search with 10+ stands.

    Returns {category_id: (amount, currency)}. This is the single source
    of truth all three tabs (Full Price List, Base Price, Customer Message
    Final) derive from -- see derive_* functions below, which are pure
    local computation with zero network calls.
    """
    ids = set()
    for label, cat_ids, formula in category_map:
        if cat_ids:
            ids.update(cat_ids)

    raw = {}
    if not ids:
        return raw

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(ids))) as executor:
        future_to_id = {
            executor.submit(get_cheapest_price, fixture_url, qty, seated_together, [cat_id]): cat_id
            for cat_id in ids
        }
        for future in concurrent.futures.as_completed(future_to_id):
            cat_id = future_to_id[future]
            try:
                raw[cat_id] = future.result()
            except Exception:
                raw[cat_id] = (None, None)

    return raw


def derive_price_list(raw_prices, category_map):
    """Given cached raw prices, resolve every D/C/B/A label to a final
    price -- no network calls. Used by Base Price and Customer Message Final."""
    label_prices = {}
    for label, cat_ids, formula in category_map:
        if cat_ids is not None:
            candidates = [raw_prices[c] for c in cat_ids if c in raw_prices and raw_prices[c][0] is not None]
            if candidates:
                candidates.sort(key=lambda p: p[0])
                label_prices[label] = candidates[0]
            else:
                label_prices[label] = (None, None)

    final = []
    for label, cat_ids, formula in category_map:
        if cat_ids is not None:
            amount, currency = label_prices.get(label, (None, None))
        else:
            base_label, multiplier = formula
            base_amount, base_currency = label_prices.get(base_label, (None, None))
            if base_amount is not None:
                amount = round(base_amount * multiplier, 2)
                currency = base_currency
            else:
                amount, currency = None, None
        final.append((label, amount, currency))

    return final


def floor_round_5(amount):
    """Round DOWN to the nearest multiple of 5 (e.g. 224.97 -> 220, 249.97 -> 245)."""
    if amount is None:
        return None
    return (int(amount) // 5) * 5


def derive_stand_price_list(raw_prices, home_team):
    """Given cached raw prices, return every real stand/category name and
    its price -- no network calls. Used by Full Price List."""
    names = TEAM_STAND_NAMES.get(home_team, {})
    results = []
    for cat_id, (amount, currency) in raw_prices.items():
        results.append({
            "id": cat_id,
            "name": names.get(cat_id, f"Category {cat_id}"),
            "price": amount if amount is not None else "N/A",
            "currency": currency or "",
        })

    # Cheapest first, unknowns (N/A) last
    results.sort(key=lambda r: (r["price"] == "N/A", r["price"] if r["price"] != "N/A" else 0))
    return results


def parse_fixture_meta(html):
    """Extract match date and stadium name from a fixture page."""
    soup = BeautifulSoup(html, "html.parser")
    date_text = ""
    stadium = ""

    for div in soup.select("div.text-ltg-grey-1.font-normal"):
        text = div.get_text(strip=True)
        link = div.find("a")
        if link and not stadium:
            stadium = link.get_text(strip=True)
        elif not link and not date_text and any(day in text for day in WEEKDAYS):
            date_text = text

    return date_text, stadium


def format_date_ddmmyyyy(date_text):
    """Convert 'Friday, August 21, 2026 at 08:00 PM' -> '21/08/2026'."""
    if not date_text:
        return ""
    try:
        date_part = date_text.split(" at ")[0]       # "Friday, August 21, 2026"
        date_part = date_part.split(", ", 1)[1]       # "August 21, 2026"
        dt = datetime.datetime.strptime(date_part, "%B %d, %Y")
        return dt.strftime("%d/%m/%Y")
    except (IndexError, ValueError):
        return date_text  # fall back to raw text if format is unexpected


def fetch_meta_network(fixture_url):
    """
    Pure network fetch for stadium/date -- deliberately touches NO session
    state, so it's safe to run in a worker thread alongside the price fetch.
    """
    meta_resp = requests.get(fixture_url, headers=HEADERS, timeout=15)
    meta_resp.raise_for_status()
    raw_date, stadium = parse_fixture_meta(meta_resp.text)
    match_date = format_date_ddmmyyyy(raw_date)
    return stadium, match_date


BASE_STYLE = r"""
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0b0f14">
<style>
  :root { color-scheme: dark; }
  * { -webkit-tap-highlight-color: transparent; box-sizing: border-box; }
  html, body { background: #0b0f14; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Arial, sans-serif;
    color: #e5e7eb;
    padding-top: env(safe-area-inset-top);
    padding-bottom: env(safe-area-inset-bottom);
    -webkit-font-smoothing: antialiased;
    margin: 0;
  }
  .card {
    background: #12171f;
    border: 1px solid #1f2733;
    border-radius: 20px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.35);
  }
  /* Inputs at 16px stop iOS Safari from auto-zooming in on focus */
  input, select, textarea, button { font-size: 16px; font-family: inherit; }
  select {
    -webkit-appearance: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16' fill='none'%3E%3Cpath d='M4 6l4 4 4-4' stroke='%239CA3AF' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 16px center;
  }
  input::placeholder { color: #5b6472; }
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-thumb { background: #263040; border-radius: 8px; }
  ::selection { background: #10b98155; }
  a { text-decoration: none; }
  button { cursor: pointer; }

  /* -----------------------------------------------------------------
     Static utility stylesheet (hand-written, no JS compilation step)
     Replaces the Tailwind CDN "Play" script, which recompiles every
     class from scratch via JavaScript on EVERY page load -- that
     runtime cost was the main source of the delay on simple button
     clicks (league/currency/tab switches), since this app does a full
     page reload for each of those. A plain <style> block like this
     just gets parsed by the browser instantly, no JS execution needed.
     Covers exactly the classes used across the app -- not a general
     Tailwind replacement.
     ----------------------------------------------------------------- */

  .tap { transition: transform 0.1s ease, opacity 0.1s ease; }
  .tap:active { transform: scale(0.96); opacity: 0.9; }

  /* Layout */
  .flex { display: flex; }
  .flex-col { flex-direction: column; }
  .flex-wrap { flex-wrap: wrap; }
  .flex-1 { flex: 1 1 0%; }
  .grid { display: grid; }
  .grid-cols-1 { grid-template-columns: repeat(1, minmax(0, 1fr)); }
  .grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .items-center { align-items: center; }
  .justify-center { justify-content: center; }
  .justify-end { justify-content: flex-end; }
  .block { display: block; }
  .hidden { display: none; }
  .fixed { position: fixed; }
  .inset-0 { top: 0; right: 0; bottom: 0; left: 0; }
  .z-50 { z-index: 50; }
  .overflow-hidden { overflow: hidden; }
  .w-full { width: 100%; }
  .w-10 { width: 2.5rem; }
  .w-14 { width: 3.5rem; }
  .h-10 { height: 2.5rem; }
  .h-14 { height: 3.5rem; }
  .min-h-screen { min-height: 100vh; }
  .max-w-3xl { max-width: 48rem; }
  .max-w-sm { max-width: 24rem; }
  .max-w-\[90\%\] { max-width: 90%; }
  .mx-auto { margin-left: auto; margin-right: auto; }
  .ml-auto { margin-left: auto; }

  /* Spacing */
  .gap-1 { gap: 0.25rem; }
  .gap-2 { gap: 0.5rem; }
  .gap-3 { gap: 0.75rem; }
  .gap-4 { gap: 1rem; }
  .p-4 { padding: 1rem; }
  .p-5 { padding: 1.25rem; }
  .px-3 { padding-left: 0.75rem; padding-right: 0.75rem; }
  .px-3\.5 { padding-left: 0.875rem; padding-right: 0.875rem; }
  .px-4 { padding-left: 1rem; padding-right: 1rem; }
  .px-5 { padding-left: 1.25rem; padding-right: 1.25rem; }
  .px-6 { padding-left: 1.5rem; padding-right: 1.5rem; }
  .py-1\.5 { padding-top: 0.375rem; padding-bottom: 0.375rem; }
  .py-2 { padding-top: 0.5rem; padding-bottom: 0.5rem; }
  .py-2\.5 { padding-top: 0.625rem; padding-bottom: 0.625rem; }
  .py-3 { padding-top: 0.75rem; padding-bottom: 0.75rem; }
  .py-3\.5 { padding-top: 0.875rem; padding-bottom: 0.875rem; }
  .py-6 { padding-top: 1.5rem; padding-bottom: 1.5rem; }
  .pt-3 { padding-top: 0.75rem; }
  .pt-5 { padding-top: 1.25rem; }
  .mt-1 { margin-top: 0.25rem; }
  .mt-3 { margin-top: 0.75rem; }
  .mt-4 { margin-top: 1rem; }
  .mt-5 { margin-top: 1.25rem; }
  .mb-1\.5 { margin-bottom: 0.375rem; }
  .mb-4 { margin-bottom: 1rem; }
  .mb-6 { margin-bottom: 1.5rem; }
  .mr-1 { margin-right: 0.25rem; }

  /* Typography */
  .text-2xl { font-size: 1.5rem; line-height: 2rem; }
  .text-lg { font-size: 1.125rem; line-height: 1.75rem; }
  .text-sm { font-size: 0.875rem; line-height: 1.25rem; }
  .text-xs { font-size: 0.75rem; line-height: 1rem; }
  .text-\[10px\] { font-size: 10px; }
  .text-\[11px\] { font-size: 11px; }
  .font-bold { font-weight: 700; }
  .font-semibold { font-weight: 600; }
  .font-medium { font-weight: 500; }
  .font-normal { font-weight: 400; }
  .text-center { text-align: center; }
  .text-left { text-align: left; }
  .text-right { text-align: right; }
  .uppercase { text-transform: uppercase; }
  .normal-case { text-transform: none; }
  .tracking-wide { letter-spacing: 0.025em; }
  .leading-relaxed { line-height: 1.625; }
  .whitespace-pre-line { white-space: pre-line; }
  .break-all { word-break: break-all; }

  /* Text colors */
  .text-white { color: #ffffff; }
  .text-gray-50 { color: #f9fafb; }
  .text-gray-100 { color: #f3f4f6; }
  .text-gray-200 { color: #e5e7eb; }
  .text-gray-400 { color: #9ca3af; }
  .text-gray-500 { color: #6b7280; }
  .text-emerald-400 { color: #34d399; }
  .text-emerald-950 { color: #022c22; }
  .text-red-300 { color: #fca5a5; }
  .text-red-400 { color: #f87171; }

  /* Background colors */
  .bg-emerald-400 { background-color: #34d399; }
  .bg-emerald-500 { background-color: #10b981; }
  .bg-emerald-500\/10 { background-color: rgba(16,185,129,0.1); }
  .bg-sky-500 { background-color: #0ea5e9; }
  .bg-red-500\/10 { background-color: rgba(239,68,68,0.1); }
  .bg-\[\#0b0f14\]\/80 { background-color: rgba(11,15,20,0.8); }
  .bg-\[\#0e131b\] { background-color: #0e131b; }
  .bg-\[\#12171f\] { background-color: #12171f; }
  .bg-\[\#141a23\] { background-color: #141a23; }
  .bg-\[\#161d27\] { background-color: #161d27; }
  .bg-\[\#202c33\] { background-color: #202c33; }
  .bg-\[\#2a3942\] { background-color: #2a3942; }

  /* Borders */
  .border { border-width: 1px; border-style: solid; }
  .border-4 { border-width: 4px; border-style: solid; }
  .border-\[\#1f2733\] { border-color: #1f2733; }
  .border-\[\#232b38\] { border-color: #232b38; }
  .border-\[\#2a3441\] { border-color: #2a3441; }
  .border-emerald-500\/25 { border-color: rgba(16,185,129,0.25); }
  .border-red-500\/30 { border-color: rgba(239,68,68,0.3); }
  .border-t-emerald-500 { border-top-color: #10b981; }
  .rounded-full { border-radius: 9999px; }
  .rounded-xl { border-radius: 0.75rem; }
  .rounded-2xl { border-radius: 1rem; }

  /* Divide (border between children) */
  .divide-y > :not([hidden]) ~ :not([hidden]) { border-top-width: 1px; border-top-style: solid; }
  .divide-\[\#1f2733\] > :not([hidden]) ~ :not([hidden]) { border-color: #1f2733; }

  /* Effects */
  .shadow-lg.shadow-emerald-500\/25 { box-shadow: 0 10px 15px -3px rgba(16,185,129,0.25), 0 4px 6px -4px rgba(16,185,129,0.25); }
  .shadow-md.shadow-emerald-500\/25 { box-shadow: 0 4px 6px -1px rgba(16,185,129,0.25), 0 2px 4px -2px rgba(16,185,129,0.25); }
  .shadow-md.shadow-sky-500\/25 { box-shadow: 0 4px 6px -1px rgba(14,165,233,0.25), 0 2px 4px -2px rgba(14,165,233,0.25); }
  .shadow-2xl.shadow-black\/40 { box-shadow: 0 25px 50px -12px rgba(0,0,0,0.4); }
  .shadow-lg.shadow-emerald-500\/20 { box-shadow: 0 10px 15px -3px rgba(16,185,129,0.2), 0 4px 6px -4px rgba(16,185,129,0.2); }
  .backdrop-blur-sm { backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px); }
  .transition { transition-property: color, background-color, border-color, box-shadow, transform, opacity; transition-duration: 0.15s; }
  .opacity-60 { opacity: 0.6; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .animate-spin { animation: spin 1s linear infinite; }

  /* Interactive states */
  .hover\:bg-\[\#1a212c\]:hover { background-color: #1a212c; }
  .hover\:bg-emerald-400:hover { background-color: #34d399; }
  .hover\:bg-red-500\/20:hover { background-color: rgba(239,68,68,0.2); }
  .hover\:text-gray-200:hover { color: #e5e7eb; }
  .focus\:outline-none:focus { outline: none; }
  .focus\:ring-2:focus { box-shadow: 0 0 0 2px #10b981; }
  .focus\:ring-emerald-500:focus { box-shadow: 0 0 0 2px #10b981; }
  .focus\:border-emerald-500:focus { border-color: #10b981; }

  /* Responsive (matches Tailwind's default sm: breakpoint) */
  @media (min-width: 640px) {
    .sm\:grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .sm\:grid-cols-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .sm\:p-6 { padding: 1.5rem; }
    .sm\:w-auto { width: auto; }
  }
</style>
"""

# AED/SAR are a straight multiplier on top of the league's own base
# currency (GBP for Premier League, EUR for La Liga) -- see LEAGUES above
# for the actual rate per league.

NAV_ACTIVE = "bg-emerald-500 text-white shadow-md shadow-emerald-500/25"
NAV_INACTIVE = "bg-[#141a23] text-gray-400 border border-[#232b38] hover:text-gray-200 hover:bg-[#1a212c]"

CUR_ACTIVE = "bg-sky-500 text-white shadow-md shadow-sky-500/25"
CUR_INACTIVE = "bg-[#141a23] text-gray-400 border border-[#232b38] hover:text-gray-200 hover:bg-[#1a212c]"

LEAGUE_ACTIVE = "bg-emerald-500 text-white shadow-lg shadow-emerald-500/25"
LEAGUE_INACTIVE = "bg-[#12171f] text-gray-400 border border-[#232b38] hover:text-gray-200 hover:bg-[#1a212c]"

LEAGUE_BAR = """
<div class="max-w-3xl mx-auto px-4 pt-5">
  <div class="grid grid-cols-2 gap-2">
    <form method="POST" action="/set-league">
      <input type="hidden" name="league" value="premier_league">
      <button type="submit" class="{pl_active} tap w-full py-3.5 rounded-xl text-sm font-bold transition">🏴 Premier League</button>
    </form>
    <form method="POST" action="/set-league">
      <input type="hidden" name="league" value="la_liga">
      <button type="submit" class="{laliga_active} tap w-full py-3.5 rounded-xl text-sm font-bold transition">🇪🇸 La Liga</button>
    </form>
  </div>
</div>
"""


def render_league_bar(selected_league):
    return (
        LEAGUE_BAR
        .replace("{pl_active}", LEAGUE_ACTIVE if selected_league == "premier_league" else LEAGUE_INACTIVE)
        .replace("{laliga_active}", LEAGUE_ACTIVE if selected_league == "la_liga" else LEAGUE_INACTIVE)
    )


CURRENCY_BAR = """
<div class="max-w-3xl mx-auto px-4 pt-3">
  <div class="flex items-center gap-2">
    <span class="text-[11px] text-gray-500 font-semibold uppercase tracking-wide mr-1">Currency</span>
    <form method="POST" action="/set-currency">
      <input type="hidden" name="currency" value="{base_currency}">
      <button type="submit" class="{base_active} tap px-3.5 py-1.5 rounded-full text-xs font-semibold transition">{base_currency}</button>
    </form>
    <form method="POST" action="/set-currency">
      <input type="hidden" name="currency" value="AED">
      <button type="submit" class="{aed_active} tap px-3.5 py-1.5 rounded-full text-xs font-semibold transition">AED</button>
    </form>
    <form method="POST" action="/set-currency">
      <input type="hidden" name="currency" value="SAR">
      <button type="submit" class="{sar_active} tap px-3.5 py-1.5 rounded-full text-xs font-semibold transition">SAR</button>
    </form>
  </div>
</div>
"""


def render_currency_bar(selected_currency, league_key):
    base_currency = LEAGUES[league_key]["base_currency"]
    return (
        CURRENCY_BAR
        .replace("{base_currency}", base_currency)
        .replace("{base_active}", CUR_ACTIVE if selected_currency == base_currency else CUR_INACTIVE)
        .replace("{aed_active}", CUR_ACTIVE if selected_currency == "AED" else CUR_INACTIVE)
        .replace("{sar_active}", CUR_ACTIVE if selected_currency == "SAR" else CUR_INACTIVE)
    )


def apply_currency_to_results(results, currency, league_key):
    """Convert a list of {price, currency, ...} dicts in place to the
    selected display currency. Used by Full Price List and Base Price,
    which both share this shape. Prices are always scraped in GBP; this
    first converts to the league's real base currency (live rate for
    La Liga's EUR), then applies the AED/SAR multiplier on top if needed."""
    league = LEAGUES[league_key]
    for r in results:
        if r["price"] != "N/A":
            base_amount = convert_scraped_price_to_league_base(r["price"], league_key)
            if currency == league["base_currency"]:
                r["price"] = round(base_amount, 2)
            else:
                r["price"] = round(base_amount * league["other_rate"], 2)
        r["currency"] = currency
    return results


NAV = """
<div class="max-w-3xl mx-auto px-4 pt-3">
  <div class="flex flex-wrap items-center gap-2">
    <a href="/pricelist" class="{price_active} tap px-4 py-2.5 rounded-full text-sm font-semibold transition">📋 Prices</a>
    <a href="/baseprice" class="{baseprice_active} tap px-4 py-2.5 rounded-full text-sm font-semibold transition">💰 Base</a>
    <a href="/message" class="{message_active} tap px-4 py-2.5 rounded-full text-sm font-semibold transition">💬 Message</a>
    <form method="POST" action="/reset" class="ml-auto">
      <button type="submit" class="tap px-4 py-2.5 rounded-full text-sm font-semibold transition bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30">
        ↺ Reset
      </button>
    </form>
  </div>
</div>

<div id="loadingOverlay" class="fixed inset-0 z-50 hidden items-center justify-center bg-[#0b0f14]/80 backdrop-blur-sm">
  <div class="flex flex-col items-center gap-4 px-6 text-center">
    <div class="w-14 h-14 border-4 border-emerald-500/25 border-t-emerald-500 rounded-full animate-spin"></div>
    <p class="text-gray-200 text-sm font-semibold">Fetching live prices…</p>
    <p class="text-gray-500 text-xs">This usually takes just a second</p>
  </div>
</div>


<script>
  (function () {
    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (!form.classList || !form.classList.contains('search-form')) return;
      var overlay = document.getElementById('loadingOverlay');
      if (overlay) {
        overlay.classList.remove('hidden');
        overlay.classList.add('flex');
      }
      var btn = form.querySelector('button[type=submit]');
      if (btn) {
        btn.disabled = true;
        btn.classList.add('opacity-60');
      }
    });
  })();
</script>
"""

TEAM_SELECT_FIELDS = """
<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
  <div>
    <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">Home Team 🏠</label>
    <select name="home_team" required class="w-full bg-[#0e131b] border border-[#2a3441] text-gray-100 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 transition">
      <option value="">Pick a home team</option>
      {% for team in home_teams %}
        <option value="{{ team }}" {% if team == home_team %}selected{% endif %}>{{ team }}</option>
      {% endfor %}
    </select>
  </div>
  <div>
    <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">Away Team ✈️</label>
    <select name="away_team" required class="w-full bg-[#0e131b] border border-[#2a3441] text-gray-100 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 transition">
      <option value="">Pick an away team</option>
      {% for team in teams %}
        <option value="{{ team }}" {% if team == away_team %}selected{% endif %}>{{ team }}</option>
      {% endfor %}
    </select>
  </div>
</div>

<div class="mt-3">
  <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">
    Custom URL <span class="text-gray-500 font-normal normal-case">(optional, only if needed)</span>
  </label>
  <input type="text" name="custom_url" value="{{ custom_url or '' }}" placeholder="Paste a fixture link here instead"
    class="w-full bg-[#0e131b] border border-[#2a3441] text-gray-100 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 transition">
</div>
"""

PRICELIST_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Prices 📋</title>
    """ + BASE_STYLE + """
</head>
<body class="min-h-screen">
    {{ league_bar|safe }}
    """ + NAV.replace("{price_active}", NAV_ACTIVE).replace("{baseprice_active}", NAV_INACTIVE).replace("{message_active}", NAV_INACTIVE) + """
    {{ currency_bar|safe }}

    <div class="max-w-3xl mx-auto px-4 py-6">
        <h1 class="text-2xl font-bold text-white mb-6">Every Stand, One Glance 📋</h1>

        <form method="POST" class="card search-form p-5 sm:p-6 mb-6">
            """ + TEAM_SELECT_FIELDS + """

            <button type="submit" class="tap mt-5 w-full sm:w-auto bg-emerald-500 hover:bg-emerald-400 text-white font-semibold px-6 py-3.5 rounded-xl text-sm transition shadow-lg shadow-emerald-500/20">
                Show Me the Prices →
            </button>
        </form>

        {% if error %}
            <div class="bg-red-500/10 border border-red-500/30 text-red-300 text-sm px-4 py-3 rounded-xl mb-6">⚠️ {{ error }}</div>
        {% endif %}

        {% if fixture_url %}
            <p class="text-xs text-gray-500 mb-4 break-all">🔗 {{ fixture_url }}</p>
        {% endif %}

        {% if results %}
        <div class="card overflow-hidden">
            <table class="w-full text-sm">
                <thead class="bg-[#161d27] text-gray-400 text-xs uppercase tracking-wide">
                    <tr>
                        <th class="text-left px-4 py-3.5">Stand / Section</th>
                        <th class="text-right px-4 py-3.5">Price</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-[#1f2733]">
                    {% for r in results %}
                    <tr class="{{ 'bg-emerald-500/10' if loop.first and r.price != 'N/A' else '' }}">
                        <td class="px-4 py-3.5 font-medium text-gray-200">{{ r.name }}</td>
                        <td class="px-4 py-3.5 text-right font-bold {{ 'text-emerald-400' if loop.first and r.price != 'N/A' else 'text-gray-200' }}">
                            {% if r.price == 'N/A' %}
                                <span class="text-gray-500 font-normal text-xs">N/A</span>
                            {% else %}
                                {{ r.currency }} {{ r.price }}
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}
    </div>
</body>
</html>
"""


def resolve_fixture(home_team, away_team, custom_url, url_suffix):
    """Given team names (or a custom URL), find the working fixture URL."""
    if custom_url:
        return custom_url, None
    elif home_team and away_team:
        fixture_url = find_fixture_url(home_team, away_team, url_suffix)
        if not fixture_url:
            return None, (
                f"Could not find a matching fixture page for {home_team} vs {away_team} "
                f"(tried both '-v-' and '-vs-' URL formats)."
            )
        return fixture_url, None
    else:
        return None, None


def resolve_form(form, league_key):
    home_team = form.get("home_team", "").strip()
    away_team = form.get("away_team", "").strip()
    custom_url = form.get("custom_url", "").strip()
    qty = DEFAULT_QTY
    seated_together = DEFAULT_SEATED_TOGETHER

    fixture_url, fixture_error = get_or_resolve_fixture(home_team, away_team, custom_url, league_key)

    return home_team, away_team, custom_url, qty, seated_together, fixture_url, fixture_error


def save_last_query(home_team, away_team, custom_url, qty, seated_together):
    """Remember the last search so other tabs can auto-load the same query."""
    session["home_team"] = home_team
    session["away_team"] = away_team
    session["custom_url"] = custom_url
    session["qty"] = qty
    session["seated_together"] = seated_together


def load_last_query():
    """Read back the last remembered search (or empty defaults if none yet)."""
    return {
        "home_team": session.get("home_team", ""),
        "away_team": session.get("away_team", ""),
        "custom_url": session.get("custom_url", ""),
        "qty": session.get("qty", DEFAULT_QTY),
        "seated_together": session.get("seated_together", DEFAULT_SEATED_TOGETHER),
    }


def _cache_key(home_team, fixture_url, qty, seated_together):
    return f"{home_team}|{fixture_url}|{qty}|{seated_together}"


def get_cached_raw_prices(home_team, fixture_url, qty, seated_together):
    """
    Return the cached {category_id: (amount, currency)} dict for this exact
    query if we already fetched it (e.g. from a different tab), else None.
    """
    key = _cache_key(home_team, fixture_url, qty, seated_together)
    if session.get("raw_prices_key") == key and "raw_prices" in session:
        return {int(k): tuple(v) for k, v in session["raw_prices"].items()}
    return None


def set_cached_raw_prices(home_team, fixture_url, qty, seated_together, raw_prices):
    key = _cache_key(home_team, fixture_url, qty, seated_together)
    session["raw_prices_key"] = key
    session["raw_prices"] = {str(k): list(v) for k, v in raw_prices.items()}


def get_or_fetch_raw_prices(home_team, fixture_url, qty, seated_together, category_map):
    """
    The core speed trick: check the session cache first. Only hits the
    live site (one request per unique category ID) on a genuinely new
    query -- switching tabs afterward reuses this with zero network calls.
    """
    cached = get_cached_raw_prices(home_team, fixture_url, qty, seated_together)
    if cached is not None:
        return cached

    raw = fetch_raw_prices(fixture_url, qty, seated_together, category_map)
    set_cached_raw_prices(home_team, fixture_url, qty, seated_together, raw)
    return raw


def get_or_resolve_fixture(home_team, away_team, custom_url, league_key):
    """
    Same caching trick for the fixture URL itself (the '-v-' vs '-vs-'
    connectivity check). Without this, every tab switch would re-run that
    live HTTP check even though the raw ticket prices were already cached.
    """
    url_suffix = LEAGUES[league_key]["url_suffix"]
    key = f"{league_key}|{home_team}|{away_team}|{custom_url}"
    if session.get("fixture_resolve_key") == key and "fixture_resolve_url" in session:
        return session["fixture_resolve_url"], session.get("fixture_resolve_error")

    fixture_url, fixture_error = resolve_fixture(home_team, away_team, custom_url, url_suffix)
    session["fixture_resolve_key"] = key
    session["fixture_resolve_url"] = fixture_url
    session["fixture_resolve_error"] = fixture_error
    return fixture_url, fixture_error


def get_league():
    return session.get("league", DEFAULT_LEAGUE)


@app.route("/", methods=["GET"])
def home():
    return redirect("/pricelist")


@app.route("/reset", methods=["POST"])
def reset():
    """Clear the saved search and all cached price/fixture data -- takes
    you back to a fully blank state without needing to restart the server."""
    session.clear()
    return redirect(request.referrer or "/pricelist")


@app.route("/set-league", methods=["POST"])
def set_league():
    """Switch between Premier League and La Liga. Clears the search and
    caches (home/away teams, currency, etc. all differ per league), and
    resets the display currency to that league's own base currency."""
    league = request.form.get("league", "").strip()
    if league in LEAGUES:
        session.clear()
        session["league"] = league
        session["currency"] = LEAGUES[league]["base_currency"]
    return redirect(request.referrer or "/pricelist")


def get_currency():
    league = get_league()
    return session.get("currency", LEAGUES[league]["base_currency"])


@app.route("/set-currency", methods=["POST"])
def set_currency():
    """Switch the display currency (league base currency / AED / SAR).
    This never touches the network -- prices are already cached, so it's
    an instant local recalculation (base price x rate) on whichever tab
    you're viewing."""
    league = get_league()
    valid_currencies = {LEAGUES[league]["base_currency"], "AED", "SAR"}
    currency = request.form.get("currency", "").strip().upper()
    if currency in valid_currencies:
        session["currency"] = currency
    return redirect(request.referrer or "/pricelist")


@app.route("/pricelist", methods=["GET", "POST"])
def pricelist():
    results = None
    error = None
    fixture_error = None
    league = get_league()
    currency = get_currency()

    if request.method == "POST":
        home_team, away_team, custom_url, qty, seated_together, fixture_url, fixture_error = resolve_form(request.form, league)
        save_last_query(home_team, away_team, custom_url, qty, seated_together)
        attempted = True
    else:
        last = load_last_query()
        home_team, away_team, custom_url = last["home_team"], last["away_team"], last["custom_url"]
        qty, seated_together = last["qty"], last["seated_together"]
        attempted = bool(home_team or custom_url)
        fixture_url = None
        if attempted:
            fixture_url, fixture_error = get_or_resolve_fixture(home_team, away_team, custom_url, league)

    if attempted:
        if fixture_error:
            error = fixture_error
        elif not fixture_url:
            error = "Please select both a home team and an away team, or provide a custom URL."
        elif home_team not in TEAM_CATEGORY_MAPS:
            error = f"No category map has been configured yet for {home_team}. Send me their Location dropdown IDs and I'll add it."
        else:
            try:
                category_map = TEAM_CATEGORY_MAPS[home_team]
                raw_prices = get_or_fetch_raw_prices(home_team, fixture_url, qty, seated_together, category_map)
                results = derive_stand_price_list(raw_prices, home_team)
                apply_currency_to_results(results, currency, league)

            except requests.RequestException as e:
                error = f"Could not fetch the page: {e}"

    return render_template_string(
        PRICELIST_TEMPLATE,
        results=results,
        error=error,
        teams=LEAGUES[league]["away_teams"],
        home_teams=LEAGUES[league]["home_teams"],
        configured_teams=list(TEAM_CATEGORY_MAPS.keys()),
        home_team=home_team,
        away_team=away_team,
        custom_url=custom_url,
        qty=qty,
        seated_together=seated_together,
        fixture_url=fixture_url,
        currency_bar=render_currency_bar(currency, league),
        league_bar=render_league_bar(league),
    )


# ---------------------------------------------------------------------------
# Base Price page — raw D/D+/C/B/A/A+/VIP prices, no rounding applied
# ---------------------------------------------------------------------------

BASEPRICE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Base Price 💰</title>
    """ + BASE_STYLE + """
</head>
<body class="min-h-screen">
    {{ league_bar|safe }}
    """ + NAV.replace("{price_active}", NAV_INACTIVE).replace("{baseprice_active}", NAV_ACTIVE).replace("{message_active}", NAV_INACTIVE) + """
    {{ currency_bar|safe }}

    <div class="max-w-3xl mx-auto px-4 py-6">
        <h1 class="text-2xl font-bold text-white mb-6">Your Cost Prices 💰</h1>

        <form method="POST" class="card search-form p-5 sm:p-6 mb-6">
            """ + TEAM_SELECT_FIELDS + """

            <button type="submit" class="tap mt-5 w-full sm:w-auto bg-emerald-500 hover:bg-emerald-400 text-white font-semibold px-6 py-3.5 rounded-xl text-sm transition shadow-lg shadow-emerald-500/20">
                Get Base Prices →
            </button>
        </form>

        {% if error %}
            <div class="bg-red-500/10 border border-red-500/30 text-red-300 text-sm px-4 py-3 rounded-xl mb-6">⚠️ {{ error }}</div>
        {% endif %}

        {% if fixture_url %}
            <p class="text-xs text-gray-500 mb-4 break-all">🔗 {{ fixture_url }}</p>
        {% endif %}

        {% if results %}
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {% for r in results %}
            <div class="card p-4 text-center">
                <div class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ r.label }}</div>
                <div class="text-lg font-bold text-white mt-1">
                    {% if r.price == 'N/A' %}
                        <span class="text-gray-500 text-sm">N/A</span>
                    {% else %}
                        {{ r.currency }} {{ r.price }}
                    {% endif %}
                </div>
            </div>
            {% endfor %}
        </div>
        {% endif %}
    </div>
</body>
</html>
"""


@app.route("/baseprice", methods=["GET", "POST"])
def baseprice():
    results = None
    error = None
    fixture_error = None
    league = get_league()
    currency = get_currency()

    if request.method == "POST":
        home_team, away_team, custom_url, qty, seated_together, fixture_url, fixture_error = resolve_form(request.form, league)
        save_last_query(home_team, away_team, custom_url, qty, seated_together)
        attempted = True
    else:
        last = load_last_query()
        home_team, away_team, custom_url = last["home_team"], last["away_team"], last["custom_url"]
        qty, seated_together = last["qty"], last["seated_together"]
        attempted = bool(home_team or custom_url)
        fixture_url = None
        if attempted:
            fixture_url, fixture_error = get_or_resolve_fixture(home_team, away_team, custom_url, league)

    if attempted:
        if fixture_error:
            error = fixture_error
        elif not fixture_url:
            error = "Please select both a home team and an away team, or provide a custom URL."
        elif home_team not in TEAM_CATEGORY_MAPS:
            error = f"No category map has been configured yet for {home_team}. Send me their Location dropdown IDs and I'll add it."
        else:
            try:
                category_map = TEAM_CATEGORY_MAPS[home_team]
                raw_prices = get_or_fetch_raw_prices(home_team, fixture_url, qty, seated_together, category_map)
                final = derive_price_list(raw_prices, category_map)

                results = [
                    {
                        "label": label,
                        "price": amount if amount is not None else "N/A",
                        "currency": orig_currency or "",
                    }
                    for label, amount, orig_currency in final
                ]
                apply_currency_to_results(results, currency, league)

            except requests.RequestException as e:
                error = f"Could not fetch the page: {e}"

    return render_template_string(
        BASEPRICE_TEMPLATE,
        results=results,
        error=error,
        teams=LEAGUES[league]["away_teams"],
        home_teams=LEAGUES[league]["home_teams"],
        configured_teams=list(TEAM_CATEGORY_MAPS.keys()),
        home_team=home_team,
        away_team=away_team,
        custom_url=custom_url,
        qty=qty,
        seated_together=seated_together,
        fixture_url=fixture_url,
        currency_bar=render_currency_bar(currency, league),
        league_bar=render_league_bar(league),
    )


# ---------------------------------------------------------------------------
# Customer Message page — rendered as a WhatsApp-style chat bubble
# ---------------------------------------------------------------------------

MESSAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Message 💬</title>
    """ + BASE_STYLE + """
    <style>
        .wa-bg {
            background-color: #0b141a;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'%3E%3Cg fill='%23182229' fill-opacity='0.6'%3E%3Cpath d='M11 18c3.866 0 7-3.134 7-7s-3.134-7-7-7-7 3.134-7 7 3.134 7 7 7zm48 25c3.866 0 7-3.134 7-7s-3.134-7-7-7-7 3.134-7 7 3.134 7 7 7zm-43-7c1.657 0 3-1.343 3-3s-1.343-3-3-3-3 1.343-3 3 1.343 3 3 3zm63 31c1.657 0 3-1.343 3-3s-1.343-3-3-3-3 1.343-3 3 1.343 3 3 3zM34 90c1.657 0 3-1.343 3-3s-1.343-3-3-3-3 1.343-3 3 1.343 3 3 3zm56-76c1.657 0 3-1.343 3-3s-1.343-3-3-3-3 1.343-3 3 1.343 3 3 3zM12 86c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm28-65c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm23-11c2.76 0 5-2.24 5-5s-2.24-5-5-5-5 2.24-5 5 2.24 5 5 5zm-6 60c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm29 22c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zM32 63c2.76 0 5-2.24 5-5s-2.24-5-5-5-5 2.24-5 5 2.24 5 5 5zm57-13c2.76 0 5-2.24 5-5s-2.24-5-5-5-5 2.24-5 5 2.24 5 5 5zm-9-21c1.105 0 2-.895 2-2s-.895-2-2-2-2 .895-2 2 .895 2 2 2zM60 91c1.105 0 2-.895 2-2s-.895-2-2-2-2 .895-2 2 .895 2 2 2zM35 41c1.105 0 2-.895 2-2s-.895-2-2-2-2 .895-2 2 .895 2 2 2zM12 60c1.105 0 2-.895 2-2s-.895-2-2-2-2 .895-2 2 .895 2 2 2z'/%3E%3C/g%3E%3C/svg%3E");
        }
        .wa-header { background-color: #202c33; }
        .bubble {
            background-color: #005c4b;
            border-radius: 8px;
            position: relative;
            box-shadow: 0 1px 0.5px rgba(0,0,0,0.3);
        }
        .bubble::before {
            content: "";
            position: absolute;
            top: 0;
            right: -8px;
            width: 0;
            height: 0;
            border-top: 8px solid #005c4b;
            border-right: 8px solid transparent;
        }
        .wa-tick { color: #53bdeb; }
        @keyframes pop {
            0% { transform: scale(1); }
            50% { transform: scale(1.15); }
            100% { transform: scale(1); }
        }
        .pop { animation: pop 0.2s ease; }
    </style>
</head>
<body class="min-h-screen">
    {{ league_bar|safe }}
    """ + NAV.replace("{price_active}", NAV_INACTIVE).replace("{baseprice_active}", NAV_INACTIVE).replace("{message_active}", NAV_ACTIVE) + """
    {{ currency_bar|safe }}

    <div class="max-w-3xl mx-auto px-4 py-6">
        <h1 class="text-2xl font-bold text-white mb-6">Ready to Send 💬</h1>

        <form method="POST" class="card search-form p-5 sm:p-6 mb-6">
            """ + TEAM_SELECT_FIELDS + """

            <button type="submit" class="tap mt-5 w-full sm:w-auto bg-emerald-500 hover:bg-emerald-400 text-white font-semibold px-6 py-3.5 rounded-xl text-sm transition shadow-lg shadow-emerald-500/20">
                Generate Message →
            </button>
        </form>

        {% if error %}
            <div class="bg-red-500/10 border border-red-500/30 text-red-300 text-sm px-4 py-3 rounded-xl mb-6">⚠️ {{ error }}</div>
        {% endif %}

        {% if message_text %}
        <div class="rounded-2xl overflow-hidden shadow-2xl shadow-black/40 max-w-sm mx-auto border border-[#1f2733]">
            <div class="wa-header text-white px-4 py-3.5 flex items-center gap-3">
                <div class="w-10 h-10 rounded-full bg-emerald-400 flex items-center justify-center text-emerald-950 font-bold text-sm">
                    {{ home_initial }}{{ away_initial }}
                </div>
                <div>
                    <div class="text-sm font-semibold">{{ home_team }} vs {{ away_team }}</div>
                    <div class="text-xs text-gray-400">online</div>
                </div>
            </div>

            <div class="wa-bg px-4 py-6">
                <div class="bubble px-3 py-2 max-w-[90%] ml-auto">
                    <p class="text-sm text-gray-50 whitespace-pre-line leading-relaxed">{{ message_text }}</p>
                    <div class="flex justify-end items-center gap-1 mt-1">
                        <span class="text-[10px] text-gray-400">{{ current_time }}</span>
                        <svg class="wa-tick" width="16" height="11" viewBox="0 0 16 11" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M11.071.653a.457.457 0 0 0-.304-.102.493.493 0 0 0-.381.178l-6.19 7.636-2.405-2.272a.463.463 0 0 0-.336-.146.47.47 0 0 0-.336.146l-.34.353a.47.47 0 0 0 0 .683l3.087 2.917a.47.47 0 0 0 .663 0l6.542-8.077a.5.5 0 0 0-.02-.686l-.98-.63z" fill="currentColor"/>
                            <path d="M15.071.653a.457.457 0 0 0-.304-.102.493.493 0 0 0-.381.178l-6.19 7.636-1.005-.95-.667.822 1.34 1.27a.47.47 0 0 0 .663 0l6.542-8.077a.5.5 0 0 0-.02-.686l-.978-.09z" fill="currentColor"/>
                        </svg>
                    </div>
                </div>
            </div>

            <div class="bg-[#202c33] px-4 py-3 flex items-center gap-3">
                <div class="flex-1 bg-[#2a3942] rounded-full px-4 py-2.5 text-sm text-gray-500">Type a message</div>
            </div>
        </div>

        <div class="max-w-sm mx-auto mt-4">
            <button id="copyBtn" onclick="copyMessage()"
                class="tap w-full bg-emerald-500 hover:bg-emerald-400 text-white font-semibold px-5 py-3.5 rounded-xl text-sm transition shadow-lg shadow-emerald-500/20 flex items-center justify-center gap-2">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="9" y="9" width="13" height="13" rx="2" stroke="white" stroke-width="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" stroke="white" stroke-width="2"/>
                </svg>
                Copy Message
            </button>
            <textarea id="rawMsg" class="hidden">{{ message_text }}</textarea>
        </div>

        <script>
            function copyMessage() {
                const text = document.getElementById('rawMsg').value;
                navigator.clipboard.writeText(text).then(() => {
                    const btn = document.getElementById('copyBtn');
                    const original = btn.innerHTML;
                    btn.innerHTML = '&#10003; Copied!';
                    btn.classList.add('pop');
                    setTimeout(() => {
                        btn.innerHTML = original;
                        btn.classList.remove('pop');
                    }, 1500);
                });
            }
        </script>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route("/message", methods=["GET", "POST"])
def message():
    message_text = None
    error = None
    fixture_error = None
    current_time = datetime.datetime.now().strftime("%H:%M")
    league = get_league()
    display_currency = get_currency()

    if request.method == "POST":
        home_team, away_team, custom_url, qty, seated_together, fixture_url, fixture_error = resolve_form(request.form, league)
        save_last_query(home_team, away_team, custom_url, qty, seated_together)
        attempted = True
    else:
        last = load_last_query()
        home_team, away_team, custom_url = last["home_team"], last["away_team"], last["custom_url"]
        qty, seated_together = last["qty"], last["seated_together"]
        attempted = bool(home_team or custom_url)
        fixture_url = None
        if attempted:
            fixture_url, fixture_error = get_or_resolve_fixture(home_team, away_team, custom_url, league)

    home_initial = home_team[:1].upper() if home_team else ""
    away_initial = away_team[:1].upper() if away_team else ""

    if attempted:
        if fixture_error:
            error = fixture_error
        elif not fixture_url:
            error = "Please select both a home team and an away team, or provide a custom URL."
        elif not (home_team and away_team) and custom_url:
            error = "When using a custom URL, please also select the home and away teams (used for the message header)."
        elif home_team not in TEAM_CATEGORY_MAPS:
            error = f"No category map has been configured yet for {home_team}. Send me their Location dropdown IDs and I'll add it."
        else:
            try:
                category_map = TEAM_CATEGORY_MAPS[home_team]

                meta_cached = session.get("meta_fixture_url") == fixture_url and "meta_stadium" in session
                cached_prices = get_cached_raw_prices(home_team, fixture_url, qty, seated_together)

                # Kick off whichever of (meta fetch, price fetch) actually
                # needs the network AT THE SAME TIME, instead of one after
                # the other -- session reads/writes stay in this thread.
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    meta_future = None if meta_cached else executor.submit(fetch_meta_network, fixture_url)
                    prices_future = None if cached_prices is not None else executor.submit(
                        fetch_raw_prices, fixture_url, qty, seated_together, category_map
                    )

                    if meta_cached:
                        stadium, match_date = session["meta_stadium"], session["meta_match_date"]
                    else:
                        stadium, match_date = meta_future.result()
                        session["meta_fixture_url"] = fixture_url
                        session["meta_stadium"] = stadium
                        session["meta_match_date"] = match_date

                    if cached_prices is not None:
                        raw_prices = cached_prices
                    else:
                        raw_prices = prices_future.result()
                        set_cached_raw_prices(home_team, fixture_url, qty, seated_together, raw_prices)

                final = derive_price_list(raw_prices, category_map)

                lines = []
                lines.append(f"{home_team} vs {away_team}")
                lines.append("")
                if stadium:
                    lines.append(stadium)
                if match_date:
                    lines.append(match_date)
                lines.append("")
                lines.append("جميع الاسعار للشخص الواحد و اقصاها اثنين متجاورة")
                lines.append("")

                league_info = LEAGUES[league]
                base_currency = league_info["base_currency"]
                base_symbol = league_info["base_symbol"]

                for label, amount, orig_currency in final:
                    if amount is not None:
                        base_amount = convert_scraped_price_to_league_base(amount, league)
                        marked_up = base_amount * 1.3
                        rounded = floor_round_5(marked_up)
                        if display_currency == base_currency:
                            converted = rounded
                        else:
                            converted = rounded * league_info["other_rate"]
                    else:
                        converted = None

                    if converted is None:
                        price_str = "N/A"
                    elif display_currency == base_currency:
                        price_str = f"{base_symbol}{converted:g}"
                    else:
                        price_str = f"{converted:g} {display_currency}"
                    lines.append(f"{label} {price_str}")
                lines.append("")
                lines.append("جميع الاسعار قابلة للتغيير ب اي وقت")
                lines.append("")
                lines.append("التقسيط ب تابي للارقام الاماراتية")

                message_text = "\n".join(lines)

            except requests.RequestException as e:
                error = f"Could not fetch the page: {e}"

    return render_template_string(
        MESSAGE_TEMPLATE,
        message_text=message_text,
        error=error,
        teams=LEAGUES[league]["away_teams"],
        home_teams=LEAGUES[league]["home_teams"],
        configured_teams=list(TEAM_CATEGORY_MAPS.keys()),
        home_team=home_team,
        away_team=away_team,
        custom_url=custom_url,
        qty=qty,
        seated_together=seated_together,
        fixture_url=fixture_url,
        home_initial=home_initial,
        away_initial=away_initial,
        current_time=current_time,
        currency_bar=render_currency_bar(display_currency, league),
        league_bar=render_league_bar(league),
    )


if __name__ == "__main__":
    # host="0.0.0.0" makes this reachable from other devices on your WiFi
    # (like your phone) at http://YOUR-COMPUTER-IP:5000 -- see chat for how
    # to find your computer's IP address.
    app.run(host="0.0.0.0", port=5000, debug=True)
