from fixtures import USE_DEFAULT, FakePrompter, make_snapshot

from support_binder import cli
from support_binder.config import RunConfig
from support_binder.engines.mock import MockEngine


def test_wizard_end_to_end(tmp_path):
    snap = make_snapshot()
    out = tmp_path / "access.migration.local.sql"
    config = RunConfig(out=str(out), verify=True)

    admin_engine = MockEngine(snap)
    verify_engine = MockEngine(snap, visible=["bookings", "cafes", "users"], rows={"bookings": 0})

    prompter = FakePrompter(
        # users-table, then owner per chosen relation (bookings, cafes, users)
        choices=["users", "user_id", cli._NONE_OWNER, cli._NONE_OWNER],
        # relation selection, then columns per relation (users uses computed default)
        selects=[
            ["bookings", "cafes", "users"],
            ["id", "user_id", "cafe_id", "status", "amount"],
            ["id", "name", "city"],
            USE_DEFAULT,
        ],
        confirms=[True, True],  # emit, then verify
    )

    result = cli.run_wizard(
        prompter, admin_engine, config,
        url_reader=lambda env, label: "postgresql://fake",
        verify_engine=verify_engine,
    )

    owners = {t.table: t.owner_column for t in result.plan.tables}
    assert owners == {"bookings": "user_id", "cafes": None, "users": None}

    # Secret column excluded from the users grant (came through the computed default).
    users_cols = next(t.granted_columns for t in result.plan.tables if t.table == "users")
    assert "password_hash" not in users_cols
    assert "password_hash" not in result.sql

    # SQL written to the chosen path and matches what was emitted.
    assert out.read_text(encoding="utf-8") == result.sql
    assert 'CREATE ROLE "support_agent_ro"' in result.sql
    assert result.sql.count("CREATE POLICY") == 1  # only the owned 'bookings' table

    # Verification ran and the scope check passed.
    assert result.verify_results is not None
    by_name = {r.name: r.passed for r in result.verify_results}
    assert by_name["scope: visible == selected"] is True


def test_non_interactive_auto_plan():
    snap = make_snapshot()
    config = RunConfig(tables=["bookings", "cafes"], non_interactive=True)
    plan = cli.build_plan_auto(snap, config)
    owners = {t.table: t.owner_column for t in plan.tables}
    assert owners == {"bookings": "user_id", "cafes": None}
    # bookings granted columns exclude nothing secret here, but the path is exercised.
    bookings_cols = next(t.granted_columns for t in plan.tables if t.table == "bookings")
    assert "user_id" in bookings_cols
