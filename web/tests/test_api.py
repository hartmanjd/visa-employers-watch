"""Tests for the website's JSON API, on the small made-up database from the dashboard tests.

Run from the repo root:  python3 -m unittest discover web/tests
"""
import csv, io, os, sys, tempfile, unittest
from unittest import mock

from fastapi.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "dashboard", "tests"))
from test_queries import make_db  # noqa: E402
from web import api  # noqa: E402


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {"RESEARCH_DB": make_db(tempfile.mkdtemp())})
        cls.env.start()
        cls.client = TestClient(api.app)
        cls.client.__enter__()
        api.q.warm_red_flags()  # build what the startup threads build, so tests don't race them
        api.q.warm_representatives()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.env.stop()

    def acme_tx(self):
        hits = self.client.get("/api/search", params={"q": "acme software"}).json()
        return next(h for h in hits if h["state"] == "TX" and h["program"] == "H-1B")

    def test_search(self):
        names = [h["employer_name"] for h in self.client.get("/api/search", params={"q": "acme"}).json()]
        self.assertIn("Acme Analytics", names)
        self.assertEqual(self.client.get("/api/search", params={"q": ""}).json(), [])

    def test_employer_page_data(self):
        d = self.client.get(f"/api/employers/{self.acme_tx()['employer_id']}").json()
        self.assertEqual(d["summary"]["cases"], 2)
        self.assertEqual([c["case_id"] for c in d["cases"]], [11, 10])
        self.assertTrue(d["cases"][0]["match_label"].startswith("Matched by clean name + state"))
        self.assertEqual([(r["program"], r["state"]) for r in d["same_tax_id"]], [("H-1B", "CA"), ("H-2B", "TX")])
        self.assertEqual(self.client.get("/api/employers/999999").status_code, 404)

    def test_application_page_and_case_number_search(self):
        d = self.client.get("/api/applications/i-1").json()
        self.assertEqual((d["application"]["case_number"], d["application"]["employer_id"]),
                         ("I-1", self.acme_tx()["employer_id"]))
        self.assertEqual(d["highlights"]["job_title"], "Developer")
        self.assertIn("Employer point of contact", [s["title"] for s in d["sections"]])
        self.assertEqual(self.client.get("/api/applications/I-404").status_code, 404)
        hits = self.client.get("/api/search", params={"q": "I-1"}).json()
        self.assertEqual([(h["employer_name"], h["case_number"]) for h in hits], [("Acme Software LLC", "I-1")])
        e = self.client.get(f"/api/employers/{self.acme_tx()['employer_id']}").json()
        self.assertEqual([j["soc_title"] for j in e["profile"]["jobs"]], ["Software Developers"])
        rows = self.client.get(f"/api/employers/{self.acme_tx()['employer_id']}/applications").json()["rows"]
        self.assertEqual((rows[0]["job_title"], rows[0]["worksite_city"], rows[0]["lawfirm"]),
                         ("Developer", "Austin", "Law Firm LLP"))

    def test_overview(self):
        o = self.client.get("/api/overview").json()
        self.assertEqual((o["totals"]["employers"], o["totals"]["cases"]), (4, 2))
        self.assertEqual(o["top_back_wages"][0]["employer_name"], "Acme Software LLC")
        self.assertEqual([r["visa_violations"] for r in o["visa_violators"]], [2])
        self.assertEqual(o["sectors"], [])

    def test_applications_are_paged(self):
        eid = self.acme_tx()["employer_id"]
        with mock.patch.object(api, "PAGE_SIZE", 1):
            p1 = self.client.get(f"/api/employers/{eid}/applications").json()
            p2 = self.client.get(f"/api/employers/{eid}/applications", params={"page": 2}).json()
        self.assertEqual((p1["total"], len(p1["rows"]), p1["rows"][0]["case_number"]), (2, 1, "I-1"))
        self.assertEqual(p2["rows"][0]["case_number"], "I-2")
        self.assertTrue(p1["rows"][0]["dol_file_url"])
        self.assertEqual(self.client.get("/api/employers/999999/applications").status_code, 404)

    def test_watchlist_and_filters(self):
        d = self.client.get("/api/watchlist").json()
        self.assertEqual([r["program"] for r in d["rows"]], ["H-1B", "H-2B"])
        self.assertEqual(d["totals"]["cases"], 2)
        d = self.client.get("/api/watchlist", params={"program": "H-1B", "since": 2020}).json()
        self.assertEqual([(r["program"], r["n_cases"]) for r in d["rows"]], [("H-1B", 1)])
        d = self.client.get("/api/watchlist", params={"visa_only": "true"}).json()
        self.assertEqual([r["program"] for r in d["rows"]], ["H-1B"])
        self.assertEqual(self.client.get("/api/watchlist", params={"program": "H-3"}).status_code, 422)

    def test_state_and_sector_stats(self):
        states = self.client.get("/api/states").json()
        self.assertEqual([r["state"] for r in states], ["CA", "NY", "TX"])
        self.assertEqual(states[2]["cases"], 2)
        h2b = self.client.get("/api/states", params={"program": "H-2B", "visa_only": "true"}).json()
        self.assertEqual([(r["state"], r["cases"]) for r in h2b], [("TX", 0)])
        sectors = self.client.get("/api/sectors").json()
        self.assertEqual([(r["sector"], r["employers"]) for r in sectors], [("11", 4)])
        self.assertEqual(self.client.get("/api/sectors", params={"program": "H-3"}).status_code, 422)
        d = self.client.get("/api/watchlist", params={"sector": "11"}).json()
        self.assertEqual(len(d["rows"]), 2)
        self.assertEqual(self.client.get("/api/watchlist", params={"sector": "99"}).status_code, 422)
        e = self.client.get(f"/api/employers/{self.acme_tx()['employer_id']}").json()
        self.assertEqual((e["employer"]["sector"], [y["year"] for y in e["case_years"]]), ("11", [2019, 2023]))

    def test_meta_and_site(self):
        meta = self.client.get("/api/meta").json()
        self.assertIn("TX", meta["states"])
        self.assertEqual(meta["sectors"]["54"], "Professional, scientific and technical services")
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Visa Employer Watch", page.text)
        self.assertEqual(self.client.get("/app.js").status_code, 200)

    def test_employer_red_flags(self):
        d = self.client.get(f"/api/employers/{self.acme_tx()['employer_id']}").json()
        self.assertEqual(d["red_flags"], [])  # too few applications to count

    def test_employer_page_skips_panels_still_being_built(self):
        with mock.patch.object(api.q, "red_flags_ready", return_value=False), \
             mock.patch.object(api.q, "representatives_ready", return_value=False):
            d = self.client.get(f"/api/employers/{self.acme_tx()['employer_id']}").json()
        self.assertEqual((d["red_flags"], d["representatives"]), (None, None))
        self.assertTrue(d["cases"])

    def test_debarments(self):
        rows = self.client.get("/api/debarments").json()
        self.assertTrue(rows)
        self.assertTrue({"program", "entity", "status", "employers", "source_url"} <= set(rows[0]))
        self.assertIn("debarments", self.client.get(f"/api/employers/{self.acme_tx()['employer_id']}").json())

    def test_representatives(self):
        d = self.client.get("/api/representatives", params={"min_clients": 1}).json()
        firm = next(r for r in d["rows"] if r["name"] == "Law Firm LLP")
        self.assertEqual(firm["clients"], 1)
        self.assertIn("H-1B", d["baseline"]["visa_violations"])
        people = self.client.get("/api/representatives", params={"kind": "person", "min_clients": 1}).json()["rows"]
        self.assertTrue(people)
        one = self.client.get(f"/api/representatives/firm/{firm['key']}").json()
        self.assertEqual([c["employer_id"] for c in one["client_list"]], [self.acme_tx()["employer_id"]])
        self.assertEqual(self.client.get("/api/representatives/firm/NOBODY").status_code, 404)
        self.assertEqual(self.client.get("/api/representatives", params={"kind": "lawyer"}).status_code, 422)
        self.assertEqual(self.client.get("/api/representatives", params={"sort": "name"}).status_code, 422)
        emp = self.client.get(f"/api/employers/{self.acme_tx()['employer_id']}").json()
        self.assertIn(firm["key"], [r["key"] for r in emp["representatives"]])

    def csv_rows(self, path, **params):
        r = self.client.get(path, params=params)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        self.assertIn("attachment; filename=", r.headers["content-disposition"])
        return r, list(csv.DictReader(io.StringIO(r.text)))

    def test_watchlist_csv_keeps_filters_and_case_ids(self):
        json_rows = self.client.get("/api/watchlist", params={"program": "H-1B"}).json()["rows"]
        r, rows = self.csv_rows("/api/watchlist.csv", program="H-1B")
        self.assertIn('filename="watchlist-H-1B.csv"', r.headers["content-disposition"])
        self.assertEqual([int(x["employer_id"]) for x in rows], [x["employer_id"] for x in json_rows])
        acme = self.acme_tx()["employer_id"]
        row = next(x for x in rows if int(x["employer_id"]) == acme)
        cases = self.client.get(f"/api/employers/{acme}").json()["cases"]
        self.assertEqual(row["whd_case_ids"].split(";"), [str(v["case_id"]) for v in cases])
        self.assertTrue(row["employer_page"].endswith(f"/#/employer/{acme}"))
        _, since = self.csv_rows("/api/watchlist.csv", since=2020)
        self.assertEqual([len(x["whd_case_ids"].split(";")) for x in since],
                         [x["n_cases"] for x in self.client.get("/api/watchlist", params={"since": 2020}).json()["rows"]])
        self.assertEqual(self.client.get("/api/watchlist.csv", params={"program": "H-9"}).status_code, 422)

    def test_employer_csvs_carry_record_ids(self):
        acme = self.acme_tx()["employer_id"]
        _, apps = self.csv_rows(f"/api/employers/{acme}/applications.csv")
        self.assertEqual(len(apps), self.client.get(f"/api/employers/{acme}/applications").json()["total"])
        self.assertTrue(all(a["case_number"] and a["application_page"].endswith("/#/application/" + a["case_number"])
                            for a in apps))
        self.assertTrue(all(a["employer_id"] == str(acme) for a in apps))
        _, cases = self.csv_rows(f"/api/employers/{acme}/cases.csv")
        self.assertTrue(cases and all(c["case_id"] and c["load_dt"] and c["match_label"] for c in cases))
        self.assertEqual(self.client.get("/api/employers/999999/cases.csv").status_code, 404)
        self.assertEqual(self.client.get("/api/employers/999999/applications.csv").status_code, 404)

    def test_debarments_csv(self):
        _, rows = self.csv_rows("/api/debarments.csv")
        self.assertEqual(len(rows), len(self.client.get("/api/debarments").json()))
        self.assertTrue({"program", "entity", "status", "matched_employer_ids", "source_url"} <= set(rows[0]))

    def test_mcp_address_is_published(self):
        mcp_url = "https://visa-employer-watch.onrender.com/mcp"
        self.assertIn(mcp_url, self.client.get("/").text)
        guide = self.client.get("/llms.txt")
        self.assertEqual(guide.status_code, 200)
        self.assertTrue(guide.headers["content-type"].startswith("text/plain"))
        self.assertIn(mcp_url, guide.text)
        for tool in self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]:
            self.assertIn(f"`{tool['name']}`", guide.text)


if __name__ == "__main__":
    unittest.main()
