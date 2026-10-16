from fastapi.testclient import TestClient

from lemma.cli import main
from lemma.server import create_app
from conftest import EX

V1 = (EX / "rules" / "bp_initiation_v1.cql")
V2 = (EX / "rules" / "bp_initiation_v2.cql")


def test_cli_exit_codes(capsys):
    assert main(["check", str(V1)]) == 0
    assert main(["diff", str(V1), str(V1), "--rule", "Start antihypertensive"]) == 0
    assert main(["diff", str(V1), str(V2), "--rule", "Start antihypertensive"]) == 1
    assert main(["verify", str(V2), str(EX / "invariants" / "safety.lemma")]) == 0
    assert main(["diff", str(V1), str(V2), "--rule", "No such rule"]) == 2


def test_api_flow():
    c = TestClient(create_app())
    assert c.get("/").status_code == 200
    a, b = V1.read_text(), V2.read_text()
    chk = c.post("/api/check", json={"source": b}).json()
    assert chk["ok"] and chk["literals"]
    bad = c.post("/api/check", json={"source": "library X\ndefine \"a\": Latest(\"zzz\") > 1"}).json()
    assert not bad["ok"]
    d = c.post("/api/diff", json={"a": a, "b": b, "rule": "Start antihypertensive"}).json()
    assert d["status"] == "different"
    g = c.post("/api/grid", json={"a": a, "b": b, "rule": "Start antihypertensive", "x": {"var": "sbp"}, "y": {"var": "dbp"}, "base": {"age": 60, "sex": "female", "observations": {}}, "size": 16}).json()
    assert g["counts"]["3"] > 0 if "3" in g["counts"] else g["counts"][3] > 0
    assert c.post("/api/diff", json={"a": a, "b": b, "rule": "Nope"}).status_code == 422
