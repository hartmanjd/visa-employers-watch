"""Build one SQLite database from the employer tables and the WHD enforcement data.

Tables:
  sources          one row per input file, from its manifest (checksum, rows, URLs)
  employers        one row per employer per program (H-1B or H-2B)
  applications     one row per visa application: its DOL case number and every
                   column of the public disclosure file (H-2B-only columns are
                   blank on H-1B rows and the other way round)
  violation_cases  one row per WHD enforcement case
  matches          links a WHD case to an employer: how it was matched and the
                   rule's false-match rate on the tax-ID benchmark
  flag_baselines,  summaries the website would otherwise compute at startup
  rep_filings      (see add_summaries in dashboard/queries.py)

With --osha DIR (the files fetch_osha.py writes), also the OSHA inspections linked to a
visa filer, with every column DOL publishes:
  osha_inspections      one row per inspection
  osha_violations       one row per citation item (deleted items kept, delete_flag X)
  osha_gen_duty_text    the written hazard of general duty clause citations, line by line
  osha_injuries         one row per injured worker in an accident tied to the inspection
  osha_accidents        one row per accident; osha_accident_text holds its written account
  osha_codes            OSHA's code list for the injury columns
  osha_matches          links an inspection to an employer, like matches does for WHD
  osha_employer_totals  per-employer counts and penalties, so the website does not add them up

Every input must have a manifest that still matches it (see provenance.py), and
every row in the data tables carries the source_id of the file it came from.
WHD cases have no tax ID, so they are linked by cleaned name + ZIP or cleaned
name + state (trade name or legal name against employer name or DBA); a pair
keeps the stronger rule. OSHA inspections are linked the same way, trying the
site and the mailing address, and the establishment name and any name after
"DBA" in it. Writes OUT.db.manifest.json.

Usage: python3 build_db.py OUT.db --lca filers_lca.csv --h2b filers_h2b.csv
                           --whd whd.csv --benchmark name_match_benchmark.csv
                           [--osha OSHA_DIR [--osha-since 2004-01-01]]
"""
import argparse, json, os, re, sqlite3, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_filers import applications_path
from fetch_whd import FIELDS as WHD_FIELDS
from fetch_osha import DETAILS as OSHA_DETAILS, LOOKUP_FIELDS as OSHA_LOOKUP_FIELDS
from employer_keys import clean_name, raw_name, zip5
from provenance import verify_manifest, write_manifest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dashboard"))
from queries import add_summaries  # noqa: E402

# Strongest first: a (case, employer) pair keeps the first rule that links it.
MATCH_RULES = {"clean name + ZIP": ["name_clean", "zip5"],
               "clean name + state": ["name_clean", "state"]}

# WHD columns: counts (..._cnt) are whole numbers, back wages and penalties (..._amt, cmp_assd) are
# dollars, and the rest (names, address, codes, dates, the FLSA repeat/willful flag) are text.
WHD_INT_COLS = [c for c in WHD_FIELDS if c.endswith("_cnt")]
WHD_REAL_COLS = [c for c in WHD_FIELDS if c.endswith("_amt") or c == "cmp_assd"]


def _whd_type(c):
    return ("INTEGER PRIMARY KEY" if c == "case_id" else "INTEGER" if c in WHD_INT_COLS else
            "REAL" if c in WHD_REAL_COLS else "TEXT")


SCHEMA = """
CREATE TABLE sources (
  source_id INTEGER PRIMARY KEY, file TEXT, sha256 TEXT, bytes INTEGER, rows INTEGER,
  created_utc TEXT, script TEXT, urls TEXT, manifest TEXT);
CREATE TABLE employers (
  employer_id INTEGER PRIMARY KEY, program TEXT, name_clean TEXT, state TEXT, zip5 TEXT,
  employer_name TEXT, dba TEXT, name_raw TEXT, dba_clean TEXT, city TEXT, address TEXT,
  address2 TEXT, postal_code TEXT, phone TEXT, phone_ext TEXT,
  fein TEXT, naics TEXT, visa_classes TEXT, n_applications INTEGER, n_certified INTEGER,
  h1b_dependent INTEGER, willful_violator INTEGER, source_files TEXT,
  source_id INTEGER REFERENCES sources);
{violation_cases}
CREATE TABLE matches (
  case_id INTEGER REFERENCES violation_cases, employer_id INTEGER REFERENCES employers,
  method TEXT, est_false_match_rate REAL, whd_name_field TEXT, employer_name_field TEXT,
  PRIMARY KEY (case_id, employer_id));
CREATE INDEX employers_fein ON employers(fein);
CREATE INDEX employers_name ON employers(name_clean, state);
CREATE INDEX matches_employer ON matches(employer_id);
""".format(violation_cases="CREATE TABLE violation_cases (\n  " + ",\n  ".join(
    f"{c} {_whd_type(c)}" for c in WHD_FIELDS) + ",\n  source_id INTEGER REFERENCES sources);")
