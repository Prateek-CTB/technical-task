"""Text normalisation shared by every source.

Booking.com asks guests two separate questions (what they liked / didn't like),
and forces an answer in the second box. "Nothing", "N/A" or "-" must not be
counted as a complaint, so filler answers are recognised here.
"""

from __future__ import annotations

import html
import re

_FILLER = re.compile(
    r"""^\W*(
        nothing(\s+(at\s+all|really|much|to\s+(complain|dislike|report|say)(\s+about)?|in\s+particular|bad|negative))?
      | none | nil | nope | no | na | n/?a | -+ | \.+ | x
      | no\s+(complaints?|issues?|problems?|negatives?|dislikes?|comments?)
      | not\s+(a\s+)?(thing|much|really|applicable)
      | all\s+(good|great|fine|ok|okay|perfect)
      | everything\s+(was\s+)?(good|great|fine|ok|okay|perfect|amazing|excellent)
      | can'?t\s+think\s+of\s+any(thing)?
      | (i\s+)?(liked|loved)\s+everything
    )\W*$""",
    re.IGNORECASE | re.VERBOSE,
)

# "Nothing, the staff were lovely." Filler opening followed by praise only.
_FILLER_THEN_PRAISE = re.compile(
    r"""^\W*(nothing|none|no\s+complaints?|all\s+good|everything\s+was\s+\w+)\b[\s,.!;:-]+
        (?=.*\b(great|good|perfect|lovely|amazing|excellent|fantastic|wonderful|friendly|helpful|
                 clean|loved|enjoyed|recommend|awesome|nice|best)\b)
        (?!.*\b(but|however|except|although|though|only|apart\s+from|other\s+than)\b)""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


def clean_text(value: object) -> str | None:
    """Unescape HTML entities, drop NUL/control characters, normalise whitespace.

    NUL bytes are removed because PostgreSQL text columns cannot store them.
    """
    if value is None:
        return None
    text = html.unescape(str(value)).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or (ch >= " " and ch != "\x7f"))
    text = text.strip()
    return text or None


def is_filler(text: str | None) -> bool:
    """True when a "didn't like" answer carries no actual complaint."""
    if not text:
        return True
    t = text.strip()
    if len(t) <= 2 or _FILLER.match(t):
        return True
    return bool(_FILLER_THEN_PRAISE.match(t)) and len(t) < 160


def fit(value: str | None, limit: int) -> str | None:
    """Truncate to a column's length so one over-long value cannot abort a whole import."""
    if value is None or len(value) <= limit:
        return value
    return value[:limit]
