"""JSON API over research.db for the dashboard website, and the site's static files.

Run from the repo root:  uvicorn web.api:app
Then open http://localhost:8000. Reads h1b-data/research.db (or RESEARCH_DB), read-only, and
downloads it on first start if it is missing (see dashboard/queries.py).
"""
import csv, io, json, os, queue, sys, threading
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dashboard"))
import queries as q  # noqa: E402
sys.path.insert(0, os.path.join(HERE, ".."))
from chat.mcp_server import router as mcp_router  # noqa: E402
from chat import engine, limits  # noqa: E402

PAGE_SIZE = 100
SITE_URL = engine.SITE_URL


@asynccontextmanager
async def lifespan(app):
    q.download_db()  # no-op when the database is already there
    threading.Thread(target=q.warm_red_flags, daemon=True).start()
    threading.Thread(target=q.warm_representatives, daemon=True).start()
    yield


app = FastAPI(title="H-1B / H-2B employer research API", lifespan=lifespan)
app.include_router(mcp_router)  # the chat's lookups as a read-only MCP server at /mcp


def con():
    """A fresh read-only connection per request; opening one takes about a millisecond."""
    return q.connect()


def check_program(program):
    if program not in (None, "", "H-1B", "H-2B"):
        raise HTTPException(422, "program must be H-1B or H-2B")


@lru_cache(maxsize=16)
def _group_stats(kind, program, visa_only):
    """State and sector totals scan every match, so each filter combination is computed once.

    Safe to cache because the database is opened read-only and does not change while the app runs.
    """
    c = con()
    try:
        fn = q.state_stats if kind == "states" else q.sector_stats
        return fn(c, program=program, visa_violations_only=visa_only)
    finally:
        c.close()


@app.get("/api/states")
def states(program: str | None = None, visa_only: bool = False):
    """Per-state visa employers and linked WHD case totals, for the map."""
    check_program(program)
    return _group_stats("states", program or None, visa_only)


@app.get("/api/sectors")
def sectors(program: str | None = None, visa_only: bool = False):
    """Per-NAICS-sector visa employers and linked WHD case totals, for the Industries page."""
    check_program(program)
    return _group_stats("sectors", program or None, visa_only)


@lru_cache(maxsize=1)
def _overview():
    c = con()
    try:
        return q.overview(c)
    finally:
        c.close()


@app.get("/api/overview")
def overview():
    """Headline numbers and starting points for the lookup page (computed once per server start)."""
    return _overview()


@app.get("/api/meta")
def meta():
    c = con()
    try:
        return {"states": q.states(c), "sectors": q.SECTORS, "sources": q.sources(c)}
    finally:
        c.close()


@app.get("/api/search")
def search(q_: str = Query("", alias="q")):
    """Employers by name, trade name or tax ID. A DOL case number returns that application's
    employer, with the case number, so the page can open the application."""
    c = con()
    try:
        hit = q.find_application(c, q_)
        if hit:
            return [dict(r, case_number=hit["case_number"])
                    for r in q.search_employers_by_id(c, hit["employer_id"])]
        return q.search_employers(c, q_)
    finally:
        c.close()


@app.get("/api/employers/{employer_id}")
def employer(employer_id: int):
    """Everything the employer page shows except the application list, which is paged."""
    c = con()
    try:
        e = q.employer(c, employer_id)
        if not e:
            raise HTTPException(404, "No employer with that id")
        cases = q.cases(c, employer_id)
        for v in cases:
            v["match_label"] = q.match_label(v["method"], v["est_false_match_rate"])
        e["sector"] = q.sector_of(e["naics"])
        e["sector_name"] = q.SECTORS.get(e["sector"])
        return {"employer": e, "summary": q.summary(c, employer_id),
                "same_tax_id": q.same_tax_id(c, employer_id), "cases": cases,
                "case_years": q.case_years(c, employer_id), "profile": q.employer_profile(c, employer_id),
                # None while the server is still building these after a restart; the page skips them.
                "red_flags": q.red_flags(c, employer_id) if q.red_flags_ready(c) else None,
                "debarments": q.employer_debarments(c, employer_id),
                "representatives": q.employer_representatives(c, employer_id) if q.representatives_ready(c) else None}
    finally:
        c.close()