INT_COLS = ["n_applications", "n_certified"] + WHD_INT_COLS
REAL_COLS = WHD_REAL_COLS
# Disclosure columns stored as numbers, so wages and worker counts can be compared and summed.
APP_INT_COLS = ["total_worker_positions", "new_employment", "continued_employment",
                "change_previous_employment", "new_concurrent_employment", "change_employer",
                "amended_petition", "worksite_workers", "total_worksite_locations",
                "cap_subject_workers", "cap_exempt_workers", "total_workers_requested",
                "total_workers_certified", "training_months", "work_experience_months",
                "supervise_how_many"]
APP_REAL_COLS = ["wage_rate_of_pay_from", "wage_rate_of_pay_to", "prevailing_wage",
                 "basic_wage_rate_from", "basic_wage_rate_to", "overtime_rate_from",
                 "overtime_rate_to", "anticipated_number_of_hours", "sunday_hours", "monday_hours",
                 "tuesday_hours", "wednesday_hours", "thursday_hours", "friday_hours",
                 "saturday_hours"]
# Disclosure columns the website reads; created empty if no input file has them.
APP_REQUIRED = ["decision_date", "job_title", "soc_code", "soc_title", "begin_date", "end_date",
                "requested_begin_date", "requested_end_date", "worksite_city", "worksite_state",
                "total_worker_positions", "total_workers_requested", "total_workers_certified",
                "wage_rate_of_pay_from", "wage_rate_of_pay_to", "wage_unit_of_pay", "basic_wage_rate_from",
                "basic_wage_rate_to", "per", "prevailing_wage", "pw_unit_of_pay", "pw_wage_level",
                "lawfirm_name_business_name", "agent_attorney_first_name", "agent_attorney_last_name",
                "attorney_agent_first_name", "attorney_agent_last_name"]
# Shown first in the applications table; the remaining disclosure columns follow in file order.
APP_FIRST = ["case_number", "program", "employer_id", "case_status", "visa_class", "fein",
             "source_file", "source_id"]


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def add_source(con, path):
    m = verify_manifest(path)  # refuses a file that changed since its manifest
    cur = con.execute(
        "INSERT INTO sources (file, sha256, bytes, rows, created_utc, script, urls, manifest)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (m["file"], m["sha256"], m["bytes"], m["rows"], m["created_utc"], m["script"],
         ";".join(s["url"] for s in m.get("sources", []) if s.get("url")), json.dumps(m)))
    return cur.lastrowid


def numeric(df, int_cols=INT_COLS, real_cols=REAL_COLS):
    for c in int_cols:
        if c in df:
            df[c] = pd.to_numeric(df[c].astype("string").str.replace(",", ""), errors="coerce").round().astype("Int64")
    for c in real_cols:
        if c in df:
            df[c] = pd.to_numeric(df[c].astype("string").str.replace(r"[$,]", "", regex=True),
                                  errors="coerce")
    return df


def load_program(con, program, filers_path, next_id):
    emp = read(filers_path)
    emp.insert(0, "employer_id", range(next_id, next_id + len(emp)))
    emp.insert(1, "program", program)
    for c in ["h1b_dependent", "willful_violator"]:
        emp[c] = (emp[c] == "True").astype(int)
    emp["source_id"] = add_source(con, filers_path)
    numeric(emp).to_sql("employers", con, if_exists="append", index=False)

    app_path = applications_path(filers_path)
    apps = read(app_path).merge(emp[["employer_id", "name_clean", "state", "zip5"]],
                                on=["name_clean", "state", "zip5"], how="left")
    if apps.employer_id.isna().any():
        raise ValueError(f"{os.path.basename(app_path)} has applications with no employer row")
    apps["program"] = program
    apps["source_id"] = add_source(con, app_path)
    apps["employer_id"] = apps.employer_id.astype(int)
    rest = [c for c in apps.columns if c not in APP_FIRST and c not in ("name_clean", "state", "zip5")]
    return emp, apps[APP_FIRST + rest]


