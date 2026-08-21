"""omodul.wayfinding_github tests.

Two layers: pure render_map_body/parse_map_body round-trip tests (no gh at
all), and operation-level tests against a FakeGithub that fakes just enough
of the GraphQL surface (createIssue/addBlockedBy/subIssues/label/id lookups)
and `gh issue edit/close/comment/label create` CLI subcommands to exercise
chart→ticket→blocking→frontier→claim→resolve→fog→complete without a network
call. Correctness against *real* GitHub's actual GraphQL semantics is proven
separately by a live smoke run against soffy88/Veya (cleaned up after).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_THREE_O = Path(__file__).resolve().parents[3]
for _lib in ("obase", "oprim", "omodul", "oskill", "oservi"):
    _p = str(_THREE_O / _lib)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from omodul import wayfinding_github as wg  # noqa: E402
from omodul.wayfinding_github import (  # noqa: E402
    WayfindingGithubError,
    parse_map_body,
    render_map_body,
)

# ---------------------------------------------------------------------------
# pure render/parse
# ---------------------------------------------------------------------------


class TestMapBodyRoundTrip:
    def test_empty_map_round_trips(self):
        body = render_map_body(destination="ship feature X")
        parsed = parse_map_body(body)
        assert parsed["destination"] == "ship feature X"
        assert parsed["decisions"] == []
        assert parsed["fog"] == []
        assert parsed["out_of_scope"] == []

    def test_full_map_round_trips(self):
        body = render_map_body(
            destination="pick a queue",
            notes="prefer open source",
            decisions=[{"title": "pick db", "link": "https://x/1", "gist": "use postgres"}],
            fog=["auth strategy unclear"],
            out_of_scope=[{"title": "rewrite auth", "reason": "separate effort"}],
        )
        parsed = parse_map_body(body)
        assert parsed["destination"] == "pick a queue"
        assert parsed["notes"] == "prefer open source"
        assert parsed["decisions"] == [
            {"title": "pick db", "link": "https://x/1", "gist": "use postgres"}
        ]
        assert parsed["fog"] == ["auth strategy unclear"]
        assert parsed["out_of_scope"] == [{"title": "rewrite auth", "reason": "separate effort"}]

    def test_decision_title_with_brackets_and_colons_still_parses(self):
        body = render_map_body(
            destination="d",
            decisions=[
                {"title": "pick [db]: postgres?", "link": "https://x/1", "gist": "yes: postgres"}
            ],
        )
        parsed = parse_map_body(body)
        assert parsed["decisions"][0]["gist"] == "yes: postgres"

    def test_missing_sections_parse_as_empty(self):
        parsed = parse_map_body("no headers at all here")
        assert parsed == {
            "destination": "",
            "notes": "",
            "decisions": [],
            "fog": [],
            "out_of_scope": [],
        }


# ---------------------------------------------------------------------------
# FakeGithub — in-memory model behind the two seams (_gh_run / graphql)
# ---------------------------------------------------------------------------


class FakeGithub:
    def __init__(self) -> None:
        self.next_number = 1
        self.issues: dict[int, dict] = {}
        self.labels: set[str] = set()
        self.viewer_login = "test-user"

    def gh_run(self, args: list[str], *, input_text: str | None = None) -> str:
        if args[:2] == ["api", "graphql"]:
            payload = json.loads(input_text)
            return json.dumps({"data": self._graphql(payload["query"], payload["variables"])})
        if args[:2] == ["label", "create"]:
            self.labels.add(args[2])
            return ""
        if args[:2] == ["issue", "edit"]:
            number = int(args[2])
            issue = self.issues[number]
            if "--add-assignee" in args:
                who = args[args.index("--add-assignee") + 1]
                issue["assignees"].append(self.viewer_login if who == "@me" else who)
            if "--body-file" in args:
                issue["body"] = input_text
            return ""
        if args[:2] == ["issue", "comment"]:
            number = int(args[2])
            self.issues[number].setdefault("comments", []).append(args[args.index("-b") + 1])
            return ""
        if args[:2] == ["issue", "close"]:
            number = int(args[2])
            self.issues[number]["closed"] = True
            return ""
        raise AssertionError(f"FakeGithub: unhandled gh args: {args}")

    def _number_from_id(self, node_id: str) -> int:
        return int(node_id.split("_", 1)[1])

    def _graphql(self, query: str, variables: dict) -> dict:
        if "createIssue(input:" in query:
            number = self.next_number
            self.next_number += 1
            parent = variables.get("parentIssueId")
            labels = [lid.split("_", 1)[1] for lid in variables["labelIds"]]
            issue = {
                "number": number,
                "id": f"I_{number}",
                "title": variables["title"],
                "body": variables["body"],
                "closed": False,
                "assignees": [],
                "labels": labels,
                "blocked_by": [],
                "sub_issues": [],
                "url": f"https://github.com/fake/repo/issues/{number}",
            }
            self.issues[number] = issue
            if parent:
                self.issues[self._number_from_id(parent)]["sub_issues"].append(number)
            return {
                "createIssue": {
                    "issue": {
                        "id": issue["id"],
                        "number": number,
                        "url": issue["url"],
                        "title": issue["title"],
                    }
                }
            }
        if "addBlockedBy(input:" in query:
            to_n = self._number_from_id(variables["issueId"])
            from_n = self._number_from_id(variables["blockingIssueId"])
            self.issues[to_n]["blocked_by"].append(from_n)
            return {"addBlockedBy": {"clientMutationId": None}}
        if "repository(owner:$owner,name:$name){ id }" in query:
            return {"repository": {"id": "R_fake"}}
        if "label(name:$label)" in query:
            name = variables["label"]
            if name not in self.labels:
                return {"repository": {"label": None}}
            return {"repository": {"label": {"id": f"L_{name}"}}}
        if "subIssues(first:100)" in query:
            number = variables["number"]
            nodes = []
            for n in self.issues[number]["sub_issues"]:
                iss = self.issues[n]
                nodes.append(
                    {
                        "number": iss["number"],
                        "title": iss["title"],
                        "state": "CLOSED" if iss["closed"] else "OPEN",
                        "url": iss["url"],
                        "assignees": {"nodes": [{"login": a} for a in iss["assignees"]]},
                        "labels": {"nodes": [{"name": lbl} for lbl in iss["labels"]]},
                        "blockedBy": {
                            "nodes": [
                                {
                                    "number": b,
                                    "state": "CLOSED" if self.issues[b]["closed"] else "OPEN",
                                }
                                for b in iss["blocked_by"]
                            ]
                        },
                    }
                )
            return {"repository": {"issue": {"subIssues": {"nodes": nodes}}}}
        if "{ body } } }" in query:
            number = variables["number"]
            return {"repository": {"issue": {"body": self.issues[number]["body"]}}}
        if "closed assignees" in query:
            number = variables["number"]
            iss = self.issues[number]
            return {
                "repository": {
                    "issue": {
                        "id": iss["id"],
                        "title": iss["title"],
                        "closed": iss["closed"],
                        "assignees": {"nodes": [{"login": a} for a in iss["assignees"]]},
                    }
                }
            }
        if "{ id } } }" in query:
            number = variables["number"]
            return {"repository": {"issue": {"id": self.issues[number]["id"]}}}
        if "viewer{ login }" in query:
            return {"viewer": {"login": self.viewer_login}}
        raise AssertionError(f"FakeGithub: unhandled query: {query}")


@pytest.fixture
def fake(monkeypatch):
    gh = FakeGithub()
    monkeypatch.setattr(wg, "_gh_run", gh.gh_run)
    return gh


REPO = "fake/repo"

# ---------------------------------------------------------------------------
# operation-level tests
# ---------------------------------------------------------------------------


class TestChartAndAddTicket:
    def test_chart_map_creates_labeled_issue(self, fake):
        issue = wg.chart_map(REPO, "pick a queue", notes="prefer open source")
        assert issue["number"] == 1
        stored = fake.issues[1]
        assert stored["labels"] == ["wayfinder:map"]
        assert "pick a queue" in stored["body"]
        assert "wayfinder:map" in fake.labels

    def test_add_ticket_is_a_sub_issue_of_the_map(self, fake):
        m = wg.chart_map(REPO, "d")
        t = wg.add_ticket(REPO, m["number"], "pick db", "which db?", "research")
        assert fake.issues[m["number"]]["sub_issues"] == [t["number"]]
        assert fake.issues[t["number"]]["labels"] == ["wayfinder:research"]
        assert "which db?" in fake.issues[t["number"]]["body"]

    def test_unknown_ticket_type_raises(self, fake):
        m = wg.chart_map(REPO, "d")
        with pytest.raises(WayfindingGithubError):
            wg.add_ticket(REPO, m["number"], "t", "q", "telepathy")


class TestFrontierAndBlocking:
    def test_frontier_lists_open_unassigned_unblocked(self, fake):
        m = wg.chart_map(REPO, "d")
        t1 = wg.add_ticket(REPO, m["number"], "A", "qa")
        t2 = wg.add_ticket(REPO, m["number"], "B", "qb")
        result = wg.frontier(REPO, m["number"])
        assert {r["number"] for r in result} == {t1["number"], t2["number"]}

    def test_blocked_ticket_excluded_until_blocker_closed(self, fake):
        m = wg.chart_map(REPO, "d")
        t1 = wg.add_ticket(REPO, m["number"], "A", "qa")
        t2 = wg.add_ticket(REPO, m["number"], "B", "qb")
        wg.wire_blocking(REPO, t1["number"], t2["number"])
        result = wg.frontier(REPO, m["number"])
        assert {r["number"] for r in result} == {t1["number"]}

        wg.claim_ticket(REPO, t1["number"])
        wg.resolve_ticket(REPO, m["number"], t1["number"], resolution="done", gist="done")
        result2 = wg.frontier(REPO, m["number"])
        assert {r["number"] for r in result2} == {t2["number"]}

    def test_claimed_ticket_excluded_from_frontier(self, fake):
        m = wg.chart_map(REPO, "d")
        t1 = wg.add_ticket(REPO, m["number"], "A", "qa")
        wg.claim_ticket(REPO, t1["number"])
        assert wg.frontier(REPO, m["number"]) == []


class TestClaimResolve:
    def test_claim_then_second_claim_fails(self, fake):
        m = wg.chart_map(REPO, "d")
        t = wg.add_ticket(REPO, m["number"], "A", "qa")
        r1 = wg.claim_ticket(REPO, t["number"], login="alice")
        assert r1["ok"] is True
        r2 = wg.claim_ticket(REPO, t["number"], login="bob")
        assert r2["ok"] is False
        assert r2["claimed_by"] == "alice"

    def test_resolve_without_claim_fails(self, fake):
        m = wg.chart_map(REPO, "d")
        t = wg.add_ticket(REPO, m["number"], "A", "qa")
        r = wg.resolve_ticket(REPO, m["number"], t["number"], resolution="x", gist="y")
        assert r["ok"] is False
        assert r["reason"] == "not claimed"

    def test_resolve_closes_ticket_and_appends_decision_to_map_body(self, fake):
        m = wg.chart_map(REPO, "d")
        t = wg.add_ticket(REPO, m["number"], "pick db", "which db?")
        wg.claim_ticket(REPO, t["number"])
        r = wg.resolve_ticket(
            REPO, m["number"], t["number"], resolution="chose postgres", gist="use postgres"
        )
        assert r["ok"] is True
        assert fake.issues[t["number"]]["closed"] is True
        assert fake.issues[t["number"]]["comments"] == ["chose postgres"]
        decisions = wg.decisions_so_far(REPO, m["number"])
        assert decisions == [
            {
                "title": "pick db",
                "link": f"https://github.com/{REPO}/issues/{t['number']}",
                "gist": "use postgres",
            }
        ]


class TestOutOfScopeFogComplete:
    def test_rule_out_of_scope_closes_and_records(self, fake):
        m = wg.chart_map(REPO, "d")
        t = wg.add_ticket(REPO, m["number"], "A", "qa")
        r = wg.rule_out_of_scope(REPO, m["number"], t["number"], "not this cycle")
        assert r["ok"] is True
        assert fake.issues[t["number"]]["closed"] is True
        body = parse_map_body(fake.issues[m["number"]]["body"])
        assert body["out_of_scope"] == [{"title": "A", "reason": "not this cycle"}]

    def test_add_fog_then_graduate_creates_sub_issue_and_clears_patch(self, fake):
        m = wg.chart_map(REPO, "d")
        wg.add_fog(REPO, m["number"], "auth strategy unclear")
        created = wg.graduate_fog(
            REPO,
            m["number"],
            "auth strategy unclear",
            [{"title": "OAuth vs session", "question": "which?"}],
        )
        assert len(created) == 1
        assert fake.issues[m["number"]]["sub_issues"] == [created[0]["number"]]
        body = parse_map_body(fake.issues[m["number"]]["body"])
        assert body["fog"] == []

    def test_complete_if_clear_false_with_open_frontier(self, fake):
        m = wg.chart_map(REPO, "d")
        wg.add_ticket(REPO, m["number"], "A", "qa")
        assert wg.complete_if_clear(REPO, m["number"]) is False
        assert fake.issues[m["number"]]["closed"] is False

    def test_complete_if_clear_true_closes_map(self, fake):
        m = wg.chart_map(REPO, "d")
        assert wg.complete_if_clear(REPO, m["number"]) is True
        assert fake.issues[m["number"]]["closed"] is True
