from support_binder.engines.base import ScopingPlan, TablePlan
from support_binder.sqlgen import build_sql, identity_bootstrap_sql


def _plan():
    return ScopingPlan("support_agent_ro", "auth.uid()", [
        TablePlan("bookings", "table", ["id", "user_id", "status"], "user_id"),
        TablePlan("cafes", "table", ["id", "name"], None),
        TablePlan("active_bookings", "view", ["id", "status"], None),
    ])


def test_role_and_schema_grant():
    sql = build_sql(_plan())
    assert 'CREATE ROLE "support_agent_ro" NOLOGIN;' in sql
    assert 'GRANT USAGE ON SCHEMA "public" TO "support_agent_ro";' in sql


def test_owned_table_gets_column_grant_and_policy():
    sql = build_sql(_plan())
    assert 'GRANT SELECT ("id", "user_id", "status") ON "public"."bookings" TO "support_agent_ro";' in sql
    assert 'ALTER TABLE "public"."bookings" ENABLE ROW LEVEL SECURITY;' in sql
    assert 'USING (auth.uid() = "user_id")' in sql


def test_public_table_grant_without_policy():
    sql = build_sql(_plan())
    assert 'GRANT SELECT ("id", "name") ON "public"."cafes" TO "support_agent_ro";' in sql
    # Only the owned table is row-secured; cafes and the view are not.
    assert sql.count("ENABLE ROW LEVEL SECURITY") == 1
    assert sql.count("CREATE POLICY") == 1


def test_policy_creation_is_idempotent():
    # Re-applying the migration must not fail on an existing policy.
    sql = build_sql(_plan())
    drop = 'DROP POLICY IF EXISTS "support_agent_ro_bookings_select" ON "public"."bookings";'
    assert drop in sql
    assert sql.index(drop) < sql.index('CREATE POLICY "support_agent_ro_bookings_select"')


def test_no_writes_or_execute_emitted():
    sql = build_sql(_plan())
    # Check executable statements only — the review header legitimately mentions
    # that no writes/EXECUTE are granted.
    statements = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    for forbidden in ("INSERT", "UPDATE", "DELETE", "EXECUTE"):
        assert forbidden not in statements.upper()


def test_secret_columns_never_appear_when_excluded():
    # password_hash was not placed in any TablePlan; it must not leak into SQL.
    assert "password_hash" not in build_sql(_plan())


def test_identity_bootstrap_is_self_contained_and_ordered():
    plan = ScopingPlan(
        "support_agent_ro", "app.current_user_id()",
        [TablePlan("bookings", "table", ["id", "user_id"], "user_id")],
        identity_setup_sql=identity_bootstrap_sql("support_agent_ro", "uuid"),
    )
    sql = build_sql(plan)
    # The function the policies depend on is created in the same migration...
    assert "CREATE SCHEMA IF NOT EXISTS app;" in sql
    assert "CREATE OR REPLACE FUNCTION app.current_user_id() RETURNS uuid" in sql
    # ...before any policy references it, and the role exists before it's granted EXECUTE.
    assert sql.index("app.current_user_id()") < sql.index("CREATE POLICY")
    assert sql.index("CREATE ROLE") < sql.index("GRANT EXECUTE ON FUNCTION")


def test_no_bootstrap_when_not_requested():
    # The default (Supabase auth.uid()) plan never emits our function.
    assert "CREATE OR REPLACE FUNCTION" not in build_sql(_plan())