def load_applications(con, frames):
    """One applications table for both programs; columns one program lacks stay empty."""
    cols = list(dict.fromkeys([c for f in frames for c in f.columns] + APP_REQUIRED))
    # Text unless listed as a number, so ZIPs, phones and codes keep their leading zeros.
    types = {c: "INTEGER" if c in APP_INT_COLS + ["employer_id", "source_id"] else
             "REAL" if c in APP_REAL_COLS else "TEXT" for c in cols}
    for f in frames:  # one at a time, to keep memory down
        numeric(f.reindex(columns=cols), APP_INT_COLS, APP_REAL_COLS).to_sql(
            "applications", con, if_exists="append", index=False, dtype=types)
    con.execute("CREATE INDEX applications_employer ON applications(employer_id)")
    con.execute("CREATE UNIQUE INDEX applications_case ON applications(case_number)")


def whd_keys(whd):
    parts = []
    for col, label in [("trade_nm", "trade name"), ("legal_name", "legal name")]:
        p = pd.DataFrame({"case_id": whd.case_id, "whd_name_field": label,
                          "name_clean": whd[col].map(clean_name),
                          "state": whd.st_cd.str.upper().str.strip(), "zip5": whd.zip_cd.map(zip5)})
        parts.append(p)
    k = pd.concat(parts)
    return k[k.name_clean != ""]


def employer_keys(emp):
    a = emp[["employer_id", "name_clean", "state", "zip5"]].assign(employer_name_field="name")
    b = emp[["employer_id", "dba_clean", "state", "zip5"]].rename(columns={"dba_clean": "name_clean"})
    b = b[b.name_clean != ""].assign(employer_name_field="DBA")
    return pd.concat([a, b])


def match(whd, emp, false_rates):
    wk, ek = whd_keys(whd), employer_keys(emp)
    found = []
    for rule, cols in MATCH_RULES.items():
        m = wk.merge(ek, on=cols)
        m = m[(m.name_clean != "") & (m[cols[1]] != "")]
        found.append(m.assign(method=rule, est_false_match_rate=false_rates.get(rule)))
    m = pd.concat(found)  # strongest rule first, so keep="first" keeps it
    m = m.drop_duplicates(["case_id", "employer_id"], keep="first")
    return m[["case_id", "employer_id", "method", "est_false_match_rate",
              "whd_name_field", "employer_name_field"]]


