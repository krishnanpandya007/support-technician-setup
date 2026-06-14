import pytest

import runtime_exec as rx


def test_fill_names_replaces_and_detects_missing():
    sql = "select status from {{bookings}} where {{bookings.owner}} = %(current_user)s"
    out = rx.fill_names(sql, {"bookings": "booking", "bookings.owner": "user_id"})
    assert out == "select status from booking where user_id = %(current_user)s"
    with pytest.raises(rx.BindingError):
        rx.fill_names("select * from {{unknown}}", {})


def test_referenced_tables():
    sql = 'select * from booking b join "payment" p on p.bid = b.id left join refund r on 1=1'
    assert rx.referenced_tables(sql) == {"booking", "payment", "refund"}


def test_check_tables_allowed():
    sql = "select 1 from booking join payment on true"
    rx.check_tables_allowed(sql, {"booking", "payment", "refund"})   # subset -> ok
    rx.check_tables_allowed(sql, set())                              # no manifest -> ok
    with pytest.raises(rx.BindingError):
        rx.check_tables_allowed("select 1 from secrets", {"booking"})


def test_shape_result_variants():
    assert rx.shape_result([]) == ({"status": "not_found"}, True)
    assert rx.shape_result([{"verdict": "MISMATCH"}]) == ("MISMATCH", False)
    rec, is_rec = rx.shape_result([{"status": "active", "amount": 500}])
    assert is_rec and rec == {"status": "active", "amount": 500}
    multi, is_rec = rx.shape_result([{"summary": "a"}, {"summary": "b"}])
    assert is_rec and multi["count"] == 2 and multi["summary"] == "a; b"


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.last_sql = None
        self.last_params = None

    def all(self, sql, params):
        self.last_sql, self.last_params = sql, params
        return self.rows


def test_execute_tool_end_to_end():
    db = _FakeDB([{"verdict": "NO_REFUND"}])
    template = "select verdict from {{refunds}} where {{refunds.owner}} = %(current_user)s " \
               "and id = %(refund_id)s"
    result, is_record = rx.execute_tool(
        template, {"refund_id": 7}, db=db,
        names={"refunds": "refund", "refunds.owner": "user_id"},
        allowed_tables={"refund"}, current_user="u-123",
    )
    assert result == "NO_REFUND" and is_record is False
    # names filled into the text; values bound as params (current_user injected, not pasted)
    assert "from refund" in db.last_sql and "{{" not in db.last_sql
    assert db.last_params == {"refund_id": 7, "current_user": "u-123"}


def test_execute_tool_blocks_disallowed_table():
    db = _FakeDB([{"verdict": "x"}])
    with pytest.raises(rx.BindingError):
        rx.execute_tool("select 1 from {{t}}", {}, db=db,
                        names={"t": "other_table"}, allowed_tables={"refund"},
                        current_user="u-1")


# A realistic generated query: documentation comments (one mentioning {{placeholders}} in
# prose), CTEs, EXTRACT(... FROM ...), a function call after FROM, and 'from' inside a
# string literal. None of these are bindings or table reads.
_REALISTIC = """\
-- Tool: check_refund_status  (checker)
-- Blind mode: {{placeholders}} are bound locally by the operator.
/* params: %(booking_id)s, %(current_user)s */
WITH booking_check AS (
    SELECT 1 FROM {{bookings}} WHERE id = %(booking_id)s
      AND {{bookings.owner}} = %(current_user)s
),
refund AS (
    SELECT status, decided_at FROM {{refunds}}
    WHERE booking_id = %(booking_id)s AND {{refunds.owner}} = %(current_user)s
)
SELECT 'APPROVED: ' || EXTRACT(DAY FROM now() - (SELECT decided_at FROM refund))::int::text
       || ' days from approval -- payout pending' AS verdict
FROM (SELECT 1) one
LEFT JOIN booking_check bc ON true"""

_REALISTIC_NAMES = {"bookings": "booking", "bookings.owner": "user_id",
                    "refunds": "refund_tbl", "refunds.owner": "user_id"}


def test_scanner_ignores_comments_literals_ctes_and_extract():
    filled = rx.fill_names(rx.strip_comments(_REALISTIC), _REALISTIC_NAMES)
    assert "{{" not in filled and "-- Tool" not in filled
    # literal kept intact (its inner '--' must not be eaten as a comment)
    assert "days from approval -- payout pending" in filled
    # only real relations: no CTE names, no 'now', no 'approval' from the literal
    assert rx.referenced_tables(filled) == {"booking", "refund_tbl"}
    rx.check_tables_allowed(filled, {"booking", "refund_tbl"})


def test_execute_tool_runs_realistic_query():
    db = _FakeDB([{"verdict": "APPROVED: 2 days from approval -- payout pending"}])
    result, is_record = rx.execute_tool(
        _REALISTIC, {"booking_id": 7}, db=db, names=_REALISTIC_NAMES,
        allowed_tables={"booking", "refund_tbl"}, current_user="u-1")
    assert is_record is False and result.startswith("APPROVED")


def test_is_runnable_query_ignores_prose():
    assert not rx.is_runnable_query("select 1  -- copied from the api docs")
    assert not rx.is_runnable_query("select 'data from vendor' as note")
    assert rx.is_runnable_query("select 1 from {{t}}")
    assert rx.is_runnable_query("select id from orders")


def test_load_names_accepts_sectioned_worksheet(tmp_path):
    p = tmp_path / "bindings.local.yaml"
    p.write_text(
        "database:\n  schema_name: public\n"
        "tables:\n  bookings: booking\n  refunds: ''\n"      # refunds left unfilled
        "columns:\n  bookings.owner: user_id\n", encoding="utf-8")
    names = rx.load_names(str(p))
    assert names == {"schema_name": "public", "bookings": "booking",
                     "bookings.owner": "user_id"}            # empty value skipped
