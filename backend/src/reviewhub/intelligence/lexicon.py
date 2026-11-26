"""Rule-based (lexicon) review analyzer: offline, deterministic and explainable (ADR 0005, 0023).

Why it exists: the LLM path depends on an external, rate-limited provider and on a data-protection
decision (D7). The lexicon always runs, needs no key, and gives every review a topic analysis.
An LLM analysis, where one exists, takes precedence over it (see ``service.PRECEDENCE``).

How it works, in order:
1. Booking.com asks two separate questions, so the field carries polarity: text in "liked" starts
   positive, text in "disliked" starts negative, the title starts neutral. Filler answers
   ("Nothing", "N/A") are ignored.
2. Each field is split into clauses (sentences, commas, "but"/"however"...). Evidence is always
   the exact clause text, so it passes the same verbatim check as LLM evidence.
3. Topic cues (multilingual word lists) decide which of the eight topics a clause is about. Some
   cues carry their own polarity ("dirty", "quiet", "overpriced"); generic cues ("room", "area")
   are weak and dropped when a clause also has a specific cue ("room was dirty" is cleanliness).
4. Clause polarity = field polarity + cue polarity + general sentiment words, with negation
   ("not clean", "no lift", "no complaints") flipping or creating polarity.

Known limits (documented in the README): sarcasm, comparisons, long multi-topic sentences,
languages outside the word lists, and spelling mistakes. No accuracy is claimed without a
human-labelled evaluation set (ADR 0014).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from reviewhub.ingestion.text import is_filler

from .language import detect_language
from .providers import ProviderResponse

MODEL = "lexicon-v1"

# --------------------------------------------------------------------------- vocabulary
# Each entry: (inherent polarity, weak?, terms). A trailing "*" matches any word ending.
_SPEC: dict[str, list[tuple[int, bool, str]]] = {
    "cleanliness": [
        (
            0,
            False,
            "cleanliness hygiene hygienic housekeeping cleaning cleaner* cleaned "
            "propreté sauberkeit limpieza pulizia limpeza netheid renlighed rengøring* städning",
        ),
        (
            1,
            False,
            "clean spotless tidy immaculate propre propres sauber limpio limpia pulito pulita "
            "limpo limpa schoon rent ren",
        ),
        (
            -1,
            False,
            "dirty dirt filthy dust dusty stain* smell* stink* stinky "
            "odour odor mould* mold* moldy hairs hair_on hair_in hair_everywhere cockroach* roach* bedbug* bed_bug* "
            "bugs insects ants pest* rubbish trash grime grimy sticky gross disgusting "
            "unclean untidy messy vomit urine poo filth sale sales saleté odeur poussière "
            "cafard* punaise* schmutzig dreckig geruch staub sucio sucia suciedad olor "
            "cucaracha* sporco sporca sporcizia puzza sujo suja vies vuil stank beskidt "
            "beskidte møg snavset lort smutsig skitten",
        ),
    ],
    "check_in": [
        (
            0,
            False,
            "check-in check_in checkin check-out check_out checkout arrival arrive* "
            "key keys keycard* key_card* door_code lockbox lock_box pin_code self_check* "
            "reception front_desk deposit bond late_arrival "
            "early_check* luggage_storage bag_drop arrivée enregistrement clé clés clef "
            "anreise schlüssel einchecken rezeption llegada llave llaves recepción arrivo "
            "chiave chiavi chegada chave inchecken sleutel indtjekning nøgle nøglen",
        ),
    ],
    "staff": [
        (
            0,
            False,
            "staff staffs employee* receptionist* manager* management host hosts owner* "
            "team crew personnel worker* customer_service service guy lady "
            "équipe réceptionniste accueil personal mitarbeiter gastgeber recepcionista "
            "atención personale funcionário* atendimento personeel medewerker* personale "
            "personalet personalen",
        ),
        (
            1,
            False,
            "friendly helpful welcoming polite courteous attentive accommodating "
            "hospitable professional sympathique aimable serviable freundlich "
            "hilfsbereit amable simpático simpatico gentile disponibile prestativo "
            "vriendelijk behulpzaam venlig hjælpsom hjälpsam trevlig",
        ),
        (
            -1,
            False,
            "rude unfriendly unhelpful impolite unprofessional arrogant dismissive "
            "aggressive hostile condescending impoli désagréable unfreundlich "
            "unhöflich maleducado grosero scortese maleducato onvriendelijk uvenlig "
            "otrevlig",
        ),
    ],
    "noise": [
        (
            0,
            False,
            "sound sounds soundproof* hear heard hearing earplug* music party partying "
            "traffic thin_walls walls_are_thin geluid ljud",
        ),
        (
            1,
            False,
            "quiet quietly peaceful silent calme silencieux silencieuse ruhig leise "
            "tranquilo tranquila silencioso silenciosa tranquillo silenzioso sossegado "
            "rustig stille rolig lugn tyst",
        ),
        (
            -1,
            False,
            "noise noises noisy loud loudly snoring snore* construction drilling banging "
            "slamming screaming shouting yelling bruit bruits bruyant bruyante lärm laut "
            "ruido ruidoso ruidosa rumore rumori rumoroso rumorosa barulho barulhento "
            "lawaai gehorig støj støjende larm buller",
        ),
    ],
    "facilities": [
        (
            0,
            False,
            "kitchen kitchenette wifi wi-fi internet lift lifts elevator* laundry "
            "washing_machine* dryer* locker lockers common_area* common_room* lounge "
            "rooftop roof_top terrace pool gym parking car_park breakfast coffee "
            "microwave dinner meal meals food hair_dryer hairdryer shared_bathroom* shared_toilet* shared_shower* bathrooms toilets "
            "showers facilities amenities stairs staircase luggage bar restaurant towels "
            "toiletries vending cuisine petit-déjeuner petit_déjeuner repas ascenseur "
            "laverie salle_commune küche frühstück aufzug fahrstuhl wlan waschmaschine "
            "parkplatz cocina desayuno ascensor lavandería aparcamiento cucina colazione "
            "ascensore lavanderia parcheggio cozinha pequeno-almoço café_da_manhã "
            "elevador keuken ontbijt køkken køkkenet morgenmad fællestoilet* "
            "fællesbad* kök frukost hiss",
        ),
    ],
    "location": [
        (
            0,
            True,
            "area city centre center cbd neighbourhood neighborhood surroundings quartier zone",
        ),
        (
            0,
            False,
            "location located position situated station train trains bus buses "
            "transport metro light_rail tram walking_distance walk_to minutes_from "
            "mins_from close_to near nearby harbour harbor beach supermarket* grocery "
            "shops restaurants_nearby emplacement situation situé située proximité lage "
            "nähe bahnhof ubicación ubicado ubicada localización cerca posizione vicino "
            "localização localizado perto ligging locatie dichtbij beliggenhed* "
            "placering läge läget",
        ),
        (
            1,
            False,
            "convenient central centrally well_located walkable centrale central "
            "céntrico zentral centraal centralt",
        ),
        (-1, False, "unsafe sketchy dodgy seedy dangerous"),
    ],
    "room_condition": [
        (0, True, "room rooms chambre zimmer habitación quarto kamer værelse værelset rummet"),
        (
            0,
            False,
            "bed beds mattress* pillow* sheets linen bedding blanket* duvet doona bunk "
            "bunks air_con aircon air_conditioning air-conditioning air-con ac a/c heater "
            "heating fan window windows curtain* blind blinds furniture wardrobe tv "
            "shower bathroom toilet sink tap hot_water water_pressure ensuite en-suite "
            "balcony view decor décor maintenance repair* "
            "matelas oreiller* literie climatisation clim salle_de_bain douche fenêtre "
            "bett matratze kissen klimaanlage badezimmer dusche cama colchón almohada* "
            "aire_acondicionado baño ducha letto materasso cuscino aria_condizionata "
            "bagno doccia colchão travesseiro ar_condicionado banheiro casa_de_banho "
            "chuveiro matras airco badkamer seng sengen madras dyne aircondition "
            "badeværelse* bruser säng madrass badrum dusch",
        ),
        (
            1,
            False,
            "spacious comfortable comfy cosy cozy modern renovated roomy spacieux "
            "confortable geräumig bequem espacioso cómodo comodo spazioso confortável "
            "comfortabel rummelig behagelig rymlig bekväm",
        ),
        (
            -1,
            False,
            "small tiny cramped broken worn dated tired old run-down rundown shabby "
            "squeak* creak* uncomfortable leak* leaking stuffy damp "
            "vétuste klein kaputt pequeña pequeño roto piccola piccolo "
            "rotto pequeno quebrado lille slidt liten trång",
        ),
    ],
    "value": [
        (
            0,
            False,
            "price prices priced cost costs rate rates fee fees charge charges charged surcharge budget "
            "money paid pay refund* prix preis precio prezzo preço prijs pris prisen",
        ),
        (
            1,
            False,
            "cheap cheaper affordable bargain value_for_money good_value great_value "
            "worth_it worth_the_money well_priced reasonably_priced reasonable_price "
            "bon_marché abordable günstig preiswert barato barata económico economico "
            "conveniente goedkoop billig billigt prisvärd",
        ),
        (
            -1,
            False,
            "expensive overpriced pricey rip-off ripoff rip_off not_worth waste_of_money "
            "poor_value cher chère teuer caro cara duur dyr dyrt",
        ),
    ],
}

_POSITIVE = [
    "good",
    "great",
    "excellent",
    "amazing",
    "awesome",
    "fantastic",
    "perfect",
    "nice",
    "lovely",
    "wonderful",
    "best",
    "superb",
    "brilliant",
    "fine",
    "decent",
    "love",
    "loved",
    "enjoy",
    "enjoyed",
    "recommend",
    "pleasant",
    "easy",
    "quick",
    "smooth",
    "fast",
    "efficient",
    "beautiful",
    "helpful",
    "friendly",
    "clean",
    "comfortable",
    "outstanding",
    "exceptional",
    "ideal",
    "bien",
    "bon",
    "bonne",
    "super",
    "génial",
    "parfait",
    "parfaite",
    "agréable",
    "gut",
    "toll",
    "schön",
    "prima",
    "bueno",
    "buena",
    "excelente",
    "genial",
    "perfecto",
    "perfecta",
    "buono",
    "buona",
    "ottimo",
    "ottima",
    "perfetto",
    "bom",
    "boa",
    "ótimo",
    "ótima",
    "goed",
    "fijn",
    "leuk",
    "godt",
    "fint",
    "dejlig",
    "bra",
    "utmärkt",
]
_NEGATIVE = [
    "bad",
    "terrible",
    "awful",
    "horrible",
    "poor",
    "worst",
    "disappointing",
    "disappointed",
    "disgusting",
    "horrendous",
    "unacceptable",
    "problem",
    "problems",
    "issue",
    "issues",
    "complain",
    "complained",
    "complaint",
    "lacking",
    "missing",
    "faulty",
    "difficult",
    "difficulty",
    "hard",
    "slow",
    "unsafe",
    "scary",
    "nightmare",
    "avoid",
    "useless",
    "mal",
    "mauvais",
    "mauvaise",
    "nul",
    "horrible",
    "schlecht",
    "furchtbar",
    "malo",
    "mala",
    "pésimo",
    "cattivo",
    "pessimo",
    "mau",
    "slecht",
    "dårlig",
    "dårligt",
    "dålig",
]
_IMPROVE = re.compile(
    r"\b(could\s+(be|have\s+been|use)|should(\s+be|\s+have)?|would\s+(be|have\s+been)\s+(nice|good|better)"
    r"|needs?\s+(to|a|an|some|more|better)|wish|not\s+enough|too\s+\w+|lack\s+of|a\s+bit\s+\w+)\b",
    re.I,
)
_NEGATORS = frozenset(
    [
        "not",
        "no",
        "never",
        "nothing",
        "without",
        "hardly",
        "barely",
        "neither",
        "nor",
        "zero",
        "lack",
        "missing",
        "pas",
        "ne",
        "sans",
        "aucun",
        "aucune",
        "nicht",
        "kein",
        "keine",
        "keinen",
        "nie",
        "ohne",
        "nunca",
        "sin",
        "ningún",
        "ninguna",
        "non",
        "mai",
        "senza",
        "nenhum",
        "nenhuma",
        "sem",
        "niet",
        "geen",
        "zonder",
        "ikke",
        "ingen",
        "intet",
        "uden",
        "inte",
        "utan",
    ]
)
_SPLIT = re.compile(
    r"[.!?;\n•|]+|,|\s+[-&]\s+|\s+(?:and|but|however|although|though|except|whereas|plus|mais|aber|pero|maar)\s+",
    re.I,
)
_WORDS = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_EDGE = " \t\"'`.,;:!?-–—()[]{}…*"


def _term_regex(term: str) -> str:
    stem = term.endswith("*")
    term = term.rstrip("*")
    body = re.escape(term).replace("_", r"[\s-]+").replace(r"\-", r"[\s-]?")
    return rf"(?<![^\W_]){body}" + (r"[^\W_]*" if stem else r"(?![^\W_])")


@dataclass(frozen=True)
class _Cue:
    topic: str
    polarity: int
    weak: bool
    pattern: re.Pattern[str]


def _build() -> list[_Cue]:
    cues = []
    for topic, groups in _SPEC.items():
        for polarity, weak, terms in groups:
            alt = "|".join(_term_regex(t) for t in terms.split())
            cues.append(_Cue(topic, polarity, weak, re.compile(alt, re.I)))
    return cues


_CUES = _build()
_POS_RE = re.compile("|".join(_term_regex(t) for t in _POSITIVE), re.I)
_NEG_RE = re.compile("|".join(_term_regex(t) for t in _NEGATIVE), re.I)
# "friendly" in "budget-friendly"/"eco-friendly" is not about staff.
_NOT_STAFF = re.compile(r"(budget|eco|family|pet|kid|wallet|user)[\s-]friendly", re.I)
# "Liked: Nothing to like about the place" is not praise.
_NOTHING_LIKED = re.compile(
    r"^\W*(nothing|none|not\s+(much|a\s+lot|really|anything)|n/?a|didn['\u2019\ufffd]?t)(?![^\W_])",
    re.I,
)
_AC = re.compile(r"(?<![^\W_])(AC|A/C)(?![^\W_])")  # upper-case only: "ac" is too ambiguous

PLAYBOOK: dict[str, str] = {
    "cleanliness": "Re-inspect the rooms and shared areas named in these reviews and review the housekeeping checklist.",
    "check_in": "Review the arrival process (instructions, key/code delivery, reception hours) for the issues guests describe.",
    "staff": "Share the specific feedback with the team lead and follow up on service standards at reception.",
    "noise": "Check the noise sources guests mention and offer quieter rooms or earplugs where possible.",
    "facilities": "Have maintenance check the shared facilities guests mention (kitchen, Wi-Fi, lift, bathrooms).",
    "location": "Improve pre-arrival directions and set expectations about the neighbourhood in the listing.",
    "room_condition": "Raise maintenance tickets for the room problems guests describe (beds, air-con, fixtures).",
    "value": "Compare the rate and fees with what guests received; make sure the listing sets accurate expectations.",
}
LABELS = {
    "cleanliness": "cleanliness",
    "check_in": "check-in",
    "staff": "staff",
    "noise": "noise",
    "facilities": "facilities",
    "location": "location",
    "room_condition": "room condition",
    "value": "value for money",
}


# --------------------------------------------------------------------------- analysis
@dataclass
class _Clause:
    field: str
    text: str
    polarity: int  # -1 / +1
    topics: list[str]


def _clauses(text: str) -> list[str]:
    out: list[str] = []
    pos = 0
    for m in [*_SPLIT.finditer(text), None]:
        end = m.start() if m else len(text)
        piece = text[pos:end].strip(_EDGE)
        if len(piece) >= 2:
            out.append(piece)
        if m:
            pos = m.end()
    return out


def _negated(clause: str, start: int) -> bool:
    before = _WORDS.findall(clause[:start].lower())[-3:]
    return any(w in _NEGATORS or w.endswith(("n't", "n’t")) for w in before)


def _score_clause(clause: str, base: int) -> tuple[float, dict[str, tuple[bool, int]]]:
    """(polarity score, {topic: (strong?, cue polarity sum)}) for one clause."""
    topics: dict[str, tuple[bool, int]] = {}
    cue_sum = 0.0
    for cue in _CUES:
        for m in cue.pattern.finditer(clause):
            if cue.topic == "staff" and cue.polarity and _NOT_STAFF.search(clause):
                continue
            neg = _negated(clause, m.start())
            pol = cue.polarity
            if neg:
                pol = -pol if pol else -1  # "no lift", "without air-con": absence is a complaint
            strong, total = topics.get(cue.topic, (False, 0))
            topics[cue.topic] = (strong or not cue.weak, total + pol)
            cue_sum += pol
    if _AC.search(clause) and "room_condition" not in topics:
        topics["room_condition"] = (True, 0)
    lex = 0.0
    for rx, sign in ((_POS_RE, 1), (_NEG_RE, -1)):
        for m in rx.finditer(clause):
            lex += -sign if _negated(clause, m.start()) else sign
    lex -= len(_IMPROVE.findall(clause))
    # Weak cues ("room", "area") give way to a specific topic in the same clause.
    if any(strong for strong, _ in topics.values()):
        topics = {t: v for t, v in topics.items() if v[0]}
    return base + 1.5 * cue_sum + 1.5 * lex, topics


def analyse(
    title: str | None, liked: str | None, disliked: str | None, rating: float | None
) -> dict[str, Any]:
    """Return an analysis in the same JSON shape the LLM is asked for (see prompt.py)."""
    fields = {"title": title or "", "liked": liked or "", "disliked": disliked or ""}
    liked_real = (
        bool(fields["liked"])
        and not is_filler(fields["liked"])
        and not _NOTHING_LIKED.match(fields["liked"])
    )
    disliked_real = bool(fields["disliked"]) and not is_filler(fields["disliked"])
    clauses: list[_Clause] = []
    for name, base, usable in (
        ("title", 0, True),
        ("liked", 1, liked_real),
        ("disliked", -1, disliked_real),
    ):
        if not usable or not fields[name]:
            continue
        for piece in _clauses(fields[name]):
            score, topics = _score_clause(piece, base)
            if not topics:
                continue
            if score == 0:
                if base == 0:
                    continue  # a neutral title fragment says nothing about polarity
                score = base
            clauses.append(_Clause(name, piece, 1 if score > 0 else -1, list(topics)))

    per_topic: dict[str, list[_Clause]] = defaultdict(list)
    for c in clauses:
        for t in c.topics:
            per_topic[t].append(c)
    aspects: list[dict[str, Any]] = []
    for topic in LABELS:  # stable order
        found = per_topic.get(topic)
        if not found:
            continue
        signs = {c.polarity for c in found}
        sentiment = "mixed" if len(signs) == 2 else ("positive" if 1 in signs else "negative")
        from_fields = any(c.field != "title" for c in found)
        # Negative evidence first, from the "disliked" box when possible.
        ordered = sorted(found, key=lambda c: (c.polarity, c.field != "disliked"))
        evidence: list[str] = []
        for c in ordered:
            if c.text not in evidence:
                evidence.append(c.text[:300])
            if len(evidence) == 3:
                break
        aspects.append(
            {
                "topic": topic,
                "sentiment": sentiment,
                "certainty": "medium" if from_fields else "low",
                "confidence": 0.6 if from_fields else 0.4,
                "evidence": evidence,
            }
        )

    has_pos = liked_real or any(a["sentiment"] in ("positive", "mixed") for a in aspects)
    has_neg = disliked_real or any(a["sentiment"] in ("negative", "mixed") for a in aspects)
    if has_pos and has_neg:
        # Booking.com makes guests fill both boxes, so nearly every review has praise and
        # criticism. The guest's own score decides the tone at the extremes.
        if rating is not None and rating >= 9:
            overall = "positive"
        elif rating is not None and rating <= 3:
            overall = "negative"
        else:
            overall = "mixed"
    elif has_pos:
        overall = "positive"
    elif has_neg:
        overall = "negative"
    elif rating is not None:
        overall = "positive" if rating >= 8 else "negative" if rating <= 4 else "neutral"
    else:
        overall = "neutral"

    negative = [a for a in aspects if a["sentiment"] in ("negative", "mixed")]
    complaint = negative[0] if negative else None
    praised = [LABELS[a["topic"]] for a in aspects if a["sentiment"] == "positive"]
    criticised = [LABELS[a["topic"]] for a in negative]
    parts = []
    if praised:
        parts.append("praises " + ", ".join(praised))
    if criticised:
        parts.append("criticises " + ", ".join(criticised))
    summary = (
        "Rule-based: guest " + "; ".join(parts) + "."
        if parts
        else "Rule-based: no specific operational topic recognised."
    )
    language = detect_language(" ".join(v for v in fields.values() if v))
    return {
        "overall_sentiment": overall,
        "summary_en": summary[:400],
        "language": language,
        "aspects": aspects,
        "main_complaint": complaint["evidence"][0][:200] if complaint else None,
        "main_complaint_topic": complaint["topic"] if complaint else None,
        "suggested_action": PLAYBOOK[complaint["topic"]] if complaint else None,
        "confidence": 0.5 if aspects else 0.3,
        "flags": ["rule_based"],
    }


class LexiconProvider:
    """The rule-based analyzer behind the same interface as an LLM provider.

    Stored as ``analyzer='lexicon', provider='builtin', model_id='lexicon-v1'``; never
    presented as AI output. No network access, no cost, fully reproducible.
    """

    provider = "builtin"
    model = MODEL
    wants_rating = True  # used only as a fallback for overall sentiment when no text cue exists

    def complete(self, system: str, user: str) -> ProviderResponse:
        m = re.search(r"<review>\s*(\{.*\})\s*</review>", user, re.S)
        data = json.loads(m.group(1)) if m else {}
        rating = data.get("rating")
        out = analyse(data.get("title"), data.get("liked"), data.get("disliked"), rating)
        return ProviderResponse(
            json.dumps(out, ensure_ascii=False), "builtin", MODEL, None, 0, 0, 0.0
        )