@app.get("/api/employers/{employer_id}/applications")
def applications(employer_id: int, page: int = Query(1, ge=1)):
    c = con()
    try:
        if not q.employer(c, employer_id):
            raise HTTPException(404, "No employer with that id")
        apps = q.applications(c, employer_id)
        start = (page - 1) * PAGE_SIZE
        return {"total": len(apps), "page": page, "page_size": PAGE_SIZE,
                "rows": apps[start:start + PAGE_SIZE]}
    finally:
        c.close()


@app.get("/api/applications/{case_number}")
def application(case_number: str):
    """One application with every column of its DOL disclosure record, grouped for display."""
    c = con()
    try:
        a = q.application(c, case_number)
        if not a:
            raise HTTPException(404, "No application with that case number")
        return {"application": {k: a[k] for k in ("case_number", "program", "employer_id", "case_status",
                                                  "visa_class", "source_file", "table_file", "table_sha256",
                                                  "dol_file_url", "employer_row_name", "employer_row_city",
                                                  "employer_row_state")},
                "highlights": q.application_highlights(a), "sections": q.application_sections(a)}
    finally:
        c.close()


@lru_cache(maxsize=1)
def _debarments():
    c = con()
    try:
        return q.debarments(c)
    finally:
        c.close()


@app.get("/api/debarments")
def debarments():
    """DOL's published debarment lists, each entry with the visa employers in the data that match it."""
    return _debarments()


@app.get("/api/watchlist")
def watchlist(program: str | None = None, state: str | None = None,
              since: int | None = Query(None, ge=1900, le=2100), visa_only: bool = False,
              min_apps: int = Query(1, ge=1), sector: str | None = None):
    check_program(program)
    if sector and sector not in q.SECTORS:
        raise HTTPException(422, "Unknown sector")
    filters = dict(program=program or None, state=state or None, since_year=since,
                   visa_violations_only=visa_only, min_applications=min_apps, sector=sector or None)
    c = con()
    try:
        return {"rows": q.watchlist(c, **filters), "totals": q.watchlist_totals(c, **filters)}
    finally:
        c.close()


# ---------- Shared representatives ----------
REP_SORTS = ("clients_visa_violations", "times_typical", "clients", "clients_with_cases", "back_wages",
             "clients_debarred")


@app.get("/api/representatives")
def representatives(kind: str = "firm", min_clients: int = Query(10, ge=1), q_: str = Query("", alias="q"),
                    program: str | None = None, sort: str = "clients_visa_violations",
                    limit: int = Query(200, ge=1, le=2000)):
    """Law firms, agent businesses (kind=firm) or attorneys and agents (kind=person) across all the
    employers they file for, with how many of those employers have WHD cases and visa-program violations."""
    check_program(program)
    if kind not in q.REP_KINDS:
        raise HTTPException(422, "kind must be firm or person")
    if sort not in REP_SORTS:
        raise HTTPException(422, f"sort must be one of {', '.join(REP_SORTS)}")
    c = con()
    try:
        rows = q.representatives(c, kind, min_clients=min_clients, query=q_ or None, program=program or None,
                                 sort=sort)
        return {"total": len(rows), "rows": rows[:limit], "baseline": q.rep_baselines(c)}
    finally:
        c.close()


@app.get("/api/representatives/{kind}/{key:path}")
def representative(kind: str, key: str):
    """One representative with every client employer and each client's WHD record."""
    c = con()
    try:
        r = q.representative(c, kind, key)
        if not r:
            raise HTTPException(404, "No representative with that key")
        return r
    finally:
        c.close()


