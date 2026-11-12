"""Decide where a "remote" job can actually be done from.

Most postings labelled remote are restricted to one country, usually the US.
This module reads the location text first and falls back to phrases in the
description. Labels:

    finland     names Finland or the Nordics
    europe      open to Europe / EMEA / EU time zones
    worldwide   open anywhere, with no restriction found in the description
    eu_country  tied to one other European country
    us_only     United States (or North America) only
    other_region  tied to some other region (India, LATAM, APAC, ...)
    unclear     remote, but nothing says where from
    not_remote  not remote and not in Finland
"""
from __future__ import annotations

import re

from .models import Job

I = re.IGNORECASE

FINLAND = re.compile(r"\b(Finland|Suomi|Helsinki|Espoo|Vantaa|Tampere|Turku|Oulu|Nordics?|Nordic countries)\b", I)
EUROPE = re.compile(r"\b(Europe(an)?|EMEA|EEA|European Union|Eurozone)\b", I)
EUROPE_CS = re.compile(r"\b(EU|CET|CEST|EET|EEST|GMT ?\+ ?[0-3]|UTC ?\+ ?[0-3])\b")  # case-sensitive on purpose
WORLDWIDE = re.compile(r"\b(worldwide|world[- ]wide|anywhere|global(ly)?|international|all countries|any ?where in the world)\b", I)

EU_COUNTRY = re.compile(
    r"\b(United Kingdom|UK|Great Britain|England|Scotland|Ireland|Germany|Deutschland|France|Spain|Portugal|Italy|"
    r"Netherlands|Belgium|Luxembourg|Austria|Switzerland|Sweden|Norway|Denmark|Iceland|Estonia|Latvia|Lithuania|"
    r"Poland|Czechia|Czech Republic|Slovakia|Hungary|Romania|Bulgaria|Greece|Croatia|Slovenia|Serbia|Ukraine|"
    r"Cyprus|Malta)\b", I)
EU_CITY = re.compile(
    r"\b(London|Manchester|Edinburgh|Dublin|Berlin|Munich|München|Hamburg|Paris|Madrid|Barcelona|Lisbon|Porto|"
    r"Milan|Rome|Amsterdam|Rotterdam|Brussels|Vienna|Zurich|Zürich|Geneva|Stockholm|Gothenburg|Oslo|Copenhagen|"
    r"Tallinn|Riga|Vilnius|Warsaw|Krak[oó]w|Wroc[lł]aw|Prague|Budapest|Bucharest|Sofia|Athens|Belgrade|Kyiv)\b", I)

US_EXPLICIT = re.compile(r"\b(United States|USA|U\.S\.A\.?|U\.S\.?|North America|Americas|AMER|NAMER)\b|\bUS\b")
US_STATE_CODE = re.compile(
    r",\s?(AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|"
    r"ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")
# Bare state or US time-zone codes ("Remote - CA", "ET/CT/PT Remote"). DE, IN, OR, ME, OK and HI are left
# out because they collide with Germany and with ordinary words.
US_TOKEN = re.compile(
    r"(?<![A-Za-z])(AL|AK|AZ|AR|CA|CO|CT|DC|FL|GA|ID|IL|IA|KS|KY|LA|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|"
    r"PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|ET|PT)(?![A-Za-z])")
US_PLACE = re.compile(
    r"\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Hawaii|Idaho|Illinois|"
    r"Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|"
    r"Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|"
    r"Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|"
    r"Washington|West Virginia|Wisconsin|Wyoming|NYC|Indianapolis|San Francisco|Bay Area|Seattle|Boston|Austin|Chicago|Los Angeles|Denver|Atlanta|Miami|"
    r"Palo Alto|Mountain View|San Jose|San Diego|Portland|Dallas|Houston|Philadelphia|Eastern Time|Pacific Time|"
    r"EST|PST|CST|EDT|PDT)\b")
OTHER_REGION = re.compile(
    r"\b(Canada|Toronto|Vancouver|Mexico|Brazil|Argentina|Colombia|Chile|LATAM|Latin America|South America|"
    r"India|Bengaluru|Bangalore|Hyderabad|Pune|Mumbai|Delhi|Chennai|Pakistan|Philippines|Manila|Vietnam|Indonesia|"
    r"Singapore|Malaysia|Thailand|Japan|Tokyo|Korea|Seoul|China|Hong Kong|Taiwan|Australia|Sydney|Melbourne|"
    r"New Zealand|APAC|Asia|Israel|Tel Aviv|Turkey|Türkiye|Turkiye|UAE|United Arab Emirates|Dubai|Uruguay|Peru|"
    r"Costa Rica|Dominican Republic|Kazakhstan|Georgia|Armenia|Saudi Arabia|Egypt|Nigeria|Kenya|South Africa|Africa|"
    r"MENA|Middle East)\b", I)

