"""End-to-end API tests: zip ingestion through the background worker, filters, authors."""

from __future__ import annotations

import time
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from rat.api import create_app
from tests.fixtures import make_zip

ALICE = ("Alice", "alice@example.com")
BOB = ("Bob", "bob@example.com")
BASE = 1_700_000_000


@pytest.fixture()
def client(rat):
    with TestClient(create_app()) as c:
        yield c


def _wait_job(client, job_id, timeout=60.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        last = r.json()
        if last["status"] in ("done", "error"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"ingestion job did not finish: {last}")


def _upload_zip(client, zip_path, name=None):
    with open(zip_path, "rb") as fh:
        r = client.post(
            "/api/repos/zip",
            files={"file": (zip_path.name, fh, "application/zip")},
            data={"name": name} if name else None,
        )
    assert r.status_code == 202, r.text
    return r.json()


def _build_repo(rat, name):
    """3 commits: Alice adds+extends src/a.txt, Bob adds src/b.txt."""
    b = rat.builder(name, base_ct=BASE)
    b.write("src/a.txt", "1\n2\n3\n")
    c1 = b.commit("add a.txt", author=ALICE)
    b.write("src/b.txt", "1\n2\n")
    c2 = b.commit("add b.txt", author=BOB)
    b.write("src/a.txt", "1\n2\n3\n4\n")
    c3 = b.commit("extend a.txt", author=ALICE)
    return b, [c1, c2, c3]


def test_zip_upload_full_flow(rat, client):
    b, shas = _build_repo(rat, "api_zip")
    payload = _upload_zip(client, make_zip(b.path, rat.tmp / "repo.zip"), name="api-zip")
    repo_id = payload["repo"]["id"]

    job = _wait_job(client, payload["job_id"])
    assert job["status"] == "done", job

    repo = client.get(f"/api/repos/{repo_id}").json()
    assert repo["status"] == "ready" and repo["error"] is None
    assert repo["n_commits"] == 3 and repo["head_sha"] == shas[-1]
    assert repo["total_added"] == 6 and repo["total_removed"] == 0
    assert repo["job"]["status"] == "done"

    metrics = client.get(f"/api/repos/{repo_id}/metrics").json()
    assert metrics["added"] == 6 and metrics["churn"] == 6
    assert metrics["modifications"] == 3
    assert metrics["scope"]["name"] == "api-zip"

    # commit-set filters through query parameters
    one = client.get(f"/api/repos/{repo_id}/metrics", params={"commits": shas[0]}).json()
    assert one["n_commits"] == 1 and one["added"] == 3

    since_iso = datetime.fromtimestamp(BASE + 2000, tz=timezone.utc).isoformat()
    window = client.get(f"/api/repos/{repo_id}/metrics", params={"since": since_iso}).json()
    assert window["n_commits"] == 2 and window["added"] == 3

    ref = client.get(f"/api/repos/{repo_id}/metrics", params={"ref": shas[1]}).json()
    assert ref["n_commits"] == 2 and ref["added"] == 5

    # files / tree / series / commits
    files = client.get(f"/api/repos/{repo_id}/files").json()
    assert files["rows"][0]["path"] == "src/a.txt"
    assert files["rows"][0]["churn"] == 4

    tree = client.get(f"/api/repos/{repo_id}/tree").json()
    assert [c["name"] for c in tree["children"]] == ["src"]

    series = client.get(f"/api/repos/{repo_id}/series", params={"bucket": "commit"}).json()
    assert len(series["points"]) == 3

    commits = client.get(f"/api/repos/{repo_id}/commits", params={"path": "src/a.txt"}).json()
    assert commits["total"] == 3
    assert commits["rows"][0]["sha"] == shas[-1]

    detail = client.get(f"/api/repos/{repo_id}/commits/{shas[0]}").json()
    assert detail["parent_sha"] is None and detail["added"] == 3

    paths = client.get(f"/api/repos/{repo_id}/paths", params={"q": "a.txt"}).json()
    assert any(p["path"] == "src/a.txt" for p in paths)


def test_authors_and_manual_merge_over_http(rat, client):
    b, _ = _build_repo(rat, "api_authors")
    payload = _upload_zip(client, make_zip(b.path, rat.tmp / "authors.zip"))
    repo_id = payload["repo"]["id"]
    assert _wait_job(client, payload["job_id"])["status"] == "done"

    authors = client.get(f"/api/repos/{repo_id}/authors").json()
    assert {a["name"] for a in authors} == {"Alice", "Bob"}
    alice = next(a for a in authors if a["name"] == "Alice")
    bob = next(a for a in authors if a["name"] == "Bob")

    assert client.get(f"/api/repos/{repo_id}/authors/suggestions").json() == []

    r = client.post(
        f"/api/repos/{repo_id}/authors/merge",
        json={"target_id": alice["id"], "source_ids": [bob["id"]]},
    )
    assert r.status_code == 200 and r.json()["merged"] == 1

    merged = client.get(f"/api/repos/{repo_id}/authors").json()
    assert len(merged) == 1 and merged[0]["n_commits"] == 3
    assert client.get(f"/api/repos/{repo_id}").json()["n_authors"] == 1

    contribs = client.get(f"/api/repos/{repo_id}/contributors").json()
    assert len(contribs) == 1
    assert contribs[0]["n_commits"] == 3
    assert contribs[0]["ownership"] == pytest.approx(1.0)

    # rename the merged author
    r = client.patch(f"/api/repos/{repo_id}/authors/{alice['id']}", json={"name": "A. Grimaldi"})
    assert r.status_code == 200
    assert client.get(f"/api/repos/{repo_id}/authors").json()[0]["name"] == "A. Grimaldi"

    # detach restores both identities
    assert client.post(f"/api/repos/{repo_id}/authors/{bob['id']}/detach").status_code == 200
    assert len(client.get(f"/api/repos/{repo_id}/authors").json()) == 2


def test_multi_repo_isolation_and_delete(rat, client):
    b1, _ = _build_repo(rat, "multi1")
    b2 = rat.builder("multi2", base_ct=BASE)
    b2.write("other.txt", "x\n")
    b2.commit("only commit", author=ALICE)

    p1 = _upload_zip(client, make_zip(b1.path, rat.tmp / "one.zip"), name="one")
    p2 = _upload_zip(client, make_zip(b2.path, rat.tmp / "two.zip"), name="two")
    assert _wait_job(client, p1["job_id"])["status"] == "done"
    assert _wait_job(client, p2["job_id"])["status"] == "done"
    id1, id2 = p1["repo"]["id"], p2["repo"]["id"]

    assert client.get(f"/api/repos/{id1}").json()["n_commits"] == 3
    assert client.get(f"/api/repos/{id2}").json()["n_commits"] == 1
    assert {r["name"] for r in client.get("/api/repos").json()} >= {"one", "two"}

    m1 = client.get(f"/api/repos/{id1}/metrics").json()
    assert m1["n_files"] == 2 and m1["scope"]["name"] == "one"

    assert client.delete(f"/api/repos/{id2}").status_code == 204
    assert client.get(f"/api/repos/{id2}").status_code == 404
    assert client.get(f"/api/repos/{id1}/metrics").json()["added"] == 6


def test_validation_and_error_responses(rat, client):
    assert client.post("/api/repos/url", json={"url": "ftp://example.com/x.git"}).status_code == 400
    assert client.post("/api/repos/url", json={"url": "not a url"}).status_code == 400
    assert client.post("/api/repos/url", json={"url": ""}).status_code == 400

    assert client.get("/api/repos/999999").status_code == 404
    assert client.get("/api/jobs/999999").status_code == 404

    r = client.post("/api/repos/zip", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/repos/zip", files={"file": ("empty.zip", b"", "application/zip")})
    assert r.status_code == 400

    b, _ = _build_repo(rat, "api_errors")
    payload = _upload_zip(client, make_zip(b.path, rat.tmp / "err.zip"))
    repo_id = payload["repo"]["id"]
    assert _wait_job(client, payload["job_id"])["status"] == "done"

    assert client.get(f"/api/repos/{repo_id}/metrics", params={"path": "no/such.txt"}).status_code == 404
    assert client.get(f"/api/repos/{repo_id}/metrics", params={"since": "not-a-date"}).status_code == 422
    assert client.get(f"/api/repos/{repo_id}/series", params={"bucket": "hour"}).status_code == 422
    assert client.get(f"/api/repos/{repo_id}/commits/deadbeef").status_code == 404
    assert client.get(f"/api/repos/{repo_id}/metrics", params={"commits": "deadbeef"}).json()["n_commits"] == 0


def test_unreachable_url_job_fails(rat, client):
    r = client.post("/api/repos/url", json={"url": "https://127.0.0.1:9/nope.git", "name": "dead"})
    assert r.status_code == 202
    repo_id = r.json()["repo"]["id"]

    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "error" and job["error"]
    repo = client.get(f"/api/repos/{repo_id}").json()
    assert repo["status"] == "error"
    assert client.get(f"/api/repos/{repo_id}/metrics").status_code == 409


def test_zip_without_git_is_rejected(rat, client):
    d = rat.tmp / "plain"
    d.mkdir()
    (d / "README.md").write_text("no git here\n")

    payload = _upload_zip(client, make_zip(d, rat.tmp / "plain.zip"))
    job = _wait_job(client, payload["job_id"])
    assert job["status"] == "error"
    assert ".git" in job["error"]

    repo = client.get(f"/api/repos/{payload['repo']['id']}").json()
    assert repo["status"] == "error" and ".git" in repo["error"]


def test_zip_slip_entries_rejected(rat, client):
    evil = rat.tmp / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.txt", "boom")
        zf.writestr("inner/keep.txt", "ok")

    payload = _upload_zip(client, evil)
    job = _wait_job(client, payload["job_id"])
    assert job["status"] == "error"
    assert "Unsafe path" in job["error"]
    assert not (rat.tmp / "escaped.txt").exists()
