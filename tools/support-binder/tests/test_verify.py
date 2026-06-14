from fixtures import make_snapshot

from support_binder.engines.base import ScopingPlan, TablePlan
from support_binder.engines.mock import MockEngine
from support_binder.verify import verify


def _plan():
    return ScopingPlan("r", "auth.uid()", [
        TablePlan("bookings", "table", ["id"], "user_id"),
        TablePlan("cafes", "table", ["id"], None),
    ])


def _by_name(results):
    return {r.name: r for r in results}


def test_all_checks_pass():
    eng = MockEngine(make_snapshot(), visible=["bookings", "cafes"], rows={"bookings": 0})
    res = _by_name(verify(eng, _plan()))
    assert res["scope: visible == selected"].passed is True
    assert res["writes blocked on all selected"].passed is True
    assert res["RLS active on owned tables"].passed is True


def test_unexpected_visible_relation_fails_scope():
    eng = MockEngine(make_snapshot(), visible=["bookings", "cafes", "secret_table"])
    res = _by_name(verify(eng, _plan()))
    assert res["scope: visible == selected"].passed is False
    assert "secret_table" in res["scope: visible == selected"].detail


def test_writable_relation_fails():
    eng = MockEngine(make_snapshot(), visible=["bookings", "cafes"],
                     writes={"bookings": False}, rows={"bookings": 0})
    res = _by_name(verify(eng, _plan()))
    assert res["writes blocked on all selected"].passed is False


def test_rows_visible_without_identity_fails_rls():
    eng = MockEngine(make_snapshot(), visible=["bookings", "cafes"], rows={"bookings": 5})
    res = _by_name(verify(eng, _plan()))
    assert res["RLS active on owned tables"].passed is False
