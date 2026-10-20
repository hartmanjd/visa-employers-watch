"""Tests for manifests, the employer build and the WHD download.

Run from the repo root:  python3 -m unittest discover h1b-data/tests
(pytest also runs these.)
"""
import csv, io, json, os, sys, tempfile, unittest
from unittest import mock
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import build_filers, fetch_whd, provenance  # noqa: E402


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def read_text(path):
    with open(path) as f:
        return f.read()


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.inp = os.path.join(self.d, "in.csv")
        self.out = os.path.join(self.d, "out.csv")
        write_csv(self.inp, [{"a": "1"}, {"a": "2"}, {"a": "3"}])
        write_csv(self.out, [{"b": "x, with comma"}, {"b": "line\nbreak"}])

    def test_manifest_records_file_inputs_and_sources(self):
        m = provenance.write_manifest(self.out, provenance.__file__, inputs=[self.inp],
                                      sources=[{"url": "https://example.gov/data"}], params={"k": 1})
        on_disk = json.loads(read_text(self.out + ".manifest.json"))
        self.assertEqual(m, on_disk)
        self.assertEqual(m["rows"], 2)  # quoted line break is one row
        self.assertEqual(m["sha256"], provenance.sha256(self.out))
        self.assertEqual(m["inputs"][0]["file"], "in.csv")
        self.assertEqual(m["inputs"][0]["rows"], 3)
        self.assertEqual(m["sources"], [{"url": "https://example.gov/data"}])
        self.assertEqual(m["script"], "provenance.py")

    def test_verify_catches_a_changed_file(self):
        provenance.write_manifest(self.out, provenance.__file__)
        provenance.verify_manifest(self.out)  # unchanged: passes
        with open(self.out, "a") as f:
            f.write("tampered\n")
        with self.assertRaisesRegex(ValueError, "has changed"):
            provenance.verify_manifest(self.out)


class BuildFilersTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        base = {"CASE_STATUS": "Certified", "VISA_CLASS": "H-1B", "TRADE_NAME_DBA": "",
                "EMPLOYER_ADDRESS1": "1 Main St", "EMPLOYER_CITY": "Austin", "EMPLOYER_STATE": "tx",
                "EMPLOYER_POSTAL_CODE": "78701-1234", "EMPLOYER_FEIN": "12-3456789", "NAICS_CODE": "5415",
                "H-1B_DEPENDENT": "No", "WILLFUL_VIOLATOR": "No",
                "EMPLOYER_POC_EMAIL": "someone@example.com", "JOB_TITLE": "Developer",
                "WAGE_RATE_OF_PAY_FROM": "120000"}
        self.q1 = os.path.join(self.d, "lca_q1.csv")
        self.q2 = os.path.join(self.d, "lca_q2.csv")
        write_csv(self.q1, [
            {**base, "CASE_NUMBER": "I-200-1", "EMPLOYER_NAME": "Acme Software, LLC"},
            {**base, "CASE_NUMBER": "I-200-2", "EMPLOYER_NAME": "ACME SOFTWARE LLC", "CASE_STATUS": "Withdrawn"},
            {**base, "CASE_NUMBER": "", "EMPLOYER_NAME": ""},  # blank padding row
        ])
        write_csv(self.q2, [
            {**base, "CASE_NUMBER": "I-200-3", "EMPLOYER_NAME": "Beta Farms Inc", "EMPLOYER_FEIN": "987654321",
             "EMPLOYER_STATE": "FL", "EMPLOYER_POSTAL_CODE": "33101", "WILLFUL_VIOLATOR": "Yes"},
        ])
        self.out = os.path.join(self.d, "filers.csv")
        with mock.patch("sys.stdout", io.StringIO()):
            build_filers.main([self.out, self.q1, self.q2, "--source-url", "https://example.gov/lca"])

    def test_every_case_number_is_kept_and_traceable(self):
        apps = read_csv(os.path.join(self.d, "filers_applications.csv"))
        self.assertEqual(sorted(a["case_number"] for a in apps), ["I-200-1", "I-200-2", "I-200-3"])
        acme = [a for a in apps if a["name_clean"] == "ACME SOFTWARE"]
        self.assertEqual({a["source_file"] for a in acme}, {"lca_q1.csv"})
        self.assertEqual({(a["state"], a["zip5"], a["fein"]) for a in acme}, {("TX", "78701", "123456789")})

    def test_employer_rows_link_to_their_applications(self):
        emp = {e["name_clean"]: e for e in read_csv(self.out)}
        self.assertEqual(set(emp), {"ACME SOFTWARE", "BETA FARMS"})
        self.assertEqual(emp["ACME SOFTWARE"]["n_applications"], "2")
        self.assertEqual(emp["ACME SOFTWARE"]["n_certified"], "1")
        self.assertEqual(emp["BETA FARMS"]["willful_violator"], "True")
        self.assertEqual(emp["BETA FARMS"]["source_files"], "lca_q2.csv")

    def test_a_case_in_two_quarters_keeps_its_latest_record(self):
        q3 = os.path.join(self.d, "lca_q3.csv")
        with open(self.q1, newline="") as f:
            first = next(csv.DictReader(f))
        write_csv(q3, [{**first, "CASE_STATUS": "Certified - Withdrawn"}])  # I-200-1 again
        out = os.path.join(self.d, "again.csv")
        with mock.patch("sys.stdout", io.StringIO()):
            build_filers.main([out, self.q1, self.q2, q3])
        apps = read_csv(os.path.join(self.d, "again_applications.csv"))
        i1 = [a for a in apps if a["case_number"] == "I-200-1"]
        self.assertEqual([(a["case_status"], a["source_file"]) for a in i1],
                         [("Certified - Withdrawn", "lca_q3.csv")])
        acme = [e for e in read_csv(out) if e["name_clean"] == "ACME SOFTWARE"][0]
        self.assertEqual((acme["n_applications"], acme["n_certified"]), ("2", "1"))

    def test_applications_keep_every_disclosure_column(self):
        apps = read_csv(os.path.join(self.d, "filers_applications.csv"))
        a = next(a for a in apps if a["case_number"] == "I-200-1")
        self.assertEqual((a["job_title"], a["wage_rate_of_pay_from"], a["employer_poc_email"]),
                         ("Developer", "120000", "someone@example.com"))
        self.assertEqual((a["employer_fein"], a["fein"], a["h1b_dependent"]), ("12-3456789", "123456789", "No"))
        # Point-of-contact details belong to an application, not to the employer row.
        self.assertNotIn("example.com", read_text(self.out))

    def test_counts_h2b_certifications(self):
        h2b = os.path.join(self.d, "h2b.csv")
        base = {"EMPLOYER_NAME": "Beta Farms", "EMPLOYER_STATE": "FL", "EMPLOYER_POSTAL_CODE": "33101",
                "EMPLOYER_FEIN": "", "NAICS_CODE": "", "EMPLOYER_ADDRESS1": "", "EMPLOYER_CITY": ""}
        write_csv(h2b, [{**base, "CASE_NUMBER": "H-1", "CASE_STATUS": "Determination Issued - Certification"},
                        {**base, "CASE_NUMBER": "H-2", "CASE_STATUS": "Determination Issued - Partial Certification"},
                        {**base, "CASE_NUMBER": "H-3", "CASE_STATUS": "Determination Issued - Denied"}])
        out = os.path.join(self.d, "h2b_filers.csv")
        with mock.patch("sys.stdout", io.StringIO()):
            build_filers.main([out, h2b])
        self.assertEqual([(e["n_applications"], e["n_certified"]) for e in read_csv(out)], [("3", "2")])

    def test_reads_the_later_spelling_of_the_dependent_flag(self):
        q4 = os.path.join(self.d, "lca_q4.csv")
        write_csv(q4, [{"CASE_NUMBER": "I-200-9", "CASE_STATUS": "Certified", "VISA_CLASS": "H-1B",
                        "EMPLOYER_POC_ADDRESS_1": "9 Side St",
                        "EMPLOYER_NAME": "Gamma Co", "EMPLOYER_STATE": "NY", "EMPLOYER_POSTAL_CODE": "10001",
                        "EMPLOYER_FEIN": "", "NAICS_CODE": "", "H_1B_DEPENDENT": "Yes", "WILLFUL_VIOLATOR": "No",
                        "EMPLOYER_ADDRESS1": "", "EMPLOYER_CITY": "", "1ST_PWD_CASE_NUMBER": "P-1"}])
        out = os.path.join(self.d, "q4.csv")
        with mock.patch("sys.stdout", io.StringIO()):
            build_filers.main([out, self.q1, q4])
        self.assertEqual([e["h1b_dependent"] for e in read_csv(out) if e["name_clean"] == "GAMMA"], ["True"])
        apps = {a["case_number"]: a for a in read_csv(os.path.join(self.d, "q4_applications.csv"))}
        self.assertEqual((apps["I-200-9"]["pwd_case_number_1"], apps["I-200-1"]["pwd_case_number_1"]), ("P-1", ""))
        self.assertEqual(apps["I-200-9"]["employer_poc_address1"], "9 Side St")  # FY2021 spelled it ..._ADDRESS_1

    def test_fiscal_year_comes_from_the_file_name_and_the_latest_year_wins(self):
        fy24 = os.path.join(self.d, "LCA_Disclosure_Data_FY2024_Q4.csv")
        fy25 = os.path.join(self.d, "LCA_Disclosure_Data_FY2025_Q1.csv")
        with open(self.q1, newline="") as f:
            first = next(csv.DictReader(f))
        write_csv(fy24, [{**first, "EMPLOYER_NAME": "Acme Software Inc", "CASE_NUMBER": "I-100-1"},
                         {**first, "CASE_NUMBER": "I-200-1"}])  # decided in FY2024, withdrawn in FY2025
        write_csv(fy25, [{**first, "CASE_STATUS": "Certified - Withdrawn"}])
        out = os.path.join(self.d, "years.csv")
        with mock.patch("sys.stdout", io.StringIO()):
            build_filers.main([out, fy24, fy25])
        apps = {a["case_number"]: a for a in read_csv(os.path.join(self.d, "years_applications.csv"))}
        self.assertEqual({k: (a["fiscal_year"], a["case_status"]) for k, a in apps.items()},
                         {"I-100-1": ("2024", "Certified"), "I-200-1": ("2025", "Certified - Withdrawn")})
        acme = read_csv(out)
        self.assertEqual([(e["n_applications"], e["first_fiscal_year"], e["last_fiscal_year"], e["employer_name"])
                          for e in acme], [("2", "2024", "2025", "Acme Software, LLC")])

    def test_fiscal_year_falls_back_to_the_decision_date(self):
        self.assertEqual(build_filers.fiscal_year("H-2B_Disclosure_Data_FY16.xlsx", None), "2016")
        got = build_filers.fiscal_year("lca.csv", pd.Series(["2024-09-30", "2024-10-01", ""]))
        self.assertEqual(list(got), ["2024", "2025", ""])

    def test_both_tables_have_manifests_with_inputs(self):
        for name in ("filers.csv", "filers_applications.csv"):
            path = os.path.join(self.d, name)
            m = provenance.verify_manifest(path)
            self.assertEqual([i["file"] for i in m["inputs"]], ["lca_q1.csv", "lca_q2.csv"])
            self.assertEqual(m["sources"], [{"url": "https://example.gov/lca"}])


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_api(total, seen_urls):
    """Serve `total` made-up WHD rows, then an empty 204-style reply."""
    def urlopen(req, timeout=None):
        url = req.full_url
        seen_urls.append(url)
        q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        off, lim = int(q["offset"]), int(q["limit"])
        rows = [{f: str(i) for f in fetch_whd.FIELDS} for i in range(off, min(off + lim, total))]
        return FakeResponse(json.dumps({"data": rows}).encode() if rows else b"")
    return urlopen


class FetchWhdTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.out = os.path.join(self.d, "whd.csv")
        self.urls = []
        patches = [mock.patch.object(fetch_whd, "PAGE", 3), mock.patch.object(fetch_whd, "PAUSE", 0),
                   mock.patch.dict(os.environ, {"DOL_API_KEY": "SECRET-KEY-123"}),
                   mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_fetch(self, total):
        with mock.patch("urllib.request.urlopen", fake_api(total, self.urls)):
            fetch_whd.main(self.out)

    def test_downloads_all_pages_and_keeps_load_date(self):
        self.run_fetch(7)
        rows = read_csv(self.out)
        self.assertEqual([r["case_id"] for r in rows], [str(i) for i in range(7)])
        self.assertIn("load_dt", rows[0])

    def test_resume_continues_after_existing_rows(self):
        self.run_fetch(4)
        self.urls.clear()
        self.run_fetch(7)  # the table grew; re-run with the same file
        self.assertIn("offset=4", self.urls[0])
        self.assertEqual([r["case_id"] for r in read_csv(self.out)], [str(i) for i in range(7)])

    def test_manifest_records_source_but_never_the_key(self):
        self.run_fetch(5)
        m = provenance.verify_manifest(self.out)
        self.assertEqual(m["rows"], 5)
        self.assertEqual(m["sources"][0]["url"], fetch_whd.URL)
        self.assertIn("load_dt", m["params"]["fields"])
        self.assertTrue(any("SECRET-KEY-123" in u for u in self.urls))  # key does go in requests
        for name in os.listdir(self.d):
            self.assertNotIn("SECRET-KEY-123", read_text(os.path.join(self.d, name)), name)

    def test_refuses_to_resume_a_file_with_other_columns(self):
        write_csv(self.out, [{"case_id": "1", "legal_name": "x"}])
        with self.assertRaisesRegex(SystemExit, "different columns"):
            self.run_fetch(3)


if __name__ == "__main__":
    unittest.main()
