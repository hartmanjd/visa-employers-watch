"""Read-only lookups on research.db for the dashboard.

Plain functions over a sqlite3 connection, returning lists of dicts, so they can
be tested without a web server. See h1b-data/README.md for the tables.
"""
import csv, datetime, gzip, hashlib, os, re, shutil, sqlite3, sys, threading, urllib.request
from functools import lru_cache


def build_once(fn):
    """Cache fn's result per arguments, and build each result only once even when several requests ask
    for it at the same time (lru_cache would let each of them build its own copy)."""
    cache, lock = {}, threading.Lock()

    def wrapper(*args):
        if args not in cache:
            with lock:
                if args not in cache:
                    cache[args] = fn(*args)
        return cache[args]
    wrapper.ready = lambda *args: args in cache
    wrapper.cache_clear = cache.clear
    wrapper.__doc__ = fn.__doc__
    return wrapper

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "h1b-data", "scripts"))
from employer_keys import clean_name, fein as clean_fein  # noqa: E402

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "h1b-data", "research.db")
DEBARMENTS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "h1b-data", "debarments.csv")

# A gzipped copy of research.db published as a GitHub release asset, for hosts (like Render)
# that only have the git checkout. The checksum is of the .gz file at that URL.
DB_URL = "https://github.com/hartmanjd/research-team/releases/download/data-2026-10-17/research.db.gz"
DB_SHA256 = "d521001549efd5ba8f60de8272d2b9c88d298b9d866f08643023c6136e91d948"


