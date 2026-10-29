from watchkeep.core.diff import diff_snapshot
from watchkeep.core.models import Item


def item(id_, updated="2026-01-01T00:00:00Z"):
    return Item(id=id_, action="review requested", title=id_, url=f"https://x/{id_}", repo="a/b", author="z", updated_at=updated)


def by_id(*items):
    return {i.id: i for i in items}


def test_first_snapshot_is_all_new():
    d = diff_snapshot({}, [item("a"), item("b")])
    assert [i.id for i in d.new] == ["a", "b"]
    assert d.updated == d.unchanged == d.resolved == []


def test_classifies_new_updated_unchanged_resolved():
    previous = by_id(item("keep"), item("moved"), item("gone"))
    d = diff_snapshot(previous, [item("keep"), item("moved", updated="2026-02-01T00:00:00Z"), item("fresh")])
    assert [i.id for i in d.new] == ["fresh"]
    assert [i.id for i in d.updated] == ["moved"]
    assert [i.id for i in d.unchanged] == ["keep"]
    assert [i.id for i in d.resolved] == ["gone"]


def test_rerequest_after_resolution_is_new_again():
    pr = item("review:pr-42")
    after_review = diff_snapshot(by_id(pr), [])
    assert after_review.resolved == [pr]
    rerequested = diff_snapshot({}, [pr])  # previous snapshot no longer has it
    assert rerequested.new == [pr]


def test_duplicate_ids_in_one_snapshot_keep_first():
    d = diff_snapshot({}, [item("a"), item("a", updated="2027-01-01T00:00:00Z")])
    assert len(d.new) == 1 and d.new[0].updated_at == "2026-01-01T00:00:00Z"
