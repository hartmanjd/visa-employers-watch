"""Tests for the dashboard's lookups, on a small made-up database.

Run from the repo root:  python3 -m unittest discover dashboard/tests
"""
import gzip, hashlib, io, os, pathlib, shutil, sqlite3, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "h1b-data", "scripts"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "h1b-data", "tests"))
import build_db, build_filers, fetch_whd, provenance, queries  # noqa: E402
from test_build_db import filing, whd_case, write_csv, write_osha  # noqa: E402

LCA_URL = "https://example.gov/lca.xlsx"


def make_db(d):
    p = lambda n: os.path.join(d, n)
    job = {"JOB_TITLE": "Developer", "SOC_CODE": "15-1252.00", "SOC_TITLE": "Software Developers",
           "WORKSITE_CITY": "Austin", "WORKSITE_STATE": "TX", "WAGE_UNIT_OF_PAY": "Year",
           "PREVAILING_WAGE": "90000", "PW_UNIT_OF_PAY": "Year", "PW_WAGE_LEVEL": "II",
           "LAWFIRM_NAME_BUSINESS_NAME": "Law Firm LLP", "AGENT_ATTORNEY_FIRST_NAME": "Ann",
           "AGENT_ATTORNEY_LAST_NAME": "Lee", "EMPLOYER_POC_EMAIL": "hr@acme.example"}
    write_csv(p("lca.csv"), [
        {**filing("I-1", "Acme Software LLC", "TX", "78701", "111111111"), **job,  # offers $95,000.50
         "DECISION_DATE": "2025-03-01"},  # fiscal year 2025
        {**filing("I-2", "ACME SOFTWARE, INC.", "TX", "78701", "111111111", status="Withdrawn"), **job,
         "WAGE_RATE_OF_PAY_FROM": "45", "WAGE_UNIT_OF_PAY": "Hour", "WORKSITE_CITY": "Dallas",
         "DECISION_DATE": "2023-11-15"},  # fiscal year 2024
        filing("I-3", "Acme Software", "CA", "94105", "111111111"),
        filing("I-4", "Acme Analytics", "NY", "10001", "444444444"),
    ])
    write_csv(p("h2b.csv"), [filing("H-1", "Acme Software", "TX", "78701", "111111111", visa="H-2B")])
    with mock.patch("sys.stdout", io.StringIO()):
        build_filers.main([p("filers_lca.csv"), p("lca.csv"), "--source-url", LCA_URL])
        build_filers.main([p("filers_h2b.csv"), p("h2b.csv")])
    old = whd_case(10, "Acme Software", "", "TX", "78701", h1b=2)
    old.update(findings_end_date="2019-05-01", bw_atp_amt="1000", cmp_assd="250")
    new = whd_case(11, "", "Acme Software LLC", "TX", "73301")  # same state, other ZIP
    new.update(findings_end_date="2023-02-01", bw_atp_amt="500", cmp_assd="0", case_violtn_cnt="3",
               flsa_violtn_cnt="2", flsa_bw_atp_amt="500", flsa_ee_atp_cnt="4", flsa_ot_bw_atp_amt="500",
               flsa_cl_violtn_cnt="1", flsa_cl_minor_cnt="1", flsa_cl_cmp_assd_amt="0",
               flsa_repeat_violator="R", naics_code_description="Software Publishers")
    write_csv(p("whd.csv"), [old, new, whd_case(12, "Zeta", "Zeta", "WA", "98101")],
              fields=fetch_whd.FIELDS)
    provenance.write_manifest(p("whd.csv"), fetch_whd.__file__, sources=[{"url": fetch_whd.URL}])
    write_csv(p("bench.csv"), [{"rule": "clean name + ZIP", "false_match_rate": "0.04"},
                               {"rule": "clean name + state", "false_match_rate": "0.03"}])
    provenance.write_manifest(p("bench.csv"), provenance.__file__)
    with mock.patch("sys.stdout", io.StringIO()):
        build_db.main([p("research.db"), "--lca", p("filers_lca.csv"), "--h2b", p("filers_h2b.csv"),
                       "--whd", p("whd.csv"), "--benchmark", p("bench.csv"), "--osha", write_osha(p("osha"))])
    return p("research.db")


class QueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.con = queries.connect(make_db(cls.d))

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def acme_tx(self, program="H-1B"):
        return next(h for h in queries.search_employers(self.con, "acme software")
                    if h["state"] == "TX" and h["program"] == program)

    def test_search_cleans_the_text_and_ranks_exact_names_first(self):
        hits = queries.search_employers(self.con, "  Acme Software, Inc. ")
        self.assertEqual([(h["employer_name"], h["state"], h["program"]) for h in hits][:3],
                         [("Acme Software LLC", "TX", "H-1B"), ("Acme Software", "CA", "H-1B"),
                          ("Acme Software", "TX", "H-2B")])
        self.assertIn("Acme Analytics", [h["employer_name"] for h in queries.search_employers(self.con, "acme")])
        self.assertEqual(queries.search_employers(self.con, "llc"), [])  # only legal-form words

    def test_search_by_tax_id_and_case_number(self):
        self.assertEqual({(h["employer_name"], h["program"]) for h in queries.search_employers(self.con, "11-1111111")},
                         {("Acme Software LLC", "H-1B"), ("Acme Software", "H-1B"), ("Acme Software", "H-2B")})
        self.assertEqual(queries.find_application(self.con, " i-2 ")["employer_id"], self.acme_tx()["employer_id"])
        self.assertIsNone(queries.find_application(self.con, "I-404"))
        self.assertIsNone(queries.find_application(self.con, "Infosys"))

    def test_application_shows_every_form_field_by_section(self):
        a = queries.application(self.con, "I-1")
        self.assertEqual((a["employer_id"], a["dol_file_url"]), (self.acme_tx()["employer_id"], LCA_URL))
        sections = {s["title"]: {f["field"]: f["value"] for f in s["fields"]}
                    for s in queries.application_sections(a)}
        self.assertEqual(sections["Job"]["job_title"], "Developer")
        self.assertEqual(sections["Wages"]["wage_rate_of_pay_from"], 95000.5)
        self.assertEqual(sections["Employer point of contact"]["employer_poc_email"], "hr@acme.example")
        self.assertEqual(sections["Attorney or agent"]["lawfirm_name_business_name"], "Law Firm LLP")
        self.assertNotIn("source_id", {f for fs in sections.values() for f in fs})
        h = queries.application_highlights(a)
        self.assertEqual((h["workers"], h["worksite"], h["lawfirm"]), (2, "Austin, TX", "Law Firm LLP"))
        self.assertAlmostEqual(h["wage_vs_prevailing"], 95000.5 / 90000 - 1)
        self.assertIsNone(queries.application(self.con, "I-404"))

    def test_employer_profile(self):
        p = queries.employer_profile(self.con, self.acme_tx()["employer_id"])
        self.assertEqual([(j["soc_title"], j["applications"], j["workers"], j["job_titles"]) for j in p["jobs"]],
                         [("Software Developers", 2, 4, ["Developer"])])
        self.assertEqual([w["name"] for w in p["worksites"]], ["Austin, TX", "Dallas, TX"])
        self.assertEqual([(r["name"], r["applications"]) for r in p["lawfirms"]], [("Law Firm LLP", 2)])
        self.assertEqual([r["name"] for r in p["attorneys"]], ["Ann Lee"])
        # $95,000.50 a year is 5.6% above $90,000; $45 an hour (x 2080 = $93,600) is 4% above.
        self.assertEqual({b["band"]: b["applications"] for b in p["wage_bands"]}["2-10% above"], 2)
        self.assertEqual(p["wage_levels"], [{"level": "II", "applications": 2}])
        self.assertEqual(queries.wage_band(0.99), "Below prevailing")
        self.assertEqual(queries.wage_band(1.6), "50%+ above")

    def test_summary_totals_the_linked_cases(self):
        s = queries.summary(self.con, self.acme_tx()["employer_id"])
        self.assertEqual((s["applications"], s["certified"], s["cases"]), (2, 1, 2))
        self.assertEqual((s["back_wages"], s["penalties"], s["h1b_violations"]), (1500.0, 250.0, 2))

    def test_cases_are_newest_first_and_say_how_they_were_linked(self):
        cs = queries.cases(self.con, self.acme_tx()["employer_id"])
        self.assertEqual([(c["case_id"], c["method"]) for c in cs],
                         [(11, "clean name + state"), (10, "clean name + ZIP")])
        self.assertEqual(cs[0]["source_urls"], fetch_whd.URL)
        self.assertEqual(cs[0]["load_dt"], "2024-01-02T03:04:05")
        self.assertEqual(queries.match_label(cs[0]["method"], cs[0]["est_false_match_rate"]),
                         "Matched by clean name + state; about 3% of links made this way were wrong "
                         "in the tax-ID test")

    def test_cases_list_each_law_violated_with_plain_names(self):
        new, old = queries.cases(self.con, self.acme_tx()["employer_id"])
        self.assertEqual(new["laws"], [
            {"law": "flsa", "name": "Minimum wage and overtime (FLSA)", "violations": 2, "back_wages": 500.0,
             "workers": 4, "penalties": 0},
            {"law": "flsa_cl", "name": "Child labor (FLSA)", "violations": 1, "back_wages": 0, "workers": 0,
             "penalties": 0}])
        self.assertEqual(new["details"], {"minimum_wage_back_wages": 0, "overtime_back_wages": 500.0,
                                          "retaliation_back_wages": 0, "child_labor_minors": 1,
                                          "repeat_violator": "Repeat", "industry": "Software Publishers"})
        self.assertEqual([r["law"] for r in old["laws"]], ["h1b"])

    def test_every_law_in_the_whd_data_is_named(self):
        counts = [c for c in fetch_whd.FIELDS if c.endswith("_violtn_cnt") and c != "case_violtn_cnt"]
        laws = queries.case_laws({c: 1 for c in counts})
        self.assertEqual(len(laws), len(counts))

    def test_applications_link_back_to_the_dol_file(self):
        apps = queries.applications(self.con, self.acme_tx()["employer_id"])
        self.assertEqual([(a["case_number"], a["case_status"]) for a in apps],
                         [("I-1", "Certified"), ("I-2", "Withdrawn")])
        self.assertEqual({a["dol_file_url"] for a in apps}, {LCA_URL})  # lca.csv came from lca.xlsx
        self.assertEqual(queries.dol_file_url("LCA_Q1.csv", "https://x.gov/LCA_Q2.xlsx;https://x.gov/LCA_Q1.xlsx"),
                         "https://x.gov/LCA_Q1.xlsx")
        self.assertEqual(len(queries.applications(self.con, self.acme_tx()["employer_id"], limit=1)), 1)

    def test_same_tax_id_finds_other_programs_and_addresses(self):
        rel = queries.same_tax_id(self.con, self.acme_tx()["employer_id"])
        self.assertEqual([(r["program"], r["state"]) for r in rel], [("H-1B", "CA"), ("H-2B", "TX")])

    def test_employer_row_carries_its_source(self):
        e = queries.employer(self.con, self.acme_tx()["employer_id"])
        self.assertEqual((e["source_file"], e["source_urls"]), ("filers_lca.csv", LCA_URL))
        self.assertIsNone(queries.employer(self.con, 999999))

    def test_watchlist_ranks_employers_with_cases_by_back_wages(self):
        w = queries.watchlist(self.con)
        self.assertEqual([(r["employer_name"], r["state"], r["program"]) for r in w],
                         [("Acme Software LLC", "TX", "H-1B"), ("Acme Software", "TX", "H-2B")])
        top = w[0]
        self.assertEqual((top["n_cases"], top["zip_links"], top["back_wages"], top["penalties"]),
                         (2, 1, 1500.0, 250.0))
        self.assertEqual((top["h1b_violations"], top["latest_case"]), (2, "2023-02-01"))

    def test_fiscal_years(self):
        eid = self.acme_tx()["employer_id"]
        self.assertEqual(queries.filing_years(self.con, eid),
                         [{"fiscal_year": 2024, "applications": 1, "certified": 0},
                          {"fiscal_year": 2025, "applications": 1, "certified": 1}])
        self.assertEqual([(r["fiscal_year"], r["H-1B"], r["H-2B"], r["employers"]) for r in queries.fiscal_years(self.con)],
                         [(2024, 1, 0, 1), (2025, 1, 0, 1)])
        self.assertEqual([a["fiscal_year"] for a in queries.applications(self.con, eid)], [2025, 2024])
        names = lambda **kw: [(r["program"], r["first_fiscal_year"], r["last_fiscal_year"])
                              for r in queries.watchlist(self.con, **kw)]
        self.assertEqual(names(filed_in=2024), [("H-1B", 2024, 2025)])
        self.assertEqual(names(filed_in=2020), [])
        self.assertEqual(queries.watchlist_totals(self.con, filed_in=2020)["cases"], 0)

    def test_watchlist_filters(self):
        names = lambda **kw: [(r["program"], r["n_cases"]) for r in queries.watchlist(self.con, **kw)]
        self.assertEqual(names(program="H-2B"), [("H-2B", 2)])
        self.assertEqual(names(state="CA"), [])
        self.assertEqual(names(since_year=2020), [("H-1B", 1), ("H-2B", 1)])
        self.assertEqual(names(visa_violations_only=True), [("H-1B", 1)])  # case 10 has H-1B, not H-2B
        self.assertEqual(names(min_applications=2), [("H-1B", 2)])
        self.assertIn("TX", queries.states(self.con))

    def test_watchlist_totals_count_each_case_once(self):
        # Cases 10 and 11 each link to both Acme TX rows (H-1B and H-2B).
        self.assertEqual(sum(r["n_cases"] for r in queries.watchlist(self.con)), 4)
        t = queries.watchlist_totals(self.con)
        self.assertEqual((t["cases"], t["back_wages"], t["penalties"]), (2, 1500.0, 250.0))
        self.assertEqual(queries.watchlist_totals(self.con, program="H-1B", since_year=2020)["cases"], 1)

    def test_state_stats_count_each_case_once_per_state(self):
        by = {r["state"]: r for r in queries.state_stats(self.con)}
        self.assertEqual(sorted(by), ["CA", "NY", "TX"])
        tx = by["TX"]
        self.assertEqual((tx["employers"], tx["employers_with_cases"], tx["cases"]), (2, 2, 2))
        self.assertEqual((tx["back_wages"], tx["share_with_cases"], tx["cases_per_100"]), (1500.0, 1.0, 100.0))
        self.assertEqual(by["CA"]["cases"], 0)
        h2b = {r["state"]: r for r in queries.state_stats(self.con, program="H-2B")}
        self.assertEqual(list(h2b), ["TX"])
        # Case 10 has H-1B violations only, so no H-2B employer keeps a case.
        self.assertEqual(queries.state_stats(self.con, program="H-2B", visa_violations_only=True)[0]["cases"], 0)

    def test_sector_stats_and_sector_filter(self):
        rows = queries.sector_stats(self.con)
        self.assertEqual([(r["sector"], r["name"], r["employers"], r["cases"]) for r in rows],
                         [("11", "Agriculture, forestry, fishing and hunting", 4, 2)])
        self.assertEqual([queries.sector_of(c) for c in ("1111", "336411", "452", "49", "", None)],
                         ["11", "31-33", "44-45", "48-49", None, None])
        self.assertEqual(len(queries.watchlist(self.con, sector="11")), 2)
        self.assertEqual(queries.watchlist(self.con, sector="54"), [])
        self.assertEqual(queries.watchlist_totals(self.con, sector="54")["cases"], 0)
        with self.assertRaises(ValueError):
            queries.watchlist(self.con, sector="99")

    def test_case_years_total_each_year(self):
        self.assertEqual([(y["year"], y["cases"], y["back_wages"])
                          for y in queries.case_years(self.con, self.acme_tx()["employer_id"])],
                         [(2019, 1, 1000.0), (2023, 1, 500.0)])

    def test_overview(self):
        o = queries.overview(self.con, min_sector_employers=1)
        t = o["totals"]
        self.assertEqual((t["employers"], t["applications"], t["employers_with_cases"]), (4, 5, 2))
        self.assertEqual((t["cases"], t["back_wages"], t["penalties"]), (2, 1500.0, 250.0))
        self.assertEqual([r["program"] for r in o["top_back_wages"]], ["H-1B", "H-2B"])
        # Case 10 found 2 H-1B violations; it links to Acme TX in both programs, but only the H-1B
        # employer violated its own program.
        self.assertEqual(o["n_visa_violators"], 1)
        self.assertEqual([(r["employer_name"], r["program"], r["n_applications"], r["visa_violations"])
                          for r in o["visa_violators"]], [("Acme Software LLC", "H-1B", 2, 2)])
        self.assertEqual([r["sector"] for r in o["sectors"]], ["11"])
        self.assertEqual(queries.overview(self.con)["sectors"], [])  # fewer than 100 employers

    def willful_db(self, yes_cases):
        """A copy of the test database where the applications `yes_cases` answer Yes to the willful
        violator question, with the employer flag set the way build_filers sets it (any Yes)."""
        path = os.path.join(self.d, f"willful_{len(yes_cases)}.db")
        shutil.copy(os.path.join(self.d, "research.db"), path)
        with sqlite3.connect(path) as w:
            for case in yes_cases:
                w.execute("UPDATE applications SET willful_violator = 'Yes' WHERE case_number = ?", (case,))
            w.execute("""UPDATE employers SET willful_violator = 1 WHERE employer_id IN
                         (SELECT employer_id FROM applications WHERE willful_violator = 'Yes')""")
        return queries.connect(path)

    def test_one_willful_answer_out_of_many_is_not_a_flag(self):
        # Acme TX (H-1B) filed two applications; a Yes on one of them is shown as a count only.
        con = self.willful_db(["I-1"])
        emp_id = self.acme_tx()["employer_id"]
        e = queries.employer(con, emp_id)
        self.assertEqual((e["willful_yes"], e["n_applications"], e["willful_violator"]), (1, 2, 0))
        hit = next(h for h in queries.search_employers(con, "acme software") if h["employer_id"] == emp_id)
        self.assertEqual((hit["willful_yes"], hit["willful_violator"]), (1, 0))
        self.assertEqual({r["willful_violator"] for r in queries.watchlist(con)}, {0})
        o = queries.overview(con)
        # Case 10 found 2 H-1B violations; it links to Acme TX in both programs, but only the H-1B
        # employer violated its own program.
        self.assertEqual(o["n_visa_violators"], 1)
        self.assertEqual([(r["employer_name"], r["program"], r["n_applications"], r["visa_violations"])
                          for r in o["visa_violators"]], [("Acme Software LLC", "H-1B", 2, 2)])
        con.close()
        # Yes on both applications makes it a self-reported willful violator everywhere.
        con = self.willful_db(["I-1", "I-2"])
        self.assertEqual(queries.employer(con, emp_id)["willful_violator"], 1)
        self.assertEqual([r["employer_id"] for r in queries.watchlist(con) if r["willful_violator"]], [emp_id])
        con.close()

    def test_database_is_opened_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.con.execute("DELETE FROM employers")

    def test_missing_database_gives_a_clear_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "build_db.py"):
            queries.connect(os.path.join(self.d, "nope.db"))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.gz = os.path.join(self.d, "research.db.gz")
        with open(make_db(self.d), "rb") as f, gzip.open(self.gz, "wb") as g:
            g.write(f.read())
        self.url = pathlib.Path(self.gz).as_uri()
        self.sha = hashlib.sha256(open(self.gz, "rb").read()).hexdigest()
        self.target = os.path.join(self.d, "out", "research.db")
        os.makedirs(os.path.dirname(self.target))

    def test_download_unpacks_a_working_database(self):
        queries.download_db(self.target, self.url, self.sha)
        con = queries.connect(self.target)
        self.assertGreater(len(queries.search_employers(con, "acme")), 0)
        self.assertEqual(os.listdir(os.path.dirname(self.target)), ["research.db"])

    def test_wrong_checksum_leaves_nothing_behind(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            queries.download_db(self.target, self.url, "0" * 64)
        self.assertEqual(os.listdir(os.path.dirname(self.target)), [])

    def test_an_older_layout_is_replaced(self):
        con = sqlite3.connect(self.target)
        con.execute("CREATE TABLE applications (case_number TEXT, case_status TEXT)")
        con.commit()
        con.close()
        queries.download_db(self.target, self.url, self.sha)
        con = queries.connect(self.target)
        self.assertIsNotNone(queries.application(con, "I-1"))

    def test_a_database_without_the_full_whd_columns_is_replaced(self):
        con = sqlite3.connect(self.target)
        con.execute("CREATE TABLE applications (case_number TEXT, job_title TEXT)")
        con.execute("CREATE TABLE violation_cases (case_id INTEGER, h1b_violtn_cnt INTEGER)")
        con.commit()
        con.close()
        queries.download_db(self.target, self.url, self.sha)
        con = queries.connect(self.target)
        self.assertIsNotNone(queries.application(con, "I-1"))

    def test_a_database_without_fiscal_years_is_replaced(self):
        con = sqlite3.connect(self.target)
        con.execute("CREATE TABLE applications (case_number TEXT, job_title TEXT)")
        con.execute("CREATE TABLE violation_cases (case_id INTEGER, flsa_cl_violtn_cnt INTEGER)")
        con.execute("CREATE TABLE osha_employer_totals (employer_id INTEGER)")
        con.commit()
        con.close()
        queries.download_db(self.target, self.url, self.sha)
        con = queries.connect(self.target)
        self.assertEqual([y["fiscal_year"] for y in queries.fiscal_years(con)], [2024, 2025])

    def test_existing_database_is_not_downloaded_again(self):
        open(self.target, "w").close()
        queries.download_db(self.target, "file:///does/not/exist.gz", "0" * 64)
        self.assertEqual(os.path.getsize(self.target), 0)


class OshaQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.con = queries.connect(make_db(cls.d))
        cls.acme = next(h for h in queries.search_employers(cls.con, "acme software")
                        if h["state"] == "TX" and h["program"] == "H-1B")["employer_id"]

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_standard_codes_read_as_citations(self):
        s = queries.osha_standard("19260652 C03 III")
        self.assertEqual((s["citation"], s["title"], s["url"]),
                         ("29 CFR 1926.652(c)(3)(iii)", "Construction: Excavations: protective systems (cave-ins)",
                          "https://www.ecfr.gov/current/title-29/section-1926.652"))
        self.assertEqual(queries.osha_standard("19101200 E01")["citation"], "29 CFR 1910.1200(e)(1)")
        self.assertEqual(queries.osha_standard("19150099 A")["title"], "Shipyards")  # no section title
        self.assertEqual(queries.osha_standard("5A0001")["citation"], "OSH Act Section 5(a)(1)")

    def test_summary_counts_live_citations_only(self):
        s = queries.osha_summary(self.con, self.acme)
        self.assertEqual((s["inspections"], s["violations"], s["serious"], s["repeat_violations"], s["penalties"],
                          s["initial_penalty"], s["deaths"]), (2, 3, 2, 1, 43275.0, 61550.0, 1))
        other = next(h for h in queries.search_employers(self.con, "acme analytics"))["employer_id"]
        self.assertEqual(queries.osha_summary(self.con, other)["inspections"], 0)

    def test_inspections_carry_citations_accidents_and_sources(self):
        insp = queries.osha_inspections(self.con, self.acme)
        self.assertEqual([(i["activity_nr"], i["type_label"], i["n_violations"], i["penalties"]) for i in insp],
                         [(900001, "Complaint", 2, 28275.0), (900002, "Fatality or catastrophe", 1, 15000.0)])
        first = insp[0]
        self.assertEqual(first["osha_url"], "https://www.osha.gov/ords/imis/establishment.inspection_detail?id=900001")
        self.assertEqual([(v["citation_id"], v["type_label"], v["deleted"]) for v in first["violations"]],
                         [("01001", "Serious", False), ("01002", "Other than serious", True), ("02001", "Repeat", False)])
        self.assertEqual(first["violations"][0]["standard_info"]["title"], "General industry: Machine guarding")
        self.assertEqual(first["violations"][0]["abatement"], "Fixed")
        self.assertIn("apiprod.dol.gov", first["source_urls"])
        fatal = insp[1]
        self.assertEqual((fatal["osha_name_field"], fatal["osha_address_field"]), ("name after DBA", "mailing"))
        self.assertEqual(fatal["violations"][0]["hazard_text"], "Employees were exposed to being struck by a forklift.")
        a = fatal["accidents"][0]
        self.assertEqual(a["narrative"], "A forklift backed up and struck him.")
        self.assertEqual([(j["degree"], j["nature"], j["body_part"], j["age"]) for j in a["injuries"]],
                         [("Killed", "Amputation", "Abdomen", 34)])

    def test_watchlist_ranks_by_penalties_and_shows_whd_beside(self):
        rows = queries.osha_watchlist(self.con, program="H-1B")
        self.assertEqual([(r["employer_id"], r["inspections"], r["penalties"], r["whd_cases"]) for r in rows],
                         [(self.acme, 2, 43275.0, 2)])
        # Acme's H-2B row has the same name and ZIP, so the inspections link to it too.
        self.assertEqual([r["program"] for r in queries.osha_watchlist(self.con)], ["H-1B", "H-2B"])
        self.assertEqual(queries.osha_watchlist(self.con, state="CA"), [])
        o = queries.osha_overview(self.con)
        self.assertEqual((o["employers"], o["employers_with_whd"], o["inspections"], o["violations"], o["deaths"]),
                         (2, 2, 2, 3, 1))


if __name__ == "__main__":
    unittest.main()