def connect(path=None):
    """Open the database read-only (mode=ro), so the dashboard can never change it."""
    path = os.path.abspath(path or os.environ.get("RESEARCH_DB") or DEFAULT_DB)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No database at {path}; build it with h1b-data/scripts/build_db.py "
                                "or set RESEARCH_DB")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _older_layout(path):
    """True for a database that predates the full disclosure columns or the full WHD columns."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(applications)")}
            whd = {r[1] for r in con.execute("PRAGMA table_info(violation_cases)")}
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False
    return bool(cols) and ("job_title" not in cols or "flsa_cl_violtn_cnt" not in whd)


def download_db(path=None, url=None, sha256=None):
    """Download the gzipped database to `path` if it is not there yet (or is an older layout
    without the full application or WHD columns), and return the path.

    The download is checked against its SHA-256 before it is unpacked, and written under a
    temporary name first, so a failed or wrong download never leaves a database behind.
    """
    path = os.path.abspath(path or os.environ.get("RESEARCH_DB") or DEFAULT_DB)
    if os.path.exists(path) and not _older_layout(path):
        return path
    url = url or os.environ.get("RESEARCH_DB_URL") or DB_URL
    sha256 = sha256 or os.environ.get("RESEARCH_DB_SHA256") or DB_SHA256
    gz, part = path + ".gz.part", path + ".part"
    try:
        digest = hashlib.sha256()
        with urllib.request.urlopen(url) as r, open(gz, "wb") as f:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != sha256:
            raise ValueError(f"Downloaded database from {url} has checksum {digest.hexdigest()}, "
                             f"expected {sha256}")
        with gzip.open(gz, "rb") as src, open(part, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        os.replace(part, path)
    finally:
        for tmp in (gz, part):
            if os.path.exists(tmp):
                os.remove(tmp)
    return path


def rows(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args)]


def find_application(con, text):
    """The application whose DOL case number is `text` (e.g. I-200-25273-349981), or None."""
    t = (text or "").strip().upper()
    if not (len(t) > 2 and t[1] == "-" and t[0] in "IH"):
        return None
    r = rows(con, "SELECT case_number, employer_id FROM applications WHERE case_number = ?", t)
    return r[0] if r else None


# The willful violator answer is the employer's own Yes/No on each application, not a DOL finding.
# employers.willful_violator is 1 when any application says Yes; WILLFUL_YES counts those answers
# (computed only for flagged employers), and an employer counts as a willful violator when more
# than half of its applications say Yes, so a single mistyped answer does not flag it.
WILLFUL_YES = """(CASE WHEN e.willful_violator = 1 THEN (SELECT COUNT(*) FROM applications w
                 WHERE w.employer_id = e.employer_id AND UPPER(w.willful_violator) = 'YES') ELSE 0 END)"""
WILLFUL_COLS = f"""{WILLFUL_YES} AS willful_yes,
               ({WILLFUL_YES}) * 2 > e.n_applications AS willful_violator"""

_RESULT_COLS = f"""e.employer_id, e.program, e.employer_name, e.dba, e.city, e.state, e.fein,
               e.n_applications, {WILLFUL_COLS},
               (SELECT COUNT(*) FROM matches m WHERE m.employer_id = e.employer_id) AS n_cases"""


def search_employers_by_id(con, employer_id):
    """The search-result row for one employer."""
    return rows(con, f"SELECT {_RESULT_COLS} FROM employers e WHERE e.employer_id = ?", employer_id)


def search_employers(con, text, limit=50):
    """Employers whose name or DBA contains the search text, after the same cleaning as matching,
    or whose tax ID is the search text.

    Ranked by exact name first, then by number of applications.
    """
    cols = _RESULT_COLS
    tax_id = clean_fein(text) if (text or "").strip().replace("-", "").isdigit() else ""
    if tax_id:  # a 9-digit tax ID (FEIN), with or without its dash
        return rows(con, f"""
            SELECT {cols} FROM employers e WHERE (';' || e.fein || ';') LIKE ?
            ORDER BY e.n_applications DESC LIMIT ?""", f"%;{tax_id};%", limit)
    key = clean_name(text)
    if not key:
        return []
    like = f"%{key}%"
    return rows(con, f"""
        SELECT {cols}
        FROM employers e
        WHERE e.name_clean LIKE ? OR e.dba_clean LIKE ?
        ORDER BY (e.name_clean = ?) DESC, e.n_applications DESC
        LIMIT ?""", like, like, key, limit)


def employer(con, employer_id):
    r = rows(con, f"""
        SELECT e.*, {WILLFUL_YES} AS willful_yes, s.file AS source_file, s.sha256 AS source_sha256, s.urls AS source_urls,
               s.created_utc AS source_created_utc
        FROM employers e JOIN sources s USING (source_id) WHERE e.employer_id = ?""", employer_id)
    for e in r:
        e["willful_violator"] = int(e["willful_yes"] * 2 > e["n_applications"])
    return r[0] if r else None


def same_tax_id(con, employer_id):
    """Other employer rows (other program, or another address) sharing a tax ID with this one."""
    emp = employer(con, employer_id)
    feins = [f for f in (emp or {}).get("fein", "").split(";") if f]
    if not feins:
        return []
    found = {}
    for f in feins:
        for r in rows(con, """
                SELECT employer_id, program, employer_name, city, state, fein, n_applications
                FROM employers WHERE employer_id != ? AND (';' || fein || ';') LIKE ?""",
                      employer_id, f"%;{f};%"):
            found[r["employer_id"]] = r
    return sorted(found.values(), key=lambda r: (r["program"], -r["n_applications"]))


def summary(con, employer_id):
    """Headline numbers: applications and the WHD cases linked to this employer."""
    return rows(con, """
        SELECT (SELECT n_applications FROM employers WHERE employer_id = ?) AS applications,
               (SELECT n_certified FROM employers WHERE employer_id = ?) AS certified,
               COUNT(v.case_id) AS cases,
               COALESCE(SUM(v.bw_atp_amt), 0) AS back_wages,
               COALESCE(SUM(v.cmp_assd), 0) AS penalties,
               COALESCE(SUM(v.h1b_violtn_cnt), 0) AS h1b_violations,
               COALESCE(SUM(v.h2b_violtn_cnt), 0) AS h2b_violations
        FROM matches m JOIN violation_cases v USING (case_id)
        WHERE m.employer_id = ?""", employer_id, employer_id, employer_id)[0]


# Laws WHD enforces, as (column prefix, plain name). Each has a ..._violtn_cnt column, and most have
# back wages (..._bw_atp_amt), workers owed (..._ee_atp_cnt) and penalties (..._cmp_assd_amt) columns.
LAWS = [
    ("flsa", "Minimum wage and overtime (FLSA)"),
    ("flsa_cl", "Child labor (FLSA)"),
    ("h1b", "H-1B visa program"),
    ("h2b", "H-2B visa program"),
    ("h2a", "H-2A farmworker visa program"),
    ("h1a", "H-1A visa program (registered nurses)"),
    ("crew", "Longshore work by foreign crew members (D-1)"),
    ("sraw", "Special and replenishment agricultural workers"),
    ("mspa", "Migrant and seasonal farmworker protections (MSPA)"),
    ("osha", "Farmworker field sanitation and housing (OSHA)"),
    ("fmla", "Family and medical leave (FMLA)"),
    ("sca", "Federal service contracts (Service Contract Act)"),
    ("dbra", "Federal construction prevailing wages (Davis-Bacon)"),
    ("cwhssa", "Federal contract overtime (CWHSSA)"),
    ("ca", "Kickbacks on federal construction (Copeland Act)"),
    ("pca", "Federal supply contracts (Walsh-Healey Public Contracts Act)"),
    ("eppa", "Lie detector tests (Employee Polygraph Protection Act)"),
    ("ccpa", "Wage garnishment limits (CCPA)"),
    ("flsa_smw14", "Special minimum wage for workers with disabilities (FLSA 14(c))"),
    ("flsa_smwpw", "Special minimum wage for patient workers (FLSA)"),
    ("flsa_smwap", "Special minimum wage for apprentices (FLSA)"),
    ("flsa_smwft", "Special minimum wage for full-time students (FLSA)"),
    ("flsa_smwl", "Special minimum wage for learners (FLSA)"),
    ("flsa_smwmg", "Special minimum wage for messengers (FLSA)"),
    ("flsa_smwsl", "Special minimum wage for student learners (FLSA)"),
    ("flsa_hmwkr", "Industrial homework (FLSA)"),
    ("eev", "Other (DOL code EEV)"),
]
# A few WHD columns break the naming pattern.
_LAW_COLS = {"flsa_smw14": {"back_wages": "flsa_smw14_bw_amt"},
             "cwhssa": {"back_wages": "cwhssa_bw_amt", "workers": "cwhssa_ee_cnt"},
             "eppa": {"workers": "eppa_ee_cnt"}}
REPEAT_VIOLATOR = {"R": "Repeat", "W": "Willful", "RW": "Repeat and willful"}


def _num(v):
    return v if isinstance(v, (int, float)) else 0


def case_laws(v):
    """The laws a WHD case found violations under (or owed back wages or penalties under), each with
    its plain name, violations, back wages, workers owed and penalties; missing columns count as 0."""
    out = []
    for prefix, name in LAWS:
        cols = {"violations": f"{prefix}_violtn_cnt", "back_wages": f"{prefix}_bw_atp_amt",
                "workers": f"{prefix}_ee_atp_cnt", "penalties": f"{prefix}_cmp_assd_amt",
                **_LAW_COLS.get(prefix, {})}
        row = {k: _num(v.get(c)) for k, c in cols.items()}
        if any(row.values()):
            out.append({"law": prefix, "name": name, **row})
    return out


def case_details(v):
    """Detail WHD gives beyond the per-law counts: the FLSA back wages split, minors employed in
    violation of child labor rules, and whether WHD marked the employer a repeat or willful violator."""
    return {"minimum_wage_back_wages": _num(v.get("flsa_mw_bw_atp_amt")),
            "overtime_back_wages": _num(v.get("flsa_ot_bw_atp_amt")),
            "retaliation_back_wages": _num(v.get("flsa_15a3_bw_atp_amt")),
            "child_labor_minors": _num(v.get("flsa_cl_minor_cnt")),
            "repeat_violator": REPEAT_VIOLATOR.get((v.get("flsa_repeat_violator") or "").strip().upper()),
            "industry": v.get("naics_code_description") or None}


def cases(con, employer_id):
    """WHD cases linked to this employer, newest first, with how each link was made, every WHD
    column, the laws it found violations under (laws) and the extra detail WHD gives (details)."""
    out = rows(con, """
        SELECT v.*, m.method, m.est_false_match_rate, m.whd_name_field, m.employer_name_field,
               s.file AS source_file, s.sha256 AS source_sha256, s.urls AS source_urls
        FROM matches m JOIN violation_cases v USING (case_id) JOIN sources s ON s.source_id = v.source_id
        WHERE m.employer_id = ?
        ORDER BY v.findings_end_date DESC, v.case_id DESC""", employer_id)
    for v in out:
        v["laws"] = case_laws(v)
        v["details"] = case_details(v)
    return out


# Columns that hold the same thing under different names in the H-1B (LCA) and H-2B files.
_WORKERS = "COALESCE(a.total_worker_positions, a.total_workers_requested)"
_WAGE_FROM = "COALESCE(a.wage_rate_of_pay_from, a.basic_wage_rate_from)"
_WAGE_TO = "COALESCE(a.wage_rate_of_pay_to, a.basic_wage_rate_to)"
_WAGE_UNIT = "COALESCE(a.wage_unit_of_pay, a.per)"
_BEGIN = "COALESCE(NULLIF(a.begin_date, ''), a.requested_begin_date)"
_END = "COALESCE(NULLIF(a.end_date, ''), a.requested_end_date)"
_LAWFIRM = "NULLIF(TRIM(a.lawfirm_name_business_name), '')"
_ATTORNEY = ("NULLIF(TRIM(COALESCE(NULLIF(a.agent_attorney_first_name, ''), a.attorney_agent_first_name, '') || ' ' ||"
             " COALESCE(NULLIF(a.agent_attorney_last_name, ''), a.attorney_agent_last_name, '')), '')")
# Hours, weeks, ... in a year, to compare a wage with the prevailing wage when units differ.
PER_YEAR = {"Hour": 2080, "Week": 52, "Bi-Weekly": 26, "Month": 12, "Year": 1}


def applications(con, employer_id, limit=None):
    """Visa applications filed by this employer, newest decision first, with the job, worksite,
    offered and prevailing wage, representation and the DOL file each came from."""
    sql = f"""
        SELECT a.case_number, a.program, a.case_status, a.visa_class, a.decision_date, a.fein,
               a.job_title, a.soc_code, a.soc_title, {_WORKERS} AS workers,
               {_BEGIN} AS begin_date, {_END} AS end_date,
               a.worksite_city, a.worksite_state, {_WAGE_FROM} AS wage_from, {_WAGE_TO} AS wage_to,
               {_WAGE_UNIT} AS wage_unit, a.prevailing_wage, a.pw_unit_of_pay, a.pw_wage_level,
               {_LAWFIRM} AS lawfirm, a.source_file,
               s.file AS table_file, s.sha256 AS table_sha256, s.urls AS table_urls
        FROM applications a JOIN sources s USING (source_id)
        WHERE a.employer_id = ? ORDER BY a.decision_date DESC, a.case_number"""
    out = rows(con, sql + " LIMIT ?", employer_id, limit) if limit else rows(con, sql, employer_id)
    for r in out:
        r["dol_file_url"] = dol_file_url(r["source_file"], r.pop("table_urls"))
    return out


def application(con, case_number):
    """One application with every disclosure column, its employer and its source file."""
    r = rows(con, """
        SELECT a.*, e.employer_name AS employer_row_name, e.city AS employer_row_city,
               e.state AS employer_row_state,
               s.file AS table_file, s.sha256 AS table_sha256, s.urls AS table_urls
        FROM applications a JOIN employers e USING (employer_id) JOIN sources s ON s.source_id = a.source_id
        WHERE a.case_number = ?""", case_number.strip().upper())
    if not r:
        return None
    r = r[0]
    r["dol_file_url"] = dol_file_url(r["source_file"], r.pop("table_urls"))
    return r


# How the application page groups the disclosure columns. A column is shown in the first section
# whose prefixes or names match it; anything else goes under "Other answers on the form".
APPLICATION_SECTIONS = [
    ("Case", ["case_number", "case_status", "visa_class", "received_date", "decision_date",
              "original_cert_date", "cap_exempt", "cap_subject_workers", "cap_exempt_workers",
              "nature_of_temporary_need", "public_disclosure"]),
    ("Job", ["job_title", "soc_code", "soc_title", "full_time_position", "begin_date", "end_date",
             "requested_begin_date", "requested_end_date", "employment_begin_date", "employment_end_date",
             "total_worker_positions", "total_workers_requested", "total_workers_certified",
             "new_employment", "continued_employment", "change_previous_employment",
             "new_concurrent_employment", "change_employer", "amended_petition", "education_level",
             "training_months", "work_experience_months", "supervise_other_emp", "supervise_how_many",
             "special_requirements", "anticipated_number_of_hours", "*_hours", "hourly_schedule_*",
             "on_the_job_training_available"]),
    ("Worksite", ["worksite_*", "total_worksite_locations", "other_worksite_location", "secondary_entity*",
                  "msa_name_oes_area_title"]),
    ("Wages", ["wage_*", "prevailing_wage", "pw_*", "basic_wage_*", "per", "overtime_*",
               "additional_wage_conditions", "pwd_case_number_*", "emergency_filing_pwd_attached",
               "deductions_from_pay", "board_lodging_other_facilities", "emp_provided_tools_equipment",
               "daily_transportation"]),
    ("Employer", ["employer_name", "trade_name_dba", "employer_address*", "employer_city", "employer_state",
                  "employer_postal_code", "employer_country", "employer_province", "employer_phone*",
                  "employer_fein", "naics_code", "type_of_employer", "h1b_dependent", "willful_violator",
                  "support_h1b", "statutory_basis"]),
    ("Employer point of contact", ["employer_poc_*"]),
    ("Attorney or agent", ["agent_representing_employer", "type_of_representation", "agent_attorney_*",
                           "attorney_agent_*", "lawfirm_*", "state_of_highest_court",
                           "name_of_highest_state_court", "agent_agreement_attached", "agent_mspa_attached"]),
    ("Preparer", ["preparer_*"]),
    ("Recruitment", ["job_order_to_swa", "swa_state", "job_order_submit_date", "phone_to_apply",
                     "email_to_apply", "website_to_apply", "foreign_labor_recruiter"]),
]
# Columns the build adds, not part of the DOL form.
_NOT_ON_FORM = {"program", "employer_id", "fein", "source_file", "source_id", "table_file", "table_sha256",
                "dol_file_url", "employer_row_name", "employer_row_city", "employer_row_state"}
FIELD_LABELS = {"soc_code": "SOC code", "soc_title": "SOC title", "naics_code": "NAICS code",
                "employer_fein": "Employer tax ID (FEIN)", "lawfirm_business_fein": "Law firm tax ID (FEIN)",
                "preparer_fein": "Preparer tax ID (FEIN)", "trade_name_dba": "Trade name (DBA)",
                "employer_poc_email": "Email", "pw_wage_level": "Prevailing wage level",
                "pw_unit_of_pay": "Prevailing wage unit", "pw_oes_year": "Prevailing wage OES year",
                "pw_tracking_number": "Prevailing wage tracking number", "per": "Wage unit",
                "msa_name_oes_area_title": "Wage area (MSA)", "swa_state": "State workforce agency",
                "job_order_to_swa": "Job order sent to state workforce agency", "h1b_dependent": "H-1B dependent",
                "support_h1b": "Supports H-1B (exempt worker)", "lawfirm_name_business_name": "Law firm or business",
                "agree_to_lc_statement": "Agrees to labor condition statements",
                "emp_mspa_attached": "MSPA registration attached", "agent_mspa_attached": "Agent MSPA registration attached",
                "supervise_other_emp": "Supervises other employees", "supervise_how_many": "Supervises how many",
                "emp_provided_tools_equipment": "Employer provides tools and equipment",
                "board_lodging_other_facilities": "Board, lodging or other facilities",
                "emp_client_appendix_b_attached": "Employer-client Appendix B attached",
                "full_time_position": "Full-time position", "on_the_job_training_available": "On-the-job training available"}


def field_label(col):
    if col in FIELD_LABELS:
        return FIELD_LABELS[col]
    words = col
    for prefix in ("employer_poc_", "agent_attorney_", "attorney_agent_", "preparer_"):
        if words.startswith(prefix):
            words = words[len(prefix):]
    words = words.replace("address1", "address").replace("address2", "address line 2")
    words = words.replace("pwd", "PWD").replace("_", " ").replace(" ext", " extension")
    words = re.sub(r"\bappendix ([a-d])\b", lambda m: "appendix " + m.group(1).upper(), words)
    return words[:1].upper() + words[1:]


def _section_of(col):
    from fnmatch import fnmatch
    for title, pats in APPLICATION_SECTIONS:
        if any(fnmatch(col, p) for p in pats):
            return title
    return "Other answers on the form"


def application_sections(app):
    """The application's non-blank form answers, grouped into sections in a fixed order."""
    order = [t for t, _ in APPLICATION_SECTIONS] + ["Other answers on the form"]
    out = {t: [] for t in order}
    for col, val in app.items():
        if col in _NOT_ON_FORM or val is None or str(val).strip() == "":
            continue
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        out[_section_of(col)].append({"field": col, "label": field_label(col), "value": val})
    return [{"title": t, "fields": out[t]} for t in order if out[t]]