# ---------- CSV downloads ----------
# Each row keeps the record IDs it came from (employer ID, DOL case number, WHD case ID) and a link to
# the site page that shows it, so a spreadsheet can be traced back to the source records.

def csv_response(rows, columns, filename):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ";".join(map(str, v)) if isinstance(v, (list, tuple)) else v for k, v in r.items()})
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def employer_page(employer_id):
    return f"{SITE_URL}/#/employer/{employer_id}"


WATCHLIST_CSV = ["employer_id", "employer_name", "dba", "city", "state", "program", "naics", "n_applications",
                 "n_certified", "n_cases", "zip_links", "violations", "h1b_violations", "h2b_violations",
                 "back_wages", "workers", "penalties", "latest_case", "willful_yes", "willful_violator",
                 "h1b_dependent", "whd_case_ids", "employer_page"]


@app.get("/api/watchlist.csv")
def watchlist_csv(program: str | None = None, state: str | None = None,
                  since: int | None = Query(None, ge=1900, le=2100), visa_only: bool = False,
                  min_apps: int = Query(1, ge=1), sector: str | None = None):
    """The watchlist with the same filters, each employer with the WHD case IDs behind its row."""
    check_program(program)
    if sector and sector not in q.SECTORS:
        raise HTTPException(422, "Unknown sector")
    filters = dict(program=program or None, state=state or None, since_year=since,
                   visa_violations_only=visa_only, min_applications=min_apps, sector=sector or None)
    c = con()
    try:
        ids = q.watchlist_case_ids(c, **filters)
        rows = [dict(r, whd_case_ids=ids.get(r["employer_id"], []), employer_page=employer_page(r["employer_id"]))
                for r in q.watchlist(c, **filters)]
    finally:
        c.close()
    parts = [program, state, sector, f"since-{since}" if since else None, "visa-only" if visa_only else None]
    name = "-".join(["watchlist", *[p for p in parts if p]])
    return csv_response(rows, WATCHLIST_CSV, f"{name}.csv")


APPLICATIONS_CSV = ["case_number", "employer_id", "program", "case_status", "visa_class", "decision_date", "fein",
                    "job_title", "soc_code", "soc_title", "workers", "begin_date", "end_date", "worksite_city",
                    "worksite_state", "wage_from", "wage_to", "wage_unit", "prevailing_wage", "pw_unit_of_pay",
                    "pw_wage_level", "lawfirm", "source_file", "dol_file_url", "table_file", "table_sha256",
                    "application_page"]


@app.get("/api/employers/{employer_id}/applications.csv")
def applications_csv(employer_id: int):
    """Every visa application this employer filed, one row per DOL case number."""
    c = con()
    try:
        if not q.employer(c, employer_id):
            raise HTTPException(404, "No employer with that id")
        rows = [dict(a, employer_id=employer_id, application_page=f"{SITE_URL}/#/application/{a['case_number']}")
                for a in q.applications(c, employer_id)]
    finally:
        c.close()
    return csv_response(rows, APPLICATIONS_CSV, f"employer-{employer_id}-applications.csv")


CASES_CSV = ["case_id", "employer_id", "trade_nm", "legal_name", "street_addr_1_txt", "cty_nm", "st_cd", "zip_cd",
             "findings_start_date", "findings_end_date", "case_violtn_cnt", "bw_atp_amt", "ee_violtd_cnt",
             "cmp_assd", "h1b_violtn_cnt", "h1b_bw_atp_amt", "h2b_violtn_cnt", "h2b_bw_atp_amt",
             "h2a_violtn_cnt", "flsa_violtn_cnt", "load_dt", "method", "est_false_match_rate", "match_label",
             "whd_name_field", "employer_name_field", "source_file", "source_sha256", "source_urls",
             "employer_page"]