REMOTE = re.compile(r"\b(remote|worldwide|anywhere|distributed|work from home|home[- ]based|wfh|telecommute|virtual)\b", I)
# Words that say nothing about place. If anything else is left in the location, it names
# somewhere we do not recognise, and guessing "open to you" would be wrong more often than right.
_NEUTRAL = re.compile(
    r"\b(remote|remotely|anywhere|distributed|work|from|home|based|wfh|telecommute|virtual|fully|full|flexible|hybrid|"
    r"office|optional|friendly|first|only|location|locations|multiple|various|and|or|the|in|of|hq|n/?a|tbd|job|role|"
    r"position|team|company|time|zone|zones|timezone|timezones|any|all|open|to|eligible)\b", I)
_WORD = re.compile(r"[^\W\d_]{3,}")

# Phrases in the description that override or fill in for the location field.
# These are deliberately narrow: "our customers in Europe" must not count as "open to Europe".
_US = r"(?:(?-i:US|USA|U\.S\.A?\.?)|United States(?: of America)?)"
DESC_US_ONLY = re.compile(
    rf"\b(?:must|need to|required to|should)\s+(?:be\s+)?(?:currently\s+)?(?:based|located|reside|residing|live|living)\s+in\s+the\s+{_US}"
    rf"|\b{_US}[- ]based\s+(?:candidates?|applicants?|only|remote)"
    rf"|\b(?:legally\s+)?(?:authori[sz]ed|eligible)\s+to\s+work\s+in\s+the\s+{_US}"
    rf"|\bwork\s+authori[sz]ation\s+in\s+the\s+{_US}"
    rf"|\b{_US}\s+(?:citizens?|residents?|persons?)\s+only"
    rf"|\bremote\s*[\(\-–,:]\s*{_US}(?![A-Za-z])"
    rf"|\b{_US}\s+only\b"
    rf"|\b(?:anywhere\s+in|within)\s+the\s+(?:continental\s+)?{_US}(?![A-Za-z])"
    rf"|\bsecurity\s+clearance\b", I)
DESC_EUROPE = re.compile(
    r"\bEMEA\b"
    r"|\b(?:Europe(?:an)?|EU)\s+time\s?zones?\b"
    r"|\b(?:based|located|living|reside|residing|anywhere|remote(?:ly)?)\s+(?:in|within|across|from)\s+(?:Europe|the\s+EU|the\s+European\s+Union)\b"
    r"|\b(?:Europe|EU)[- ]based\b"
    r"|\bremote\s*[\(\-–,:]\s*(?:Europe|EU|EMEA)\b"
    r"|\b(?-i:CET|CEST|EET)\b"
    r"|\b(?:UTC|GMT)\s?[+±]\s?[0-3]\b", I)
DESC_WORLDWIDE = re.compile(
    r"\b(?:work\s+from\s+anywhere|anywhere\s+in\s+the\s+world|remote\s*[\(\-–,:]?\s*(?:worldwide|global(?:ly)?)|"
    r"hir(?:e|ing)\s+(?:globally|anywhere|internationally|worldwide)|from\s+any\s+country|"
    r"any\s+time\s?zone|location[- ]agnostic)\b", I)

EUROPEAN_COUNTRIES = {
    "united kingdom", "uk", "ireland", "germany", "france", "spain", "portugal", "italy", "netherlands", "belgium",
    "luxembourg", "austria", "switzerland", "sweden", "norway", "denmark", "iceland", "estonia", "latvia",
    "lithuania", "poland", "czechia", "czech republic", "slovakia", "hungary", "romania", "bulgaria", "greece",
    "croatia", "slovenia", "serbia", "ukraine", "cyprus", "malta",
}
NORTH_AMERICA = {"united states", "usa", "us", "canada"}


def is_remote(job: Job) -> bool:
    if job.remote:
        return True
    return bool(REMOTE.search(job.location or "") or REMOTE.search(job.title or ""))


def _from_restrictions(countries: list[str]) -> tuple[str, str] | None:
    """Himalayas gives an explicit list of allowed countries; an empty list means none."""
    names = {c.strip().lower() for c in countries if c and c.strip()}
    if not names:
        return None
    if "finland" in names:
        if len(names) > 3:
            return "europe", f"Finland is one of {len(names)} allowed countries"
        return "finland", "Finland is on the allowed-country list"
    if any(EUROPE.search(c) or "emea" in c for c in names):
        return "europe", "allowed regions include Europe"
    if names <= NORTH_AMERICA:
        return "us_only", "allowed countries: " + ", ".join(sorted(names))
    europe = names & EUROPEAN_COUNTRIES
    if europe:
        return "eu_country", "allowed countries include " + ", ".join(sorted(europe)[:3])
    return "other_region", "allowed countries: " + ", ".join(sorted(names)[:3])


