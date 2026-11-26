"""Deterministic generator of FICTIONAL demo reviews (ADR 0013).

Every sentence is invented here; nothing is copied or paraphrased from real guest
feedback. Output is a pure function of (seed, date range): each property-day is
generated from its own RNG seeded by ``seed|property|day``, so extending the date
range never changes earlier days and re-seeding is idempotent.

The generator records the topics it *intended* each review to contain
(``truth``). That is generator ground truth for demonstration summaries only, not
analysis of the text.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

T = TypeVar("T")
SYDNEY = ZoneInfo("Australia/Sydney")
DEFAULT_SEED = 20261126
TOPICS = (
    "cleanliness",
    "check_in",
    "staff",
    "noise",
    "facilities",
    "location",
    "room_condition",
    "value",
)


@dataclass(frozen=True)
class Profile:
    per_week: float  # average reviews published per week
    positive: float  # share of strongly positive experiences
    mixed: float
    issues: tuple[str, ...]  # topics guests complain about most here
    strengths: tuple[str, ...]
    rooms: tuple[str, ...]


PROFILES: dict[str, Profile] = {
    "olympic-paddington": Profile(
        1.2,
        0.48,
        0.24,
        ("room_condition", "noise", "staff"),
        ("location", "value"),
        ("Standard Double", "Superior Double Room (Ensuite)", "Twin Room", "Single Room"),
    ),
    "venus-potts-point-sydney": Profile(
        1.8,
        0.62,
        0.22,
        ("noise", "cleanliness"),
        ("location", "staff"),
        ("Double Room", "Queen Room with City View", "Twin Room", "Studio"),
    ),
    "venus-surry-hills": Profile(
        2.2,
        0.52,
        0.24,
        ("cleanliness", "check_in", "staff"),
        ("value", "location"),
        ("Double Room", "Deluxe Queen", "Triple Room", "Twin Room"),
    ),
    "chateau-de-venus": Profile(
        3.0,
        0.74,
        0.17,
        ("value", "facilities"),
        ("location", "staff", "facilities"),
        ("Harbour View Double", "Queen Room", "Family Room", "Standard Twin"),
    ),
}
SEASON = {
    1: 1.4,
    2: 1.2,
    3: 1.0,
    4: 1.15,
    5: 0.85,
    6: 0.8,
    7: 0.9,
    8: 0.85,
    9: 0.95,
    10: 1.05,
    11: 1.1,
    12: 1.35,
}

# ----------------------------------------------------------------------------- phrase banks (English)
POS: dict[str, list[str]] = {
    "cleanliness": [
        "The room was spotless when we arrived.",
        "Housekeeping did a lovely job, fresh towels every day.",
        "Everything felt properly clean, bathroom included.",
        "Beds were made beautifully and the floors were clean.",
        "Clean, tidy and smelled fresh.",
        "Honestly the cleanest budget stay I have had in Sydney.",
    ],
    "check_in": [
        "Check-in took about two minutes.",
        "Early check-in was no problem, they even held our bags.",
        "Self check-in code worked first time, very smooth.",
        "Arrived late and the process was painless.",
        "Got our keys straight away and the directions were clear.",
    ],
    "staff": [
        "The woman on reception went out of her way to help.",
        "Staff were friendly and gave us great restaurant tips.",
        "Everyone we dealt with was polite and quick to sort things out.",
        "The night manager was a legend, sorted our taxi at 5am.",
        "Really helpful team, nothing was too much trouble.",
        "Lovely, welcoming people.",
    ],
    "noise": [
        "Surprisingly quiet for the area.",
        "Slept right through, no noise at all.",
        "Thick walls and a calm corridor, we slept well.",
        "Street noise was barely noticeable with the window shut.",
    ],
    "facilities": [
        "The kitchen was well equipped and always tidy.",
        "Wi-Fi was fast enough for video calls.",
        "Loved the rooftop terrace in the evening.",
        "Laundry facilities were handy and cheap.",
        "Lifts worked fine and the lobby was comfortable.",
        "Free luggage storage was a big help.",
    ],
    "location": [
        "Great location, five minutes from the station.",
        "You can walk to the harbour and plenty of cafes from here.",
        "Perfect base for exploring the city.",
        "Right near the train, bus and good restaurants.",
        "Couldn't ask for a better spot for the price.",
    ],
    "room_condition": [
        "The bed was very comfortable.",
        "Room was bright and the air-con worked well.",
        "Newly refreshed rooms, good shower pressure.",
        "Comfy bed, good blackout curtains, nice little desk.",
        "Bigger than expected for the price.",
    ],
    "value": [
        "Excellent value for Sydney.",
        "Cheap for what you get, would book again.",
        "Hard to beat at this price.",
        "Fair price and nothing hidden.",
        "Worth every dollar.",
    ],
}
NEG: dict[str, list[str]] = {
    "cleanliness": [
        "Bathroom had hair in the shower and the floor was sticky.",
        "The room smelled musty and the carpet looked grubby.",
        "Found dust on every surface and stains on the sheets.",
        "Rubbish bin hadn't been emptied from the guest before us.",
        "Saw a cockroach near the kitchen on day two.",
        "Towels weren't replaced even after we asked.",
    ],
    "check_in": [
        "Reception was unattended for 40 minutes when we arrived.",
        "They had no record of our booking and it took ages to sort out.",
        "Check-in was slow and confusing, the key card failed twice.",
        "Was told our room wasn't ready at 4pm.",
        "Door code didn't work and nobody picked up the phone.",
        "Checkout was rushed and they chased us for a deposit we'd already paid.",
    ],
    "staff": [
        "The man at the desk was rude when I asked about the bill.",
        "Staff seemed uninterested and dismissed our complaint.",
        "Nobody followed up on what they promised.",
        "Our request for extra pillows was ignored three times.",
        "Reception was curt and unhelpful.",
        "Felt like a nuisance for asking a simple question.",
    ],
    "noise": [
        "Very loud at night, music from the street until 3am.",
        "Thin walls, heard the neighbours all night.",
        "Rubbish trucks and sirens woke us before 6.",
        "The corridor was noisy, doors slamming constantly.",
        "Couldn't sleep because of the bar next door.",
    ],
    "facilities": [
        "The lift was out of order for two days with bags to carry up four floors.",
        "Wi-Fi kept dropping and was useless in the room.",
        "Kitchen had almost no clean pots and the fridge was full.",
        "Only one working washing machine for the whole building.",
        "No hot water in the shared bathroom one morning.",
        "The air-con in the common area was broken.",
    ],
    "location": [
        "Bit of a walk to the station at night.",
        "The street feels a bit sketchy after dark.",
        "Further from the centre than the map suggests.",
    ],
    "room_condition": [
        "The mattress was sagging and the pillows were flat.",
        "Air-con rattled and barely cooled the room.",
        "Paint was peeling and the shower leaked.",
        "Room was tiny and tired, curtain rail hanging off.",
        "Window wouldn't close properly and the room was freezing.",
        "Furniture was chipped and the lamp didn't work.",
    ],
    "value": [
        "Too expensive for what it is.",
        "Felt overpriced once I added the extra fees.",
        "They charged a surprise cleaning fee at checkout.",
        "Not worth the money compared with other places nearby.",
        "Paid for a view we never got.",
    ],
}
EXTRA_POS = [
    "Would stay again.",
    "Recommend for a short trip.",
    "Happy overall.",
    "Thanks for a good stay!",
    "Exactly what we needed.",
]
EXTRA_NEG = [
    "Won't be back.",
    "Disappointing overall.",
    "Hopefully they fix this.",
    "Needs attention.",
    "Unfortunately that spoiled the stay.",
]
TITLES_POS = [
    "Great stay",
    "Really good value",
    "Would come back",
    "Lovely staff",
    "Perfect for a city break",
    "Clean and convenient",
    "Happy with everything",
    "Solid choice",
    "Nice and quiet",
    "Better than expected",
]
TITLES_MIX = [
    "Good location, some issues",
    "Decent but not perfect",
    "Okay for the price",
    "Mixed experience",
    "Fine for a night or two",
    "Some ups and downs",
]
TITLES_NEG = [
    "Disappointed",
    "Not as described",
    "Needs work",
    "Would not recommend",
    "Poor experience",
    "Let down by the basics",
    "Noisy and tired",
]
FILLER = ["Nothing", "Nothing really", "N/A", "-", "Nothing to complain about", "None"]
REPLY_POS = [
    "Thank you for your kind words, we hope to welcome you back soon.",
    "Thanks for staying with us and for the lovely feedback!",
    "We're delighted you enjoyed your stay.",
]
REPLY_NEG = [
    "Thank you for letting us know. We're sorry about your experience and have shared this with the team.",
    "We apologise that we fell short and appreciate the honest feedback. We're looking into it.",
    "Sorry to hear this. Please contact us directly so we can make it right.",
]

# Short non-English texts: language -> topic -> (positive, negative). Invented sentences.
FOREIGN: dict[str, dict[str, tuple[list[str], list[str]]]] = {
    "fr": {
        "location": (
            ["Très bien situé, proche du métro.", "Emplacement idéal pour visiter la ville."],
            ["Un peu loin du centre le soir."],
        ),
        "cleanliness": (
            ["Chambre propre et bien rangée."],
            ["La salle de bain n'était pas très propre."],
        ),
        "staff": (
            ["Personnel aimable et serviable."],
            ["Le personnel à l'accueil était peu aimable."],
        ),
        "noise": (
            ["Calme la nuit, on a bien dormi."],
            ["Beaucoup de bruit de la rue pendant la nuit."],
        ),
        "value": (["Bon rapport qualité-prix."], ["Trop cher pour ce que c'est."]),
    },
    "es": {
        "location": (
            ["Muy bien ubicado, cerca de todo.", "Excelente ubicación para turistas."],
            ["Un poco lejos de la estación."],
        ),
        "cleanliness": (["Habitación muy limpia."], ["El baño estaba sucio y olía mal."]),
        "staff": (["El personal fue muy amable."], ["La recepción no ayudó con nuestro problema."]),
        "noise": (["Tranquilo, dormimos muy bien."], ["Había mucho ruido por la noche."]),
        "value": (["Buena relación calidad-precio."], ["Demasiado caro para lo que ofrece."]),
    },
    "de": {
        "location": (
            ["Super Lage, alles in der Nähe.", "Sehr zentral gelegen."],
            ["Etwas weit weg vom Bahnhof."],
        ),
        "cleanliness": (["Das Zimmer war sehr sauber."], ["Das Bad war leider nicht sauber."]),
        "staff": (
            ["Das Personal war freundlich und hilfsbereit."],
            ["Die Rezeption war unfreundlich."],
        ),
        "noise": (["Ruhig, wir haben gut geschlafen."], ["Nachts sehr laut von der Straße."]),
        "value": (["Gutes Preis-Leistungs-Verhältnis."], ["Für den Preis nicht zu empfehlen."]),
    },
    "ja": {
        "location": (
            ["駅から近くてとても便利でした。", "立地が良く観光に最適です。"],
            ["夜は駅まで少し遠く感じました。"],
        ),
        "cleanliness": (["部屋がとても清潔でした。"], ["バスルームが汚れていました。"]),
        "staff": (["スタッフが親切で助かりました。"], ["フロントの対応が冷たかったです。"]),
        "noise": (["静かでよく眠れました。"], ["夜は外の音がうるさかったです。"]),
        "value": (["値段の割に満足です。"], ["料金の割に設備が古いです。"]),
    },
}
COUNTRIES = [
    ("AU", 34, "en"),
    ("NZ", 8, "en"),
    ("GB", 10, "en"),
    ("US", 10, "en"),
    ("CA", 3, "en"),
    ("SG", 3, "en"),
    ("DE", 7, "de"),
    ("FR", 7, "fr"),
    ("ES", 4, "es"),
    ("JP", 6, "ja"),
    ("CN", 4, "en"),
    ("KR", 4, "en"),
]
TRAVELLERS = [("Solo traveller", 38), ("Couple", 34), ("Group of friends", 18), ("Family", 10)]


@dataclass
class Dataset:
    records: list[dict[str, Any]]
    truth: dict[
        str, dict[str, Any]
    ]  # review_id -> {"topics": [...], "experience": str, "incident": bool}


def _rng(*parts: object) -> random.Random:
    return random.Random("|".join(str(p) for p in parts))


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _weighted(rng: random.Random, pairs: list[tuple[T, float]]) -> T:
    return rng.choices([a for a, _ in pairs], weights=[w for _, w in pairs])[0]


def _week_factor(seed: int, slug: str, monday: date) -> float:
    rng = _rng(seed, slug, "week", monday)
    if rng.random() < 0.09:
        return rng.uniform(0.1, 0.35)  # a very quiet week
    if rng.random() < 0.08:
        return rng.uniform(1.6, 2.2)  # a busy week (event, conference)
    return rng.uniform(0.8, 1.2)


def _incident(seed: int, slug: str, day: date, profile: Profile) -> str | None:
    """Topic of a negative-review cluster active on ``day``, if any (range-independent)."""
    for back in range(0, 17):
        start = day - timedelta(days=back)
        rng = _rng(seed, slug, "incident", start)
        if rng.random() < 0.012:  # ~4 clusters a year per property, each 1-2 weeks
            length = rng.randint(7, 16)
            if back < length:
                return rng.choice(profile.issues)
    return None


def _pick_topics(
    rng: random.Random, profile: Profile, positive: bool, k: int, force: str | None
) -> list[str]:
    boosted = profile.strengths if positive else profile.issues
    weights = [(t, 4.0 if t in boosted else 1.0) for t in TOPICS]
    chosen: list[str] = [force] if force else []
    while len(chosen) < k:
        t = _weighted(rng, weights)
        if t not in chosen:
            chosen.append(t)
    return chosen


def _style(rng: random.Random, text: str) -> str:
    if rng.random() < 0.1:
        text = text[0].lower() + text[1:]
    if rng.random() < 0.3 and text.endswith("."):
        text = text[:-1]
    if rng.random() < 0.08 and text.endswith("."):
        text = text[:-1] + "!"
    if rng.random() < 0.05 and len(text) > 12:  # an occasional typo
        i = rng.randrange(3, len(text) - 3)
        text = text[:i] + text[i + 1] + text[i] + text[i + 2 :]
    return text


def _compose(
    rng: random.Random, bank: dict[str, list[str]], topics: list[str], extras: list[str]
) -> str:
    parts = [rng.choice(bank[t]) for t in topics]
    if rng.random() < 0.25:
        parts.append(rng.choice(extras))
    rng.shuffle(parts)
    joiner = rng.choice([" ", " ", "\n", " - "])
    return joiner.join(_style(rng, p) for p in parts)


def _review(
    seed: int, slug: str, profile: Profile, day: date, k: int, incident: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = _rng(seed, slug, day, "review", k)
    in_incident = incident is not None
    p_pos = max(0.15, profile.positive - (0.3 if in_incident else 0.0))
    p_mix = profile.mixed
    roll = rng.random()
    experience = "positive" if roll < p_pos else "mixed" if roll < p_pos + p_mix else "negative"
    if experience == "positive":
        rating = _weighted(rng, [(10, 52), (9, 26), (8, 16), (7, 6)])
    elif experience == "mixed":
        rating = _weighted(rng, [(8, 22), (7, 34), (6, 26), (5, 18)])
    else:
        rating = _weighted(rng, [(5, 18), (4, 18), (3, 22), (2, 18), (1, 24)])

    force = incident if in_incident and experience != "positive" and rng.random() < 0.8 else None
    liked_t: list[str] = []
    disliked_t: list[str] = []
    if experience in ("positive", "mixed"):
        liked_t = _pick_topics(rng, profile, True, rng.choice([1, 1, 2, 3]), None)
    if experience in ("mixed", "negative"):
        disliked_t = _pick_topics(
            rng,
            profile,
            False,
            rng.choice([1, 1, 2, 2, 3]) if experience == "negative" else 1,
            force,
        )
    if experience == "negative" and rng.random() < 0.35:
        liked_t = _pick_topics(rng, profile, True, 1, None)

    country, _, native = _weighted(rng, [(c, c[1]) for c in COUNTRIES])
    use_native = native != "en" and rng.random() < 0.65
    if use_native:
        bank = FOREIGN[native]
        lt = [t for t in liked_t if t in bank] or ([next(iter(bank))] if liked_t else [])
        dt = [t for t in disliked_t if t in bank] or ([next(iter(bank))] if disliked_t else [])
        liked = " ".join(rng.choice(bank[t][0]) for t in lt[:2])
        disliked = " ".join(rng.choice(bank[t][1]) for t in dt[:2])
        language = native
        liked_t, disliked_t = lt[:2], dt[:2]
    else:
        liked = _compose(rng, POS, liked_t, EXTRA_POS) if liked_t else ""
        disliked = _compose(rng, NEG, disliked_t, EXTRA_NEG) if disliked_t else ""
        if not disliked and rng.random() < 0.4:
            disliked = rng.choice(FILLER)
        language = "xu" if rng.random() < 0.12 else "en"  # Booking tags some English as undetected

    title = ""
    if rng.random() < 0.5 and not use_native:
        title = rng.choice(
            {"positive": TITLES_POS, "mixed": TITLES_MIX, "negative": TITLES_NEG}[experience]
        )
    if rng.random() < 0.015:  # a rating-only review
        liked = disliked = title = ""
        liked_t, disliked_t = [], []

    lag = min(14, int(rng.expovariate(1 / 3.5)))
    checkout = day - timedelta(days=lag)
    nights = _weighted(rng, [(1, 28), (2, 34), (3, 20), (4, 10), (5, 8)])
    hour = _weighted(
        rng,
        [
            (h, w)
            for h, w in zip(
                range(24),
                [1, 1, 1, 1, 1, 1, 2, 3, 4, 4, 4, 4, 4, 4, 4, 4, 5, 6, 8, 9, 9, 7, 4, 2],
                strict=True,
            )
        ],
    )
    local = datetime.combine(day, time(hour, rng.randrange(60), rng.randrange(60)), tzinfo=SYDNEY)
    reply = ""
    if rng.random() < 0.62:
        reply = rng.choice(REPLY_NEG if experience != "positive" else REPLY_POS)

    review_id = f"demo-{slug}-{day:%Y%m%d}-{k}"
    record = {
        "review_id": review_id,
        "property": slug,
        "rating": rating,
        "review_date": local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": title,
        "liked": liked,
        "disliked": disliked,
        "language": language,
        "country": country,
        "traveller_type": _weighted(rng, [(t, w) for t, w in TRAVELLERS]),
        "room_type": rng.choice(profile.rooms),
        "checkin_date": (checkout - timedelta(days=nights)).isoformat(),
        "checkout_date": checkout.isoformat(),
        "nights": nights,
        "stay_status": "stayed",
        "management_reply": reply,
    }
    truth = {
        "topics": sorted(set(liked_t) | set(disliked_t)),
        "negative_topics": sorted(set(disliked_t)),
        "positive_topics": sorted(set(liked_t)),
        "experience": experience,
        "incident": force is not None,
    }
    return record, truth


def generate(seed: int = DEFAULT_SEED, end: date | None = None, days: int = 364) -> Dataset:
    """Fictional reviews published on each day in ``[end - days + 1, end]`` (Sydney dates)."""
    end = end or datetime.now(SYDNEY).date()
    records: list[dict[str, Any]] = []
    truth: dict[str, dict[str, Any]] = {}
    for offset in range(days):
        day = end - timedelta(days=days - 1 - offset)
        monday = day - timedelta(days=day.weekday())
        for slug, profile in PROFILES.items():
            rng = _rng(seed, slug, day, "count")
            lam = profile.per_week / 7 * SEASON[day.month] * _week_factor(seed, slug, monday)
            incident = _incident(seed, slug, day, profile)
            if incident:
                lam *= 1.5
            for k in range(_poisson(rng, lam)):
                record, info = _review(seed, slug, profile, day, k, incident)
                records.append(record)
                truth[record["review_id"]] = info
    return Dataset(records, truth)
