library StatinInitiation version '1.0.0'

// ILLUSTRATIVE, SIMPLIFIED statin-initiation rule (ACC/AHA-style structure).
// Demonstrates unit conversion: the LDL threshold is written in mmol/L, the schema stores mg/dL.
// Not clinical guidance.

define "Very high LDL":
  Latest("ldl") >= 4.9 'mmol/L'

define "Diabetes in age band":
  HasCondition("diabetes") and AgeInYears() >= 40 and AgeInYears() <= 75

define "Recommend statin":
  "Very high LDL" or "Diabetes in age band" or HasCondition("ascvd")
