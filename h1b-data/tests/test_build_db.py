"""Tests for the SQLite database builder and the workbook converter.

Run from the repo root:  python3 -m unittest discover h1b-data/tests
"""
import csv, io, json, os, sqlite3, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import build_db, build_filers, fetch_osha, fetch_whd, provenance, xlsx_to_csv  # noqa: E402


def write_csv(path, rows, fields=None):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def filing(case, name, state, zip_, fein, dba="", visa="H-1B", status="Certified"):
    return {"CASE_NUMBER": case, "CASE_STATUS": status, "VISA_CLASS": visa, "EMPLOYER_NAME": name,
            "TRADE_NAME_DBA": dba, "EMPLOYER_ADDRESS1": "1 Main St", "EMPLOYER_CITY": "Town",
            "EMPLOYER_STATE": state, "EMPLOYER_POSTAL_CODE": zip_, "EMPLOYER_FEIN": fein,
            "NAICS_CODE": "1111", "H-1B_DEPENDENT": "No", "WILLFUL_VIOLATOR": "No",
            **({"BASIC_WAGE_RATE_FROM": "18.25"} if visa == "H-2B" else
               {"TOTAL_WORKER_POSITIONS": "2", "WAGE_RATE_OF_PAY_FROM": "$95,000.50"})}


def whd_case(case_id, trade, legal, state, zip_, h1b=0, h2b=0):
    row = {f: "" for f in fetch_whd.FIELDS}
    row.update({"case_id": str(case_id), "trade_nm": trade, "legal_name": legal, "st_cd": state,
                "zip_cd": zip_, "h1b_violtn_cnt": str(h1b), "h2b_violtn_cnt": str(h2b),
                "bw_atp_amt": "1500.5", "load_dt": "2024-01-02T03:04:05"})
    return row


def write_osha(d):
    """A folder of made-up fetch_osha.py details files, with manifests. Acme Software (TX 78701) has a
    complaint inspection with three citation items (one deleted), and an inspection of a jobsite in
    another ZIP under "ZETA CORP DBA ACME SOFTWARE" whose mailing ZIP is Acme's, after a fatal
    accident. Omega does not match, and the 1999 Acme inspection is older than the default cut-off."""
    os.makedirs(d, exist_ok=True)
    blank = lambda t: {c: "" for c in fetch_osha.DETAILS[t][1]}
    def insp(nr, name, zip_, open_date, kind, mail_zip=""):
        return {**blank("inspection"), "activity_nr": nr, "estab_name": name, "site_address": "5 Site Rd",
                "site_city": "AUSTIN", "site_state": "TX", "site_zip": zip_, "mail_state": "TX" if mail_zip else "",
                "mail_zip": mail_zip, "insp_type": kind, "insp_scope": "B", "owner_type": "A", "safety_hlth": "S",
                "nr_in_estab": "40", "open_date": open_date + "T00:00:00", "close_case_date": "",
                "load_dt": "2026-10-16T00:20:19"}
    def viol(nr, cid, std, kind, initial, current, exposed="1", deleted="", **kw):
        return {**blank("violation"), "activity_nr": nr, "citation_id": cid, "standard": std, "viol_type": kind,
                "initial_penalty": initial, "current_penalty": current, "nr_exposed": exposed, "nr_instances": "1",
                "delete_flag": deleted, "gravity": "10" if kind == "S" else "", "issuance_date": "2024-05-01T00:00:00",
                "abate_complete": "X", "load_dt": "2026-10-16T00:20:19", **kw}
    files = {
        "inspection": [insp("900001", "ACME SOFTWARE LLC", "78701", "2024-03-01", "B"),
                       insp("900002", "ZETA CORP DBA ACME SOFTWARE", "78750", "2022-06-01", "M", mail_zip="78701"),
                       insp("900003", "OMEGA BUILDERS", "78701", "2024-01-01", "H"),
                       insp("900004", "ACME SOFTWARE", "78701", "1999-01-01", "H")],
        "violation": [viol("900001", "01001", "19100212 A01", "S", "16550", "8275", exposed="9"),
                      viol("900001", "01002", "19101200 E01", "O", "1000", "0", deleted="X"),
                      viol("900001", "02001", "19260501 B13", "R", "30000", "20000", exposed="3"),
                      viol("900002", "01001", "5A0001", "S", "15000", "15000"),
                      viol("900003", "01001", "19100212 A01", "S", "5000", "5000")],
        "violation_gen_duty_std": [
            {"activity_nr": "900002", "citation_id": "01001", "line_nr": "2", "line_text": "struck by a forklift.", "load_dt": ""},
            {"activity_nr": "900002", "citation_id": "01001", "line_nr": "1", "line_text": "Employees were exposed to being", "load_dt": ""}],
        "accident_injury": [{**blank("accident_injury"), "summary_nr": "700001", "rel_insp_nr": "900002", "age": "34",
                             "sex": "M", "nature_of_inj": "1", "part_of_body": "1", "degree_of_inj": "1",
                             "occ_code": "0", "injury_line_nr": "1", "load_dt": "2026-10-16T00:17:53"}],
        "accident": [{**blank("accident"), "summary_nr": "700001", "event_date": "2022-05-30T00:00:00",
                      "event_desc": "Employee is struck by forklift and killed", "event_keyword": "FORKLIFT,STRUCK BY",
                      "fatality": "X", "load_dt": "2026-10-16T00:17:53"},
                     {**blank("accident"), "summary_nr": "700099", "event_date": "2021-01-01T00:00:00",
                      "event_desc": "Unrelated accident", "load_dt": ""}],
        "accident_abstract": [{"summary_nr": "700001", "line_nr": "2", "abstract_text": "struck him.", "load_dt": ""},
                              {"summary_nr": "700001", "line_nr": "1", "abstract_text": "A forklift backed up and ", "load_dt": ""}],
    }
    for t, rows in files.items():
        path = os.path.join(d, f"osha_{t}.csv")
        write_csv(path, rows, fetch_osha.DETAILS[t][1])
        provenance.write_manifest(path, fetch_osha.__file__, sources=[{"url": fetch_osha.BASE.format(t)}])
    path = os.path.join(d, "osha_accident_lookup2.csv")
    write_csv(path, [{"accident_code": "IN", "accident_number": "1", "accident_letter": "", "accident_value": "Amputation", "load_dt": ""},
                     {"accident_code": "BD", "accident_number": "1", "accident_letter": "", "accident_value": "Abdomen", "load_dt": ""}],
              fetch_osha.LOOKUP_FIELDS)
    provenance.write_manifest(path, fetch_osha.__file__, sources=[{"url": fetch_osha.BASE.format("accident_lookup2")}])
    return d


