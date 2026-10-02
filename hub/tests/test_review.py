"""H2 end to end: S4 with a fake agent, the checks, review decisions, and Publish."""
from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import replace

import pytest
from conftest import craig_zip, drain, git

from scribe_hub.app import Hub, create_app
from scribe_hub.jobs import queue

NPC = """---
title: Pat Alpha
type: pc
status: alive
---

# Pat Alpha

## Relationships

- Trusts the innkeeper.

## Appearance

Tall.
"""
CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- **Older entry.**\n"
DMLOG = ("# DM Change Log\n\n## Entry Template\n\n```text\n## YYYY-MM-DD - Title\n```\n\n"
         "## 2026-09-01 - Old\n\n- Actor: DM\n")

FAKE_AGENT = textwrap.dedent('''
    import json, os, pathlib, sys
    repo = pathlib.Path(sys.argv[1])
    spec = json.loads(pathlib.Path(os.environ["FAKE_AGENT_SPEC"]).read_text())
    if spec.get("exit"):
        sys.exit(spec["exit"])
    for rel, text in spec.get("touch", {}).items():          # files it must NOT edit
        p = repo / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    if spec.get("commit"):                                   # and commits them itself
        import subprocess
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=a", "-c", "user.email=a@a",
                        "commit", "-qm", "agent"], check=True)
    if spec.get("sleep"):
        import time; time.sleep(spec["sleep"])
    if spec.get("proposals") is not None:
        (repo / "_INBOX/Session-060-proposals.json").write_text(json.dumps(spec["proposals"]))
        (repo / "_INBOX/Session-060-Downstream-Plan.md").write_text("# Plan\\n")
''')


def P(pid, op, target, **kw):
    base = {"proposal_id": pid, "op": op, "target": target, "entity": "Pat Alpha",
            "secret": False, "rationale": "Because the session said so.",
            "evidence": [{"utterance_id": "t2-1200"}], "conflicts_with": []}
    return {**base, **kw}


GOOD = [
    P("p-060-001", "update-section", "Characters/PCs/Pat-Alpha.md", section="Appearance",
      after="Tall, with a new scar."),
    P("p-060-002", "add-to-list", "Characters/PCs/Pat-Alpha.md", section="Relationships",
      after="- Owes the gatekeeper a favor."),
    P("p-060-003", "set-frontmatter", "Characters/PCs/Pat-Alpha.md", key="status",
      after="wounded"),
    P("p-060-004", "create-note", "Chronicle/Sessions/Session-060-The-Gate.md",
      after="---\ntitle: Session 060\ntype: session\n---\n\n# Session 060\n", entity="Session 060"),
]
BAD = [
    P("p-060-005", "update-section", "Characters/PCs/Pat-Alpha.md", section="Quotes",
      after="x"),                                                             # no such section
    P("p-060-006", "update-section", "Characters/PCs/Pat-Alpha.md", section="Appearance",
      after="x", evidence=[{"utterance_id": "t9-0"}]),                        # invented evidence
    P("p-060-007", "update-section", "Characters/PCs/Pat-Alpha.md", section="Appearance",
      after="A secret.", secret=True),                                        # no DM Only callout
    P("p-060-008", "create-note", "../escape.md", after="---\na: 1\n---\n"),  # escapes the vault
    P("p-060-009", "frobnicate", "Characters/PCs/Pat-Alpha.md", after="x"),   # unknown op
]


@pytest.fixture
def review(tmp_path, settings, vault, monkeypatch):
    origin, clone = vault
    for rel, text in {"Characters/PCs/Pat-Alpha.md": NPC, "CHANGELOG.md": CHANGELOG,
                      "Meta/DM-Change-Log.md": DMLOG}.items():
        (clone / rel).parent.mkdir(parents=True, exist_ok=True)
        (clone / rel).write_text(text)
    git(clone, "add", "-A")
    git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed notes")
    git(clone, "push", "-q", "origin", "main")
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_AGENT)
    spec = tmp_path / "spec.json"
    monkeypatch.setenv("FAKE_AGENT_SPEC", str(spec))
    s = replace(settings, s4_command=(sys.executable, str(agent), "{dir}"))
    hub = Hub(s, gpu_transport=None)
    from fastapi.testclient import TestClient
    client = TestClient(create_app(hub, run_worker=False))
    yield hub, client, spec, origin, clone
    hub.db.close()