def application_highlights(app):
    """The headline numbers of one application, the same for H-1B and H-2B forms."""
    unit = app.get("wage_unit_of_pay") or app.get("per")
    offered = app.get("wage_rate_of_pay_from") if app.get("wage_rate_of_pay_from") is not None \
        else app.get("basic_wage_rate_from")
    offered_to = app.get("wage_rate_of_pay_to") if app.get("wage_rate_of_pay_from") is not None \
        else app.get("basic_wage_rate_to")
    prevailing, pw_unit = app.get("prevailing_wage"), app.get("pw_unit_of_pay")
    a, b = _annual(offered, unit), _annual(prevailing, pw_unit)
    return {
        "job_title": app.get("job_title"), "soc_title": app.get("soc_title"),
        "workers": first_present(app.get("total_worker_positions"), app.get("total_workers_requested")),
        "workers_certified": app.get("total_workers_certified"),
        "begin_date": app.get("begin_date") or app.get("requested_begin_date"),
        "end_date": app.get("end_date") or app.get("requested_end_date"),
        "worksite": ", ".join(x for x in (app.get("worksite_city"), app.get("worksite_state")) if x),
        "wage_from": offered, "wage_to": offered_to or None, "wage_unit": unit,
        "prevailing_wage": prevailing, "pw_unit": pw_unit, "pw_wage_level": app.get("pw_wage_level"),
        "wage_vs_prevailing": a / b - 1 if a and b else None,
        "lawfirm": (app.get("lawfirm_name_business_name") or "").strip() or None,
    }