class BuildDbTests(unittest.TestCase):
    def setUp(self):
        self.d = d = tempfile.mkdtemp()
        p = lambda n: os.path.join(d, n)
        write_csv(p("lca.csv"), [
            filing("I-1", "Acme Software LLC", "TX", "78701", "111111111"),
            filing("I-2", "ACME SOFTWARE, INC.", "TX", "78701", "111111111"),
            filing("I-3", "Delta Labs", "CA", "94105", "333333333", dba="Delta Research"),
        ])
        write_csv(p("h2b.csv"), [
            filing("H-1", "Beta Farms", "FL", "33101", "222222222", visa="H-2B"),
            filing("H-2", "Acme Software", "NY", "10001", "999999999", visa="H-2B"),
        ])
        with mock.patch("sys.stdout", io.StringIO()):
            build_filers.main([p("filers_lca.csv"), p("lca.csv")])
            build_filers.main([p("filers_h2b.csv"), p("h2b.csv")])
        write_csv(p("whd.csv"), [
            whd_case(10, "Acme Software", "Acme Software LLC", "TX", "78701", h1b=2),  # ZIP match
            whd_case(11, "", "Beta Farms Inc", "FL", "33999", h2b=1),                 # state match only
            whd_case(12, "Delta Research", "", "CA", "94105"),                       # matches the DBA
            whd_case(13, "Gamma Co", "Gamma Co", "WA", "98101"),                     # no match
        ], fields=fetch_whd.FIELDS)
        provenance.write_manifest(p("whd.csv"), fetch_whd.__file__,
                                  sources=[{"url": fetch_whd.URL}])
        write_csv(p("bench.csv"), [{"rule": "clean name + ZIP", "false_match_rate": "0.04"},
                                   {"rule": "clean name + state", "false_match_rate": "0.03"}])
        provenance.write_manifest(p("bench.csv"), provenance.__file__)
        self.db = p("research.db")
        self.args = [self.db, "--lca", p("filers_lca.csv"), "--h2b", p("filers_h2b.csv"),
                     "--whd", p("whd.csv"), "--benchmark", p("bench.csv")]
        with mock.patch("sys.stdout", io.StringIO()):
            build_db.main(self.args)
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)

    def q(self, sql, *args):
        return self.con.execute(sql, args).fetchall()

    def test_violation_cases_keep_every_whd_column_with_its_type(self):
        cols = {r[1]: r[2] for r in self.q("PRAGMA table_info(violation_cases)")}
        self.assertEqual(list(cols), fetch_whd.FIELDS + ["source_id"])
        self.assertEqual((cols["flsa_cl_violtn_cnt"], cols["fmla_bw_atp_amt"], cols["flsa_repeat_violator"]),
                         ("INTEGER", "REAL", "TEXT"))

    def test_every_application_links_to_its_employer(self):
        rows = self.q("SELECT a.case_number, e.program, e.name_clean, e.state FROM applications a"
                      " JOIN employers e USING (employer_id) ORDER BY a.case_number")
        self.assertEqual(rows, [("H-1", "H-2B", "BETA FARMS", "FL"), ("H-2", "H-2B", "ACME SOFTWARE", "NY"),
                                ("I-1", "H-1B", "ACME SOFTWARE", "TX"), ("I-2", "H-1B", "ACME SOFTWARE", "TX"),
                                ("I-3", "H-1B", "DELTA LABS", "CA")])
        self.assertEqual(self.q("SELECT n_applications FROM employers WHERE name_clean='ACME SOFTWARE'"
                                " AND program='H-1B'"), [(2,)])

    def test_matches_use_the_strongest_rule_and_carry_its_error_rate(self):
        rows = self.q("SELECT m.case_id, e.name_clean, e.program, m.method, m.est_false_match_rate,"
                      " m.whd_name_field, m.employer_name_field FROM matches m"
                      " JOIN employers e USING (employer_id) ORDER BY m.case_id, m.whd_name_field")
        self.assertEqual(rows, [
            (10, "ACME SOFTWARE", "H-1B", "clean name + ZIP", 0.04, "trade name", "name"),  # both names match; trade name is tried first
            (11, "BETA FARMS", "H-2B", "clean name + state", 0.03, "legal name", "name"),
            (12, "DELTA LABS", "H-1B", "clean name + ZIP", 0.04, "trade name", "DBA"),
        ])
        # Same cleaned name in another state (Acme in NY) is not linked to case 10.
        self.assertEqual(self.q("SELECT COUNT(*) FROM matches WHERE case_id=10"), [(1,)])

    def test_applications_keep_disclosure_columns_and_numbers(self):
        rows = self.q("SELECT case_number, naics_code, total_worker_positions, wage_rate_of_pay_from"
                      " FROM applications WHERE case_number IN ('I-1', 'H-1') ORDER BY case_number")
        self.assertEqual(rows, [("H-1", "1111", None, None), ("I-1", "1111", 2, 95000.5)])
        self.assertEqual(self.q("SELECT basic_wage_rate_from FROM applications WHERE case_number='H-1'"), [(18.25,)])

    def test_cases_keep_numbers_and_load_dates(self):
        self.assertEqual(self.q("SELECT case_id, h1b_violtn_cnt, bw_atp_amt, load_dt FROM violation_cases"
                                " WHERE case_id=10"), [(10, 2, 1500.5, "2024-01-02T03:04:05")])
        self.assertEqual(self.q("SELECT COUNT(*) FROM violation_cases"), [(4,)])

    def test_every_row_points_to_a_recorded_source(self):
        for table in ["employers", "applications", "violation_cases"]:
            orphans = self.q(f"SELECT COUNT(*) FROM {table} t LEFT JOIN sources s USING (source_id)"
                             " WHERE s.sha256 IS NULL")
            self.assertEqual(orphans, [(0,)], table)
        files = [r[0] for r in self.q("SELECT file FROM sources ORDER BY source_id")]
        self.assertEqual(files, ["filers_lca.csv", "filers_lca_applications.csv", "filers_h2b.csv",
                                 "filers_h2b_applications.csv", "whd.csv", "bench.csv"])
        whd_src = self.q("SELECT urls, rows FROM sources WHERE file='whd.csv'")
        self.assertEqual(whd_src, [(fetch_whd.URL, 4)])

    def test_database_has_its_own_manifest(self):
        m = provenance.verify_manifest(self.db)
        self.assertIsNone(m["rows"])
        self.assertEqual(m["params"]["tables"]["matches"], 3)
        self.assertEqual(len(m["inputs"]), 6)

    def test_refuses_an_input_changed_after_its_manifest(self):
        with open(os.path.join(self.d, "whd.csv"), "a") as f:
            f.write("tampered\n")
        with self.assertRaisesRegex(ValueError, "whd.csv has changed"):
            build_db.main(self.args)


class OshaBuildTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        p = lambda n: os.path.join(d, n)
        write_csv(p("lca.csv"), [filing("I-1", "Acme Software LLC", "TX", "78701", "111111111")])
        write_csv(p("h2b.csv"), [filing("H-1", "Beta Farms", "FL", "33101", "222222222", visa="H-2B")])
        with mock.patch("sys.stdout", io.StringIO()):
            build_filers.main([p("filers_lca.csv"), p("lca.csv")])
            build_filers.main([p("filers_h2b.csv"), p("h2b.csv")])
        write_csv(p("whd.csv"), [whd_case(13, "Gamma Co", "Gamma Co", "WA", "98101")], fields=fetch_whd.FIELDS)
        provenance.write_manifest(p("whd.csv"), fetch_whd.__file__, sources=[{"url": fetch_whd.URL}])
        write_csv(p("bench.csv"), [{"rule": "clean name + ZIP", "false_match_rate": "0.04"},
                                   {"rule": "clean name + state", "false_match_rate": "0.03"}])
        provenance.write_manifest(p("bench.csv"), provenance.__file__)
        self.osha = write_osha(p("osha"))
        self.db = p("research.db")
        with mock.patch("sys.stdout", io.StringIO()):
            build_db.main([self.db, "--lca", p("filers_lca.csv"), "--h2b", p("filers_h2b.csv"), "--whd", p("whd.csv"),
                           "--benchmark", p("bench.csv"), "--osha", self.osha])
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)

    def q(self, sql, *args):
        return self.con.execute(sql, args).fetchall()

    def test_links_by_site_or_mailing_address_and_either_name(self):
        self.assertEqual(self.q("SELECT activity_nr, method, est_false_match_rate, osha_name_field, osha_address_field,"
                                " employer_name_field FROM osha_matches ORDER BY activity_nr"), [
            (900001, "clean name + ZIP", 0.04, "establishment name", "site", "name"),
            (900002, "clean name + ZIP", 0.04, "name after DBA", "mailing", "name")])

    def test_keeps_only_linked_inspections_and_their_details(self):
        self.assertEqual(self.q("SELECT activity_nr FROM osha_inspections ORDER BY 1"), [(900001,), (900002,)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM osha_violations"), [(4,)])  # the deleted item is kept
        self.assertEqual(self.q("SELECT summary_nr FROM osha_accidents"), [(700001,)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM osha_codes"), [(2,)])
        cols = {r[1]: r[2] for r in self.q("PRAGMA table_info(osha_violations)")}
        self.assertEqual(list(cols), fetch_osha.DETAILS["violation"][1] + ["source_id"])
        self.assertEqual((cols["initial_penalty"], cols["nr_exposed"], cols["standard"]), ("REAL", "INTEGER", "TEXT"))

    def test_employer_totals_leave_out_deleted_items(self):
        self.assertEqual(self.q("SELECT inspections, zip_links, inspections_with_violations, violations, serious, willful,"
                                " repeat_violations, initial_penalty, penalties, accidents, deaths, latest_inspection"
                                " FROM osha_employer_totals"),
                         [(2, 2, 2, 3, 2, 0, 1, 61550.0, 43275.0, 1, 1, "2024-03-01T00:00:00")])

    def test_since_drops_older_inspections_and_rows_keep_sources(self):
        self.assertEqual(self.q("SELECT COUNT(*) FROM osha_inspections WHERE open_date < '2004'"), [(0,)])
        for t in ["osha_inspections", "osha_violations", "osha_injuries", "osha_accidents", "osha_accident_text"]:
            self.assertEqual(self.q(f"SELECT COUNT(*) FROM {t} JOIN sources USING (source_id) WHERE sha256 IS NULL"), [(0,)])
        self.assertEqual(self.q("SELECT urls FROM sources WHERE file='osha_violation.csv'"), [(fetch_osha.BASE.format("violation"),)])
        m = provenance.verify_manifest(self.db)
        self.assertEqual((m["params"]["osha_opened_since"], m["params"]["tables"]["osha_matches"]), ("2004-01-01", 2))


class XlsxToCsvTests(unittest.TestCase):
    def test_converts_first_sheet_and_skips_blank_rows(self):
        from openpyxl import Workbook
        import datetime
        d = tempfile.mkdtemp()
        wb = Workbook()
        ws = wb.active
        ws.append(["CASE_NUMBER", "EMPLOYER_POSTAL_CODE", "RECEIVED_DATE", "WAGE"])
        ws.append(["I-1", "02134", datetime.datetime(2025, 1, 31), 50000.0])
        ws.append([None, None, None, None])
        ws.append(["I-2", None, None, 12.5])
        xlsx = os.path.join(d, "in.xlsx")
        wb.save(xlsx)
        out = os.path.join(d, "out.csv")
        with mock.patch("sys.stdout", io.StringIO()):
            xlsx_to_csv.main([xlsx, out, "--source-url", "https://example.gov/in.xlsx"])
        with open(out, newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows, [["CASE_NUMBER", "EMPLOYER_POSTAL_CODE", "RECEIVED_DATE", "WAGE"],
                                ["I-1", "02134", "2025-01-31", "50000"], ["I-2", "", "", "12.5"]])
        m = provenance.verify_manifest(out)
        self.assertEqual((m["rows"], m["inputs"][0]["file"], m["inputs"][0]["rows"]), (2, "in.xlsx", None))
        self.assertEqual(m["sources"], [{"url": "https://example.gov/in.xlsx"}])


if __name__ == "__main__":
    unittest.main()
