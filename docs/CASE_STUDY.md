# Case study: a hypertension threshold change, JNC 8 style to ACC/AHA 2017 style

This is a worked example of what Lemma tells you about a real guideline revision.
Both rule files are **simplified, illustrative encodings** (`examples/rules/bp_initiation_v1.cql`
and `bp_initiation_v2.cql`). The ACC/AHA version uses a crude "high risk" proxy
(ASCVD, diabetes or CKD) instead of the guideline's 10-year ASCVD risk estimate, which Lemma
does not model. Nothing here is clinical guidance.

Reproduce it:

```
lemma --schema examples/schema.toml diff   examples/rules/bp_initiation_v1.cql examples/rules/bp_initiation_v2.cql --rule "Start antihypertensive" --zones
lemma --schema examples/schema.toml impact examples/rules/bp_initiation_v1.cql examples/rules/bp_initiation_v2.cql --rule "Start antihypertensive"
```

## What changed in the text

Three named definitions were removed, three added, and the final rule was rewritten. A text diff
shows that something moved. It does not say who is affected.

## What the semantic diff proves

- **Gained: yes.** Some patients are recommended treatment only by the new version. The smallest
  witness is a 60-year-old woman whose only record is a systolic pressure of 140 mmHg. The old
  version needed 150 for a healthy patient of that age; the new one needs only 140.
- **Lost: none.** No patient who was recommended treatment by the old version is dropped by the
  new one. The solver proves this over the whole schema space, not over a sample.
- Changed patients can have a systolic pressure anywhere from 81 to 149 mmHg and a diastolic
  from 40 to 89 mmHg, so the difference is not confined to one narrow band.

So the revision only widens treatment. That is the property a reviewer most wants to be sure of,
and here it is a proof rather than a spot check.

## Who is affected (synthetic cohort, n = 5000, seed 7)

| | Old | New | Gained | Lost |
|---|---|---|---|---|
| Recommended treatment | 9.5% | 16.8% | 7.3% (6.6 to 8.1) | 0.0% |

| Age band | n | Changed |
|---|---|---|
| 18-39 | 733 | 1.1% |
| 40-59 | 2136 | 1.8% |
| 60-74 | 1479 | 12.3% |
| 75+ | 652 | 21.0% |

The change lands overwhelmingly on older patients (the old rule was most conservative there).
By sex the effect is similar (female 7.0%, male 7.6%).

The cohort is synthetic, so these are properties of the generator, not of any real population.
Run the same command on a real extract (`lemma cohort` reads and writes CSV) to get real numbers.

## Fragile decisions

Of 150 sampled patients, 21 (14.0%) sit within measurement tolerance of a threshold under the new
version, meaning a normal reading error could flip the recommendation. That is a property of
putting a hard line at 130/80, and it is useful to know before the rule drives an alert.

## What this does not show

- It does not say the new guideline is better or worse. Lemma describes the logic, not the evidence.
- The "high risk" proxy changes who is treated; a faithful encoding would need the risk calculator.
- The cohort percentages depend on the generator's assumptions.