def run_to_review(hub, client, headers, worker, spec, spec_body):
    from scribe_hub import auth
    spec.write_text(json.dumps(spec_body))
    client.post("/sessions?number=60", headers=headers, files={
        "file": ("c.zip", craig_zip([(1, "gm_user", "1001"), (2, "pat_user", "1002")]), "x")})
    drain(hub)
    _, wtok = auth.create_service_token(hub.db, "w", "worker")
    w = {"Authorization": f"Bearer {wtok}", **{k: v for k, v in worker.items()
                                                if k != "Authorization"}}
    while (r := client.post("/worker/claim", headers=w)).status_code == 200:
        j = r.json()
        client.post(f"/worker/jobs/{j['job']}/result", headers=w, json={"utterances": [
            {"start_ms": 1200 if j["track"] == 2 else 0, "end_ms": 2000, "text": "Words."}]})
    drain(hub)       # S3 then S4


@pytest.fixture
def headers(review):
    from conftest import TS

    from scribe_hub import auth
    hub = review[0]
    code = auth.create_pair_code(hub.db, "Mac", 600)
    _, _, tok = auth.redeem_pair_code(hub.db, code, 600)
    return {"Authorization": f"Bearer {tok}", **TS}


def cards(client, headers):
    return client.get("/sessions/60/proposals", headers=headers).json()


def test_checks_sort_good_from_bad_and_fill_before_from_the_file(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD + BAD}})
    data = cards(client, headers)
    by = {p["proposal_id"]: p for p in data["proposals"]}
    assert {k for k, p in by.items() if p["state"] == "pending"} == \
        {"p-060-001", "p-060-002", "p-060-003", "p-060-004"}
    reasons = {k: p["check_error"] for k, p in by.items() if p["state"] == "rejected_by_checks"}
    assert "no section" in reasons["p-060-005"]
    assert "not an utterance" in reasons["p-060-006"]
    assert "DM Only" in reasons["p-060-007"]
    assert "not allowed" in reasons["p-060-008"]
    assert "unknown op" in reasons["p-060-009"]
    assert by["p-060-001"]["before"] == "Tall."                       # from the file, not the model
    assert by["p-060-003"]["before"] == "alive"
    ev = by["p-060-001"]["evidence"][0]
    assert ev["text"] == "Words." and ev["speaker"] == "Pat/Alpha" and ev["track"] == 2
    s = client.get("/sessions/60", headers=headers).json()
    assert s["state"] == "review" and "4 proposal(s)" in s["detail"]
    # the agent's two outputs were committed on the session branch, nothing else
    files = git(clone, "show", "--name-only", "--format=", "scribe/session-060").split()
    assert sorted(files) == ["_INBOX/Session-060-Downstream-Plan.md",
                             "_INBOX/Session-060-proposals.json"]


def test_files_the_agent_must_not_touch_are_reverted_and_reported(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {
        "touch": {"README.md": "vandalised\n", "Characters/NPCs/New.md": "x\n"},
        "proposals": {"schema": 1, "session": 60, "proposals": GOOD[:1]}})
    data = cards(client, headers)
    assert data["batch"]["violations"] == ["Characters/NPCs/New.md", "README.md"]
    assert (clone / "README.md").read_text() == "test vault\n"
    assert not (clone / "Characters/NPCs/New.md").exists()
    detail = client.get("/sessions/60", headers=headers).json()["detail"]
    assert "2 agent edit(s) reverted" in detail


def test_a_failed_agent_run_is_not_retried(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {"exit": 3})
    s4 = [j for j in queue.for_session(hub.db, 60) if j["stage"] == "s4"]
    assert len(s4) == 1 and s4[0]["state"] == "failed" and s4[0]["attempts"] == 1
    s = client.get("/sessions/60", headers=headers).json()
    assert s["state"] == "failed" and "exited 3" in s["detail"]


