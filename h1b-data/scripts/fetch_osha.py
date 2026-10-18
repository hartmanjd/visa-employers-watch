"""Download OSHA enforcement records through the DOL API.

Needs a free API key from https://dataportal.dol.gov, set as env var DOL_API_KEY
(added to each request URL, as fetch_whd.py does; the key is never written anywhere).

Three steps, so that full records are downloaded only for inspections linked to visa filers:

  python3 fetch_osha.py index OUT.csv SINCE [BEFORE]   (dates like 2004-01-01)
      The name, site and mailing ZIP and state, and open date of every inspection opened
      on or after SINCE (and before BEFORE, if given): just what matching needs, so a
      whole month fits in one request. Re-run with the same OUT to resume after the
      last finished month.

  python3 match_osha.py INDEX.csv FILERS_LCA.csv FILERS_H2B.csv IDS.txt
      The activity numbers of the inspections that match a visa filer.

  python3 fetch_osha.py details IDS.txt OUTDIR
      For those inspections, every column of the inspection itself and every row of the
      OSHA tables that describe what it found: violation (one row per citation item),
      violation_gen_duty_std (the written hazard of general duty clause citations, fetched
      for the inspections that have one),
      accident_injury (by rel_insp_nr), and for the accidents found there, accident and
      accident_abstract (the written account); plus accident_lookup2, OSHA's code list
      for the injury columns. One CSV per table in OUTDIR, named osha_<table>.csv. A re-run
      skips the tables already finished (those with a manifest).

Each CSV gets OUT.csv.manifest.json recording the source URL, download time, row count
and checksum. Every row keeps DOL's load_dt (when DOL loaded it).
"""
import csv, datetime, json, os, sys, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from provenance import write_manifest

BASE = "https://apiprod.dol.gov/v4/get/OSHA/{}/json"
PAGE = 1000  # rows per request with every column (the API refuses responses over a few MB)
INDEX_PAGE = 10000  # rows per request in the index step, which asks for only INDEX_FIELDS
PAUSE = 1  # seconds between requests; the API rate-limits bursts
BATCH = 100  # activity numbers per "in" filter in the details step (longer URLs are refused)
INDEX_FIELDS = ["activity_nr", "estab_name", "site_state", "site_zip", "mail_state", "mail_zip", "open_date"]
INSPECTION_FIELDS = [
    "activity_nr", "reporting_id", "state_flag", "estab_name", "site_address", "site_city",
    "site_state", "site_zip", "owner_type", "owner_code", "adv_notice", "safety_hlth", "sic_code",
    "naics_code", "insp_type", "insp_scope", "why_no_insp", "union_status", "safety_manuf",
    "safety_const", "safety_marit", "health_manuf", "health_const", "health_marit", "migrant",
    "mail_street", "mail_city", "mail_state", "mail_zip", "host_est_key", "nr_in_estab",
    "open_date", "case_mod_date", "close_conf_date", "close_case_date", "load_dt"]
# table -> (field the activity/summary numbers filter on, all columns in DOL's order)
DETAILS = {
    "inspection": ("activity_nr", INSPECTION_FIELDS),
    "violation": ("activity_nr", [
        "activity_nr", "citation_id", "delete_flag", "standard", "viol_type", "issuance_date",
        "abate_date", "abate_complete", "current_penalty", "initial_penalty", "contest_date",
        "final_order_date", "nr_instances", "nr_exposed", "rec", "gravity", "emphasis", "hazcat",
        "fta_insp_nr", "fta_issuance_date", "fta_penalty", "fta_contest_date",
        "fta_final_order_date", "hazsub1", "hazsub2", "hazsub3", "hazsub4", "hazsub5", "load_dt"]),
    "violation_gen_duty_std": ("activity_nr", [
        "activity_nr", "citation_id", "line_nr", "line_text", "load_dt"]),
    "accident_injury": ("rel_insp_nr", [
        "summary_nr", "rel_insp_nr", "age", "sex", "nature_of_inj", "part_of_body",
        "src_of_injury", "event_type", "evn_factor", "hum_factor", "occ_code", "degree_of_inj",
        "task_assigned", "hazsub", "const_op", "const_op_cause", "fat_cause", "fall_distance",
        "fall_ht", "injury_line_nr", "load_dt"]),
    "accident": ("summary_nr", [
        "summary_nr", "report_id", "event_date", "event_time", "event_desc", "event_keyword",
        "const_end_use", "build_stories", "nonbuild_ht", "project_cost", "project_type",
        "sic_list", "fatality", "state_flag", "abstract_text", "load_dt"]),
    "accident_abstract": ("summary_nr", ["summary_nr", "line_nr", "abstract_text", "load_dt"]),
}
LOOKUP_FIELDS = ["accident_code", "accident_number", "accident_letter", "accident_value", "load_dt"]


