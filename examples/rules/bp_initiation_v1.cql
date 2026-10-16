library BloodPressureInitiation version '1.0.0'

// ILLUSTRATIVE, SIMPLIFIED encoding of a JNC 8-style drug-treatment threshold.
// For demonstrating the tooling only. Not clinical guidance; verify against the source guideline.

define "Older without comorbidity":
  AgeInYears() >= 60 and not (HasCondition("diabetes") or HasCondition("ckd"))

define "Elevated for older adults":
  Latest("sbp") >= 150 'mmHg' or Latest("dbp") >= 90 'mmHg'

define "Elevated standard":
  Latest("sbp") >= 140 'mmHg' or Latest("dbp") >= 90 'mmHg'

define "Start antihypertensive":
  ("Older without comorbidity" and "Elevated for older adults")
  or (not "Older without comorbidity" and "Elevated standard")
