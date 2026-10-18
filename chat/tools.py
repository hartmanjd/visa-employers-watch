"""The lookups the chat model may call, as tool definitions plus the code that runs them.

Each tool is a thin wrapper over a function in dashboard/queries.py, so the chat answers from the
same tested queries as the website. Results are trimmed to what an answer needs and always carry
the record IDs to cite: `employer_id`, WHD `case_id`, OSHA `activity_nr` and LCA/H-2B `case_number`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dashboard"))
import queries as q  # noqa: E402

MAX_ROWS = 25
PROGRAMS = ["H-1B", "H-2B"]
WATCHLIST_SORTS = ["back_wages", "n_cases", "violations", "visa_violations", "workers", "penalties",
                   "n_applications"]
OSHA_SORTS = ["penalties", "inspections", "violations", "serious", "willful", "repeat_violations", "deaths",
              "whd_back_wages", "n_applications"]
GROUP_SORTS = ["employers", "employers_with_cases", "share_with_cases", "cases", "cases_per_100",
               "back_wages", "penalties", "visa_violations"]


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


# What the fields mean, for any model that reads the tool results (the chat and the MCP server).
DATA_NOTES = """- Applications are FY2025 DOL disclosure records (LCA for H-1B, H-2B certifications), one row per case number, latest status.
- An employer row is one company name at one address in one program; one company can have several rows. Rows sharing a tax ID are listed under same_tax_id_elsewhere.
- WHD cases cover every law WHD enforces (minimum wage, overtime, child labor, H-1B, H-2B, H-2A and others) and every year in the data, not only visa violations. Say "visa-program violations" only for h1b_violations on an H-1B employer or h2b_violations on an H-2B employer.
- WHD records have no tax ID, so cases are linked to employers by cleaned name plus ZIP code or state. Some links are wrong (about 3 to 4 percent in a tax-ID test). When a single case drives your answer, mention that it is a name match.
- A case with 0 violations and $0 back wages means WHD investigated and recorded no violations.
- Each case's laws list says which laws WHD found violations under (minimum wage and overtime, child labor, family leave, federal contracts, the visa programs and others), with violations, back wages, workers owed and penalties for each. child_labor_minors, repeat_violator (WHD's own Repeat or Willful finding under the FLSA) and the minimum wage, overtime and retaliation back wage split appear when WHD recorded them. WHD publishes these counts, not a description of what happened.
- red_flags are patterns in the employer's own FY2025 applications (wages at the floor, mostly entry-level wage levels, many withdrawn or denied applications, most workers placed outside the home state). They are reasons for a closer look, not evidence of a violation; say so.
- dol_debarments are entries on DOL's published debarment lists (H-1B, H-2A, H-2B, permanent labor certification) whose name, and state when DOL gives one, match the employer. Debarment is a DOL enforcement finding. The match is by name, so say it is a name match.
- representatives are the law firms, agent businesses and individual attorneys or agents named on applications, grouped across every employer they file for (firms by cleaned name, people by name and state). times_typical compares how many of their clients have visa-program violations with what the same mix of H-1B and H-2B employers shows overall. Representing an employer does not make a firm responsible for its violations; say so.
- osha is the employer's OSHA safety and health inspections (opened 2004 or later), linked by cleaned name plus ZIP code or state of the inspected site or the mailing address, like WHD cases; say it is a name match when one inspection drives your answer. Each inspection lists its citation items: the OSHA standard broken (citation and title), the type (Serious, Willful, Repeat, Other than serious), workers exposed, the penalty OSHA first proposed (initial_penalty) and the current one after settlement or decision (penalty), and whether the hazard was fixed. Deleted citation items are left out. accidents give OSHA's written account of a death or serious injury tied to the inspection and the injured workers (killed, hospitalized). OSHA accident reports are published after the case closes, so recent inspections may have none yet. osha_url is OSHA's own page for the inspection; cite it.
- willful_violator is the employer's own Yes/No answer on its applications, not a DOL finding. It counts only when most applications say Yes; willful_yes gives the count.
- Money is in U.S. dollars. back_wages is the back wages WHD found owed; penalties are civil money penalties assessed."""

_PROGRAM = {"type": "string", "enum": PROGRAMS, "description": "Visa program: H-1B or H-2B"}
_VISA_ONLY = {"type": "boolean", "description": "Count only WHD cases that found violations of the "
              "employer's own visa program (H-1B or H-2B)"}
_LIMIT = {"type": "integer", "description": f"How many rows to return, at most {MAX_ROWS} (default 10)"}

TOOLS = [
    {"name": "search_employers",
     "description": "Find visa employers by company name, trade name (DBA) or 9-digit tax ID (FEIN). "
                    "Returns up to 10 matches with employer_id, program, location, applications and "
                    "linked WHD case count. One company can have several rows: one per program and address.",
     "input_schema": _schema({"query": {"type": "string", "description": "Name, DBA or tax ID"}}, ["query"])},
    {"name": "get_employer",
     "description": "One employer's details: address, tax ID, industry, application counts, flags, "
                    "totals of its linked WHD cases, each linked case (case_id, dates, violations, back "
                    "wages, penalties, which laws were violated, how it was matched), its most common jobs and how its offered wages "
                    "compare with the prevailing wage, other rows sharing its tax ID, and any matching entry on DOL's "
                    "published debarment lists, and the law firms and attorneys or agents that filed its applications with how many of their other clients have visa-program violations, "
                    "and its linked OSHA inspections with each citation (standard, type, penalty) and any accident reports.",
     "input_schema": _schema({"employer_id": {"type": "integer"}}, ["employer_id"])},
    {"name": "list_applications",
     "description": "An employer's visa applications, newest decision first: case_number, status, job, "
                    "worksite, workers, offered and prevailing wage, law firm.",
     "input_schema": _schema({"employer_id": {"type": "integer"}, "limit": _LIMIT}, ["employer_id"])},
    {"name": "get_application",
     "description": "One visa application by its DOL case number (like I-200-25126-941960 or "
                    "H-300-25001-123456): job, workers, dates, worksite, offered against prevailing wage, "
                    "law firm and the employer that filed it.",
     "input_schema": _schema({"case_number": {"type": "string"}}, ["case_number"])},
    {"name": "watchlist",
     "description": "Visa employers that have linked WHD cases, ranked, with filters, plus totals that "
                    "count each case once. Use it for rankings and counts across employers.",
     "input_schema": _schema({
         "program": _PROGRAM,
         "state": {"type": "string", "description": "Two-letter employer state, like TX"},
         "sector": {"type": "string", "enum": list(q.SECTORS), "description": "NAICS sector key"},
         "since_year": {"type": "integer", "description": "Keep cases whose findings ended in or after this year"},
         "visa_violations_only": _VISA_ONLY,
         "min_applications": {"type": "integer", "description": "Only employers with at least this many applications"},
         "sort_by": {"type": "string", "enum": WATCHLIST_SORTS, "description": "Highest first (default back_wages)"},
         "limit": _LIMIT})},
    {"name": "state_stats",
     "description": "Per employer state: visa employers, how many have linked WHD cases, the share, "
                    "cases, cases per 100 employers, back wages, penalties and visa-program violations.",
     "input_schema": _schema({"program": _PROGRAM, "visa_violations_only": _VISA_ONLY,
                              "sort_by": {"type": "string", "enum": GROUP_SORTS}, "limit": _LIMIT})},
    {"name": "industry_stats",
     "description": "The same numbers as state_stats, per NAICS industry sector.",
     "input_schema": _schema({"program": _PROGRAM, "visa_violations_only": _VISA_ONLY,
                              "sort_by": {"type": "string", "enum": GROUP_SORTS}, "limit": _LIMIT})},
    {"name": "representatives",
     "description": "Law firms and agent businesses (kind firm) or individual attorneys and agents (kind person) "
                    "across every employer they file for: clients, clients with linked WHD cases, clients with "
                    "visa-program violations, times_typical (that count against the overall rate for the same "
                    "client mix), debarred clients and back wages. Without key it ranks representatives with at "
                    "least min_clients clients, optionally filtered by name. With key (from a ranked row or "
                    "get_employer) it returns that representative's clients that have WHD cases or debarments.",
     "input_schema": _schema({
         "kind": {"type": "string", "enum": ["firm", "person"], "description": "Default firm"},
         "query": {"type": "string", "description": "Part of the firm or person name"},
         "key": {"type": "string", "description": "One representative's key, to list its clients"},
         "program": _PROGRAM,
         "min_clients": {"type": "integer", "description": "Default 10"},
         "sort_by": {"type": "string", "enum": ["clients_visa_violations", "times_typical", "clients",
                                                "back_wages", "clients_debarred"],
                     "description": "Highest first (default clients_visa_violations)"},
         "limit": _LIMIT})},
    {"name": "osha_watchlist",
     "description": "Visa employers with linked OSHA safety and health inspections, ranked: inspections, citation "
                    "items, Serious, Willful and Repeat items, current penalties, deaths in accidents tied to the "
                    "inspections, and the employer's linked WHD cases and back wages beside them. Use with_whd for "
                    "employers with both safety and wage records.",
     "input_schema": _schema({
         "program": _PROGRAM,
         "state": {"type": "string", "description": "Two-letter employer state, like TX"},
         "sector": {"type": "string", "enum": list(q.SECTORS), "description": "NAICS sector key"},
         "with_whd": {"type": "boolean", "description": "Only employers that also have linked WHD cases"},
         "min_applications": {"type": "integer", "description": "Only employers with at least this many applications"},
         "sort_by": {"type": "string", "enum": OSHA_SORTS, "description": "Highest first (default penalties)"},
         "limit": _LIMIT})},
    {"name": "data_overview",
     "description": "Totals for the whole database: employers, applications, employers with linked "
                    "cases, cases, back wages and penalties, and the OSHA inspections linked to visa employers.",
     "input_schema": _schema({})},
]


def _limit(args, default=10):
    return max(1, min(int(args.get("limit") or default), MAX_ROWS))


def _money(x):
    return round(x or 0, 2)


def search_employers(con, args):
    hits = q.search_employers(con, args["query"], limit=10)
    return {"matches": [{k: h[k] for k in ("employer_id", "employer_name", "dba", "program", "city", "state",
                                           "fein", "n_applications", "n_cases", "willful_violator", "willful_yes")}
                        for h in hits]}


def get_employer(con, args):
    eid = int(args["employer_id"])
    e = q.employer(con, eid)
    if not e:
        raise LookupError(f"No employer with id {eid}")
    keep = ("employer_id", "program", "employer_name", "dba", "address", "city", "state", "zip5", "fein",
            "naics", "visa_classes", "n_applications", "n_certified", "h1b_dependent", "willful_violator",
            "willful_yes")
    cases = q.cases(con, eid)
    profile = q.employer_profile(con, eid)
    debarred = [{k: d[k] for k in ("program", "entity", "entity_type", "location", "start_date", "end_date",
                                   "status", "violation", "applications_during", "match", "source_url")}
                for d in q.employer_debarments(con, eid)]
    return {
        "employer": {**{k: e.get(k) for k in keep}, "sector": q.SECTORS.get(q.sector_of(e["naics"]))},
        "totals": {k: (_money(v) if k in ("back_wages", "penalties") else v)
                   for k, v in q.summary(con, eid).items()},
        "cases": [{"case_id": v["case_id"], "whd_name": v["trade_nm"] or v["legal_name"],
                   "whd_city": v["cty_nm"], "whd_state": v["st_cd"],
                   "findings_start": (v["findings_start_date"] or "")[:10],
                   "findings_end": (v["findings_end_date"] or "")[:10],
                   "violations": v["case_violtn_cnt"], "back_wages": _money(v["bw_atp_amt"]),
                   "workers_owed": v["ee_violtd_cnt"], "penalties": _money(v["cmp_assd"]),
                   "h1b_violations": v["h1b_violtn_cnt"], "h2b_violations": v["h2b_violtn_cnt"],
                   "laws": [{"law": r["name"], "violations": r["violations"], "back_wages": _money(r["back_wages"]),
                             "workers_owed": r["workers"], "penalties": _money(r["penalties"])}
                            for r in v["laws"]],
                   **{k: (_money(x) if k.endswith("back_wages") else x)
                      for k, x in v["details"].items() if x},
                   "match": q.match_label(v["method"], v["est_false_match_rate"])}
                  for v in cases[:MAX_ROWS]],
        "cases_not_shown": max(0, len(cases) - MAX_ROWS),
        "top_jobs": [{k: j[k] for k in ("soc_title", "applications", "workers")} for j in profile["jobs"][:5]],
        "offered_vs_prevailing_wage": [b for b in profile["wage_bands"] if b["applications"]],
        "same_tax_id_elsewhere": q.same_tax_id(con, eid)[:10],
        "red_flags": [{k: f[k] for k in ("title", "applications", "of", "baseline", "detail")}
                      for f in q.red_flags(con, eid)],
        "dol_debarments": debarred,
        "representatives": [{k: r[k] for k in _REP_KEEP} for r in q.employer_representatives(con, eid)],
        **({"osha": _osha(con, eid)} if q.osha_ready(con) else {}),
    }


def _osha(con, eid):
    insp = q.osha_inspections(con, eid)
    summary = q.osha_summary(con, eid)
    return {
        "totals": {k: (_money(v) if k.endswith("penalty") or k == "penalties" else v)
                   for k, v in summary.items() if k != "employer_id"},
        "inspections": [{
            "activity_nr": i["activity_nr"], "osha_url": i["osha_url"], "establishment": i["estab_name"],
            "site": f"{i['site_address']}, {i['site_city']}, {i['site_state']} {i['site_zip']}",
            "opened": (i["open_date"] or "")[:10], "closed": (i["close_case_date"] or "")[:10] or None,
            "type": i["type_label"], "focus": i["focus"], "scope": i["scope_label"], "employees": i["nr_in_estab"],
            "initial_penalty": _money(i["initial_penalty"]), "penalty": _money(i["penalties"]),
            "citations": [{"citation_id": v["citation_id"], "standard": v["standard_info"]["citation"],
                           "standard_title": v["standard_info"]["title"], "type": v["type_label"],
                           "gravity": v["gravity"], "workers_exposed": v["nr_exposed"],
                           "initial_penalty": _money(v["initial_penalty"]), "penalty": _money(v["current_penalty"]),
                           "abatement": v["abatement"], **({"hazard": v["hazard_text"]} if v["hazard_text"] else {})}
                          for v in i["violations"] if not v["deleted"]],
            "accidents": [{"summary_nr": a["summary_nr"], "date": (a["event_date"] or "")[:10],
                           "description": a["event_desc"], "account": (a["narrative"] or "")[:1500] or None,
                           "injured": [{k: j[k] for k in ("degree", "nature", "body_part", "age") if j[k]}
                                       for j in a["injuries"]]}
                          for a in i["accidents"]],
            "match": q.match_label(i["method"], i["est_false_match_rate"])}
            for i in insp[:10]],
        "inspections_not_shown": max(0, len(insp) - 10),
    }


_REP_KEEP = ("kind", "key", "name", "clients", "applications", "clients_with_cases", "clients_visa_violations",
             "expected_visa_violations", "times_typical", "clients_debarred", "cases", "back_wages")


def representatives(con, args):
    kind = args.get("kind") or "firm"
    if args.get("key"):
        r = q.representative(con, kind, args["key"])
        if not r:
            raise LookupError(f"No {kind} with key {args['key']!r}; use the key from a ranked row or get_employer")
        flagged = [c for c in r["client_list"] if c["cases"] or c["debarred"]]
        return {**{k: r[k] for k in _REP_KEEP}, "name_variants": r["name_variants"][:5], "feins": r["feins"][:5],
                "clients_with_records": flagged[:_limit(args, MAX_ROWS)],
                "clients_with_records_not_shown": max(0, len(flagged) - _limit(args, MAX_ROWS))}
    rows = q.representatives(con, kind, min_clients=int(args.get("min_clients") or 10),
                             query=args.get("query"), program=args.get("program"),
                             sort=args.get("sort_by") or "clients_visa_violations")
    n = _limit(args)
    return {"rows": [{k: r[k] for k in _REP_KEEP} for r in rows[:n]], "total": len(rows),
            "rows_not_shown": max(0, len(rows) - n),
            "overall_visa_violation_rate": {p: round(v, 4) for p, v in q.rep_baselines(con)["visa_violations"].items()}}


_APP_KEEP = ("case_number", "case_status", "visa_class", "decision_date", "job_title", "soc_title", "workers",
             "worksite_city", "worksite_state", "wage_from", "wage_to", "wage_unit", "prevailing_wage",
             "pw_unit_of_pay", "pw_wage_level", "lawfirm")


def list_applications(con, args):
    eid = int(args["employer_id"])
    e = q.employer(con, eid)
    if not e:
        raise LookupError(f"No employer with id {eid}")
    apps = q.applications(con, eid, limit=_limit(args))
    return {"employer_id": eid, "total_applications": e["n_applications"],
            "applications": [{k: a.get(k) for k in _APP_KEEP} for a in apps]}


def get_application(con, args):
    a = q.application(con, args["case_number"])
    if not a:
        raise LookupError(f"No application with case number {args['case_number']!r}")
    h = q.application_highlights(a)
    return {"case_number": a["case_number"], "program": a["program"], "case_status": a.get("case_status"),
            "visa_class": a.get("visa_class"), "decision_date": a.get("decision_date"),
            "employer_id": a["employer_id"], "employer_name": a["employer_row_name"],
            "employer_city": a["employer_row_city"], "employer_state": a["employer_row_state"],
            **{k: v for k, v in h.items() if v is not None}}


def watchlist(con, args):
    filters = dict(program=args.get("program"), state=(args.get("state") or "").upper() or None,
                   sector=args.get("sector"), since_year=args.get("since_year"),
                   visa_violations_only=bool(args.get("visa_violations_only")),
                   min_applications=int(args.get("min_applications") or 1))
    rows = q.watchlist(con, **filters)
    for r in rows:
        r["visa_violations"] = r["h1b_violations"] if r["program"] == "H-1B" else r["h2b_violations"]
    key = args.get("sort_by") or "back_wages"
    if key not in WATCHLIST_SORTS:
        raise ValueError(f"sort_by must be one of {WATCHLIST_SORTS}")
    rows.sort(key=lambda r: (-(r[key] or 0), r["employer_id"]))
    totals = q.watchlist_totals(con, **filters)
    keep = ("employer_id", "employer_name", "program", "city", "state", "n_applications", "n_cases",
            "violations", "visa_violations", "back_wages", "workers", "penalties", "latest_case")
    return {"filters": {k: v for k, v in filters.items() if v}, "sorted_by": key,
            "employers": len(rows), "cases": totals["cases"], "back_wages": _money(totals["back_wages"]),
            "penalties": _money(totals["penalties"]),
            "rows": [{k: (_money(r[k]) if k in ("back_wages", "penalties") else r[k]) for k in keep}
                     for r in rows[:_limit(args)]]}


def osha_watchlist(con, args):
    if not q.osha_ready(con):
        raise LookupError("This database has no OSHA data")
    rows = q.osha_watchlist(con, program=args.get("program"), state=(args.get("state") or "").upper() or None,
                            sector=args.get("sector"), with_whd=bool(args.get("with_whd")),
                            min_applications=int(args.get("min_applications") or 1))
    key = args.get("sort_by") or "penalties"
    if key not in OSHA_SORTS:
        raise ValueError(f"sort_by must be one of {OSHA_SORTS}")
    rows.sort(key=lambda r: (-(r[key] or 0), r["employer_id"]))
    keep = ("employer_id", "employer_name", "program", "city", "state", "n_applications", "inspections",
            "violations", "serious", "willful", "repeat_violations", "initial_penalty", "penalties", "accidents",
            "deaths", "latest_inspection", "whd_cases", "whd_back_wages")
    money_keys = ("initial_penalty", "penalties", "whd_back_wages")
    return {"sorted_by": key, "employers": len(rows),
            "rows": [{k: (_money(r[k]) if k in money_keys else r[k]) for k in keep} for r in rows[:_limit(args)]]}


def _group(con, args, fn, name_key):
    rows = fn(con, program=args.get("program"), visa_violations_only=bool(args.get("visa_violations_only")))
    key = args.get("sort_by") or "employers"
    if key not in GROUP_SORTS:
        raise ValueError(f"sort_by must be one of {GROUP_SORTS}")
    rows.sort(key=lambda r: -(r[key] or 0))
    return {"sorted_by": key, "groups": len(rows),
            "rows": [{**{k: r[k] for k in (name_key, "employers", "employers_with_cases", "cases", "visa_violations")},
                      "share_with_cases": round(r["share_with_cases"], 4),
                      "cases_per_100": round(r["cases_per_100"], 2),
                      "back_wages": _money(r["back_wages"]), "penalties": _money(r["penalties"])}
                     for r in rows[:_limit(args)]]}


def state_stats(con, args):
    return _group(con, args, q.state_stats, "state")


def industry_stats(con, args):
    out = _group(con, args, q.sector_stats, "name")
    for r in out["rows"]:
        r["industry"] = r.pop("name")
    return out


def data_overview(con, args):
    t = q.overview(con)["totals"]
    out = {k: (_money(v) if k in ("back_wages", "penalties") else v) for k, v in t.items()}
    o = q.osha_overview(con)
    if o:
        out["osha"] = {k: (_money(v) if k == "penalties" else v) for k, v in o.items()}
    return out


RUNNERS = {t["name"]: globals()[t["name"]] for t in TOOLS}


def run_tool(con, name, args):
    """Run one tool; raises KeyError for an unknown tool and LookupError/ValueError for bad input."""
    return RUNNERS[name](con, args or {})