# ---------- OSHA ----------
# CSV from fetch_osha.py -> (table, integer columns, dollar columns). Everything else is text.
OSHA_FILES = {
    "inspection": ("osha_inspections", ["activity_nr", "nr_in_estab"], []),
    "violation": ("osha_violations", ["activity_nr", "nr_instances", "nr_exposed"],
                  ["current_penalty", "initial_penalty", "fta_penalty"]),
    "violation_gen_duty_std": ("osha_gen_duty_text", ["activity_nr", "line_nr"], []),
    "accident_injury": ("osha_injuries", [
        "summary_nr", "rel_insp_nr", "age", "nature_of_inj", "part_of_body", "src_of_injury", "event_type",
        "evn_factor", "hum_factor", "occ_code", "degree_of_inj", "task_assigned", "const_op",
        "const_op_cause", "fat_cause", "fall_distance", "fall_ht", "injury_line_nr"], []),
    "accident": ("osha_accidents", ["summary_nr", "build_stories", "nonbuild_ht"], []),
    "accident_abstract": ("osha_accident_text", ["summary_nr", "line_nr"], []),
    "accident_lookup2": ("osha_codes", ["accident_number"], []),
}
OSHA_SCHEMA = """
CREATE TABLE osha_matches (
  activity_nr INTEGER REFERENCES osha_inspections, employer_id INTEGER REFERENCES employers,
  method TEXT, est_false_match_rate REAL, osha_name_field TEXT, osha_address_field TEXT,
  employer_name_field TEXT, PRIMARY KEY (activity_nr, employer_id));
CREATE INDEX osha_matches_employer ON osha_matches(employer_id);
CREATE INDEX osha_violations_activity ON osha_violations(activity_nr);
CREATE INDEX osha_gen_duty_activity ON osha_gen_duty_text(activity_nr);
CREATE INDEX osha_injuries_activity ON osha_injuries(rel_insp_nr);
CREATE INDEX osha_accident_text_summary ON osha_accident_text(summary_nr);
"""
# Per employer, over its linked inspections: deleted citation items are left out, and a citation's
# penalty is DOL's current amount (after settlement or decision); the proposed amount is kept beside it.
OSHA_TOTALS_SQL = """
CREATE TABLE osha_employer_totals AS
WITH v AS (SELECT activity_nr, COUNT(*) AS violations, SUM(viol_type = 'S') AS serious,
                  SUM(viol_type = 'W') AS willful, SUM(viol_type = 'R') AS repeat_,
                  COALESCE(SUM(initial_penalty), 0) AS initial_penalty,
                  COALESCE(SUM(current_penalty), 0) AS current_penalty
           FROM osha_violations WHERE COALESCE(delete_flag, '') != 'X' GROUP BY activity_nr),
     a AS (SELECT rel_insp_nr AS activity_nr, COUNT(DISTINCT summary_nr) AS accidents,
                  SUM(degree_of_inj = 1) AS deaths, SUM(degree_of_inj = 2) AS hospitalized
           FROM osha_injuries GROUP BY rel_insp_nr)
SELECT m.employer_id, COUNT(*) AS inspections,
       SUM(m.method = 'clean name + ZIP') AS zip_links,
       SUM(COALESCE(v.violations, 0) > 0) AS inspections_with_violations,
       COALESCE(SUM(v.violations), 0) AS violations, COALESCE(SUM(v.serious), 0) AS serious,
       COALESCE(SUM(v.willful), 0) AS willful, COALESCE(SUM(v.repeat_), 0) AS repeat_violations,
       COALESCE(SUM(v.initial_penalty), 0) AS initial_penalty,
       COALESCE(SUM(v.current_penalty), 0) AS penalties,
       COALESCE(SUM(a.accidents), 0) AS accidents, COALESCE(SUM(a.deaths), 0) AS deaths,
       COALESCE(SUM(a.hospitalized), 0) AS hospitalized,
       MIN(i.open_date) AS first_inspection, MAX(i.open_date) AS latest_inspection
FROM osha_matches m JOIN osha_inspections i USING (activity_nr)
LEFT JOIN v USING (activity_nr) LEFT JOIN a USING (activity_nr)
GROUP BY m.employer_id;
CREATE UNIQUE INDEX osha_totals_employer ON osha_employer_totals(employer_id);
"""


def _after_dba(s):
    """The name after "DBA" in an OSHA establishment name ("ACME INC DBA ACME FARMS" -> "ACME FARMS")."""
    m = re.search(r"\bD/?B/?A\b(.*)$", raw_name(s))
    return clean_name(m.group(1)) if m else ""


def osha_keys(insp):
    parts = []
    for name_field, names in [("establishment name", insp.estab_name.map(clean_name)),
                              ("name after DBA", insp.estab_name.map(_after_dba))]:
        for addr, prefix in [("site", "site_"), ("mailing", "mail_")]:
            parts.append(pd.DataFrame({
                "activity_nr": insp.activity_nr, "osha_name_field": name_field, "osha_address_field": addr,
                "name_clean": names, "state": insp[prefix + "state"].str.upper().str.strip(),
                "zip5": insp[prefix + "zip"].map(zip5)}))
    k = pd.concat(parts)
    return k[k.name_clean != ""]


def match_osha(insp, emp, false_rates):
    """Links of OSHA inspections to employers, by the WHD rules: an (inspection, employer) pair keeps the
    strongest rule, and within a rule the site address before the mailing address."""
    ok, ek = osha_keys(insp), employer_keys(emp)
    found = []
    for rule, cols in MATCH_RULES.items():
        m = ok.merge(ek, on=cols)
        m = m[(m.name_clean != "") & (m[cols[1]] != "")]
        found.append(m.assign(method=rule, est_false_match_rate=false_rates.get(rule)))
    m = pd.concat(found)  # strongest rule first, site before mailing, so keep="first" keeps them
    m = m.drop_duplicates(["activity_nr", "employer_id"], keep="first")
    return m[["activity_nr", "employer_id", "method", "est_false_match_rate", "osha_name_field",
              "osha_address_field", "employer_name_field"]]


