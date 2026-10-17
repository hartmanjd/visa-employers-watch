"""Tests for the shared-representative lookups, on a small hand-built database.

Run from the repo root:  python3 -m unittest dashboard.tests.test_representatives
"""
import os, sqlite3, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import queries  # noqa: E402

APP_COLS = ["employer_id", "lawfirm_name_business_name", "lawfirm_business_fein", "agent_attorney_first_name",
            "agent_attorney_last_name", "agent_attorney_state", "attorney_agent_first_name",
            "attorney_agent_last_name", "attorney_agent_state"]


def make_db(d):
    path = os.path.join(d, "reps.db")
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE employers (employer_id INTEGER, employer_name TEXT, name_clean TEXT, dba_clean TEXT,
                   city TEXT, state TEXT, program TEXT, n_applications INTEGER)""")
    con.execute(f"CREATE TABLE applications (employer_id INTEGER, {', '.join(c + ' TEXT' for c in APP_COLS[1:])})")
    con.execute("CREATE TABLE matches (employer_id INTEGER, case_id INTEGER)")
    con.execute("""CREATE TABLE violation_cases (case_id INTEGER, bw_atp_amt REAL, h1b_violtn_cnt INTEGER,
                   h2b_violtn_cnt INTEGER)""")
    # Ten H-1B employers and two H-2B employers. Firm "Smith & Jones, LLP" files for 1-4 and an H-2B one,
    # spelled two ways; "Other Law PC" files for 5-10.
    for i in range(1, 11):
        con.execute("INSERT INTO employers VALUES (?, ?, ?, '', 'Austin', 'TX', 'H-1B', 1)",
                    (i, f"Client {i}", f"CLIENT {i}"))
    con.execute("INSERT INTO employers VALUES (11, 'Farm', 'FARM', '', 'Waco', 'TX', 'H-2B', 1)")
    con.execute("INSERT INTO employers VALUES (12, 'Ranch', 'RANCH', '', 'Waco', 'TX', 'H-2B', 1)")
    app = lambda eid, firm, first="", last="", st="", h2b=False: con.execute(
        f"INSERT INTO applications VALUES ({', '.join('?' * len(APP_COLS))})",
        (eid, firm, "12-3456789" if firm.startswith("Smith") else "", "" if h2b else first, "" if h2b else last,
         "" if h2b else st, first if h2b else "", last if h2b else "", st if h2b else ""))
    for eid in (1, 2):
        app(eid, "Smith & Jones, LLP", "Ann", "Lee", "NY")
    for eid in (3, 4):
        app(eid, "SMITH AND JONES LLP", "ann", "lee", "NY")
    app(11, "Smith & Jones LLP", "Ann", "Lee", "NY", h2b=True)
    for eid in range(5, 11):
        app(eid, "Other Law PC")
    app(12, "")
    # Case 100 (H-1B violations) links to clients 1 and 2, and counts once for the firm. Client 3 has a case
    # under other laws only. The H-2B farm has an H-2B violation.
    con.executemany("INSERT INTO violation_cases VALUES (?, ?, ?, ?)",
                    [(100, 1000, 3, 0), (101, 50, 0, 0), (102, 700, 0, 5)])
    con.executemany("INSERT INTO matches VALUES (?, ?)", [(1, 100), (2, 100), (3, 101), (11, 102)])
    con.commit()
    con.close()
    return path


class RepresentativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = queries.connect(make_db(tempfile.mkdtemp()))

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def firm(self, name):
        return next(r for r in queries.representatives(self.con, "firm", min_clients=1) if r["name"] == name)

    def test_firm_spellings_count_as_one_and_cases_count_once(self):
        r = self.firm("Smith & Jones, LLP")
        self.assertEqual(r["key"], queries.firm_key("SMITH AND JONES LLP"))
        self.assertEqual((r["clients"], r["applications"], r["programs"]), (5, 5, {"H-1B": 4, "H-2B": 1}))
        self.assertEqual((r["clients_with_cases"], r["clients_visa_violations"]), (4, 3))
        self.assertEqual((r["cases"], r["back_wages"]), (3, 1750))  # case 100 once, not twice

    def test_typical_rate_follows_the_client_mix(self):
        rates = queries.rep_baselines(self.con)["visa_violations"]
        self.assertAlmostEqual(rates["H-1B"], 2 / 10)  # clients 1 and 2; client 3's case is not a visa violation
        self.assertAlmostEqual(rates["H-2B"], 1 / 2)
        r = self.firm("Smith & Jones, LLP")
        self.assertEqual(r["expected_visa_violations"], round(4 * 0.2 + 1 * 0.5, 1))
        self.assertEqual(r["times_typical"], round(3 / 1.3, 1))
        other = self.firm("Other Law PC")
        self.assertEqual((other["clients_visa_violations"], other["times_typical"]), (0, 0.0))

    def test_ranking_filters_and_people(self):
        names = [r["name"] for r in queries.representatives(self.con, "firm", min_clients=1)]
        self.assertEqual(names, ["Smith & Jones, LLP", "Other Law PC"])  # most clients with violations first
        self.assertEqual(queries.representatives(self.con, "firm", min_clients=6)[0]["name"], "Other Law PC")
        self.assertEqual([r["name"] for r in queries.representatives(self.con, "firm", min_clients=1, query="other law")],
                         ["Other Law PC"])
        self.assertEqual([r["name"] for r in queries.representatives(self.con, "firm", min_clients=1, program="H-2B")],
                         ["Smith & Jones, LLP"])
        people = queries.representatives(self.con, "person", min_clients=1)
        self.assertEqual([(p["key"], p["clients"]) for p in people], [("ANN LEE (NY)", 5)])  # both form layouts
        with self.assertRaises(ValueError):
            queries.representatives(self.con, "lawyer")

    def test_one_representative_and_an_employers_representatives(self):
        key = queries.firm_key("Smith & Jones, LLP")
        r = queries.representative(self.con, "firm", key)
        self.assertEqual(r["feins"], ["123456789"])
        self.assertEqual(r["name_variants"][0], {"name": "Smith & Jones, LLP", "applications": 2})
        self.assertEqual([c["employer_id"] for c in r["client_list"]][:3], [1, 2, 11])  # violators first, by back wages
        self.assertEqual(r["client_list"][0]["back_wages"], 1000)
        person = queries.representative(self.con, "person", "ANN LEE (NY)")
        self.assertEqual(person["firms"][0]["key"], key)
        self.assertIsNone(queries.representative(self.con, "firm", "NOBODY"))
        reps = queries.employer_representatives(self.con, 3)
        self.assertEqual([(x["kind"], x["name"]) for x in reps], [("firm", "Smith & Jones, LLP"), ("person", "ANN LEE (NY)")])
        self.assertEqual(queries.employer_representatives(self.con, 12), [])


    def test_build_once_builds_each_result_once_under_concurrent_requests(self):
        import threading, time
        calls = []

        @queries.build_once
        def slow(x):
            calls.append(x)
            time.sleep(0.05)
            return x * 2

        out = []
        threads = [threading.Thread(target=lambda: out.append(slow(3))) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((calls, out), ([3], [6] * 8))
        self.assertTrue(slow.ready(3))
        self.assertFalse(slow.ready(4))
        queries.employer_representatives(self.con, 3)
        self.assertTrue(queries.representatives_ready(self.con))

if __name__ == "__main__":
    unittest.main()