def test_no_proposals_file_and_bad_json_fail_plainly(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {"proposals": None})
    assert "no proposals file" in client.get("/sessions/60", headers=headers).json()["detail"]
    spec.write_text(json.dumps({"proposals": {"schema": 2, "session": 60, "proposals": []}}))
    assert client.post("/sessions/60/propose", headers=headers).status_code == 202
    drain(hub)
    assert "schema 1" in client.get("/sessions/60", headers=headers).json()["detail"]


def test_without_a_signed_in_agent_the_session_says_what_to_run(review, headers):
    hub, client, spec, origin, clone = review
    hub.settings = replace(hub.settings, s4_command=(), s4_provider="claude",
                           claude_token_file="/nonexistent")
    run_to_review(hub, client, headers, headers, spec, {"proposals": None})
    s = client.get("/sessions/60", headers=headers).json()
    assert s["state"] == "blocked" and "claude setup-token" in s["detail"]


def decide(client, headers, pid, action, after=None):
    body = {"action": action} | ({"after": after} if after is not None else {})
    return client.patch(f"/proposals/{pid}", headers=headers, json=body)


def test_decisions_and_edits(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD + BAD}})
    by = {p["proposal_id"]: p for p in cards(client, headers)["proposals"]}
    assert decide(client, headers, by["p-060-001"]["id"], "accept").json()["state"] == "accepted"
    assert decide(client, headers, by["p-060-002"]["id"], "reject").json()["state"] == "rejected"
    assert decide(client, headers, by["p-060-003"]["id"], "defer").json()["state"] == "deferred"
    r = decide(client, headers, by["p-060-002"]["id"], "accept", after="- two\n- items")
    assert r.status_code == 422 and "one list item" in r.json()["detail"]
    r = decide(client, headers, by["p-060-002"]["id"], "accept", after="- Owes the gatekeeper.")
    assert r.status_code == 200 and r.json()["edited_after"] == "- Owes the gatekeeper."
    assert decide(client, headers, by["p-060-005"]["id"], "accept").status_code == 409


def accept_all_good(client, headers):
    for p in cards(client, headers)["proposals"]:
        if p["state"] == "pending":
            decide(client, headers, p["id"], "accept")


def test_publish_needs_push_access_and_dry_run_changes_nothing(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD}})
    main_before = git(origin, "rev-parse", "main").strip()
    assert client.post("/sessions/60/publish", headers=headers, json={}).status_code == 409
    accept_all_good(client, headers)
    dry = client.post("/sessions/60/publish", headers=headers, json={"dry_run": True}).json()
    assert dry["dry_run"] and "Characters/PCs/Pat-Alpha.md" in dry["files"]
    assert "CHANGELOG.md" in dry["files"]
    assert "Chronicle/Sessions/Session-060-The-Gate.md" in dry["files"]
    r = client.post("/sessions/60/publish", headers=headers, json={})
    assert r.status_code == 403                                    # SCRIBE_PUSH_BRANCHES is off
    assert git(origin, "rev-parse", "main").strip() == main_before
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert "scribe/publish-060" not in git(clone, "branch")
    assert all(p["state"] == "accepted" for p in cards(client, headers)["proposals"])


