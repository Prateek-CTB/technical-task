"""Offline language identification for short review texts (ADR 0023).

Deliberately small: a script check for non-Latin alphabets, then stop-word voting for the Latin
languages Booking.com guests most often write in. Short or ambiguous texts return ``und``
(undetermined) instead of a guess; "Location" or "Good" alone cannot be attributed reliably.
"""

from __future__ import annotations

import re
import unicodedata

METHOD = "stopwords-v1"

#: High-frequency function words; words shared by several languages are left out on purpose.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        [
            "the",
            "and",
            "was",
            "were",
            "is",
            "are",
            "very",
            "with",
            "for",
            "not",
            "but",
            "this",
            "that",
            "have",
            "had",
            "our",
            "room",
            "staff",
            "there",
            "they",
            "it's",
            "would",
            "could",
            "which",
            "from",
            "their",
            "been",
            "very",
            "great",
            "good",
            "nice",
        ]
    ),
    "fr": frozenset(
        [
            "le",
            "la",
            "les",
            "et",
            "est",
            "était",
            "très",
            "avec",
            "pour",
            "pas",
            "mais",
            "nous",
            "une",
            "des",
            "du",
            "au",
            "aux",
            "chambre",
            "c'est",
            "il",
            "elle",
            "sont",
            "été",
            "bien",
            "trop",
            "peu",
            "personnel",
            "propre",
        ]
    ),
    "de": frozenset(
        [
            "der",
            "die",
            "das",
            "und",
            "ist",
            "war",
            "sehr",
            "mit",
            "für",
            "nicht",
            "aber",
            "wir",
            "ein",
            "eine",
            "zimmer",
            "es",
            "sind",
            "auch",
            "zu",
            "gut",
            "sauber",
            "leider",
        ]
    ),
    "es": frozenset(
        [
            "el",
            "la",
            "los",
            "las",
            "y",
            "es",
            "era",
            "muy",
            "con",
            "para",
            "pero",
            "nosotros",
            "una",
            "del",
            "habitación",
            "está",
            "estaba",
            "son",
            "fue",
            "bien",
            "limpio",
            "también",
        ]
    ),
    "it": frozenset(
        [
            "il",
            "lo",
            "la",
            "gli",
            "le",
            "e",
            "è",
            "era",
            "molto",
            "con",
            "per",
            "non",
            "ma",
            "noi",
            "una",
            "della",
            "camera",
            "sono",
            "stato",
            "anche",
            "bene",
            "pulito",
        ]
    ),
    "pt": frozenset(
        [
            "o",
            "a",
            "os",
            "as",
            "e",
            "é",
            "era",
            "muito",
            "com",
            "para",
            "não",
            "mas",
            "nós",
            "uma",
            "do",
            "da",
            "quarto",
            "são",
            "foi",
            "bem",
            "também",
            "limpo",
            "ótimo",
        ]
    ),
    "nl": frozenset(
        [
            "de",
            "het",
            "een",
            "en",
            "is",
            "was",
            "erg",
            "zeer",
            "met",
            "voor",
            "niet",
            "maar",
            "wij",
            "kamer",
            "zijn",
            "ook",
            "goed",
            "schoon",
            "heel",
        ]
    ),
    "da": frozenset(
        [
            "og",
            "er",
            "var",
            "meget",
            "med",
            "for",
            "ikke",
            "men",
            "vi",
            "en",
            "et",
            "værelset",
            "værelse",
            "det",
            "der",
            "til",
            "godt",
            "også",
            "rent",
        ]
    ),
    "sv": frozenset(
        [
            "och",
            "är",
            "var",
            "mycket",
            "med",
            "för",
            "inte",
            "men",
            "vi",
            "en",
            "ett",
            "rummet",
            "rum",
            "det",
            "som",
            "till",
            "bra",
            "också",
            "rent",
        ]
    ),
}

_SCRIPTS = (
    ("zh", re.compile(r"[一-鿿]")),
    ("ja", re.compile(r"[぀-ヿ]")),
    ("ko", re.compile(r"[가-힯]")),
    ("ru", re.compile(r"[Ѐ-ӿ]")),
    ("ar", re.compile(r"[؀-ۿ]")),
    ("he", re.compile(r"[֐-׿]")),
    ("th", re.compile(r"[฀-๿]")),
    ("el", re.compile(r"[Ͱ-Ͽ]")),
)
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)


def detect_language(text: str | None) -> str:
    """ISO 639-1 code, or ``und`` when the text is too short or ambiguous."""
    if not text or not text.strip():
        return "und"
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "und"
    # Japanese uses kana as well as Han characters, so test kana before Han.
    for code, pattern in sorted(_SCRIPTS, key=lambda s: s[0] != "ja"):
        hits = len(pattern.findall(text))
        if hits and hits >= 0.3 * len(letters):
            return code
    words = [unicodedata.normalize("NFC", w.lower()) for w in _WORD.findall(text)]
    if len(words) < 3:
        return "und"
    scores = {lang: sum(w in sw for w in words) for lang, sw in _STOPWORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < 2 or top == second:
        return "und"
    return best