def first_present(*vals):
    return next((v for v in vals if v is not None and v != ""), None)


def _annual(amount, unit):
    per = PER_YEAR.get(unit or "")
    return amount * per if amount is not None and per else None


# Offered wage as a share of the prevailing wage: the bands of the wage chart.
WAGE_BANDS = [(None, 1.0, "Below prevailing"), (1.0, 1.02, "0-2% above"), (1.02, 1.1, "2-10% above"),
              (1.1, 1.25, "10-25% above"), (1.25, 1.5, "25-50% above"), (1.5, None, "50%+ above")]


def wage_band(ratio):
    for lo, hi, label in WAGE_BANDS:
        if (lo is None or ratio >= lo) and (hi is None or ratio < hi):
            return label


def employer_profile(con, employer_id):
    """What this employer's applications ask for: jobs, worksites, who filed them, and how the
    offered wage compares with the prevailing wage (H-1B, where both are on the form)."""
    top = lambda expr, n=10, where="1": rows(con, f"""
        SELECT {expr} AS name, COUNT(*) AS applications, SUM({_WORKERS}) AS workers
        FROM applications a WHERE a.employer_id = ? AND {where} AND name IS NOT NULL AND name != ''
        GROUP BY 1 ORDER BY applications DESC, name LIMIT {n}""", employer_id)
    jobs = rows(con, f"""
        SELECT a.soc_code, a.soc_title, COUNT(*) AS applications, SUM({_WORKERS}) AS workers
        FROM applications a WHERE a.employer_id = ? AND COALESCE(a.soc_title, '') != ''
        GROUP BY 1, 2 ORDER BY applications DESC LIMIT 10""", employer_id)
    for j in jobs:
        titles = [r["job_title"] for r in rows(con, """
            SELECT job_title, COUNT(*) AS n FROM applications
            WHERE employer_id = ? AND soc_code IS ? AND soc_title = ? AND COALESCE(job_title, '') != ''
            GROUP BY 1 ORDER BY n DESC, job_title""", employer_id, j["soc_code"], j["soc_title"])]
        j["job_titles"], j["more_titles"] = titles[:5], max(0, len(titles) - 5)
    bands = {label: 0 for _, _, label in WAGE_BANDS}
    levels = {}
    for r in con.execute("""
            SELECT wage_rate_of_pay_from, wage_unit_of_pay, prevailing_wage, pw_unit_of_pay, pw_wage_level
            FROM applications WHERE employer_id = ? AND program = 'H-1B'""", (employer_id,)):
        offered, prevailing = _annual(r[0], r[1]), _annual(r[2], r[3])
        if offered and prevailing:
            bands[wage_band(offered / prevailing)] += 1
        if r[4]:
            levels[r[4]] = levels.get(r[4], 0) + 1
    return {
        "jobs": jobs,
        "worksites": top("a.worksite_city || ', ' || a.worksite_state", where="a.worksite_city != ''"),
        "worksite_states": top("a.worksite_state", n=60),
        "lawfirms": top(_LAWFIRM),
        "attorneys": top(_ATTORNEY),
        "wage_bands": [{"band": k, "applications": v} for k, v in bands.items()],
        "wage_levels": [{"level": k, "applications": levels[k]} for k in sorted(levels)],
    }


def dol_file_url(csv_name, urls):
    """The DOL workbook a converted CSV came from, e.g. LCA_..._Q1.csv -> .../LCA_..._Q1.xlsx."""
    stem = os.path.splitext(csv_name or "")[0]
    for u in (urls or "").split(";"):
        if stem and os.path.splitext(u.rsplit("/", 1)[-1])[0] == stem:
            return u
    return ""


def _watchlist_filter(program, since_year, visa_violations_only, min_applications, state, sector=None):
    where, args = ["e.n_applications >= ?"], [min_applications]
    if sector:
        prefixes = SECTOR_PREFIXES.get(sector)
        if prefixes is None:
            raise ValueError(f"Unknown NAICS sector {sector!r}")
        where.append("(" + " OR ".join("e.naics LIKE ?" for _ in prefixes) + ")")
        args += [f"{p}%" for p in prefixes]
    if program:
        where.append("e.program = ?")
        args.append(program)
    if state:
        where.append("e.state = ?")
        args.append(state)
    if since_year:
        where.append("v.findings_end_date >= ?")
        args.append(f"{int(since_year):04d}")
    if visa_violations_only:
        where.append("CASE e.program WHEN 'H-1B' THEN v.h1b_violtn_cnt WHEN 'H-2B' THEN v.h2b_violtn_cnt END > 0")
    return " AND ".join(where), args


