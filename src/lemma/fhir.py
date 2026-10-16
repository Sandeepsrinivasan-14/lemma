"""Turn a witness patient into a FHIR R4 collection Bundle, plus a structural sanity check.

The bundle is built to be easy to load into a FHIR server or test harness. `structural_check`
verifies shape only (required fields, internal references, numeric values); it is NOT a substitute
for validating with the HL7 validator or a HAPI FHIR server, which Lemma does not run.
Terminology codes come from the schema and are illustrative; verify them before real use.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fractions import Fraction

from .numeric import fmt
from .patient import Patient
from .schema import Schema

EVAL_TIME = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)  # fixed "now" so bundles are deterministic
LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
UCUM = "http://unitsofmeasure.org"
UCUM_CODES = {"mmHg": "mm[Hg]", "mL/min/1.73m2": "mL/min/{1.73_m2}", "%": "%", "mg/dL": "mg/dL", "umol/L": "umol/L", "mmol/L": "mmol/L"}


def _num(x: Fraction):
    s = fmt(x)
    return float(s) if "." in s else int(s)


def patient_bundle(schema: Schema, p: Patient, bundle_id: str = "lemma-witness") -> dict:
    pid = "pt-" + (p.id or "1").replace(" ", "-").replace("=", "-")
    entries = []

    def add(res: dict):
        entries.append({"fullUrl": f"urn:lemma:{res['resourceType']}/{res['id']}", "resource": res})

    birth_year = EVAL_TIME.year - int(p.age)
    add({"resourceType": "Patient", "id": pid, "gender": p.sex, "birthDate": f"{birth_year}-01-01"})
    subject = {"reference": f"Patient/{pid}"}
    for code, ov in sorted(p.obs.items()):
        spec = schema.obs[code]
        samples = []
        if ov.value is not None:
            samples.append(("latest", ov.value, EVAL_TIME))
        if ov.prior is not None and ov.gap_hours is not None:
            samples.append(("prior", ov.prior, EVAL_TIME - timedelta(hours=int(ov.gap_hours))))
        for tag, value, when in samples:
            coding = {"system": LOINC, "code": spec.loinc, "display": spec.label} if spec.loinc else {"display": spec.label}
            add(
                {
                    "resourceType": "Observation",
                    "id": f"{code}-{tag}",
                    "status": "final",
                    "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs" if code in ("sbp", "dbp") else "laboratory"}]}],
                    "code": {"coding": [coding], "text": spec.label},
                    "subject": subject,
                    "effectiveDateTime": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valueQuantity": {"value": _num(value), "unit": spec.unit, "system": UCUM, "code": UCUM_CODES.get(spec.unit, spec.unit)},
                }
            )
    for code in sorted(p.conditions):
        f = schema.conditions[code]
        coding = [{"system": SNOMED, "code": f.coding, "display": f.label}] if f.system == "snomed" and f.coding else []
        add(
            {
                "resourceType": "Condition",
                "id": f"cond-{code}",
                "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
                "code": {"coding": coding, "text": f.label},
                "subject": subject,
            }
        )
    for code in sorted(p.medications):
        f = schema.medications[code]
        coding = [{"system": RXNORM, "code": f.coding, "display": f.label}] if f.system == "rxnorm" and f.coding else []
        add(
            {
                "resourceType": "MedicationStatement",
                "id": f"med-{code}",
                "status": "active",
                "medicationCodeableConcept": {"coding": coding, "text": f.label},
                "subject": subject,
            }
        )
    return {"resourceType": "Bundle", "id": bundle_id, "type": "collection", "timestamp": EVAL_TIME.strftime("%Y-%m-%dT%H:%M:%SZ"), "entry": entries}


def structural_check(bundle: dict) -> list[str]:
    """Return a list of structural problems (empty = shape looks fine). Not HL7 validation."""
    problems: list[str] = []
    if bundle.get("resourceType") != "Bundle":
        return ["not a Bundle"]
    ids = set()
    for e in bundle.get("entry", []):
        r = e.get("resource", {})
        rt, rid = r.get("resourceType"), r.get("id")
        if not rt or not rid:
            problems.append("resource without resourceType/id")
            continue
        ids.add(f"{rt}/{rid}")
    for e in bundle.get("entry", []):
        r = e["resource"]
        rt = r.get("resourceType")
        if rt == "Observation":
            for req in ("status", "code", "subject", "effectiveDateTime", "valueQuantity"):
                if req not in r:
                    problems.append(f"Observation/{r['id']}: missing {req}")
            vq = r.get("valueQuantity", {})
            if not isinstance(vq.get("value"), (int, float)):
                problems.append(f"Observation/{r['id']}: valueQuantity.value is not numeric")
        if rt == "Patient":
            bd = r.get("birthDate", "")
            if len(bd) != 10:
                problems.append("Patient.birthDate is not YYYY-MM-DD")
        ref = (r.get("subject") or {}).get("reference")
        if ref and ref not in ids:
            problems.append(f"{rt}/{r['id']}: subject {ref} not in bundle")
    return problems
