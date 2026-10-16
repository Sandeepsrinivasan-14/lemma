"""HTTP API + static UI. Run with `lemma serve`.

Binds to localhost by default. There is no authentication: do not expose this to a network, and do not
paste real patient data into it (the cohort used for impact numbers is synthetic).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .lang.check import LemmaCheckError
from .lang.lexer import LemmaSyntaxError
from .paths import examples_dir
from .schema import load_schema
from .service import Workspace

WEB = Path(__file__).parent / "web"


class Source(BaseModel):
    source: str = Field(max_length=60000)


class RuleReq(Source):
    rule: str


class DiffReq(BaseModel):
    a: str = Field(max_length=60000)
    b: str = Field(max_length=60000)
    rule: str
    rule_b: Optional[str] = None
    mode: str = "fires"
    summary: bool = True


class ImpactReq(BaseModel):
    a: str = Field(max_length=60000)
    b: str = Field(max_length=60000)
    rule: str
    rule_b: Optional[str] = None
    n: int = Field(3000, ge=100, le=20000)
    seed: int = 7


class VerifyReq(BaseModel):
    source: str = Field(max_length=60000)
    invariants: str = Field(max_length=20000)


class GridReq(BaseModel):
    a: str = Field(max_length=60000)
    b: str = Field(max_length=60000)
    rule: str
    rule_b: Optional[str] = None
    x: dict
    y: dict
    base: dict
    size: int = Field(56, ge=8, le=90)


def create_app(schema_path: Optional[str] = None) -> FastAPI:
    ex = examples_dir()
    schema = load_schema(schema_path or ex / "schema.toml")
    ws = Workspace(schema)
    app = FastAPI(title="Lemma", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.exception_handler(LemmaSyntaxError)
    async def _syntax(_, e: LemmaSyntaxError):
        return JSONResponse(status_code=422, content={"error": "syntax", "message": e.msg, "line": e.line, "col": e.col})

    @app.exception_handler(LemmaCheckError)
    async def _check(_, e: LemmaCheckError):
        return JSONResponse(status_code=422, content={"error": "check", "diagnostics": [d.to_dict() for d in e.diagnostics], "message": str(e)})

    @app.exception_handler(KeyError)
    async def _key(_, e: KeyError):
        return JSONResponse(status_code=422, content={"error": "unknown-name", "message": str(e.args[0]) if e.args else "unknown name"})

    @app.get("/api/schema")
    def get_schema() -> dict:
        return ws.schema_info()

    @app.get("/api/examples")
    def get_examples() -> dict:
        rules = {p.stem: p.read_text() for p in sorted((ex / "rules").glob("*.cql"))}
        invs = {p.stem: p.read_text() for p in sorted((ex / "invariants").glob("*.lemma"))}
        return {"rules": rules, "invariants": invs}

    @app.post("/api/check")
    def check(req: Source) -> dict:
        return ws.check(req.source)

    @app.post("/api/diff")
    def diff(req: DiffReq) -> dict:
        if req.mode not in ("fires", "strict"):
            raise HTTPException(422, "mode must be 'fires' or 'strict'")
        return ws.diff(req.a, req.b, req.rule, req.rule_b, req.summary, req.mode)

    @app.post("/api/grid")
    def grid(req: GridReq) -> dict:
        return ws.grid(req.a, req.b, req.rule, req.x, req.y, req.base, req.rule_b, req.size, req.size)

    @app.post("/api/impact")
    def impact(req: ImpactReq) -> dict:
        return ws.impact(req.a, req.b, req.rule, req.n, req.seed, req.rule_b)

    @app.post("/api/verify")
    def verify(req: VerifyReq) -> dict:
        return ws.verify(req.source, req.invariants)

    @app.post("/api/missing")
    def missing(req: RuleReq) -> dict:
        return ws.missing(req.source, req.rule)

    @app.post("/api/robustness")
    def robustness(req: RuleReq) -> dict:
        return ws.robustness(req.source, req.rule)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def serve(host: str = "127.0.0.1", port: int = 8000, schema_path: Optional[str] = None) -> None:
    import uvicorn

    uvicorn.run(create_app(schema_path), host=host, port=port, log_level="warning")