@app.get("/api/employers/{employer_id}/cases.csv")
def cases_csv(employer_id: int):
    """The WHD cases linked to this employer, with WHD's own field names and how each link was made."""
    c = con()
    try:
        if not q.employer(c, employer_id):
            raise HTTPException(404, "No employer with that id")
        rows = [dict(v, employer_id=employer_id, employer_page=employer_page(employer_id),
                     match_label=q.match_label(v["method"], v["est_false_match_rate"]))
                for v in q.cases(c, employer_id)]
    finally:
        c.close()
    return csv_response(rows, CASES_CSV, f"employer-{employer_id}-whd-cases.csv")


DEBARMENTS_CSV = ["program", "entity", "entity_type", "location", "state", "start_date", "end_date", "status",
                  "violation", "cfr_citation", "willful_violator", "match", "matched_employer_ids",
                  "source_url", "source_file"]


@app.get("/api/debarments.csv")
def debarments_csv():
    """Every DOL debarment entry, with the IDs of the visa employers that match it."""
    rows = [dict(d, matched_employer_ids=[e["employer_id"] for e in d["employers"]]) for d in _debarments()]
    return csv_response(rows, DEBARMENTS_CSV, "dol-debarments.csv")


# ---------- Ask: questions answered by Claude from the database ----------
MAX_QUESTION = 600
MAX_HISTORY = 6  # earlier messages sent back with a follow-up (three questions and their answers)
MAX_TURN = 4000
spend = limits.SpendCap(path=os.environ.get("CHAT_SPEND_FILE", "/tmp/visa-chat-spend.json"))
rate = limits.RateLimit()
_client = None


def chat_client():
    """The Anthropic client, or None when no API key is set."""
    global _client
    if _client is None and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def visitor(request):
    """The visitor's IP address. Render puts the real one first in X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/api/ask")
def ask_status():
    """Whether the Ask page can take questions right now."""
    if not chat_client():
        return {"enabled": False, "reason": "not_set_up"}
    if spend.left() < limits.RESERVE_USD:
        return {"enabled": False, "reason": "daily_limit"}
    return {"enabled": True, "model": engine.MODEL}


@app.post("/api/ask")
def ask(body: dict, request: Request):
    """Answer one question, streamed as JSON lines: a "tool" line as each lookup starts, then one
    "answer" line (or an "error" line). `history` holds earlier questions and answers as plain text."""
    question = str(body.get("question") or "").strip()
    if not question or len(question) > MAX_QUESTION:
        raise HTTPException(422, f"Ask a question of up to {MAX_QUESTION} characters.")
    history = body.get("history") or []
    if not isinstance(history, list):
        raise HTTPException(422, "history must be a list")
    history = [{"role": m.get("role"), "content": str(m.get("content") or "")[:MAX_TURN]}
               for m in history[-MAX_HISTORY:] if isinstance(m, dict)]
    while history and history[0]["role"] != "user":
        history = history[1:]
    if any(m["role"] != ("user", "assistant")[i % 2] or not m["content"] for i, m in enumerate(history)) \
            or len(history) % 2:
        raise HTTPException(422, "history must alternate question and answer")
    client = chat_client()
    if not client:
        raise HTTPException(503, "The Ask page isn't set up on this server yet.")
    wait = rate.check(visitor(request))
    if wait:
        raise HTTPException(429, wait)
    if not spend.reserve():
        raise HTTPException(503, "Today's question budget is used up. It resets at midnight UTC.")

    events = queue.Queue()

    def work():
        c, cost = con(), 0.0
        try:
            r = engine.answer(c, question, history, client=client, on_event=events.put)
            cost = r["cost_usd"]
            events.put({"type": "answer", **{k: r[k] for k in ("answer", "raw", "citations", "unverified", "tool_calls",
                                                               "stop_reason", "model")}})
        except Exception as e:  # the API failing, timing out or rejecting the key
            events.put({"type": "error", "message": "The answer couldn't be finished. Please try again.",
                        "detail": type(e).__name__})
        finally:
            spend.settle(cost)
            c.close()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while (ev := events.get()) is not None:
            yield json.dumps(ev, default=str) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
