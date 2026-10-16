# Method note

How Lemma decides "do these two rule versions differ?", and what its answers do and do not guarantee.

## Model
A patient is: age, sex, a set of conditions and medications, and for each observation an optional current value, an optional single prior value and the hours between them. Values sit on the schema's measurement grid (`value = k * step`) inside plausible ranges, and schema constraints (for example diastolic below systolic) restrict which patients exist.

## Semantics
Rules are evaluated in Kleene three-valued logic, as in CQL: a missing value makes a comparison *null*; `false and null` is false, `true or null` is true; a rule *fires* only when definitely true. `Delta` and `Ratio` are null unless both values exist within the stated time window.

## Encoding
Each boolean becomes a pair `(is_true, is_false)` and each number a pair `(is_null, value)` over Z3 integers/reals. `Ratio(x) >= r` is encoded linearly as `value >= r * prior` with `prior > 0`, which keeps the problem in linear arithmetic.

## Questions as satisfiability
- *Diff:* is `fires_B and not fires_A` satisfiable (gained)? Is `fires_A and not fires_B` (lost)? Unsatisfiable both ways means equivalent over the model.
- *Invariant "never when C":* is `fires and C` satisfiable? Satisfiable gives a counterexample patient. Vacuity is checked (rule never fires, or C impossible).
- *Missing data:* take a firing patient, drop one observation; does the result change?
- *Robustness:* smallest change, in units of measurement tolerance, that flips a patient's decision (L-infinity, solved with an optimiser).

## Witnesses
Solutions are minimised with `z3.Optimize` (fewest present facts, then smallest distance from a typical patient) and then **replayed through the independent concrete interpreter**. If the interpreter disagrees with the solver, Lemma raises `EncodingMismatch` instead of reporting.

## What is checked against what
| Check | Against |
| --- | --- |
| SMT encoding | independent interpreter (E1; Hypothesis property tests) |
| Interpreter | official HL7 translator + `cql-execution` engine (E10) |
| Bug detection | boundary tests and fuzzing, on author-generated mutants (E2) |

## Known gaps
Only the CQL-like subset is supported. Proofs cover the grid model above, not arbitrary real-valued patients or histories with several prior values. The JS reference engine uses floating point, which disagrees with Lemma's exact arithmetic exactly at some thresholds (see README, E10).
