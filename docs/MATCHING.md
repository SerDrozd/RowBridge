# Matching behavior

RowBridge separates candidate generation from candidate scoring. Candidate generation decides which row pairs are plausible enough to inspect. Scoring measures the strength of each plausible pair.

## Field roles

A run requires one primary identity field on each side. Three supporting roles are optional:

- secondary text;
- amount;
- date.

A source column cannot be reused for incompatible roles within the same mapping.

## Text normalization

Text is normalized with Unicode compatibility normalization and case folding. Punctuation and spacing are removed, while Unicode letters and digits are preserved.

This keeps values such as Cyrillic names usable during both candidate generation and fuzzy scoring.

## Amount and date parsing

Amounts are parsed separately from general text. Common decimal and grouping formats are supported, including `1,234.56`, `1.234,56`, and accounting-style negatives such as `(1,234.56)`.

Identifier-like text such as `INV-00123` is not treated as an amount simply because it contains digits.

Dates support ISO dates, ISO datetime text produced from spreadsheet cells, and the explicit date formats handled by the parser.

## Base weights

When all rule types are enabled:

- primary identity: 55%;
- secondary text: 20%;
- amount: 15%;
- date: 10%.

When optional fields are omitted, those rules are removed and the remaining active weights are normalized during scoring.

## Candidate generation

The normal path avoids a Cartesian product.

1. Exact normalized primary blocks are checked first.
2. Supporting blocks can add plausible candidates.
3. If a source row still has no candidate, a limited fuzzy fallback may run.
4. Candidate counts are capped per source row.
5. Global fuzzy fallback is disabled when the target side exceeds the configured scan limit.

Large inputs can therefore leave a difficult row unmatched instead of falling back to an uncontrolled all-against-all fuzzy comparison.

## Support-field rescue

A common amount or date is not enough to rescue an unrelated primary identifier.

A weak primary comparison can only be rescued when strong secondary-text agreement is present together with amount or date support. This reduces review candidates caused by repeated totals or common dates.

## One-to-one selection

Scored candidates are sorted by descending score, then by Side A row index and Side B row index.

RowBridge walks that order and selects a candidate when neither source row has already been used. This produces deterministic one-to-one output, but it is a greedy score-ordered selection rather than a global assignment optimizer.

For a selected pair, RowBridge also checks competitors involving either source row. If another candidate is within the configured ambiguity margin, the selected pair remains in review even when its score is high.

## Statuses

- `auto_matched`: selected by the matcher with sufficient confidence and no close competitor;
- `review`: plausible but requires a human decision;
- `confirmed`: a review pair accepted by a human;
- `manual_matched`: two unmatched rows linked by a human without an algorithmic score;
- `unmatched`: no selected pair.

## Evidence

Evidence records the contribution and detail for each active rule. Human actions do not replace the original algorithmic evidence. They change the current state and append an audit event.