def test_publish_applies_accepted_cards_to_main(review, headers):
    hub, client, spec, origin, clone = review
    hub.settings = replace(hub.settings, push_branches=True)
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD}})
    by = {p["proposal_id"]: p for p in cards(client, headers)["proposals"]}
    decide(client, headers, by["p-060-001"]["id"], "accept")
    decide(client, headers, by["p-060-002"]["id"], "accept", after="- Owes the gatekeeper.")
    decide(client, headers, by["p-060-003"]["id"], "reject")
    decide(client, headers, by["p-060-004"]["id"], "defer")
    r = client.post("/sessions/60/publish", headers=headers, json={}).json()
    assert r["pushed"] and r["applied"] == 2
    npc = git(origin, "show", "main:Characters/PCs/Pat-Alpha.md")
    assert "Tall, with a new scar." in npc and "- Owes the gatekeeper." in npc
    assert "status: alive" in npc                                   # rejected: untouched
    tree = git(origin, "ls-tree", "-r", "--name-only", "main")
    assert "Session-060-The-Gate" not in tree                       # deferred: not created
    assert "_INBOX/Session-060-Full-Transcript.md" in tree
    cl = git(origin, "show", "main:CHANGELOG.md")
    assert "**Session 060 ingested" in cl and cl.index("Session 060") < cl.index("Older entry")
    dm = git(origin, "show", "main:Meta/DM-Change-Log.md")
    assert "Session 060 ingest published" in dm and dm.index("Session 060") < dm.index("2026-09-01")
    assert "Scribe Hub" in git(origin, "log", "-1", "--format=%an", "main")
    states = {p["proposal_id"]: p["state"] for p in cards(client, headers)["proposals"]}
    assert states == {"p-060-001": "applied", "p-060-002": "applied", "p-060-003": "rejected",
                      "p-060-004": "deferred"}
    assert client.get("/sessions/60", headers=headers).json()["state"] == "published"
    assert client.post("/sessions/60/publish", headers=headers, json={}).status_code == 409


def test_publish_stops_if_the_dm_edited_the_same_text_since(review, headers):
    hub, client, spec, origin, clone = review
    hub.settings = replace(hub.settings, push_branches=True)
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD[:1]}})
    accept_all_good(client, headers)
    # Nate edits the same section in Obsidian on another machine, and pushes
    other = clone.parent / "obsidian"
    import subprocess
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    p = other / "Characters/PCs/Pat-Alpha.md"
    p.write_text(p.read_text().replace("Tall.", "Tall and grey-eyed."))
    git(other, "-c", "user.name=n", "-c", "user.email=n@n", "commit", "-qam", "DM edit")
    git(other, "push", "-q", "origin", "main")
    nates = git(origin, "rev-parse", "main").strip()
    r = client.post("/sessions/60/publish", headers=headers, json={})
    assert r.status_code == 409
    assert "changed since" in r.json()["detail"]["conflicts"][0]["reason"]
    assert git(origin, "rev-parse", "main").strip() == nates          # nothing pushed
    card = cards(client, headers)["proposals"][0]
    assert card["state"] == "pending" and "changed since" in card["check_error"]


def test_propose_rerun_supersedes_the_old_cards(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD[:2]}})
    spec.write_text(json.dumps({"proposals": {"schema": 1, "session": 60,
                                              "proposals": GOOD[2:3]}}))
    assert client.post("/sessions/60/propose", headers=headers).status_code == 202
    assert client.post("/sessions/60/propose", headers=headers).status_code == 409   # one at a time
    drain(hub)
    assert [p["proposal_id"] for p in cards(client, headers)["proposals"]] == ["p-060-003"]


