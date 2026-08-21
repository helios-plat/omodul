"""omodul.wayfinding_github — Wayfinding map backed by real GitHub Issues.

Same concepts as ``omodul.wayfinding`` (WayfindingKernel: Map/Ticket/
DecisionGist, frontier/claim/resolve/fog/out-of-scope), different substrate:
the map is a GitHub issue labeled ``wayfinder:map``, tickets are its native
**sub-issues** (``parentIssueId``/``subIssues`` — not a task-list convention),
blocking is GitHub's native issue-dependency graph (``blockedBy``/
``blocking``). This is the storage the original wayfinder skill spec calls
for: "native... essential because it renders the frontier *visually* in the
tracker's own UI" — a human can open the map issue on github.com and see the
sub-issues progress bar and blocked-by badges without calling any tool.

The map issue's **body** is the only structured state this module keeps
outside GitHub's own object graph (Destination/Notes/Decisions so
far/Not yet specified/Out of scope — see ``render_map_body``); everything
else (open/closed, assignee-as-claim, blocking) lives on the issues
themselves and is queried live, never cached locally.

Concurrency note: resolve/add_fog/graduate_fog/rule_out_of_scope do a
read-modify-write of the map body (fetch → parse → mutate → render → push).
Two sessions editing the same map concurrently can race on that — the
wayfinder skill itself expects concurrent sessions and doesn't mandate
locking here; claim (native assignee) is the actual work-distribution
mechanism, the body is an index.

All GitHub access goes through two seams — ``_gh_run`` (CLI subcommands)
and ``_graphql`` (raw GraphQL via ``gh api graphql --input -``) — both
monkeypatchable for tests without touching the network.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

_TICKET_TYPES = ("research", "prototype", "grilling", "task")

_LABEL_COLORS = {
    "wayfinder:map": "5319e7",
    "wayfinder:research": "0e8a16",
    "wayfinder:prototype": "1d76db",
    "wayfinder:grilling": "fbca04",
    "wayfinder:task": "d93f0b",
}


class WayfindingGithubError(Exception):
    retryable = False


# ---------------------------------------------------------------------------
# gh CLI / GraphQL seams
# ---------------------------------------------------------------------------


def _gh_run(args: list[str], *, input_text: str | None = None) -> str:
    kwargs: dict[str, Any] = {}
    if input_text is not None:
        kwargs["input"] = input_text
    result = subprocess.run(["gh", *args], capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise WayfindingGithubError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = {"query": query, "variables": variables}
    out = _gh_run(["api", "graphql", "--input", "-"], input_text=json.dumps(payload))
    data = json.loads(out)
    if "errors" in data:
        raise WayfindingGithubError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def _split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise WayfindingGithubError(f"repo must be 'owner/name': {repo!r}")
    return owner, name


# ---------------------------------------------------------------------------
# Map body: pure render / parse (the only structured state outside GitHub's
# own object graph — open tickets are never listed here, they're a live
# subIssues query, per the wayfinder spec's "map is an index, not a store").
# ---------------------------------------------------------------------------


def render_map_body(
    *,
    destination: str,
    notes: str = "",
    decisions: list[dict[str, str]] | None = None,
    fog: list[str] | None = None,
    out_of_scope: list[dict[str, str]] | None = None,
) -> str:
    lines = ["## Destination", "", destination.strip(), "", "## Notes", "", notes.strip(), ""]
    lines += ["## Decisions so far", ""]
    lines += [f"- [{d['title']}]({d['link']}): {d['gist']}" for d in decisions or []]
    lines += ["", "## Not yet specified", ""]
    lines += [f"- {patch}" for patch in fog or []]
    lines += ["", "## Out of scope", ""]
    lines += [f"- {o['title']} — {o['reason']}" for o in out_of_scope or []]
    return "\n".join(lines) + "\n"


_DECISION_RE = re.compile(r"^-\s*\[(?P<title>.+?)\]\((?P<link>.+?)\):\s*(?P<gist>.+)$")


def parse_map_body(body: str) -> dict[str, Any]:
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in (body or "").splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()

    decisions = []
    for line in sections.get("Decisions so far", "").splitlines():
        m = _DECISION_RE.match(line.strip())
        if m:
            decisions.append(m.groupdict())

    fog = [
        line.strip()[2:].strip()
        for line in sections.get("Not yet specified", "").splitlines()
        if line.strip().startswith("- ")
    ]

    out_of_scope = []
    for line in sections.get("Out of scope", "").splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        title, _, reason = line[2:].partition(" — ")
        out_of_scope.append({"title": title.strip(), "reason": reason.strip()})

    return {
        "destination": sections.get("Destination", ""),
        "notes": sections.get("Notes", ""),
        "decisions": decisions,
        "fog": fog,
        "out_of_scope": out_of_scope,
    }


def _rerender(parsed: dict[str, Any]) -> str:
    return render_map_body(
        destination=parsed["destination"],
        notes=parsed["notes"],
        decisions=parsed["decisions"],
        fog=parsed["fog"],
        out_of_scope=parsed["out_of_scope"],
    )


# ---------------------------------------------------------------------------
# GraphQL-backed primitives
# ---------------------------------------------------------------------------


def _repository_id(owner: str, name: str) -> str:
    data = _graphql(
        "query($owner:String!,$name:String!){ repository(owner:$owner,name:$name){ id } }",
        {"owner": owner, "name": name},
    )
    return data["repository"]["id"]


def _label_id(owner: str, name: str, label_name: str) -> str:
    data = _graphql(
        "query($owner:String!,$name:String!,$label:String!)"
        "{ repository(owner:$owner,name:$name){ label(name:$label){ id } } }",
        {"owner": owner, "name": name, "label": label_name},
    )
    label = data["repository"]["label"]
    if label is None:
        raise WayfindingGithubError(f"label not found: {label_name!r} — call ensure_labels() first")
    return label["id"]


def _issue_id(owner: str, name: str, number: int) -> str:
    data = _graphql(
        "query($owner:String!,$name:String!,$number:Int!)"
        "{ repository(owner:$owner,name:$name){ issue(number:$number){ id } } }",
        {"owner": owner, "name": name, "number": number},
    )
    issue = data["repository"]["issue"]
    if issue is None:
        raise WayfindingGithubError(f"issue #{number} not found in {owner}/{name}")
    return issue["id"]


def _query_issue_state(owner: str, name: str, number: int) -> dict[str, Any]:
    data = _graphql(
        "query($owner:String!,$name:String!,$number:Int!){ repository(owner:$owner,name:$name){ "
        "issue(number:$number){ id title closed assignees(first:5){ nodes { login } } } } }",
        {"owner": owner, "name": name, "number": number},
    )
    issue = data["repository"]["issue"]
    if issue is None:
        raise WayfindingGithubError(f"issue #{number} not found in {owner}/{name}")
    return {
        "id": issue["id"],
        "title": issue["title"],
        "closed": issue["closed"],
        "assignees": [a["login"] for a in issue["assignees"]["nodes"]],
    }


def _read_map_body(owner: str, name: str, number: int) -> str:
    data = _graphql(
        "query($owner:String!,$name:String!,$number:Int!)"
        "{ repository(owner:$owner,name:$name){ issue(number:$number){ body } } }",
        {"owner": owner, "name": name, "number": number},
    )
    issue = data["repository"]["issue"]
    if issue is None:
        raise WayfindingGithubError(f"map issue #{number} not found in {owner}/{name}")
    return issue["body"] or ""


def _update_map_body(repo: str, number: int, body: str) -> None:
    _gh_run(["issue", "edit", str(number), "--repo", repo, "--body-file", "-"], input_text=body)


def _create_issue(
    owner: str,
    name: str,
    *,
    title: str,
    body: str,
    label_names: list[str],
    parent_issue_id: str | None = None,
) -> dict[str, Any]:
    repo_id = _repository_id(owner, name)
    label_ids = [_label_id(owner, name, ln) for ln in label_names]
    query = (
        "mutation($repositoryId:ID!,$title:String!,$body:String!,"
        "$labelIds:[ID!],$parentIssueId:ID){ "
        "createIssue(input:{repositoryId:$repositoryId,title:$title,body:$body,"
        "labelIds:$labelIds,parentIssueId:$parentIssueId}){ issue { id number url title } } }"
    )
    variables = {
        "repositoryId": repo_id,
        "title": title,
        "body": body,
        "labelIds": label_ids,
        "parentIssueId": parent_issue_id,
    }
    data = _graphql(query, variables)
    return data["createIssue"]["issue"]


def _query_sub_issues(owner: str, name: str, map_number: int) -> list[dict[str, Any]]:
    query = (
        "query($owner:String!,$name:String!,$number:Int!){ repository(owner:$owner,name:$name){ "
        "issue(number:$number){ subIssues(first:100){ nodes { "
        "number title state url "
        "assignees(first:5){ nodes { login } } "
        "labels(first:10){ nodes { name } } "
        "blockedBy(first:10){ nodes { number state } } "
        "} } } } }"
    )
    data = _graphql(query, {"owner": owner, "name": name, "number": map_number})
    issue = data["repository"]["issue"]
    if issue is None:
        raise WayfindingGithubError(f"map issue #{map_number} not found in {owner}/{name}")
    return issue["subIssues"]["nodes"]


def _viewer_login() -> str:
    data = _graphql("query{ viewer{ login } }", {})
    return data["viewer"]["login"]


# ---------------------------------------------------------------------------
# Public operations (mirrors omodul.wayfinding.WayfindingKernel's surface)
# ---------------------------------------------------------------------------


def ensure_labels(repo: str) -> None:
    """Idempotently create/update the wayfinder:* labels this module needs."""
    for label, color in _LABEL_COLORS.items():
        _gh_run(["label", "create", label, "--repo", repo, "--color", color, "--force"])


def chart_map(
    repo: str, destination: str, *, notes: str = "", title: str | None = None
) -> dict[str, Any]:
    """Create the map issue (label ``wayfinder:map``). Does not create any tickets."""
    owner, name = _split_repo(repo)
    ensure_labels(repo)
    body = render_map_body(destination=destination, notes=notes)
    return _create_issue(
        owner,
        name,
        title=title or f"Wayfind: {destination[:60]}",
        body=body,
        label_names=["wayfinder:map"],
    )


def add_ticket(
    repo: str, map_number: int, title: str, question: str, ticket_type: str = "task"
) -> dict[str, Any]:
    """Create a ticket as a native sub-issue of the map issue."""
    if ticket_type not in _TICKET_TYPES:
        raise WayfindingGithubError(f"unknown ticket type: {ticket_type!r}")
    owner, name = _split_repo(repo)
    parent_id = _issue_id(owner, name, map_number)
    body = f"## Question\n\n{question}\n"
    return _create_issue(
        owner,
        name,
        title=title,
        body=body,
        label_names=[f"wayfinder:{ticket_type}"],
        parent_issue_id=parent_id,
    )


def wire_blocking(repo: str, from_number: int, to_number: int) -> None:
    """Native issue dependency: ``to_number`` is blocked by ``from_number``."""
    owner, name = _split_repo(repo)
    from_id = _issue_id(owner, name, from_number)
    to_id = _issue_id(owner, name, to_number)
    _graphql(
        "mutation($issueId:ID!,$blockingIssueId:ID!){ "
        "addBlockedBy(input:{issueId:$issueId,blockingIssueId:$blockingIssueId}){ "
        "clientMutationId } }",
        {"issueId": to_id, "blockingIssueId": from_id},
    )


def frontier(repo: str, map_number: int) -> list[dict[str, Any]]:
    """Open + unassigned + unblocked sub-issues, in the order GitHub returns them."""
    owner, name = _split_repo(repo)
    nodes = _query_sub_issues(owner, name, map_number)
    result = []
    for n in nodes:
        if n["state"] != "OPEN" or n["assignees"]["nodes"]:
            continue
        if any(b["state"] != "CLOSED" for b in n["blockedBy"]["nodes"]):
            continue
        ticket_type = next(
            (
                lbl["name"].split(":", 1)[1]
                for lbl in n["labels"]["nodes"]
                if lbl["name"].startswith("wayfinder:")
            ),
            None,
        )
        result.append(
            {"number": n["number"], "title": n["title"], "url": n["url"], "type": ticket_type}
        )
    return result


def claim_ticket(repo: str, ticket_number: int, login: str | None = None) -> dict[str, Any]:
    """Assign the ticket (the assignee *is* the claim — no separate lease state)."""
    owner, name = _split_repo(repo)
    state = _query_issue_state(owner, name, ticket_number)
    if state["closed"]:
        return {"ok": False, "reason": "ticket is closed"}
    if state["assignees"]:
        return {"ok": False, "reason": "not claimable", "claimed_by": state["assignees"][0]}
    who = login or "@me"
    _gh_run(["issue", "edit", str(ticket_number), "--repo", repo, "--add-assignee", who])
    return {"ok": True, "ticket_number": ticket_number, "claimed_by": login or _viewer_login()}


def resolve_ticket(
    repo: str,
    map_number: int,
    ticket_number: int,
    *,
    resolution: str,
    gist: str,
    link: str | None = None,
) -> dict[str, Any]:
    """Comment the resolution, close the ticket, append a DecisionGist to the map body."""
    owner, name = _split_repo(repo)
    state = _query_issue_state(owner, name, ticket_number)
    if state["closed"]:
        return {"ok": False, "reason": "already closed"}
    if not state["assignees"]:
        return {"ok": False, "reason": "not claimed"}

    _gh_run(["issue", "comment", str(ticket_number), "--repo", repo, "-b", resolution])
    _gh_run(["issue", "close", str(ticket_number), "--repo", repo, "-r", "completed"])

    parsed = parse_map_body(_read_map_body(owner, name, map_number))
    ticket_url = f"https://github.com/{repo}/issues/{ticket_number}"
    parsed["decisions"].append({"title": state["title"], "link": link or ticket_url, "gist": gist})
    _update_map_body(repo, map_number, _rerender(parsed))
    return {"ok": True, "ticket_number": ticket_number}


def rule_out_of_scope(
    repo: str, map_number: int, ticket_number: int, reason: str
) -> dict[str, Any]:
    """Close the ticket without a decision; record it in the map's Out of scope section."""
    owner, name = _split_repo(repo)
    state = _query_issue_state(owner, name, ticket_number)
    if state["closed"]:
        return {"ok": False, "reason": "already closed"}
    _gh_run(
        [
            "issue",
            "close",
            str(ticket_number),
            "--repo",
            repo,
            "-r",
            "not planned",
            "-c",
            f"Out of scope: {reason}",
        ]
    )
    parsed = parse_map_body(_read_map_body(owner, name, map_number))
    parsed["out_of_scope"].append({"title": state["title"], "reason": reason})
    _update_map_body(repo, map_number, _rerender(parsed))
    return {"ok": True, "ticket_number": ticket_number}


