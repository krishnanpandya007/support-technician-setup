from support_binder.engines.base import ScopingPlan, TablePlan
from support_binder.manifest import build_allowed_schema


def _plan():
    return ScopingPlan("support_agent_ro", "app.current_user_id()", [
        TablePlan("users", "table", ["id", "email"], "id"),
        TablePlan("cafes", "table", ["id", "name"], None),
    ], schema="public")


def test_allowed_schema_manifest_contents():
    y = build_allowed_schema(_plan())
    assert 'role: "support_agent_ro"' in y
    assert 'schema: "public"' in y
    assert 'identity_expr: "app.current_user_id()"' in y
    assert 'name: "users"' in y
    assert 'owner_column: "id"' in y        # row-scoped
    assert 'columns: ["id", "email"]' in y
    # public/reference table carries an explicit null owner
    assert 'name: "cafes"' in y
    assert "owner_column: null" in y


def test_allowed_schema_manifest_is_valid_yaml():
    import importlib.util
    if importlib.util.find_spec("yaml") is None:
        return  # yaml not installed in this env; content assertions above still apply
    import yaml
    doc = yaml.safe_load(build_allowed_schema(_plan()))
    assert doc["role"] == "support_agent_ro"
    by_name = {t["name"]: t for t in doc["tables"]}
    assert by_name["users"]["owner_column"] == "id"
    assert by_name["users"]["columns"] == ["id", "email"]
    assert by_name["cafes"]["owner_column"] is None