def watchlist(con, program=None, since_year=None, visa_violations_only=False, min_applications=1,
              state=None, sector=None):
    """Visa employers with linked WHD cases, one row per employer, most back wages first.

    `since_year` keeps cases whose findings end in or after that year. `visa_violations_only` keeps
    cases with violations under the employer's own visa program (H-1B or H-2B). Cases with no
    findings date are dropped when `since_year` is set. `zip_links` counts the cases linked by
    name + ZIP, the stricter of the two match rules.
    """
    where, args = _watchlist_filter(program, since_year, visa_violations_only, min_applications, state,
                                    sector)
    return rows(con, f"""
        SELECT e.employer_id, e.employer_name, e.dba, e.city, e.state, e.program, e.naics,
               e.n_applications, e.n_certified, {WILLFUL_COLS}, e.h1b_dependent,
               COUNT(*) AS n_cases,
               SUM(m.method = 'clean name + ZIP') AS zip_links,
               COALESCE(SUM(v.case_violtn_cnt), 0) AS violations,
               COALESCE(SUM(v.h1b_violtn_cnt), 0) AS h1b_violations,
               COALESCE(SUM(v.h2b_violtn_cnt), 0) AS h2b_violations,
               COALESCE(SUM(v.bw_atp_amt), 0) AS back_wages,
               COALESCE(SUM(v.ee_violtd_cnt), 0) AS workers,
               COALESCE(SUM(v.cmp_assd), 0) AS penalties,
               MAX(NULLIF(v.findings_end_date, '')) AS latest_case
        FROM matches m JOIN employers e USING (employer_id) JOIN violation_cases v USING (case_id)
        WHERE {where}
        GROUP BY e.employer_id
        ORDER BY back_wages DESC, n_cases DESC, e.employer_id""", *args)


def watchlist_case_ids(con, **filters):
    """The WHD case IDs behind each watchlist row, as {employer_id: [case_id, ...]}, newest first."""
    where, args = _watchlist_filter(filters.get("program"), filters.get("since_year"),
                                    filters.get("visa_violations_only", False),
                                    filters.get("min_applications", 1), filters.get("state"),
                                    filters.get("sector"))
    out = {}
    for r in rows(con, f"""
            SELECT m.employer_id, v.case_id
            FROM matches m JOIN employers e USING (employer_id) JOIN violation_cases v USING (case_id)
            WHERE {where}
            ORDER BY m.employer_id, v.findings_end_date DESC, v.case_id DESC""", *args):
        out.setdefault(r["employer_id"], []).append(r["case_id"])
    return out


def watchlist_totals(con, **filters):
    """Totals for the watchlist, counting each WHD case once.

    A case can link to several employer rows (one company at two addresses, or in both programs),
    so adding up the watchlist rows would count it more than once.
    """
    where, args = _watchlist_filter(filters.get("program"), filters.get("since_year"),
                                    filters.get("visa_violations_only", False),
                                    filters.get("min_applications", 1), filters.get("state"),
                                    filters.get("sector"))
    return rows(con, f"""
        SELECT COUNT(*) AS cases, COALESCE(SUM(bw_atp_amt), 0) AS back_wages,
               COALESCE(SUM(cmp_assd), 0) AS penalties
        FROM violation_cases WHERE case_id IN (
            SELECT m.case_id
            FROM matches m JOIN employers e USING (employer_id) JOIN violation_cases v USING (case_id)
            WHERE {where})""", *args)[0]


def overview(con, top=5, min_sector_employers=100):
    """Headline numbers and starting points for the lookup page.

    `top_back_wages`: employers with the most back wages in linked WHD cases. `visa_violators`: employers
    whose linked cases found violations of their own visa program (H-1B or H-2B), most applications first;
    `visa_violations` counts those violations.
    `sectors`: industries with at least `min_sector_employers` visa employers, by share with a case.
    """
    totals = rows(con, """
        SELECT (SELECT COUNT(*) FROM employers) AS employers,
               (SELECT COUNT(*) FROM applications) AS applications,
               (SELECT COUNT(DISTINCT employer_id) FROM matches) AS employers_with_cases""")[0]
    totals.update(watchlist_totals(con))
    visa = watchlist(con, visa_violations_only=True)
    for r in visa:
        r["visa_violations"] = r["h1b_violations"] if r["program"] == "H-1B" else r["h2b_violations"]
    visa.sort(key=lambda r: (-r["n_applications"], r["employer_id"]))
    sectors = [r for r in sector_stats(con) if r["employers"] >= min_sector_employers]
    sectors.sort(key=lambda r: -r["share_with_cases"])
    return {"totals": totals, "top_back_wages": watchlist(con)[:top], "visa_violators": visa[:top],
            "n_visa_violators": len(visa), "sectors": sectors[:top]}


# NAICS sectors (first two digits of the code). Three sectors span several two-digit prefixes.
SECTORS = {
    "11": "Agriculture, forestry, fishing and hunting", "21": "Mining, oil and gas",
    "22": "Utilities", "23": "Construction", "31-33": "Manufacturing", "42": "Wholesale trade",
    "44-45": "Retail trade", "48-49": "Transportation and warehousing", "51": "Information",
    "52": "Finance and insurance", "53": "Real estate and rental", "54": "Professional, scientific and technical services",
    "55": "Management of companies", "56": "Administrative, support and staffing services",
    "61": "Educational services", "62": "Health care and social assistance",
    "71": "Arts, entertainment and recreation", "72": "Accommodation and food services",
    "81": "Other services (repair, personal care, etc.)", "92": "Public administration",
}
SECTOR_PREFIXES = {k: [str(p) for p in range(int(k[:2]), int(k[-2:]) + 1)] for k in SECTORS}
_SECTOR_OF = {p: k for k, ps in SECTOR_PREFIXES.items() for p in ps}


def sector_of(naics):
    """The sector key ("54", "31-33", ...) for a NAICS code, or None when the code is missing or invalid."""
    return _SECTOR_OF.get((naics or "")[:2])


def _group_stats(con, key_sql, program=None, visa_violations_only=False):
    """Per-group visa employer counts and linked WHD case totals, each case counted once per group."""
    emp_where, args = ["1"], []
    if program:
        emp_where.append("e.program = ?")
        args.append(program)
    case_where = ["1"]
    if visa_violations_only:
        case_where.append("CASE e.program WHEN 'H-1B' THEN v.h1b_violtn_cnt WHEN 'H-2B' THEN v.h2b_violtn_cnt END > 0")
    emp = rows(con, f"""
        SELECT {key_sql} AS grp, COUNT(*) AS employers, SUM(n_applications) AS applications
        FROM employers e WHERE {" AND ".join(emp_where)} GROUP BY grp""", *args)
    linked = rows(con, f"""
        SELECT grp, COUNT(DISTINCT employer_id) AS employers_with_cases
        FROM (SELECT {key_sql} AS grp, e.employer_id
              FROM matches m JOIN employers e USING (employer_id) JOIN violation_cases v USING (case_id)
              WHERE {" AND ".join(emp_where + case_where)})
        GROUP BY grp""", *args)
    cases = rows(con, f"""
        SELECT grp, COUNT(*) AS cases, COALESCE(SUM(bw_atp_amt), 0) AS back_wages,
               COALESCE(SUM(cmp_assd), 0) AS penalties,
               COALESCE(SUM(h1b_violtn_cnt), 0) + COALESCE(SUM(h2b_violtn_cnt), 0) AS visa_violations
        FROM (SELECT DISTINCT {key_sql} AS grp, v.case_id, v.bw_atp_amt, v.cmp_assd, v.h1b_violtn_cnt,
                     v.h2b_violtn_cnt
              FROM matches m JOIN employers e USING (employer_id) JOIN violation_cases v USING (case_id)
              WHERE {" AND ".join(emp_where + case_where)})
        GROUP BY grp""", *args)
    out = {r["grp"]: dict(r, employers_with_cases=0, cases=0, back_wages=0.0, penalties=0.0,
                          visa_violations=0) for r in emp}
    for r in linked + cases:
        if r["grp"] in out:
            out[r["grp"]].update({k: v for k, v in r.items() if k != "grp"})
    for r in out.values():
        r["share_with_cases"] = r["employers_with_cases"] / r["employers"] if r["employers"] else 0
        r["cases_per_100"] = 100 * r["cases"] / r["employers"] if r["employers"] else 0
    return out