def get(table, params):
    key = os.environ.get("DOL_API_KEY", "").strip()
    if key:
        params = {**params, "X-API-KEY": key}
    q = urllib.parse.urlencode(params)
    for attempt in range(10):
        try:
            with urllib.request.urlopen(f"{BASE.format(table)}?{q}", timeout=120) as r:
                body = r.read()
            time.sleep(PAUSE)
            return json.loads(body).get("data", []) if body.strip() else []  # 204 = past the end
        except Exception as e:  # retry transient errors and rate limits (429)
            wait = getattr(e, "headers", None) and e.headers.get("Retry-After")
            wait = int(wait) if wait and wait.isdigit() else min(2 ** (attempt + 2), 300)
            print("retry", table, attempt, e, f"waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit("API kept failing")


def get_all(table, flt=None, fields=None, page_size=PAGE):
    rows, off = [], 0
    while True:
        params = {"limit": page_size, "offset": off}
        if flt:
            params["filter_object"] = json.dumps(flt, separators=(",", ":"))
        if fields:
            params["fields"] = ",".join(fields)
        page = get(table, params)
        rows += page
        off += page_size
        if len(page) < page_size:
            return rows


def months(since, before=None):
    d = datetime.date.fromisoformat(since).replace(day=1)
    end = datetime.date.fromisoformat(before) if before else datetime.date.today() + datetime.timedelta(days=1)
    while d < end:
        nxt = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        yield d, nxt
        d = nxt


def index(out, since, before=None):
    done_file = out + ".progress"
    done = set(open(done_file).read().split()) if os.path.exists(done_file) else set()
    if done and not os.path.exists(out):
        raise SystemExit(f"{done_file} exists without {out}; delete it to start over")
    with open(out, "a" if done else "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_FIELDS, extrasaction="ignore")
        if not done:
            w.writeheader()
        for start, nxt in months(since, before):
            label = start.isoformat()[:7]
            if label in done:
                continue
            flt = {"and": [{"field": "open_date", "operator": "gte", "value": start.isoformat()},
                           {"field": "open_date", "operator": "lt", "value": nxt.isoformat()}]}
            rows = get_all("inspection", flt, INDEX_FIELDS, INDEX_PAGE)
            w.writerows(rows)
            f.flush()  # whole months only, so a cut-off run resumes cleanly
            with open(done_file, "a") as p:
                p.write(label + "\n")
            print(label, len(rows), file=sys.stderr)
    write_manifest(out, __file__, sources=[{"url": BASE.format("inspection"),
                                            "note": "DOL OSHA Inspection dataset (10334), matching fields only"}],
                   params={"fields": INDEX_FIELDS, "opened_since": since, "opened_before": before,
                           "page_size": INDEX_PAGE})
    print(f"wrote {out} (+ manifest)")


def fetch_by(table, field, ids, path):
    if os.path.exists(path + ".manifest.json"):  # finished in an earlier run
        return
    cols = DETAILS[table][1]
    ids = sorted(set(ids))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for i in range(0, len(ids), BATCH):
            w.writerows(get_all(table, {"field": field, "operator": "in", "value": ids[i:i + BATCH]}))
            print(table, min(i + BATCH, len(ids)), "/", len(ids), file=sys.stderr)
    write_manifest(path, __file__, sources=[{"url": BASE.format(table), "note": f"DOL OSHA {table} dataset"}],
                   params={"filter_field": field, "ids": len(ids)})


def details(ids_file, outdir):
    os.makedirs(outdir, exist_ok=True)
    ids = [int(x) for x in open(ids_file).read().split()]
    for table in ("inspection", "violation", "accident_injury"):
        fetch_by(table, DETAILS[table][0], ids, os.path.join(outdir, f"osha_{table}.csv"))
    # The written hazard exists only for general duty clause citations (standard 5A0001).
    with open(os.path.join(outdir, "osha_violation.csv"), newline="") as f:
        gen_duty = [int(r["activity_nr"]) for r in csv.DictReader(f) if r["standard"].strip().upper().startswith("5A")]
    fetch_by("violation_gen_duty_std", "activity_nr", gen_duty, os.path.join(outdir, "osha_violation_gen_duty_std.csv"))
    with open(os.path.join(outdir, "osha_accident_injury.csv"), newline="") as f:
        summaries = [int(r["summary_nr"]) for r in csv.DictReader(f)]
    for table in ("accident", "accident_abstract"):
        fetch_by(table, "summary_nr", summaries, os.path.join(outdir, f"osha_{table}.csv"))
    path = os.path.join(outdir, "osha_accident_lookup2.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOOKUP_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(get_all("accident_lookup2"))
    write_manifest(path, __file__, sources=[{"url": BASE.format("accident_lookup2"),
                                             "note": "DOL OSHA Accident Lookup2 dataset (injury code list)"}])
    print(f"wrote details for {len(ids)} inspections to {outdir}")


if __name__ == "__main__":
    if len(sys.argv) in (4, 5) and sys.argv[1] == "index":
        index(*sys.argv[2:])
    elif len(sys.argv) == 4 and sys.argv[1] == "details":
        details(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__)
