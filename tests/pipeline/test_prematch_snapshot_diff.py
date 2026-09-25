from mystake.models.snapshot import parse_prematch_snapshot
from mystake.pipeline.prematch_snapshot_diff import diff_prematch_snapshots


def make_snapshot(ev):
    return parse_prematch_snapshot({"id": 1, "ev": ev})


def test_no_change_produces_empty_diff():
    snapshot = make_snapshot({"55": {"2001": {"coef": 1.8}}})

    diff = diff_prematch_snapshots(snapshot, snapshot)

    assert diff.has_changes is False
    assert diff.added_markets == ()
    assert diff.removed_markets == ()
    assert diff.added_selections == ()
    assert diff.removed_selections == ()
    assert diff.price_changes == ()


def test_market_added():
    previous = make_snapshot({"55": {"2001": {"coef": 1.8}}})
    current = make_snapshot(
        {
            "55": {"2001": {"coef": 1.8}},
            "56": {"3001": {"coef": 2.0}},
        }
    )

    diff = diff_prematch_snapshots(previous, current)

    assert [c.market_id for c in diff.added_markets] == ["56"]
    assert diff.removed_markets == ()


def test_market_removed():
    previous = make_snapshot(
        {
            "55": {"2001": {"coef": 1.8}},
            "56": {"3001": {"coef": 2.0}},
        }
    )
    current = make_snapshot({"55": {"2001": {"coef": 1.8}}})

    diff = diff_prematch_snapshots(previous, current)

    assert [c.market_id for c in diff.removed_markets] == ["56"]
    assert diff.added_markets == ()


def test_selection_added():
    previous = make_snapshot({"55": {"2001": {"coef": 1.8}}})
    current = make_snapshot({"55": {"2001": {"coef": 1.8}, "2002": {"coef": 2.5}}})

    diff = diff_prematch_snapshots(previous, current)

    assert [c.selection_id for c in diff.added_selections] == ["2002"]
    assert diff.added_selections[0].market_id == "55"


def test_selection_removed():
    previous = make_snapshot({"55": {"2001": {"coef": 1.8}, "2002": {"coef": 2.5}}})
    current = make_snapshot({"55": {"2001": {"coef": 1.8}}})

    diff = diff_prematch_snapshots(previous, current)

    assert [c.selection_id for c in diff.removed_selections] == ["2002"]


def test_price_change_detected():
    previous = make_snapshot({"55": {"2001": {"coef": 1.8}}})
    current = make_snapshot({"55": {"2001": {"coef": 1.95}}})

    diff = diff_prematch_snapshots(previous, current)

    assert len(diff.price_changes) == 1
    change = diff.price_changes[0]
    assert change.selection_id == "2001"
    assert change.market_id == "55"
    assert change.old == 1.8
    assert change.new == 1.95


def test_unchanged_price_produces_no_change():
    previous = make_snapshot({"55": {"2001": {"coef": 1.8}}})
    current = make_snapshot({"55": {"2001": {"coef": 1.8}}})

    diff = diff_prematch_snapshots(previous, current)

    assert diff.price_changes == ()


def test_dict_reordering_alone_produces_no_false_changes():
    previous = make_snapshot(
        {
            "55": {"2001": {"coef": 1.8}, "2002": {"coef": 2.1}},
            "56": {"3001": {"coef": 1.5}},
        }
    )
    # Same logical content, different insertion order.
    current = make_snapshot(
        {
            "56": {"3001": {"coef": 1.5}},
            "55": {"2002": {"coef": 2.1}, "2001": {"coef": 1.8}},
        }
    )

    diff = diff_prematch_snapshots(previous, current)

    assert diff.has_changes is False
