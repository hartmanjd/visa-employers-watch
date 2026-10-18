"""Tests for the chat tools, answer loop and grader, on the small made-up database. No API calls:
the model is replaced by a scripted fake.

Run from the repo root:  python3 -m unittest discover chat/tests
"""
import os, sys, tempfile, unittest
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "dashboard", "tests"))
from chat import engine, eval as chat_eval, tools  # noqa: E402
from test_queries import make_db  # noqa: E402


def text(t):
    return NS(type="text", text=t)


def call(i, name, args):
    return NS(type="tool_use", id=f"t{i}", name=name, input=args)


def reply(*blocks, stop=None, inp=100, out=20):
    stop = stop or ("tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn")
    return NS(content=list(blocks), stop_reason=stop,
              usage=NS(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeClient:
    """Returns the scripted responses in order and records each request."""
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        self.messages = self

    def create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        return self.responses.pop(0)


class ChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = tools.q.connect(make_db(tempfile.mkdtemp()))
        cls.acme = next(h for h in tools.run_tool(cls.con, "search_employers", {"query": "acme software"})["matches"]
                        if h["state"] == "TX" and h["program"] == "H-1B")["employer_id"]

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_tools_return_record_ids(self):
        e = tools.run_tool(self.con, "get_employer", {"employer_id": self.acme})
        self.assertEqual([c["case_id"] for c in e["cases"]], [11, 10])  # newest first
        self.assertIn("Matched by", e["cases"][0]["match"])
        self.assertEqual((e["totals"]["cases"], e["totals"]["back_wages"]), (2, 1500.0))
        apps = tools.run_tool(self.con, "list_applications", {"employer_id": self.acme})
        self.assertEqual({a["case_number"] for a in apps["applications"]}, {"I-1", "I-2"})
        w = tools.run_tool(self.con, "watchlist", {"visa_violations_only": True})
        self.assertEqual((w["employers"], w["rows"][0]["employer_id"], w["rows"][0]["visa_violations"]), (1, self.acme, 2))
        self.assertEqual(tools.run_tool(self.con, "get_application", {"case_number": "i-1"})["employer_id"], self.acme)
        with self.assertRaises(LookupError):
            tools.run_tool(self.con, "get_application", {"case_number": "I-404"})
        with self.assertRaises(ValueError):
            tools.run_tool(self.con, "watchlist", {"sort_by": "name"})
        with self.assertRaises(KeyError):
            tools.run_tool(self.con, "drop_table", {})

    def test_representatives_tool_ranks_and_lists_clients(self):
        e = tools.run_tool(self.con, "get_employer", {"employer_id": self.acme})
        firm = next(r for r in e["representatives"] if r["kind"] == "firm")
        self.assertEqual((firm["name"], firm["clients"]), ("Law Firm LLP", 1))
        ranked = tools.run_tool(self.con, "representatives", {"min_clients": 1, "query": "law firm"})
        self.assertEqual([r["key"] for r in ranked["rows"]], [firm["key"]])
        self.assertIn("H-1B", ranked["overall_visa_violation_rate"])
        one = tools.run_tool(self.con, "representatives", {"key": firm["key"]})
        self.assertEqual([c["employer_id"] for c in one["clients_with_records"]], [self.acme])
        with self.assertRaises(LookupError):
            tools.run_tool(self.con, "representatives", {"key": "NOBODY"})

    def test_osha_tools(self):
        o = tools.run_tool(self.con, "get_employer", {"employer_id": self.acme})["osha"]
        self.assertEqual((o["totals"]["inspections"], o["totals"]["penalties"]), (2, 43275.0))
        first = o["inspections"][0]
        self.assertEqual([c["citation_id"] for c in first["citations"]], ["01001", "02001"])  # deleted item left out
        self.assertEqual(first["citations"][0]["standard"], "29 CFR 1910.212(a)(1)")
        self.assertEqual(o["inspections"][1]["accidents"][0]["injured"], [{"degree": "Killed", "nature": "Amputation",
                                                                           "body_part": "Abdomen", "age": 34}])
        w = tools.run_tool(self.con, "osha_watchlist", {"program": "H-1B"})
        self.assertEqual([(r["employer_id"], r["deaths"], r["whd_cases"]) for r in w["rows"]], [(self.acme, 1, 2)])
        with self.assertRaises(ValueError):
            tools.run_tool(self.con, "osha_watchlist", {"sort_by": "name"})
        self.assertEqual(tools.run_tool(self.con, "data_overview", {})["osha"]["inspections"], 2)

    def test_every_tool_has_a_runner_and_a_closed_schema(self):
        for t in tools.TOOLS:
            self.assertIn(t["name"], tools.RUNNERS)
            self.assertFalse(t["input_schema"]["additionalProperties"])

    def test_answer_runs_tools_and_links_citations(self):
        client = FakeClient(
            reply(call(1, "search_employers", {"query": "acme software"})),
            reply(call(2, "get_employer", {"employer_id": self.acme})),
            reply(text(f"Acme [employer {self.acme}] has 2 cases, including [WHD case 10]. "
                       "See [application I-1] and [WHD case 999].")))
        r = engine.answer(self.con, "Tell me about Acme Software", client=client)
        self.assertEqual([c["name"] for c in r["tool_calls"]], ["search_employers", "get_employer"])
        self.assertIn(f"[employer {self.acme}]({engine.SITE_URL}/#/employer/{self.acme})", r["answer"])
        self.assertIn(f"[WHD case 10]({engine.SITE_URL}/#/employer/{self.acme})", r["answer"])
        # Never returned by a tool: the application (not listed) and the made-up case.
        self.assertEqual([(c["kind"], c["id"]) for c in r["unverified"]], [("application", "I-1"), ("WHD case", "999")])
        self.assertIn("[WHD case 999] (unverified)", r["answer"])
        self.assertEqual(r["usage"]["input_tokens"], 300)
        self.assertAlmostEqual(r["cost_usd"], (300 * 0.10 + 60 * 0.50) / 1e6)
        first = client.requests[0]
        self.assertEqual((first["model"], first["cache_control"]), ("claude-haiku-5-5", {"type": "ephemeral"}))
        self.assertEqual(len(first["tools"]), len(tools.TOOLS))
        # The second request carries the first tool's result back.
        result = client.requests[1]["messages"][-1]["content"][0]
        self.assertEqual((result["type"], result["tool_use_id"]), ("tool_result", "t1"))
        self.assertIn('"employer_id"', result["content"])

    def test_a_failing_tool_is_reported_to_the_model(self):
        client = FakeClient(reply(call(1, "get_employer", {"employer_id": 404})),
                            reply(text("The data doesn't cover that employer.")))
        r = engine.answer(self.con, "Employer 404?", client=client)
        self.assertEqual(r["tool_calls"][0]["error"], "No employer with id 404")
        result = client.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertEqual(r["stop_reason"], "end_turn")

    def test_the_loop_stops_after_the_round_limit(self):
        client = FakeClient(*[reply(call(i, "data_overview", {})) for i in range(engine.MAX_TOOL_ROUNDS + 1)])
        r = engine.answer(self.con, "Loop forever", client=client)
        self.assertEqual(len(client.requests), engine.MAX_TOOL_ROUNDS + 1)
        self.assertIn("more lookups than allowed", r["answer"])

    def test_bare_case_numbers_from_tools_are_linked(self):
        seen = {"employer": set(), "WHD case": set(), "application": {"I-200-25126-941960"}, "case_employer": {}}
        text, cites, unverified = engine.link_citations(
            "Application I-200-25126-941960 pays $10 [application I-200-25126-941960]; I-200-00000-000000 does not.",
            seen)
        self.assertEqual(text.count("](" + engine.SITE_URL + "/#/application/I-200-25126-941960)"), 2)
        self.assertIn(" I-200-00000-000000 does not", text)
        self.assertEqual((len(cites), unverified), (1, []))

    def test_grader(self):
        case = {"numbers": [3346196, 2], "cites": [("employer", "7")], "percents": [0.085]}
        good = {"raw": "LinkedIn had 2 cases and about $3.3 million in back wages (8.5% of employers).",
                "citations": [{"kind": "employer", "id": "7", "verified": True}], "unverified": []}
        self.assertTrue(chat_eval.grade(case, good)["passed"])
        bad = dict(good, raw="LinkedIn had 2 cases and $3.1 million.", unverified=[{"kind": "WHD case", "id": "1"}])
        checks = chat_eval.grade(case, bad)["checks"]
        self.assertEqual([k for k, ok in checks.items() if not ok],
                         ["no_unverified_citations", "number 3.3462e+06", "percent 8.5%"])
        self.assertTrue(chat_eval.grade({"no_data": True}, {"raw": "The data doesn't cover revenue.",
                                                            "citations": [], "unverified": []})["passed"])
        written = {"raw": "Texas has five cases. The data can't tell us that.", "citations": [], "unverified": []}
        self.assertTrue(chat_eval.grade({"numbers": [5], "mentions": [["TX", "Texas"]], "no_data": True},
                                        written)["passed"])
        self.assertFalse(chat_eval.grade({"numbers": [6]}, written)["passed"])

    def test_eval_cases_build_from_the_database(self):
        cases = chat_eval.build_cases(self.con)
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        acme = next(c for c in cases if c["kind"] == "employer")
        self.assertEqual((acme["numbers"], acme["cites"]), ([2, 1500.0], [("employer", str(self.acme))]))
        self.assertEqual(sum(c["kind"] == "no_data" for c in cases), 4)


if __name__ == "__main__":
    unittest.main()
