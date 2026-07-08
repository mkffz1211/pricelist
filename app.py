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
import datetime
import secrets
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

# ---------------------------------------------------------------------------
# URL-building rules (per team quirks discovered from the live site)
# ---------------------------------------------------------------------------

BASE_DOMAIN = "https://www.livefootballtickets.com/us/fixtures/"

# Some team names don't slugify cleanly (e.g. "and" gets dropped on the site)
SLUG_OVERRIDES = {
    "Brighton and Hove Albion": "brighton-hove-albion",
}


def slugify(team_name):
    slug = team_name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


def team_slug(team_name):
    return SLUG_OVERRIDES.get(team_name, slugify(team_name))


def build_fixture_url_with_connector(home_team, away_team, connector):
    home_slug = team_slug(home_team)
    away_slug = team_slug(away_team)
    return f"{BASE_DOMAIN}{home_slug}-{connector}-{away_slug}-tickets-english-premier-league.html"


def find_fixture_url(home_team, away_team):
    """
    Check both '-v-' and '-vs-' fixture URLs IN PARALLEL (instead of trying
    one, waiting, then trying the other) and return whichever works,
    preferring '-v-' if both do. Returns None if neither resolves.
    """
    candidates = {
        connector: build_fixture_url_with_connector(home_team, away_team, connector)
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


BASE_STYLE = """
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0b0f14">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  :root { color-scheme: dark; }
  * { -webkit-tap-highlight-color: transparent; }
  html, body { background: #0b0f14; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Arial, sans-serif;
    color: #e5e7eb;
    padding-top: env(safe-area-inset-top);
    padding-bottom: env(safe-area-inset-bottom);
    -webkit-font-smoothing: antialiased;
  }
  .card {
    background: #12171f;
    border: 1px solid #1f2733;
    border-radius: 20px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.35);
  }
  /* Inputs at 16px stop iOS Safari from auto-zooming in on focus */
  input, select, textarea, button { font-size: 16px; }
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
  .tap { transition: transform 0.1s ease, opacity 0.1s ease; }
  .tap:active { transform: scale(0.96); opacity: 0.9; }
</style>
"""

# All site prices are in GBP. AED and SAR are both a straight 5x multiplier
# on the GBP price (no separate live exchange rate lookup).
CURRENCY_RATES = {
    "GBP": 1,
    "AED": 5,
    "SAR": 5,
}

NAV_ACTIVE = "bg-emerald-500 text-white shadow-md shadow-emerald-500/25"
NAV_INACTIVE = "bg-[#141a23] text-gray-400 border border-[#232b38] hover:text-gray-200 hover:bg-[#1a212c]"

CUR_ACTIVE = "bg-sky-500 text-white shadow-md shadow-sky-500/25"
CUR_INACTIVE = "bg-[#141a23] text-gray-400 border border-[#232b38] hover:text-gray-200 hover:bg-[#1a212c]"

CURRENCY_BAR = """
<div class="max-w-3xl mx-auto px-4 pt-3">
  <div class="flex items-center gap-2">
    <span class="text-[11px] text-gray-500 font-semibold uppercase tracking-wide mr-1">Currency</span>
    <form method="POST" action="/set-currency">
      <input type="hidden" name="currency" value="GBP">
      <button type="submit" class="{gbp_active} tap px-3.5 py-1.5 rounded-full text-xs font-semibold transition">GBP</button>
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


def render_currency_bar(selected_currency):
    return (
        CURRENCY_BAR
        .replace("{gbp_active}", CUR_ACTIVE if selected_currency == "GBP" else CUR_INACTIVE)
        .replace("{aed_active}", CUR_ACTIVE if selected_currency == "AED" else CUR_INACTIVE)
        .replace("{sar_active}", CUR_ACTIVE if selected_currency == "SAR" else CUR_INACTIVE)
    )


def apply_currency_to_results(results, currency):
    """Convert a list of {price, currency, ...} dicts in place to the
    selected display currency (GBP price x rate). Used by Full Price List
    and Base Price, which both share this shape."""
    rate = CURRENCY_RATES.get(currency, 1)
    for r in results:
        if r["price"] != "N/A":
            r["price"] = round(r["price"] * rate, 2)
        r["currency"] = currency
    return results


NAV = """
<div class="max-w-3xl mx-auto px-4 pt-5">
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
    """ + NAV.replace("{price_active}", NAV_ACTIVE).replace("{baseprice_active}", NAV_INACTIVE).replace("{message_active}", NAV_INACTIVE) + """
    {{ currency_bar|safe }}

    <div class="max-w-3xl mx-auto px-4 py-6">
        <h1 class="text-2xl font-bold text-white mb-1">Every Stand, One Glance 📋</h1>
        <p class="text-gray-400 text-sm mb-6">Real stand names with the cheapest live price for each. Junior-only tickets are hidden.</p>

        <form method="POST" class="card search-form p-5 sm:p-6 mb-6">
            """ + TEAM_SELECT_FIELDS + """

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4">
                <div>
                    <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">Quantity</label>
                    <input type="number" name="qty" value="{{ qty or 2 }}" min="1"
                        class="w-full bg-[#0e131b] border border-[#2a3441] text-gray-100 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 transition">
                </div>
                <div class="flex items-center">
                    <label class="flex items-center gap-3 text-sm font-medium text-gray-300 bg-[#0e131b] border border-[#2a3441] rounded-xl px-4 py-3.5 w-full cursor-pointer">
                        <input type="checkbox" name="seatedTogether" {% if seated_together %}checked{% endif %} class="w-5 h-5 rounded accent-emerald-500">
                        Seated together
                    </label>
                </div>
            </div>

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


def resolve_fixture(home_team, away_team, custom_url):
    """Given team names (or a custom URL), find the working fixture URL."""
    if custom_url:
        return custom_url, None
    elif home_team and away_team:
        fixture_url = find_fixture_url(home_team, away_team)
        if not fixture_url:
            return None, (
                f"Could not find a matching fixture page for {home_team} vs {away_team} "
                f"(tried both '-v-' and '-vs-' URL formats)."
            )
        return fixture_url, None
    else:
        return None, None


def resolve_form(form):
    home_team = form.get("home_team", "").strip()
    away_team = form.get("away_team", "").strip()
    custom_url = form.get("custom_url", "").strip()
    qty = form.get("qty", "2").strip()
    seated_together = form.get("seatedTogether") == "on"

    fixture_url, fixture_error = get_or_resolve_fixture(home_team, away_team, custom_url)

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
        "qty": session.get("qty", "2"),
        "seated_together": session.get("seated_together", False),
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


def get_or_resolve_fixture(home_team, away_team, custom_url):
    """
    Same caching trick for the fixture URL itself (the '-v-' vs '-vs-'
    connectivity check). Without this, every tab switch would re-run that
    live HTTP check even though the raw ticket prices were already cached.
    """
    key = f"{home_team}|{away_team}|{custom_url}"
    if session.get("fixture_resolve_key") == key and "fixture_resolve_url" in session:
        return session["fixture_resolve_url"], session.get("fixture_resolve_error")

    fixture_url, fixture_error = resolve_fixture(home_team, away_team, custom_url)
    session["fixture_resolve_key"] = key
    session["fixture_resolve_url"] = fixture_url
    session["fixture_resolve_error"] = fixture_error
    return fixture_url, fixture_error


@app.route("/", methods=["GET"])
def home():
    return redirect("/pricelist")


@app.route("/reset", methods=["POST"])
def reset():
    """Clear the saved search and all cached price/fixture data -- takes
    you back to a fully blank state without needing to restart the server."""
    session.clear()
    return redirect(request.referrer or "/pricelist")


def get_currency():
    return session.get("currency", "GBP")


@app.route("/set-currency", methods=["POST"])
def set_currency():
    """Switch the display currency (GBP/AED/SAR). This never touches the
    network -- prices are already cached, so it's an instant local
    recalculation (GBP price x rate) on whichever tab you're viewing."""
    currency = request.form.get("currency", "GBP").strip().upper()
    if currency in CURRENCY_RATES:
        session["currency"] = currency
    return redirect(request.referrer or "/pricelist")


@app.route("/pricelist", methods=["GET", "POST"])
def pricelist():
    results = None
    error = None
    fixture_error = None
    currency = get_currency()

    if request.method == "POST":
        home_team, away_team, custom_url, qty, seated_together, fixture_url, fixture_error = resolve_form(request.form)
        save_last_query(home_team, away_team, custom_url, qty, seated_together)
        attempted = True
    else:
        last = load_last_query()
        home_team, away_team, custom_url = last["home_team"], last["away_team"], last["custom_url"]
        qty, seated_together = last["qty"], last["seated_together"]
        attempted = bool(home_team or custom_url)
        fixture_url = None
        if attempted:
            fixture_url, fixture_error = get_or_resolve_fixture(home_team, away_team, custom_url)

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
                apply_currency_to_results(results, currency)

            except requests.RequestException as e:
                error = f"Could not fetch the page: {e}"

    return render_template_string(
        PRICELIST_TEMPLATE,
        results=results,
        error=error,
        teams=TEAMS,
        home_teams=HOME_TEAMS,
        configured_teams=list(TEAM_CATEGORY_MAPS.keys()),
        home_team=home_team,
        away_team=away_team,
        custom_url=custom_url,
        qty=qty,
        seated_together=seated_together,
        fixture_url=fixture_url,
        currency_bar=render_currency_bar(currency),
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
    """ + NAV.replace("{price_active}", NAV_INACTIVE).replace("{baseprice_active}", NAV_ACTIVE).replace("{message_active}", NAV_INACTIVE) + """
    {{ currency_bar|safe }}

    <div class="max-w-3xl mx-auto px-4 py-6">
        <h1 class="text-2xl font-bold text-white mb-1">Your Cost Prices 💰</h1>
        <p class="text-gray-400 text-sm mb-6">Raw D / D+ / C / B / A / A+ / VIP, exactly as fetched — no markup, no rounding.</p>

        <form method="POST" class="card search-form p-5 sm:p-6 mb-6">
            """ + TEAM_SELECT_FIELDS + """

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4">
                <div>
                    <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">Quantity</label>
                    <input type="number" name="qty" value="{{ qty or 2 }}" min="1"
                        class="w-full bg-[#0e131b] border border-[#2a3441] text-gray-100 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 transition">
                </div>
                <div class="flex items-center">
                    <label class="flex items-center gap-3 text-sm font-medium text-gray-300 bg-[#0e131b] border border-[#2a3441] rounded-xl px-4 py-3.5 w-full cursor-pointer">
                        <input type="checkbox" name="seatedTogether" {% if seated_together %}checked{% endif %} class="w-5 h-5 rounded accent-emerald-500">
                        Seated together
                    </label>
                </div>
            </div>

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
    currency = get_currency()

    if request.method == "POST":
        home_team, away_team, custom_url, qty, seated_together, fixture_url, fixture_error = resolve_form(request.form)
        save_last_query(home_team, away_team, custom_url, qty, seated_together)
        attempted = True
    else:
        last = load_last_query()
        home_team, away_team, custom_url = last["home_team"], last["away_team"], last["custom_url"]
        qty, seated_together = last["qty"], last["seated_together"]
        attempted = bool(home_team or custom_url)
        fixture_url = None
        if attempted:
            fixture_url, fixture_error = get_or_resolve_fixture(home_team, away_team, custom_url)

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
                apply_currency_to_results(results, currency)

            except requests.RequestException as e:
                error = f"Could not fetch the page: {e}"

    return render_template_string(
        BASEPRICE_TEMPLATE,
        results=results,
        error=error,
        teams=TEAMS,
        home_teams=HOME_TEAMS,
        configured_teams=list(TEAM_CATEGORY_MAPS.keys()),
        home_team=home_team,
        away_team=away_team,
        custom_url=custom_url,
        qty=qty,
        seated_together=seated_together,
        fixture_url=fixture_url,
        currency_bar=render_currency_bar(currency),
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
    """ + NAV.replace("{price_active}", NAV_INACTIVE).replace("{baseprice_active}", NAV_INACTIVE).replace("{message_active}", NAV_ACTIVE) + """
    {{ currency_bar|safe }}

    <div class="max-w-3xl mx-auto px-4 py-6">
        <h1 class="text-2xl font-bold text-white mb-1">Ready to Send 💬</h1>
        <p class="text-gray-400 text-sm mb-6">Your customer-facing price list, marked up and rounded, previewed exactly as it'll look in chat.</p>

        <form method="POST" class="card search-form p-5 sm:p-6 mb-6">
            """ + TEAM_SELECT_FIELDS + """

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4">
                <div>
                    <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">Quantity</label>
                    <input type="number" name="qty" value="{{ qty or 2 }}" min="1"
                        class="w-full bg-[#0e131b] border border-[#2a3441] text-gray-100 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 transition">
                </div>
                <div class="flex items-center">
                    <label class="flex items-center gap-3 text-sm font-medium text-gray-300 bg-[#0e131b] border border-[#2a3441] rounded-xl px-4 py-3.5 w-full cursor-pointer">
                        <input type="checkbox" name="seatedTogether" {% if seated_together %}checked{% endif %} class="w-5 h-5 rounded accent-emerald-500">
                        Seated together
                    </label>
                </div>
            </div>

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
    display_currency = get_currency()

    if request.method == "POST":
        home_team, away_team, custom_url, qty, seated_together, fixture_url, fixture_error = resolve_form(request.form)
        save_last_query(home_team, away_team, custom_url, qty, seated_together)
        attempted = True
    else:
        last = load_last_query()
        home_team, away_team, custom_url = last["home_team"], last["away_team"], last["custom_url"]
        qty, seated_together = last["qty"], last["seated_together"]
        attempted = bool(home_team or custom_url)
        fixture_url = None
        if attempted:
            fixture_url, fixture_error = get_or_resolve_fixture(home_team, away_team, custom_url)

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
                rate = CURRENCY_RATES.get(display_currency, 1)
                for label, amount, orig_currency in final:
                    if amount is not None:
                        marked_up = amount * 1.3
                        rounded = floor_round_5(marked_up)
                        converted = rounded * rate
                    else:
                        converted = None

                    if converted is None:
                        price_str = "N/A"
                    elif display_currency == "GBP":
                        price_str = f"£{converted:g}"
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
        teams=TEAMS,
        home_teams=HOME_TEAMS,
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
        currency_bar=render_currency_bar(display_currency),
    )


if __name__ == "__main__":
    # host="0.0.0.0" makes this reachable from other devices on your WiFi
    # (like your phone) at http://YOUR-COMPUTER-IP:5000 -- see chat for how
    # to find your computer's IP address.
    app.run(host="0.0.0.0", port=5000, debug=True)
