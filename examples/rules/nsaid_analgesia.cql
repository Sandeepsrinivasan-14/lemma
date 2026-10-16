library NsaidAnalgesia version '1.0.0'

// ILLUSTRATIVE rule used to demonstrate invariant checking and missing-data analysis.
// Not clinical guidance.

define "Eligible adult":
  AgeInYears() >= 18 and not HasCondition("ckd")

define "Kidney function acceptable":
  Latest("egfr") >= 60 'mL/min/1.73m2'

define "Recommend NSAID":
  "Eligible adult" and "Kidney function acceptable"
