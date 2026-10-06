"""Unit tests for the ``git log -z`` stream parser (format contract in rat.gitio)."""

from __future__ import annotations

import pytest

from rat.gitio import GitLogFormatError, parse_log_record

SHA_A = "a" * 40
SHA_B = "b" * 40
CT = 1_700_000_000


def make_record(
    *,
    sha: str = SHA_A,
    parents: str = "",
    aN: str = "Alice Mapped",
    aE: str = "alice.mapped@example.com",
    an: str = "Alice Raw",
    ae: str = "alice.raw@example.com",
    ct: int | str = CT,
    subject: str = "a subject",
    entries: bytes = b"",
) -> bytes:
    """Assemble a record byte-for-byte like ``git log --numstat -z`` emits it.

    Layout: header fields NUL-separated, then ``\\0\\0\\n``, then NUL-terminated
    numstat entries.  A rename entry contributes three NUL-separated fields
    (counts with an empty path, then the old path, then the new path).
    """
    header = "\0".join([sha, parents, aN, aE, an, ae, str(ct), subject]).encode("utf-8")
    return header + b"\0\0\n" + entries


def test_simple_record_with_two_entries():
    rec = make_record(parents=SHA_B, entries=b"3\t1\tsrc/a.txt\0-\t-\tbin.dat\0")
    c = parse_log_record(rec)
    assert c.sha == SHA_A
    assert c.parents == [SHA_B]
    assert c.author_name == "Alice Mapped" and c.author_email == "alice.mapped@example.com"
    assert c.raw_name == "Alice Raw" and c.raw_email == "alice.raw@example.com"
    assert c.ct == CT
    assert c.subject == "a subject"
    assert len(c.changes) == 2

    first, second = c.changes
    assert (first.path, first.old_path, first.added, first.removed, first.is_binary) == (
        "src/a.txt",
        None,
        3,
        1,
        False,
    )
    assert (second.path, second.added, second.removed, second.is_binary) == ("bin.dat", 0, 0, True)


def test_binary_entry_is_not_measured():
    rec = make_record(entries=b"-\t-\timg.png\0")
    (ch,) = parse_log_record(rec).changes
    assert ch.is_binary is True
    assert ch.added == 0 and ch.removed == 0


def test_rename_entry():
    rec = make_record(entries=b"2\t1\t\0old/name.c\0new/name.c\0")
    (ch,) = parse_log_record(rec).changes
    assert ch.path == "new/name.c"  # changes are attributed to the new path
    assert ch.old_path == "old/name.c"
    assert ch.added == 2 and ch.removed == 1


def test_pure_rename_zero_counts():
    rec = make_record(entries=b"0\t0\t\0a.txt\0b.txt\0")
    (ch,) = parse_log_record(rec).changes
    assert ch.old_path == "a.txt" and ch.path == "b.txt"
    assert ch.added == 0 and ch.removed == 0


def test_rename_followed_by_normal_entry():
    rec = make_record(entries=b"0\t0\t\0old.c\0new.c\0\n1\t0\tother.txt\0")
    changes = parse_log_record(rec).changes
    assert [(c.path, c.old_path) for c in changes] == [("new.c", "old.c"), ("other.txt", None)]
    assert changes[1].added == 1


def test_path_with_tab_quote_and_unicode_stays_raw():
    # With -z, git never quotes paths: tabs, quotes and non-ASCII arrive verbatim.
    path = 'dir/we\tird "quoted" \u00e9\u00e8\u00fc.txt'
    rec = make_record(entries=b"1\t0\t" + path.encode("utf-8") + b"\0")
    (ch,) = parse_log_record(rec).changes
    assert ch.path == path


def test_tolerates_leading_newlines():
    rec = b"\n\r\n" + make_record(entries=b"1\t0\tf.txt\0")
    c = parse_log_record(rec)
    assert c.sha == SHA_A
    assert c.changes[0].path == "f.txt"


def test_record_without_changes():
    c = parse_log_record(make_record(entries=b""))
    assert c.changes == []


def test_merge_record_keeps_all_parents():
    rec = make_record(parents=f"{SHA_B} {'c' * 40}", entries=b"1\t0\tf\0")
    assert parse_log_record(rec).parents == [SHA_B, "c" * 40]


def test_sha256_length_accepted():
    sha = "f" * 64
    rec = make_record(sha=sha, entries=b"1\t0\tf\0")
    assert parse_log_record(rec).sha == sha


@pytest.mark.parametrize("rec", [b"", b"garbage", b"short\0record", b"\x00" * 8, b"\x00\0\0\n"])
def test_desync_records_raise(rec):
    with pytest.raises(GitLogFormatError):
        parse_log_record(rec)


def test_bad_sha_raises():
    with pytest.raises(GitLogFormatError):
        parse_log_record(make_record(sha="z" * 40))


def test_bad_committer_date_raises():
    with pytest.raises(GitLogFormatError):
        parse_log_record(make_record(ct="not-a-number", entries=b"1\t0\tf\0"))
