library AcuteKidneyInjuryAlert version '1.0.0'

// ILLUSTRATIVE trend rule modelled on the KDIGO creatinine criteria:
// a rise of >= 0.3 mg/dL within 48 hours, or >= 1.5 x baseline within 7 days.
// Verify against the guideline before any real use.

define "Rise of 0.3 mg/dL within 48 hours":
  Delta("creatinine", 48 hours) >= 0.3 'mg/dL'

define "1.5 times baseline within 7 days":
  Ratio("creatinine", 7 days) >= 1.5

define "AKI alert":
  "Rise of 0.3 mg/dL within 48 hours" or "1.5 times baseline within 7 days"
