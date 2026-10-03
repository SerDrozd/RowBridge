from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y")


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM.sub("", decomposed.casefold())


def parse_amount(value: str) -> Decimal | None:
    cleaned = value.strip().replace(" ", "")
    if not cleaned:
        return None
    if any(character.isalpha() for character in cleaned):
        return None
    if cleaned.count(",") == 1 and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_date(value: str) -> date | None:
    stripped = value.strip()
    if not stripped:
        return None
    for format_string in _DATE_FORMATS:
        try:
            return datetime.strptime(stripped, format_string).date()
        except ValueError:
            continue
    return None