def state_stats(con, program=None, visa_violations_only=False):
    """One row per employer state: visa employers, how many have linked WHD cases, and case totals."""
    out = _group_stats(con, "e.state", program, visa_violations_only)
    return sorted(({**{k: v for k, v in r.items() if k != "grp"}, "state": r["grp"]}
                   for r in out.values() if r["grp"]), key=lambda r: r["state"])


def sector_stats(con, program=None, visa_violations_only=False):
    """One row per NAICS sector, like state_stats, most visa employers first.

    Codes outside the sector list (missing or malformed) are left out.
    """
    case = " ".join(f"WHEN substr(e.naics, 1, 2) = '{p}' THEN '{k}'" for p, k in _SECTOR_OF.items())
    out = _group_stats(con, f"CASE {case} END", program, visa_violations_only)
    return sorted(({**{k: v for k, v in r.items() if k != "grp"}, "sector": r["grp"], "name": SECTORS[r["grp"]]}
                   for r in out.values() if r["grp"]), key=lambda r: -r["employers"])


def case_years(con, employer_id):
    """Linked WHD cases and back wages per year the findings ended, for the employer page's chart."""
    return rows(con, """
        SELECT CAST(substr(v.findings_end_date, 1, 4) AS INTEGER) AS year, COUNT(*) AS cases,
               COALESCE(SUM(v.bw_atp_amt), 0) AS back_wages
        FROM matches m JOIN violation_cases v USING (case_id)
        WHERE m.employer_id = ? AND v.findings_end_date != ''
        GROUP BY year ORDER BY year""", employer_id)


def states(con):
    return [r["state"] for r in rows(con, "SELECT DISTINCT state FROM employers "
                                          "WHERE state != '' ORDER BY state")]


def match_label(method, rate):
    """Plain-language confidence for a name-based link."""
    pct = f"{rate:.0%}" if rate is not None else "an unknown share"
    return f"Matched by {method}; about {pct} of links made this way were wrong in the tax-ID test"


def sources(con):
    return rows(con, "SELECT source_id, file, rows, sha256, urls, created_utc, script FROM sources"
                     " ORDER BY source_id")


# ---------- Red flags ----------
# Plain, explainable patterns in an employer's own FY2025 applications that are worth a closer look.
# None is evidence of a violation. Each flag needs a minimum number of applications, so a handful of
# filings cannot trip it, and each is shown next to the same share across all employers in the program.

_ANNUAL = """(CASE {unit} WHEN 'Hour' THEN 2080 WHEN 'Week' THEN 52 WHEN 'Bi-Weekly' THEN 26
              WHEN 'Month' THEN 12 WHEN 'Year' THEN 1 END)"""
_AT_FLOOR = (f"(a.wage_rate_of_pay_from * {_ANNUAL.format(unit='a.wage_unit_of_pay')}"
             f" < 1.02 * a.prevailing_wage * {_ANNUAL.format(unit='a.pw_unit_of_pay')})")
_HAS_WAGES = (f"(a.wage_rate_of_pay_from > 0 AND a.prevailing_wage > 0 AND {_ANNUAL.format(unit='a.wage_unit_of_pay')}"
              f" IS NOT NULL AND {_ANNUAL.format(unit='a.pw_unit_of_pay')} IS NOT NULL)")
_NOT_CERTIFIED = "(a.case_status IN ('Withdrawn', 'Denied', 'Determination Issued - Denied', 'Determination Issued - Rejected'))"
_AWAY = "(a.worksite_state != '' AND a.worksite_state != e.state)"

# key, title, programs, numerator, denominator (both SQL over applications a JOIN employers e),
# threshold share, minimum denominator, and the sentence that explains it ({k} of {n}, {share}, {base}).
RED_FLAGS = [
    ("wage_floor", "Wages at the floor", ("H-1B",), _AT_FLOOR, _HAS_WAGES, 0.95, 20,
     "{k} of {n} applications ({share}) offer less than 2% above the prevailing wage, against {base} for all "
     "H-1B applications. Paying right at the legal minimum is common, but a filer that almost always does so is "
     "worth comparing with what workers are actually paid."),
    ("entry_level", "Mostly entry-level wage levels", ("H-1B",), "(a.pw_wage_level = 'I')",
     "(a.pw_wage_level IN ('I', 'II', 'III', 'IV'))", 0.75, 20,
     "{k} of {n} applications ({share}) use wage level I, the entry-level prevailing wage, against {base} for "
     "all H-1B applications. Level I is meant for jobs needing little experience."),
    # H-1B only: H-2B withdrawals are routine (the season's cap fills), so the flag would say little there.
    ("not_certified", "Many withdrawn or denied applications", ("H-1B",), _NOT_CERTIFIED, "1", 0.4, 10,
     "{k} of {n} applications ({share}) were withdrawn before a decision, denied or rejected, against {base} "
     "for all H-1B applications."),
    ("away", "Most workers placed outside the home state", ("H-1B", "H-2B"), _AWAY, "(a.worksite_state != '')",
     0.95, 20,
     "{k} of {n} applications ({share}) put the worker in a different state from the employer's address, against "
     "{base} for all {program} applications. This is typical of staffing and consulting firms that place workers "
     "at client sites, where wage and hour problems are harder to see."),
]


def _db_path(con):
    return con.execute("PRAGMA database_list").fetchone()[2]


def red_flags_ready(con):
    """True once the program baselines are built, so a page can skip the flags rather than wait."""
    return _flag_baselines.ready(_db_path(con))


def warm_red_flags(path=None):
    """Compute the program baselines ahead of the first employer page (they take a few seconds)."""
    con = connect(path)
    try:
        _flag_baselines(_db_path(con))
    finally:
        con.close()


@build_once
def _flag_baselines(db_key):
    con = connect(db_key)
    try:
        out = {}
        for key, _, programs, num, den, *_ in RED_FLAGS:
            for p in programs:
                r = rows(con, f"""SELECT SUM(CASE WHEN {num} THEN 1 ELSE 0 END) AS k, COUNT(*) AS n
                                  FROM applications a JOIN employers e USING (employer_id)
                                  WHERE e.program = ? AND {den}""", p)[0]
                out[(key, p)] = (r["k"] or 0) / r["n"] if r["n"] else None
        return out
    finally:
        con.close()