def seed(origin, clone, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (clone / rel).parent.mkdir(parents=True, exist_ok=True)
        (clone / rel).write_text(text)
    git(clone, "add", "-A")
    git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    git(clone, "push", "-q", "origin", "main")


def test_agent_commits_and_names_with_spaces_or_accents_are_reverted(review, headers):
    hub, client, spec, origin, clone = review
    seed(origin, clone, {"Characters/NPCs/Zoë Two.md": "original\n"})
    run_to_review(hub, client, headers, headers, spec, {
        "touch": {"Characters/NPCs/Zoë Two.md": "vandalised\n", "Places/New Town.md": "x\n"},
        "commit": True,
        "proposals": {"schema": 1, "session": 60, "proposals": GOOD[:1]}})
    data = cards(client, headers)
    assert data["batch"]["violations"] == ["Characters/NPCs/Zoë Two.md", "Places/New Town.md"]
    assert (clone / "Characters/NPCs/Zoë Two.md").read_text() == "original\n"
    assert not (clone / "Places/New Town.md").exists()
    branch = git(clone, "show", "scribe/session-060:Characters/NPCs/Zoë Two.md")
    assert branch == "original\n"                                 # the agent's commit is gone
    assert "_INBOX/Session-060-proposals.json" in git(
        clone, "ls-tree", "-r", "--name-only", "scribe/session-060")
    assert git(clone, "status", "--porcelain").strip() == ""


def test_an_agent_that_runs_too_long_is_stopped_and_its_edits_dropped(review, headers):
    hub, client, spec, origin, clone = review
    hub.settings = replace(hub.settings, s4_timeout_s=1)
    run_to_review(hub, client, headers, headers, spec, {
        "touch": {"README.md": "vandalised\n"}, "sleep": 5,
        "proposals": {"schema": 1, "session": 60, "proposals": GOOD[:1]}})
    s = client.get("/sessions/60", headers=headers).json()
    assert s["state"] == "failed" and "was stopped" in s["detail"]
    assert (clone / "README.md").read_text() == "test vault\n"
    assert git(clone, "status", "--porcelain").strip() == ""


def test_a_hub_restart_does_not_rerun_the_agent(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD[:1]}})
    client.post("/sessions/60/propose", headers=headers)
    job = queue.claim(hub.db, "hub")                  # the hub dies while the agent runs
    assert job["stage"] == "s4"
    queue.recover(hub.db)
    assert queue.get(hub.db, job["id"])["state"] == "failed"
    s = client.get("/sessions/60", headers=headers).json()
    assert s["state"] == "failed" and "run it again from the app" in s["detail"]


def test_conflicts_with_is_read_as_note_and_section_refs(review, headers):
    hub, client, spec, origin, clone = review
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": [
            {**GOOD[0], "conflicts_with": ["Characters/PCs/Pat-Alpha.md#Relationships",
                                           {"target": "Places/Gate.md"}]},
            {**GOOD[1], "conflicts_with": [7]}]}})
    by = {p["proposal_id"]: p for p in cards(client, headers)["proposals"]}
    assert by["p-060-001"]["conflicts"] == [
        {"target": "Characters/PCs/Pat-Alpha.md", "section": "Relationships"},
        {"target": "Places/Gate.md", "section": None}]
    assert by["p-060-002"]["state"] == "rejected_by_checks"
    assert "not a note or section reference" in by["p-060-002"]["check_error"]


def test_publish_stops_if_a_new_note_was_made_there_since(review, headers):
    hub, client, spec, origin, clone = review
    hub.settings = replace(hub.settings, push_branches=True)
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD[3:4]}})
    accept_all_good(client, headers)
    import subprocess
    other = clone.parent / "obsidian"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    seed(origin, other, {"Chronicle/Sessions/Session-060-The-Gate.md": "Nate's own notes\n"})
    nates = git(origin, "rev-parse", "main").strip()
    r = client.post("/sessions/60/publish", headers=headers, json={})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]["conflicts"][0]["reason"]
    assert git(origin, "rev-parse", "main").strip() == nates
    assert git(origin, "show", "main:Chronicle/Sessions/Session-060-The-Gate.md") == \
        "Nate's own notes\n"


def test_a_refused_push_reads_plainly_and_keeps_the_cards(review, headers):
    hub, client, spec, origin, clone = review
    hub.settings = replace(hub.settings, push_branches=True)
    run_to_review(hub, client, headers, headers, spec, {"proposals": {
        "schema": 1, "session": 60, "proposals": GOOD[:1]}})
    accept_all_good(client, headers)
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'main moved' >&2\nexit 1\n")
    hook.chmod(0o755)
    r = client.post("/sessions/60/publish", headers=headers, json={})
    assert r.status_code == 409 and r.json()["detail"].startswith("Publish stopped:")
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert [p["state"] for p in cards(client, headers)["proposals"]] == ["accepted"]
    hook.unlink()
    assert client.post("/sessions/60/publish", headers=headers, json={}).json()["pushed"]
