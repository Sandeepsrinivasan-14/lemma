library BloodPressureInitiation version '2.0.0'

// ILLUSTRATIVE, SIMPLIFIED encoding of an ACC/AHA 2017-style threshold.
// "High risk" is a crude proxy (the guideline uses a 10-year ASCVD risk estimate, which is not modelled).
// For demonstrating the tooling only. Not clinical guidance; verify against the source guideline.

define "High risk proxy":
  HasCondition("ascvd") or HasCondition("diabetes") or HasCondition("ckd")

define "Stage 1 or higher":
  Latest("sbp") >= 130 'mmHg' or Latest("dbp") >= 80 'mmHg'

define "Stage 2":
  Latest("sbp") >= 140 'mmHg' or Latest("dbp") >= 90 'mmHg'

define "Start antihypertensive":
  ("High risk proxy" and "Stage 1 or higher") or "Stage 2"