def red_flags(con, employer_id):
    """The red flags this employer's FY2025 applications trip, each with its counts and the program baseline."""
    e = employer(con, employer_id)
    if not e:
        return []
    db_key = _db_path(con)
    pct = lambda x: f"{100 * x:.0f}%" if x is not None else "–"
    out = []
    for key, title, programs, num, den, threshold, min_n, text in RED_FLAGS:
        if e["program"] not in programs:
            continue
        r = rows(con, f"""SELECT SUM(CASE WHEN {num} THEN 1 ELSE 0 END) AS k, COUNT(*) AS n
                          FROM applications a JOIN employers e USING (employer_id)
                          WHERE a.employer_id = ? AND {den}""", employer_id)[0]
        k, n = r["k"] or 0, r["n"]
        if n < min_n or k / n < threshold:
            continue
        base = _flag_baselines(db_key).get((key, e["program"]))
        out.append({"key": key, "title": title, "applications": k, "of": n, "share": k / n, "baseline": base,
                    "threshold": threshold, "min_applications": min_n,
                    "detail": text.format(k=f"{k:,}", n=f"{n:,}", share=pct(k / n), base=pct(base),
                                          program=e["program"])})
    return out


# DOL debarment lists (h1b-data/debarments.csv, made by parse_debarments.py). The list is small and is
# kept in git rather than in research.db, so it can be refreshed without rebuilding the database.

def _debarment_keys(entity):
    """Cleaned names an entry can match: the whole name and each part around d/b/a or aka."""
    parts = re.split(r"\b(?:d/b/a|dba|aka)\b:?|;", entity, flags=re.I)
    return {k for k in (clean_name(x) for x in [entity, *parts]) if k}


@lru_cache(maxsize=4)
def load_debarments(path=None):
    path = path or DEBARMENTS_CSV
    if not os.path.exists(path):
        return ()
    with open(path, newline="", encoding="utf-8") as f:
        out = []
        for i, r in enumerate(csv.DictReader(f)):
            out.append({**r, "debarment_id": i + 1, "keys": frozenset(_debarment_keys(r["entity"]))})
        return tuple(out)


def debarment_status(d, today=None):
    """'active' while the debarment period runs (or it is permanent), 'ended' after, 'starts later' before."""
    today = (today or datetime.date.today()).isoformat()
    if d["start_date"] and d["start_date"] > today:
        return "starts later"
    return "active" if d["end_date"] == "permanent" or d["end_date"] >= today else "ended"


def _public(d, today=None):
    out = {k: v for k, v in d.items() if k not in ("keys", "name_clean")}
    out["status"] = debarment_status(d, today)
    out["match"] = ("Same cleaned name and state as the DOL entry" if d["state"] else
                    "Same cleaned name; DOL's H-1B list gives no address, so the state is not checked")
    return out


def employer_debarments(con, employer_id, path=None, today=None):
    """DOL debarment entries matching this employer by cleaned name (or DBA), and state when DOL gives one.

    Each entry counts this employer's applications decided inside the debarment period."""
    e = rows(con, "SELECT name_clean, dba_clean, state FROM employers WHERE employer_id = ?", employer_id)
    if not e:
        return []
    names = {n for n in (e[0]["name_clean"], e[0]["dba_clean"]) if n}
    out = []
    for d in load_debarments(path):
        if names & d["keys"] and (not d["state"] or d["state"] == e[0]["state"]):
            end = "9999-12-31" if d["end_date"] == "permanent" else d["end_date"]
            during = rows(con, """SELECT COUNT(*) AS n FROM applications WHERE employer_id = ?
                                  AND substr(decision_date, 1, 10) BETWEEN ? AND ?""",
                          employer_id, d["start_date"] or "0000-01-01", end)[0]["n"]
            out.append({**_public(d, today), "applications_during": during})
    return out


def debarments(con, path=None, today=None):
    """Every DOL debarment entry, each with the visa employers in the data that match it."""
    entries = load_debarments(path)
    by_key = {}
    for e in rows(con, "SELECT employer_id, name_clean, dba_clean, state FROM employers"):
        for n in {e["name_clean"], e["dba_clean"]} - {None, ""}:
            by_key.setdefault(n, []).append(e)
    out = []
    for d in entries:
        ids = sorted({e["employer_id"] for k in d["keys"] for e in by_key.get(k, [])
                      if not d["state"] or e["state"] == d["state"]})
        out.append({**_public(d, today), "employers": [r for i in ids for r in rows(con, """
            SELECT employer_id, employer_name, program, city, state, n_applications FROM employers
            WHERE employer_id = ?""", i)]})
    return out


# ---------- Shared representatives ----------
# The law firms, agent businesses and individual attorneys or agents named on applications, grouped across
# every employer they file for. A firm is keyed by its cleaned name (so "Fragomen, Del Rey, ..., LLP" and
# "FRAGOMEN, DEL REY, ... LLP" are one firm); a person by their uppercased name plus the state on the form.
# Representing an employer does not make a firm responsible for that employer's violations.

REP_KINDS = {"firm": "Law firm or agent business", "person": "Attorney or agent"}


def firm_key(name):
    return clean_name(name or "")


def person_key(first, last, state):
    name = " ".join(f"{first or ''} {last or ''}".upper().split())
    return f"{name} ({state.strip().upper()})" if name and (state or "").strip() else name


def _db_key(con):
    return con.execute("PRAGMA database_list").fetchone()[2]


def representatives_ready(con):
    """True once the representative index is built, so a page can skip the panel rather than wait."""
    db_key = _db_key(con)
    return _rep_index.ready(db_key) and _debarred_ids.ready(db_key)


def warm_representatives(path=None):
    """Build the representative index ahead of the first request (it takes a few seconds)."""
    con = connect(path)
    try:
        _rep_index(_db_key(con))
        _debarred_ids(_db_key(con))
    finally:
        con.close()


