"""Concept search for the typeahead (FR-1). Served at `/concepts`; the public path is `/api/concepts?q=`.

Reads the vocabulary through the READER connection only. Only condition and drug concepts can appear in a cohort
definition, so only those are searchable; classification concepts ('C', e.g. ATC classes) are included because
definitions use them as ancestors. Results are `{concept_id, name, domain}` with the domain spelled as the definition
model spells it. No patient-level data is touched.
"""
from fastapi import APIRouter, Request
from sqlalchemy import case, func, select
from starlette.concurrency import run_in_threadpool

from app.cohort_engine.schema import concept
from app.db.session import reader_engine
from app.errors import error_response

router = APIRouter(prefix="/concepts")

MIN_QUERY_CHARS = 2     # shorter queries run no search at all
MAX_QUERY_CHARS = 100
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
SEARCHABLE_DOMAINS = {"condition": "Condition", "drug": "Drug"}  # request spelling -> vocabulary spelling

def search_statement(needle: str, domains: list[str], limit: int):
    """Names containing `needle`, best first: prefix matches, then word starts, then anything else; shorter names
    first within a rank. `needle` is lower-cased and every value is a bound parameter; LIKE wildcards are escaped."""
    name = func.lower(concept.c.concept_name)
    rank = case((name.startswith(needle, autoescape=True), 0), (name.contains(" " + needle, autoescape=True), 1),
                else_=2)
    return (
        select(concept.c.concept_id, concept.c.concept_name, concept.c.domain_id)
        .where(concept.c.domain_id.in_(domains), concept.c.standard_concept.in_(["S", "C"]),
               concept.c.invalid_reason.is_(None), name.contains(needle, autoescape=True))
        .order_by(rank, func.length(concept.c.concept_name), concept.c.concept_name, concept.c.concept_id)
        .limit(limit)
    )


def _search(needle: str, domains: list[str], limit: int) -> list[dict]:
    with reader_engine().connect() as conn:
        rows = conn.execute(search_statement(needle, domains, limit)).all()
    return [{"concept_id": row.concept_id, "name": row.concept_name, "domain": row.domain_id.lower()} for row in rows]


def _invalid(request: Request, field: str, message: str):
    return error_response(request, 422, "Invalid search", [{"field": field, "message": message}])


@router.get("")
async def search_concepts(request: Request):
    params = request.query_params
    query = params.get("q", "").strip()
    if len(query) > MAX_QUERY_CHARS:
        return _invalid(request, "q", f"The query can have at most {MAX_QUERY_CHARS} characters")
    if not query.isprintable():
        return _invalid(request, "q", "The query contains characters that cannot be searched")

    domain = params.get("domain")
    if domain is not None and domain.lower() not in SEARCHABLE_DOMAINS:
        return _invalid(request, "domain", "The domain must be condition or drug")
    raw_limit = params.get("limit", str(DEFAULT_LIMIT))
    if not (raw_limit.isascii() and raw_limit.isdigit() and 1 <= int(raw_limit) <= MAX_LIMIT):
        return _invalid(request, "limit", f"The limit must be a whole number from 1 to {MAX_LIMIT}")

    if len(query) < MIN_QUERY_CHARS:
        return []  # too short to be a useful search: nothing is run
    domains = [SEARCHABLE_DOMAINS[domain.lower()]] if domain else list(SEARCHABLE_DOMAINS.values())
    return await run_in_threadpool(_search, query.lower(), domains, int(raw_limit))
