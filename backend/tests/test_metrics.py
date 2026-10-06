"""Metric-correctness tests against hand-computed expectations.

Basic fixture (BASE = 1_700_000_000; commit n has committer date BASE + n*1000):

    c1 Alice  src/a.txt = "l1..l5"   (5 lines)      +5/-0
    c2 Bob    src/a.txt = 7 lines    (append 2)     +2/-0
    c3 Alice  src/a.txt = 6 lines    (delete "l2")  +0/-1
    c4 Bob    rename src/a.txt -> src/b.txt (exact) +0/-0   (pure rename)
    c5 Alice  add docs/x.md          (2 lines)      +2/-0
    c6 Bob    delete docs/x.md       (2 lines)      +0/-2
    c7 Alice  add bin.dat            (binary)       +0/-0

Repo totals: added 9, removed 3, growth 6, churn 12, modifications 5 of 7
commits.  These expectations follow spec sections 2.1-2.5 directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rat import authors as authors_svc
from rat import metrics as m

ALICE = ("Alice", "alice@example.com")
BOB = ("Bob", "bob@example.com")
BASE = 1_700_000_000


def ct(n: int) -> int:
    """Committer date of the n-th commit of a fixture built from BASE."""
    return BASE + n * 1000


def _files_by_path(rat, repo_id, **kwargs) -> dict:
    return {r["path"]: r for r in rat.files(repo_id, **kwargs)["rows"]}


@pytest.fixture()
def basic(rat):
    """7-commit fixture exercising add / modify / rename / delete / binary."""
    b = rat.builder("basic", base_ct=BASE)
    b.write("src/a.txt", "l1\nl2\nl3\nl4\nl5\n")
    c1 = b.commit("add a.txt", author=ALICE)
    b.write("src/a.txt", "l1\nl2\nl3\nl4\nl5\nl6\nl7\n")
    c2 = b.commit("append to a.txt", author=BOB)
    b.write("src/a.txt", "l1\nl3\nl4\nl5\nl6\nl7\n")
    c3 = b.commit("remove l2", author=ALICE)
    b.git("mv", "src/a.txt", "src/b.txt")
    c4 = b.commit("rename a to b", author=BOB)
    b.write("docs/x.md", "m1\nm2\n")
    c5 = b.commit("add x.md", author=ALICE)
    (b.path / "docs/x.md").unlink()
    c6 = b.commit("delete x.md", author=BOB)
    b.write("bin.dat", b"\x00\x01\x02\x03\xff\x00binary\n")
    c7 = b.commit("add binary", author=ALICE)
    rid = rat.ingest(b, name="basic")
    return SimpleNamespace(
        rat=rat, rid=rid, b=b, c1=c1, c2=c2, c3=c3, c4=c4, c5=c5, c6=c6, c7=c7
    )


# ---------------------------------------------------------------------------
# Repository metrics (spec 2.3: directory metrics on the root)
# ---------------------------------------------------------------------------


def test_repository_metrics(basic):
    bundle = basic.rat.bundle(basic.rid)
    assert bundle["scope"]["kind"] == "repo"
    assert bundle["n_commits"] == 7
    assert bundle["added"] == 9
    assert bundle["removed"] == 3
    assert bundle["growth"] == 6
    assert bundle["churn"] == 12
    assert bundle["n_files"] == 4
    assert bundle["modifications"] == 5
    assert bundle["modification_frequency"] == pytest.approx(5 / 7)
    assert bundle["churn_rate"] == pytest.approx(12 / 7)


def test_repo_summary_row(basic):
    row = basic.rat.repo(basic.rid)
    assert row["status"] == "ready"
    assert row["head_sha"] == basic.c7
    assert row["n_commits"] == 7
    assert row["n_files"] == 4
    assert row["n_dirs"] == 2
    assert row["n_identities"] == 2
    assert row["n_authors"] == 2
    assert row["total_added"] == 9 and row["total_removed"] == 3
    assert row["first_ct"] == ct(1) and row["last_ct"] == ct(7)


# ---------------------------------------------------------------------------
# File metrics (spec 2.1)
# ---------------------------------------------------------------------------


def test_file_metrics(basic):
    files = _files_by_path(basic.rat, basic.rid)
    assert set(files) == {"src/a.txt", "src/b.txt", "docs/x.md", "bin.dat"}

    a = files["src/a.txt"]
    assert a["added"] == 7 and a["removed"] == 1 and a["churn"] == 8
    assert a["modifications"] == 3 and a["commits"] == 3
    assert a["renamed"] is False and a["is_binary"] is False

    b = files["src/b.txt"]
    assert b["added"] == 0 and b["removed"] == 0 and b["churn"] == 0
    assert b["commits"] == 1 and b["modifications"] == 0
    assert b["renamed"] is True  # pure rename does not change metrics

    x = files["docs/x.md"]
    assert x["added"] == 2 and x["removed"] == 2 and x["churn"] == 4
    assert x["modifications"] == 2

    binr = files["bin.dat"]
    assert binr["is_binary"] is True and binr["churn"] == 0 and binr["modifications"] == 0


def test_file_owners(basic):
    files = _files_by_path(basic.rat, basic.rid)
    owners = files["src/a.txt"]["owners"]
    assert owners and owners[0]["name"] == "Alice"
    assert owners[0]["share"] == pytest.approx(6 / 8)  # Alice 6 churn vs Bob 2


def test_file_scope(basic):
    bundle = basic.rat.bundle(basic.rid, path="src/a.txt")
    assert bundle["scope"]["kind"] == "file"
    assert bundle["n_files"] == 1
    assert bundle["added"] == 7 and bundle["removed"] == 1


# ---------------------------------------------------------------------------
# Directory metrics (spec 2.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,added,removed,mods", [("src", 7, 1, 3), ("docs", 2, 2, 2)])
def test_directory_metrics(basic, path, added, removed, mods):
    bundle = basic.rat.bundle(basic.rid, path=path)
    assert bundle["scope"]["kind"] == "dir"
    assert bundle["added"] == added and bundle["removed"] == removed
    assert bundle["churn"] == added + removed
    assert bundle["modifications"] == mods
    assert bundle["modification_frequency"] == pytest.approx(mods / 7)


def test_directory_modifications_count_commit_once(rat):
    # One commit touching three files counts as ONE modification for a dir.
    b = rat.builder("dirone", base_ct=BASE)
    b.write("d/one.txt", "1\n")
    b.write("d/two.txt", "1\n")
    b.write("d/sub/three.txt", "1\n")
    b.commit("touches three files", author=ALICE)
    rid = rat.ingest(b, name="dirone")

    d = rat.bundle(rid, path="d")
    assert d["n_files"] == 3 and d["added"] == 3
    assert d["modifications"] == 1
    assert d["modification_frequency"] == 1.0
    assert rat.bundle(rid, path="d/sub")["added"] == 1
    assert rat.bundle(rid)["modifications"] == 1


def test_tree_root(basic):
    tree = basic.rat.tree(basic.rid)
    children = {c["name"]: c for c in tree["children"]}
    assert children["src"]["kind"] == "dir"
    assert children["docs"]["kind"] == "dir"
    assert children["bin.dat"]["kind"] == "file"  # top-level file kind
    assert children["bin.dat"]["binary"] is True
    assert children["src"]["n_files"] == 2
    assert children["src"]["n_files_head"] == 1  # b.txt at HEAD, a.txt renamed away
    assert children["docs"]["n_files_head"] == 0  # x.md was deleted
    assert children["src"]["churn"] == 8
    assert children["docs"]["modifications"] == 2


def test_tree_dir(basic):
    tree = basic.rat.tree(basic.rid, path="src")
    children = {c["name"]: c for c in tree["children"]}
    assert set(children) == {"a.txt", "b.txt"}
    assert children["a.txt"]["kind"] == "file"
    assert children["a.txt"]["at_head"] is False
    assert children["b.txt"]["at_head"] is True
    assert children["a.txt"]["churn"] == 8 and children["b.txt"]["churn"] == 0


# ---------------------------------------------------------------------------
# Commit-set metrics (spec 2.4)
# ---------------------------------------------------------------------------


def test_single_commit_set(basic):
    one = basic.rat.bundle(basic.rid, shas=[basic.c1])
    assert one["n_commits"] == 1
    assert one["added"] == 5 and one["removed"] == 0
    assert one["modifications"] == 1
    assert one["churn_rate"] == pytest.approx(5.0)


def test_pure_rename_commit_set(basic):
    one = basic.rat.bundle(basic.rid, shas=[basic.c4])
    assert one["n_commits"] == 1
    assert one["churn"] == 0 and one["modifications"] == 0

    detail = basic.rat.commit_detail(basic.rid, basic.c4)
    (f,) = detail["files"]
    assert f["path"] == "src/b.txt" and f["old_path"] == "src/a.txt"
    assert f["renamed"] is True and f["added"] == 0 and f["removed"] == 0


def test_since_until_boundaries(basic):
    rat, rid = basic.rat, basic.rid

    after2 = rat.bundle(rid, since=ct(2))  # H_t: ct >= t (inclusive)
    assert after2["n_commits"] == 6
    assert after2["added"] == 4 and after2["removed"] == 3

    before2 = rat.bundle(rid, until=ct(2))  # exclusive end
    assert before2["n_commits"] == 1
    assert before2["added"] == 5 and before2["removed"] == 0

    window = rat.bundle(rid, since=ct(1), until=ct(3))  # H_{i,j}: i <= ct < j
    assert window["n_commits"] == 2
    assert window["added"] == 7 and window["removed"] == 0


def test_ref_commit_filter(basic):
    rat, rid = basic.rat, basic.rid
    at_c3 = rat.bundle(rid, ref=basic.c3)
    assert at_c3["n_commits"] == 3
    assert at_c3["added"] == 7 and at_c3["removed"] == 1 and at_c3["churn"] == 8

    files = _files_by_path(rat, rid, ref=basic.c3)
    assert set(files) == {"src/a.txt"}
    assert files["src/a.txt"]["added"] == 7


def test_explicit_commit_list_takes_precedence(basic):
    two = basic.rat.bundle(basic.rid, shas=[basic.c1, basic.c5], since=ct(2))
    assert two["n_commits"] == 2
    assert two["added"] == 7 and two["removed"] == 0


def test_empty_commit_counts_as_commit_not_modification(rat):
    b = rat.builder("empty", base_ct=BASE)
    b.write("f.txt", "1\n")
    b.commit("c1", author=ALICE)
    c2 = b.commit("empty", author=BOB, add_all=False)
    rid = rat.ingest(b, name="empty")

    bundle = rat.bundle(rid)
    assert bundle["n_commits"] == 2
    assert bundle["added"] == 1 and bundle["removed"] == 0
    assert bundle["modifications"] == 1
    assert bundle["modification_frequency"] == pytest.approx(0.5)

    page = rat.commits_page(rid)
    row = next(r for r in page["rows"] if r["sha"] == c2)
    assert row["churn"] == 0 and row["n_files"] == 0


def test_merge_commits_excluded(rat):
    b = rat.builder("mergec", base_ct=BASE)
    b.write("base.txt", "a\nb\nc\n")
    b.commit("init", author=ALICE)
    b.git("checkout", "-q", "-b", "feat")
    b.write("feat.txt", "f1\nf2\n")
    b.commit("feat work", author=BOB)
    b.git("checkout", "-q", "main")
    b.write("main.txt", "m1\n")
    b.commit("main work", author=ALICE)
    merge_sha = b.merge("feat")

    rid = rat.ingest(b, name="mergec")
    row = rat.repo(rid)
    assert row["head_sha"] == merge_sha
    assert row["n_commits"] == 3  # H-bar excludes the merge commit

    bundle = rat.bundle(rid)
    assert bundle["n_commits"] == 3
    assert bundle["added"] == 6 and bundle["removed"] == 0

    with pytest.raises(m.MetricsError):
        rat.commit_detail(rid, merge_sha)


def test_rename_with_modification_attributed_to_new_path(rat):
    b = rat.builder("renmov", base_ct=BASE)
    b.write("old.txt", "".join(f"{i}\n" for i in range(1, 11)))  # 10 lines
    b.commit("init", author=ALICE)
    b.git("mv", "old.txt", "new.txt")
    b.write("new.txt", "".join(f"{i}\n" for i in range(1, 13)))  # +2 lines
    b.commit("rename and extend", author=BOB)

    rid = rat.ingest(b, name="renmov")
    files = _files_by_path(rat, rid)
    assert set(files) == {"old.txt", "new.txt"}
    assert files["new.txt"]["added"] == 2 and files["new.txt"]["removed"] == 0
    assert files["new.txt"]["renamed"] is True
    assert files["old.txt"]["added"] == 10 and files["old.txt"]["removed"] == 0

    detail = rat.commit_detail(rid, b.head())
    (f,) = detail["files"]
    assert f["path"] == "new.txt" and f["old_path"] == "old.txt"
    assert f["added"] == 2 and f["renamed"] is True


# ---------------------------------------------------------------------------
# Author metrics (spec 2.5)
# ---------------------------------------------------------------------------


def test_author_filter(basic):
    rat, rid = basic.rat, basic.rid
    alice = rat.author_id(rid, "Alice")
    bob = rat.author_id(rid, "Bob")

    a = rat.bundle(rid, author_ids=[alice])
    assert a["n_commits"] == 4 and a["added"] == 7 and a["removed"] == 1

    b = rat.bundle(rid, author_ids=[bob])
    assert b["n_commits"] == 3 and b["added"] == 2 and b["removed"] == 2

    combined = rat.bundle(rid, since=ct(2), author_ids=[alice])
    assert combined["n_commits"] == 3  # c3, c5, c7
    assert combined["added"] == 2 and combined["removed"] == 1


def test_contributors_ownership(basic):
    contribs = {c["name"]: c for c in basic.rat.contributors(basic.rid)}
    assert set(contribs) == {"Alice", "Bob"}

    a = contribs["Alice"]
    assert a["added"] == 7 and a["removed"] == 1 and a["churn"] == 8
    assert a["n_commits"] == 4 and a["modifications"] == 3
    assert a["ownership"] == pytest.approx(8 / 12)
    assert a["churn_rate"] == pytest.approx(8 / 7)

    b = contribs["Bob"]
    assert b["added"] == 2 and b["removed"] == 2 and b["churn"] == 4
    assert b["n_commits"] == 3 and b["modifications"] == 2
    assert b["ownership"] == pytest.approx(4 / 12)


def test_contributors_ignore_author_filter(basic):
    alice = basic.rat.author_id(basic.rid, "Alice")
    contribs = basic.rat.contributors(basic.rid, author_ids=[alice])
    assert {c["name"] for c in contribs} == {"Alice", "Bob"}  # ownership stays meaningful


def test_contributors_scoped_to_directory(basic):
    contribs = {c["name"]: c for c in basic.rat.contributors(basic.rid, path="src")}
    assert contribs["Alice"]["churn"] == 6  # a.txt hunks only, not docs/x.md
    assert contribs["Bob"]["churn"] == 2
    assert contribs["Alice"]["ownership"] == pytest.approx(6 / 8)


# ---------------------------------------------------------------------------
# Series, commit explorer, path search
# ---------------------------------------------------------------------------


def test_series_by_commit(basic):
    series = basic.rat.series(basic.rid, bucket="commit")
    assert series["bucket"] == "commit"
    points = series["points"]
    assert [p["sha"] for p in points] == [basic.c1, basic.c2, basic.c3, basic.c4,
                                          basic.c5, basic.c6, basic.c7]
    assert points[0]["added"] == 5 and points[0]["modifications"] == 1
    assert points[3]["churn"] == 0  # pure rename


def test_series_auto_daily_bucket(basic):
    series = basic.rat.series(basic.rid, bucket="auto")
    assert series["bucket"] == "day"
    # commits 1-6 fall on day 19675, commit 7 crosses into day 19676 (UTC)
    assert len(series["points"]) == 2
    first, last = series["points"]
    assert first["added"] == 9 and first["removed"] == 3
    assert first["commits"] == 6 and first["modifications"] == 5
    assert last["commits"] == 1 and last["churn"] == 0


def test_commits_page(basic):
    page = basic.rat.commits_page(basic.rid)
    assert page["total"] == 7
    assert page["rows"][0]["sha"] == basic.c7  # newest first
    assert page["rows"][0]["author_name"] == "Alice"

    oldest_second_half = basic.rat.commits_page(basic.rid, limit=3, offset=3, newest_first=False)
    assert [r["sha"] for r in oldest_second_half["rows"]] == [basic.c4, basic.c5, basic.c6]


def test_commits_page_scoped(basic):
    page = basic.rat.commits_page(basic.rid, path="src")
    rows = {r["sha"]: r for r in page["rows"]}
    assert page["total"] == 7
    assert rows[basic.c1]["added"] == 5 and rows[basic.c1]["n_files"] == 1
    assert rows[basic.c5]["added"] == 0  # docs/x.md is outside src/
    assert rows[basic.c5]["total_added"] == 2


def test_commits_search(basic):
    page = basic.rat.commits_page(basic.rid, q="rename")
    assert page["total"] == 1
    assert page["rows"][0]["sha"] == basic.c4

    page_by_sha = basic.rat.commits_page(basic.rid, q=basic.c5[:8])
    assert page_by_sha["total"] == 1


def test_commit_detail_initial(basic):
    detail = basic.rat.commit_detail(basic.rid, basic.c1)
    assert detail["parent_sha"] is None  # root commit
    assert detail["added"] == 5 and detail["removed"] == 0
    assert len(detail["files"]) == 1
    assert detail["files"][0]["path"] == "src/a.txt"
    assert detail["files"][0]["added"] == 5


def test_commit_detail_binary(basic):
    detail = basic.rat.commit_detail(basic.rid, basic.c7)
    (f,) = detail["files"]
    assert f["path"] == "bin.dat" and f["is_binary"] is True
    assert detail["added"] == 0 and detail["removed"] == 0


def test_scope_normalisation_and_errors(basic):
    assert basic.rat.bundle(basic.rid, path="src/")["scope"]["kind"] == "dir"
    assert basic.rat.bundle(basic.rid, path="./src/a.txt")["scope"]["kind"] == "file"
    with pytest.raises(m.MetricsError) as ei:
        basic.rat.bundle(basic.rid, path="nope/does-not-exist")
    assert ei.value.status_code == 404


def test_search_paths(basic):
    paths = basic.rat._with_conn(lambda conn: m.search_paths(conn, basic.rid, "a.txt"))
    assert any(p["path"] == "src/a.txt" for p in paths)

    all_paths = basic.rat._with_conn(lambda conn: m.search_paths(conn, basic.rid, "src"))
    assert all_paths[0]["path"] == "src" and all_paths[0]["kind"] == "dir"


# ---------------------------------------------------------------------------
# Author identity merging: .mailmap and manual merges
# ---------------------------------------------------------------------------


def test_mailmap_merges_identities(rat):
    b = rat.builder("mailmap", base_ct=BASE)
    b.write(".mailmap", "Alice Smith <alice@example.com> <alice.smith@example.com>\n")
    b.write("f.txt", "1\n")
    b.commit("first", author=("Alice Smith", "alice@example.com"))
    b.write("f.txt", "1\n2\n")
    b.commit("second", author=("Alice S", "alice.smith@example.com"))

    rid = rat.ingest(b, name="mailmap")

    authors = rat.authors(rid)
    assert len(authors) == 1
    a = authors[0]
    assert a["name"] == "Alice Smith" and a["email"] == "alice@example.com"
    assert a["n_commits"] == 2
    raw = {(r["name"], r["email"]) for r in a["raw"]}
    assert ("Alice S", "alice.smith@example.com") in raw

    row = rat.repo(rid)
    assert row["n_identities"] == 1 and row["n_authors"] == 1

    contribs = rat.contributors(rid)
    assert len(contribs) == 1 and contribs[0]["n_commits"] == 2


def test_manual_merge_and_detach(rat):
    b = rat.builder("manual", base_ct=BASE)
    b.write("a.txt", "1\n")
    b.commit("c1", author=("Bob Jones", "bob@work.com"))
    b.write("b.txt", "1\n")
    b.commit("c2", author=("Bobby", "bob@home.com"))
    b.write("c.txt", "1\n")
    b.commit("c3", author=("Alice", "alice@x.com"))

    rid = rat.ingest(b, name="manual")
    authors = {a["name"]: a for a in rat.authors(rid)}
    assert set(authors) == {"Bob Jones", "Bobby", "Alice"}

    bob, bobby = authors["Bob Jones"]["id"], authors["Bobby"]["id"]
    assert rat.merge(rid, target=bob, sources=[bobby])["merged"] == 1

    merged = {a["name"]: a for a in rat.authors(rid)}
    assert len(merged) == 2
    assert merged["Bob Jones"]["n_commits"] == 2
    assert [al["name"] for al in merged["Bob Jones"]["aliases"]] == ["Bobby"]
    assert rat.repo(rid)["n_authors"] == 2

    contribs = {c["author_id"]: c for c in rat.contributors(rid)}
    assert contribs[bob]["n_commits"] == 2
    assert contribs[bob]["ownership"] == pytest.approx(2 / 3)

    # detach restores a standalone author
    rat.detach(rid, bobby)
    detached = rat.authors(rid)
    assert len(detached) == 3
    assert all(a["n_commits"] == 1 for a in detached)
    assert rat.repo(rid)["n_authors"] == 3


def test_merge_suggestions(rat):
    b = rat.builder("sugg", base_ct=BASE)
    b.write("a.txt", "1\n")
    b.commit("c1", author=("Bob", "bob@x.com"))
    b.write("b.txt", "1\n")
    b.commit("c2", author=("Robert Jones", "bob@x.com"))
    b.write("c.txt", "1\n")
    b.commit("c3", author=("Alice", "alice@x.com"))

    rid = rat.ingest(b, name="sugg")
    sugg = rat._with_conn(lambda conn: authors_svc.suggestions(conn, rid))
    same_email = [s for s in sugg if s["reason"] == "Same email address"]
    assert len(same_email) == 1
    assert {i["email"] for i in same_email[0]["identities"]} == {"bob@x.com"}
    assert len(same_email[0]["identities"]) == 2