@build_once
def _rep_index(db_key):
    """{kind: {key: {"names", "employers", "feins"}}} plus each employer's WHD case outcomes."""
    con = connect(db_key)
    try:
        reps = {k: {} for k in REP_KINDS}
        firm_keys = {}
        have = {r[1] for r in con.execute("PRAGMA table_info(applications)")}
        col = lambda c: f"a.{c}" if c in have else "''"  # the H-1B and H-2B files name these differently
        either = lambda c1, c2: f"COALESCE(NULLIF({col(c1)}, ''), {col(c2)})"
        for r in con.execute(f"""
                SELECT a.employer_id, {col("lawfirm_name_business_name")}, {col("lawfirm_business_fein")},
                       {either("agent_attorney_first_name", "attorney_agent_first_name")},
                       {either("agent_attorney_last_name", "attorney_agent_last_name")},
                       {either("agent_attorney_state", "attorney_agent_state")}, COUNT(*)
                FROM applications a GROUP BY 1, 2, 3, 4, 5, 6"""):
            eid, firm, ffein, first, last, st, n = r
            firm = (firm or "").strip()
            if firm:
                key = firm_keys.get(firm)
                if key is None:
                    key = firm_keys[firm] = firm_key(firm)
                if key:
                    _rep_add(reps["firm"], key, firm, eid, n, clean_fein(ffein))
            pkey = person_key(first, last, st)
            if pkey:
                _rep_add(reps["person"], pkey, pkey, eid, n, None, firm)
        outcomes = {}
        for r in con.execute("""
                SELECT m.employer_id, e.program, v.case_id, COALESCE(v.bw_atp_amt, 0),
                       CASE e.program WHEN 'H-1B' THEN v.h1b_violtn_cnt WHEN 'H-2B' THEN v.h2b_violtn_cnt END
                FROM matches m JOIN employers e USING (employer_id) JOIN violation_cases v USING (case_id)"""):
            o = outcomes.setdefault(r[0], {"cases": {}, "visa": False})
            o["cases"][r[2]] = r[3]
            o["visa"] = o["visa"] or bool(r[4])
        employers = {r[0]: r[1] for r in con.execute("SELECT employer_id, program FROM employers")}
        return {"reps": reps, "outcomes": outcomes, "programs": employers}
    finally:
        con.close()


def _rep_add(group, key, name, employer_id, n=1, fein=None, firm=None):
    g = group.get(key)
    if g is None:
        g = group[key] = {"names": {}, "employers": {}, "feins": set(), "firms": {}}
    g["names"][name] = g["names"].get(name, 0) + n
    g["employers"][employer_id] = g["employers"].get(employer_id, 0) + n
    if fein:
        g["feins"].add(fein)
    if firm:
        g["firms"][firm] = g["firms"].get(firm, 0) + n


def _rep_summary(kind, key, g, idx, debarred):
    out, prog = idx["outcomes"], idx["programs"]
    clients = g["employers"]
    with_case = [e for e in clients if e in out]
    visa = [e for e in with_case if out[e]["visa"]]
    cases = {c: bw for e in with_case for c, bw in out[e]["cases"].items()}  # a case linked twice counts once
    programs = {}
    for e in clients:
        programs[prog.get(e)] = programs.get(prog.get(e), 0) + 1
    # What the same client mix would show at each program's overall rate, so H-2B-heavy agents are
    # compared with H-2B employers rather than with H-1B ones.
    base = _program_rates(idx)
    expected = sum(c * base.get(p, 0) for p, c in programs.items())
    return {"kind": kind, "key": key, "name": max(g["names"], key=g["names"].get),
            "clients": len(clients), "applications": sum(clients.values()),
            "programs": {p: c for p, c in programs.items() if p},
            "clients_with_cases": len(with_case), "clients_visa_violations": len(visa),
            "expected_visa_violations": round(expected, 1),
            "times_typical": round(len(visa) / expected, 1) if expected else None,
            "clients_debarred": len(debarred & set(clients)),
            "cases": len(cases), "back_wages": sum(cases.values())}


@build_once
def _debarred_ids(db_key):
    con = connect(db_key)
    try:
        return frozenset(e["employer_id"] for d in debarments(con) for e in d["employers"])
    finally:
        con.close()


def _program_rates(idx):
    """Share of each program's employers with a visa-program violation of their own."""
    cached = idx.get("program_rates")
    if cached is None:
        n, k = {}, {}
        for e, p in idx["programs"].items():
            n[p] = n.get(p, 0) + 1
            o = idx["outcomes"].get(e)
            k[p] = k.get(p, 0) + bool(o and o["visa"])
        cached = idx["program_rates"] = {p: k[p] / n[p] for p in n}
    return cached


def rep_baselines(con):
    """Share of all visa employers, by program, with a visa-program violation of their own."""
    idx = _rep_index(_db_key(con))
    return {"visa_violations": _program_rates(idx), "employers": len(idx["programs"])}


@build_once
def _rep_table(db_key, kind):
    idx = _rep_index(db_key)
    debarred = _debarred_ids(db_key)
    return [_rep_summary(kind, k, g, idx, debarred) for k, g in idx["reps"][kind].items()]


def representatives(con, kind="firm", min_clients=10, query=None, program=None, sort="clients_visa_violations"):
    """Representatives with at least `min_clients` client employers, most clients with visa-program
    violations first. `query` keeps names containing it (after cleaning)."""
    if kind not in REP_KINDS:
        raise ValueError(f"kind must be one of {', '.join(REP_KINDS)}")
    rows_ = _rep_table(_db_key(con), kind)
    needle = clean_name(query) if query else ""
    out = [r for r in rows_ if r["clients"] >= min_clients
           and (not needle or needle in r["key"])
           and (not program or program in r["programs"])]
    if sort == "visa_share":
        out.sort(key=lambda r: (-r["clients_visa_violations"] / r["clients"], -r["clients"]))
    else:
        out.sort(key=lambda r: (-r.get(sort, 0), -r["clients"], r["key"]))
    return out


def representative(con, kind, key):
    """One representative: its totals, the names and tax IDs it filed under, and every client employer."""
    if kind not in REP_KINDS:
        return None
    db_key = _db_key(con)
    idx = _rep_index(db_key)
    g = idx["reps"][kind].get(key)
    if not g:
        return None
    debarred = _debarred_ids(db_key)
    out = _rep_summary(kind, key, g, idx, debarred)
    out["name_variants"] = sorted(({"name": n, "applications": c} for n, c in g["names"].items()),
                                  key=lambda r: -r["applications"])[:10]
    out["feins"] = sorted(g["feins"])
    out["firms"] = sorted(({"name": n, "key": firm_key(n), "applications": c} for n, c in g["firms"].items()),
                          key=lambda r: -r["applications"])[:10]
    ids = list(g["employers"])
    clients = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        clients += rows(con, f"""
            SELECT employer_id, employer_name, city, state, program, n_applications FROM employers
            WHERE employer_id IN ({",".join("?" * len(chunk))})""", *chunk)
    for c in clients:
        o = idx["outcomes"].get(c["employer_id"])
        c["applications_by_rep"] = g["employers"][c["employer_id"]]
        c["cases"] = len(o["cases"]) if o else 0
        c["back_wages"] = sum(o["cases"].values()) if o else 0
        c["visa_violations"] = bool(o and o["visa"])
        c["debarred"] = c["employer_id"] in debarred
    clients.sort(key=lambda c: (-c["debarred"], -c["visa_violations"], -c["back_wages"], -c["applications_by_rep"]))
    out["client_list"] = clients
    out["baseline"] = rep_baselines(con)
    return out


def employer_representatives(con, employer_id):
    """The firms and people that filed this employer's applications, each with its other clients' record."""
    db_key = _db_key(con)
    idx = _rep_index(db_key)
    debarred = _debarred_ids(db_key)
    out = []
    for kind in REP_KINDS:
        for key, g in idx["reps"][kind].items():
            if employer_id in g["employers"]:
                out.append(_rep_summary(kind, key, g, idx, debarred))
    return sorted(out, key=lambda r: (r["kind"] != "firm", -r["clients"]))
