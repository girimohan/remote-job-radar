"""Rough language detection for postings: English, Finnish, German, or other.

Good enough to flag "this posting is written in Finnish", which usually means the job needs Finnish.
"""
from __future__ import annotations

import re

_MARKERS = {
    "en": [" the ", " and ", " you ", " with ", " our ", " for ", " to ", " of ", " we ", " is ", " are ", " will "],
    "fi": [" ja ", " on ", " sekä ", " että ", " tai ", " kanssa ", " olet ", " meillä ", " haemme ", " sinulla ",
           " myös ", " työ", " tehtäv", " osaami", " kokemus", " etsimme ", " tarjoamme "],
    "de": [" und ", " der ", " die ", " das ", " mit ", " für ", " wir ", " sie ", " ist ", " eine ", " du ", " bei ",
           " von ", " deine "],
}
_TITLE_FI = re.compile(
    r"kehittäjä|asiantuntija|insinööri|suunnittelija|konsultti|päällikkö|harjoittelija|työntekijä|ohjelmisto|"
    r"sovellus|tekoäly|koneoppimi|arkkitehti|myyjä|johtaja|vastaava|koordinaattori|analyytikko|kesätyö", re.I)
_TITLE_DE = re.compile(
    r"\(m/w/d\)|\(w/m/d\)|\(m/f/d\)|\(f/m/d\)|\(all genders\)|entwickler|ingenieur|werkstudent|praktik|berater|"
    r"leiter|mitarbeiter|sachbearbeiter|softwareentwickl", re.I)


def detect(title: str, description: str = "") -> str:
    body = f" {(description or '')[:2500].lower()} "
    if len(body) >= 200:
        counts = {lang: sum(body.count(m) for m in markers) for lang, markers in _MARKERS.items()}
        best = max(counts, key=counts.get)
        if counts[best] >= 4:
            return best
        return "other" if len(body) >= 600 else "en"
    t = title or ""
    if _TITLE_FI.search(t):
        return "fi"
    # "(m/f/d)" on its own is common on English postings from German companies, so require a German word too.
    if re.search(r"entwickler|ingenieur|werkstudent|praktik|berater|leiter|mitarbeiter|sachbearbeiter", t, re.I):
        return "de"
    return "en"
