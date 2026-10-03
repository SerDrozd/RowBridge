# Matching behavior

RowBridge separates **candidate generation** from **candidate scoring**. The first stage asks which pairs are plausible enough to inspect; the second stage asks how strong each plausible pair is.

## Field roles

A run requires one primary identity field on each side. Three supporting roles are optional:

- secondary text;
- amount;
- date.

The application rejects a mapping that reuses a selected source column for incompatible roles.

## Normalization

Text comparison normalizes case and punctuation before fuzzy similarity is calculated. Amounts and dates are parsed through dedicated helpers rather than through general text normalization.

Identifier-like text such as `INV-00123` is not accepted as a numeric amount simply because it contains digits.

## Default weights

When all rule types are enabled:

- primary identity: 55%;
- secondary text: 20%;
- amount: 15%;
- date: 10%.

When optional fields are omitted, their weights are removed and the remaining rules are normalized.

## Candidate generation

The normal path is intentionally not a Cartesian product.

1. Exact normalized primary blocks are checked first.
2. Supporting blocks can add plausible candidates.
3. If a source row still has no candidate, a bounded fuzzy fallback may run.
4. Per-source-row candidate counts are capped.
5. The global fuzzy fallback is disabled once the target side is too large for a safe scan.

This means matching behavior degrades conservatively: a large run may leave a difficult row unmatched instead of turning into an uncontrolled all-against-all fuzzy comparison.

## Support-field rescue

A common amount or date is not enough to rescue an unrelated primary identifier.

A weak primary comparison can only be rescued when there is strong secondary-text agreement together with amount or date support. This reduces accidental review candidates caused by repeated totals or common dates.

## One-to-one resolution

Scored candidates are resolved globally and deterministically so a source row is not assigned to multiple rows on the other side.

For a selected pair, RowBridge also checks the nearest competitor involving either source row. If another candidate is within the configured ambiguity margin, the selected pair remains in review even when the raw score is high.

## Statuses

- `auto_matched` — selected by the matcher with sufficient confidence and no close competitor;
- `review` — plausible but requires a human decision;
- `confirmed` — a review pair accepted by a human;
- `manual_matched` — two unmatched rows linked by a human without an algorithmic score;
- `unmatched` — no selected pair.

## Evidence

Evidence records the contribution and detail for each active rule. Human decisions never replace this algorithmic evidence; they change the current status and append an audit event.