def load_osha(con, osha_dir, emp, false_rates, since):
    """Load the OSHA inspections opened on or after `since` that match a visa filer, with their details."""
    path = lambda t: os.path.join(osha_dir, f"osha_{t}.csv")
    insp = read(path("inspection"))
    insp = insp[insp.open_date.str[:10] >= since]
    m = match_osha(insp, emp, false_rates)
    m["activity_nr"] = m.activity_nr.astype(int)
    keep = set(m.activity_nr)
    con.executescript("".join(
        f"CREATE TABLE {table} (" + ", ".join(
            f"{c} {'INTEGER' if c in ints else 'REAL' if c in reals else 'TEXT'}"
            + (" PRIMARY KEY" if (t, c) in {("inspection", "activity_nr"), ("accident", "summary_nr")} else "")
            for c in (OSHA_DETAILS[t][1] if t in OSHA_DETAILS else OSHA_LOOKUP_FIELDS))
        + ", source_id INTEGER REFERENCES sources);\n"
        for t, (table, ints, reals) in OSHA_FILES.items()) + OSHA_SCHEMA)
    summaries = set()
    for t, (table, ints, reals) in OSHA_FILES.items():
        df = insp if t == "inspection" else read(path(t))
        if "activity_nr" in df:
            df = df[pd.to_numeric(df.activity_nr).isin(keep)]
        elif t == "accident_injury":
            df = df[pd.to_numeric(df.rel_insp_nr).isin(keep)]
            summaries = set(pd.to_numeric(df.summary_nr))
        elif t != "accident_lookup2":
            df = df[pd.to_numeric(df.summary_nr).isin(summaries)]
        df = numeric(df.copy(), ints, reals)
        df["source_id"] = add_source(con, path(t))
        df.to_sql(table, con, if_exists="append", index=False)
    m.to_sql("osha_matches", con, if_exists="append", index=False)
    con.executescript(OSHA_TOTALS_SQL)
    return [path(t) for t in OSHA_FILES]


def build(out, lca, h2b, whd_path, bench, osha_dir=None, osha_since="2004-01-01"):
    if os.path.exists(out):
        os.remove(out)
    con = sqlite3.connect(out)
    con.executescript(SCHEMA)
    lca_emp, lca_apps = load_program(con, "H-1B", lca, 1)
    h2b_emp, h2b_apps = load_program(con, "H-2B", h2b, len(lca_emp) + 1)
    load_applications(con, [lca_apps, h2b_apps])
    emp = pd.concat([lca_emp, h2b_emp])

    whd = read(whd_path).reindex(columns=WHD_FIELDS)  # an older, narrower download leaves columns empty
    whd["source_id"] = add_source(con, whd_path)
    whd = numeric(whd)
    whd["case_id"] = whd.case_id.astype(int)
    whd.to_sql("violation_cases", con, if_exists="append", index=False)

    add_source(con, bench)
    false_rates = dict(zip(read(bench).rule, pd.to_numeric(read(bench).false_match_rate)))
    m = match(whd, emp, false_rates)
    m.to_sql("matches", con, if_exists="append", index=False)
    osha_inputs = load_osha(con, osha_dir, emp, false_rates, osha_since) if osha_dir else []
    con.commit()
    add_summaries(con)  # the website's slow whole-table aggregates, so it does not compute them at startup
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["sources", "employers", "applications", "violation_cases", "matches"]
              + (["osha_inspections", "osha_violations", "osha_accidents", "osha_matches"] if osha_dir else [])}
    con.close()
    inputs = [lca, applications_path(lca), h2b, applications_path(h2b), whd_path, bench] + osha_inputs
    params = {"match_rules": MATCH_RULES, "tables": counts}
    if osha_dir:
        params["osha_opened_since"] = osha_since
    write_manifest(out, __file__, inputs=inputs, params=params)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--lca", required=True)
    ap.add_argument("--h2b", required=True)
    ap.add_argument("--whd", required=True)
    ap.add_argument("--benchmark", required=True)
    ap.add_argument("--osha", help="folder of osha_*.csv files from fetch_osha.py details")
    ap.add_argument("--osha-since", default="2004-01-01", help="keep OSHA inspections opened on or after this date")
    a = ap.parse_args(argv)
    counts = build(a.out, a.lca, a.h2b, a.whd, a.benchmark, a.osha, a.osha_since)
    print(f"{a.out}: " + ", ".join(f"{t}={n}" for t, n in counts.items()))


if __name__ == "__main__":
    main()