def add_fog(repo: str, map_number: int, patch: str) -> None:
    owner, name = _split_repo(repo)
    parsed = parse_map_body(_read_map_body(owner, name, map_number))
    if patch not in parsed["fog"]:
        parsed["fog"].append(patch)
    _update_map_body(repo, map_number, _rerender(parsed))


def graduate_fog(
    repo: str, map_number: int, patch: str, new_tickets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """new_tickets: list of {"title":, "question":, "type":}."""
    owner, name = _split_repo(repo)
    parsed = parse_map_body(_read_map_body(owner, name, map_number))
    if patch not in parsed["fog"]:
        raise WayfindingGithubError(f"fog patch not found: {patch!r}")
    created = [
        add_ticket(repo, map_number, spec["title"], spec["question"], spec.get("type", "task"))
        for spec in new_tickets
    ]
    parsed["fog"] = [f for f in parsed["fog"] if f != patch]
    _update_map_body(repo, map_number, _rerender(parsed))
    return created


def decisions_so_far(repo: str, map_number: int) -> list[dict[str, Any]]:
    owner, name = _split_repo(repo)
    return parse_map_body(_read_map_body(owner, name, map_number))["decisions"]


def complete_if_clear(repo: str, map_number: int) -> bool:
    """Close the map issue once frontier and fog are both empty."""
    owner, name = _split_repo(repo)
    fog = parse_map_body(_read_map_body(owner, name, map_number))["fog"]
    if fog or frontier(repo, map_number):
        return False
    _gh_run(["issue", "close", str(map_number), "--repo", repo, "-r", "completed"])
    return True
