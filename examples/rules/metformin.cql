library MetforminInitiation version '1.0.0'

// ILLUSTRATIVE rule. The eGFR floor mirrors the commonly cited metformin labelling
// (initiation not recommended below 45; contraindicated below 30). Verify against the current label.

define "Eligible":
  HasCondition("diabetes") and not OnMedication("metformin")

define "Glycaemic need":
  Latest("hba1c") >= 6.5 '%'

define "Renal function adequate":
  Latest("egfr") >= 45 'mL/min/1.73m2'

define "Recommend metformin":
  "Eligible" and "Glycaemic need" and "Renal function adequate"
