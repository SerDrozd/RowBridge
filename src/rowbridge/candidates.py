from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import ROUND_FLOOR, Decimal

from rapidfuzz import process
from rapidfuzz.fuzz import WRatio

from rowbridge.matching_utils import normalize_text, parse_amount, parse_date
from rowbridge.models import CandidateGeneration, CsvTable, FieldMapping, MatchSettings


def _primary_block_keys(value: str) -> tuple[str, ...]:
    normalized = normalize_text(value)
    if not normalized:
        return ()
    if len(normalized) <= 6:
        return (f"whole:{normalized}",)
    keys = {
        f"prefix:{normalized[:6]}",
        f"suffix:{normalized[-4:]}",
    }
    return tuple(sorted(keys))


def _text_block_keys(value: str) -> tuple[str, ...]:
    normalized = normalize_text(value)
    if not normalized:
        return ()
    if len(normalized) <= 4:
        return (f"whole:{normalized}",)
    return (f"prefix:{normalized[:4]}", f"suffix:{normalized[-4:]}")


def _amount_bucket(value: str, tolerance: float) -> int | None:
    amount = parse_amount(value)
    if amount is None:
        return None
    width = Decimal(str(tolerance)) if tolerance > 0 else Decimal("0.01")
    return int((amount / width).to_integral_value(rounding=ROUND_FLOOR))


def _date_bucket(value: str, window_days: int) -> int | None:
    parsed = parse_date(value)
    if parsed is None:
        return None
    width = max(window_days, 1)
    return parsed.toordinal() // width


def _add_to_index(index: dict[str, set[int]], keys: Iterable[str], row_index: int) -> None:
    for key in keys:
        index[key].add(row_index)


def generate_candidates(
    table_a: CsvTable,
    table_b: CsvTable,
    mapping: FieldMapping,
    settings: MatchSettings,
) -> CandidateGeneration:
    exact_primary: dict[str, set[int]] = defaultdict(set)
    primary_blocks: dict[str, set[int]] = defaultdict(set)
    secondary_blocks: dict[str, set[int]] = defaultdict(set)
    support_blocks: dict[tuple[int, int], set[int]] = defaultdict(set)

    normalized_b: list[str] = []
    for b_index, row_b in enumerate(table_b.rows):
        primary = normalize_text(row_b[mapping.primary_b])
        normalized_b.append(primary)
        if primary:
            exact_primary[primary].add(b_index)
        _add_to_index(primary_blocks, _primary_block_keys(row_b[mapping.primary_b]), b_index)

        if mapping.secondary_b:
            _add_to_index(
                secondary_blocks,
                _text_block_keys(row_b[mapping.secondary_b]),
                b_index,
            )

        if mapping.amount_b and mapping.date_b:
            amount_bucket = _amount_bucket(row_b[mapping.amount_b], settings.amount_tolerance)
            date_bucket = _date_bucket(row_b[mapping.date_b], settings.date_window_days)
            if amount_bucket is not None and date_bucket is not None:
                for amount_delta in (-1, 0, 1):
                    for date_delta in (-1, 0, 1):
                        support_key = (amount_bucket + amount_delta, date_bucket + date_delta)
                        support_blocks[support_key].add(b_index)

    generated: list[tuple[int, ...]] = []
    fallback_rows = 0

    for row_a in table_a.rows:
        primary = normalize_text(row_a[mapping.primary_a])
        candidates: set[int] = set()

        if primary and primary in exact_primary:
            candidates.update(exact_primary[primary])
        else:
            for block_key in _primary_block_keys(row_a[mapping.primary_a]):
                candidates.update(primary_blocks.get(block_key, ()))

            if mapping.secondary_a and mapping.secondary_b:
                for block_key in _text_block_keys(row_a[mapping.secondary_a]):
                    candidates.update(secondary_blocks.get(block_key, ()))

            if mapping.amount_a and mapping.date_a and mapping.amount_b and mapping.date_b:
                amount_bucket = _amount_bucket(row_a[mapping.amount_a], settings.amount_tolerance)
                date_bucket = _date_bucket(row_a[mapping.date_a], settings.date_window_days)
                if amount_bucket is not None and date_bucket is not None:
                    candidates.update(support_blocks.get((amount_bucket, date_bucket), ()))

        if not candidates and primary and normalized_b:
            fallback_rows += 1
            matches = process.extract(
                primary,
                normalized_b,
                scorer=WRatio,
                limit=settings.fallback_candidates,
                score_cutoff=settings.rescue_primary_similarity * 100,
            )
            candidates.update(match[2] for match in matches)

        if len(candidates) > settings.max_candidates_per_row:
            ranked = sorted(
                candidates,
                key=lambda b_index: (
                    -WRatio(primary, normalized_b[b_index]),
                    b_index,
                ),
            )
            candidates = set(ranked[: settings.max_candidates_per_row])

        generated.append(tuple(sorted(candidates)))

    generated_pairs = sum(len(items) for items in generated)
    return CandidateGeneration(
        by_a=tuple(generated),
        possible_pairs=len(table_a.rows) * len(table_b.rows),
        generated_pairs=generated_pairs,
        fallback_rows=fallback_rows,
    )