def _from_description(desc: str) -> tuple[str, str] | None:
    m = DESC_US_ONLY.search(desc)
    if m:
        return "us_only", f'description says "{m.group(0).strip()}"'
    m = DESC_EUROPE.search(desc)
    if m:
        return "europe", f'description mentions "{m.group(0).strip()}"'
    m = DESC_WORLDWIDE.search(desc)
    if m:
        return "worldwide", f'description says "{m.group(0).strip()}"'
    return None


def names_place(text: str) -> bool:
    """True when the text mentions a country, region or city we know."""
    return any(p.search(text) for p in (FINLAND, EUROPE, EU_COUNTRY, EU_CITY, US_EXPLICIT, US_STATE_CODE, US_PLACE,
                                        OTHER_REGION, WORLDWIDE))


def _from_title(title: str) -> tuple[str, str] | None:
    """Place names in a title. "Global"/"International" are skipped: in titles they describe the job, not the place."""
    reason = f'title "{title[:60]}"'
    if FINLAND.search(title):
        return "finland", reason
    if EUROPE.search(title) or re.search(r"\b(EU|EMEA)\b", title):
        return "europe", reason
    if US_EXPLICIT.search(title):
        return "us_only", reason
    if EU_COUNTRY.search(title) or EU_CITY.search(title):
        return "eu_country", reason
    if re.search(r"\b(LATAM|APAC|Canada|India|Brazil|Mexico|Australia|Philippines|Japan|Singapore|Argentina|Colombia)\b", title):
        return "other_region", reason
    return None


def classify(job: Job) -> tuple[str, str]:
    """Return (label, human-readable reason)."""
    loc = job.location or ""
    desc = job.description or ""

    if FINLAND.search(loc):
        # Finland as one entry in a long list of countries means "remote across Europe", not a Finland job.
        if is_remote(job) and len(set(m.lower() for m in EU_COUNTRY.findall(loc))) >= 3:
            return "europe", f'Finland is one of several countries listed: "{loc[:60]}"'
        return "finland", f'location "{loc[:60]}"'
    if not is_remote(job):
        return "not_remote", "not marked remote"

    if job.restrictions:
        hit = _from_restrictions(job.restrictions)
        if hit:
            return hit

    us_restricted = DESC_US_ONLY.search(desc)

    if EUROPE.search(loc) or EUROPE_CS.search(loc):
        return "europe", f'location "{loc[:60]}"'

    has_us = bool(US_EXPLICIT.search(loc))
    has_us_soft = bool(US_STATE_CODE.search(loc) or US_PLACE.search(loc) or US_TOKEN.search(loc))
    eu_country = EU_COUNTRY.search(loc)
    eu_city = EU_CITY.search(loc)
    other = OTHER_REGION.search(loc)

    if WORLDWIDE.search(loc) and not (has_us or eu_country or other):
        if us_restricted:
            return "us_only", f'listed as worldwide, but description says "{us_restricted.group(0).strip()}"'
        return "worldwide", f'location "{loc[:60]}"'

    if eu_country:
        return "eu_country", f'location "{loc[:60]}"'
    if has_us:
        return "us_only", f'location "{loc[:60]}"'
    if eu_city and not has_us_soft:
        return "eu_country", f'location "{loc[:60]}"'
    if has_us_soft:
        return "us_only", f'location "{loc[:60]}"'
    if other:
        return "other_region", f'location "{loc[:60]}"'

    # Free-text headers (Hacker News) carry the company and role too, so leftover words mean nothing there.
    leftover = None if job.match_full_text else _WORD.search(_NEUTRAL.sub(" ", loc))
    if leftover:
        if job.default_label:
            return job.default_label, f'location "{loc[:60]}" on a Europe-focused board'
        return "other_region", f'location "{loc[:60]}" names a place outside Europe'

    # Location is bare ("Remote") or empty. Some employers put the place in the title instead
    # ("Software Engineer - Helsinki, Finland", "Senior Engineer LATAM").
    if not job.match_full_text:
        hit = _from_title(job.title or "")
        if hit:
            return hit

    # Location is bare ("Remote") or empty: the description is all we have.
    hit = _from_description(desc)
    if hit:
        return hit
    return "unclear", "posting does not say where you can work from"
