from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _valid_grouping(value: str, separator: str) -> bool:
    unsigned = value.lstrip("+-")
    groups = unsigned.split(separator)
    return (
        len(groups) > 1
        and groups[0].isdigit()
        and 1 <= len(groups[0]) <= 3
        and all(group.isdigit() and len(group) == 3 for group in groups[1:])
    )


def parse_amount(value: str) -> Decimal | None:
    stripped = value.strip()
    if not stripped:
        return None
    if any(character.isalpha() for character in stripped):
        return None

    parenthesized_negative = stripped.startswith("(") and stripped.endswith(")")
    if ("(" in stripped or ")" in stripped) and not parenthesized_negative:
        return None
    if parenthesized_negative:
        stripped = stripped[1:-1].strip()

    cleaned = stripped.replace(" ", "")
    cleaned = re.sub(r"[^0-9,.\-]", "", cleaned)
    comma_count = cleaned.count(",")
    dot_count = cleaned.count(".")

    if comma_count and dot_count:
        decimal_separator = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        integer_part, fractional_part = cleaned.rsplit(decimal_separator, 1)

        if decimal_separator in integer_part or grouping_separator in fractional_part:
            return None
        if grouping_separator in integer_part:
            if not _valid_grouping(integer_part, grouping_separator):
                return None
            integer_part = integer_part.replace(grouping_separator, "")

        cleaned = f"{integer_part}.{fractional_part}"
    elif comma_count == 1:
        cleaned = cleaned.replace(",", ".")
    elif comma_count > 1:
        if not _valid_grouping(cleaned, ","):
            return None
        cleaned = cleaned.replace(",", "")
    elif dot_count > 1:
        if not _valid_grouping(cleaned, "."):
            return None
        cleaned = cleaned.replace(".", "")

    if parenthesized_negative:
        if cleaned.startswith("-"):
            return None
        cleaned = f"-{cleaned}"

    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_date(value: str) -> date | None:
    stripped = value.strip()
    if not stripped:
        return None

    try:
        return datetime.fromisoformat(stripped).date()
    except ValueError:
        pass

    for format_string in _DATE_FORMATS:
        try:
            return datetime.strptime(stripped, format_string).date()
        except ValueError:
            continue
    return None
