from fixtures import make_snapshot

from support_binder.engines.base import Column, ForeignKey, Relation
from support_binder.introspect import (
    detect_owner_column,
    guess_users_table,
    identity_cast_type,
    looks_secret,
    owner_candidates,
)


def test_looks_secret():
    assert looks_secret("password_hash")
    assert looks_secret("api_key")
    assert looks_secret("access_token")
    assert looks_secret("credential")
    assert not looks_secret("email")
    assert not looks_secret("status")


def test_guess_users_table():
    assert guess_users_table(make_snapshot()) == "users"


def test_owner_via_foreign_key():
    snap = make_snapshot()
    bookings = snap.get("bookings")
    assert owner_candidates(bookings, "users") == ["user_id"]
    assert detect_owner_column(bookings, "users") == "user_id"


def test_public_table_has_no_owner():
    snap = make_snapshot()
    cafes = snap.get("cafes")
    assert owner_candidates(cafes, "users") == []
    assert detect_owner_column(cafes, "users") is None


def test_users_table_scoped_on_primary_key_not_self_fk():
    # A users table with a self-referential FK (managerId -> users.id). The owner must be
    # the row's own identity (PK 'id'), never the self-FK.
    users = Relation(
        "users", "table",
        columns=[Column("id", "uuid", False), Column("managerId", "uuid", True),
                 Column("email", "text", False)],
        foreign_keys=[ForeignKey("managerId", "users", "id")],
        primary_key=["id"],
    )
    assert owner_candidates(users, "users") == ["id"]
    assert detect_owner_column(users, "users") == "id"


def test_users_table_falls_back_to_id_without_pk_introspection():
    # Engine didn't supply a primary key: still scope on a column named 'id', not the self-FK.
    users = Relation(
        "users", "table",
        columns=[Column("id", "uuid", False), Column("managerId", "uuid", True)],
        foreign_keys=[ForeignKey("managerId", "users", "id")],
    )
    assert detect_owner_column(users, "users") == "id"


def test_identity_cast_type():
    uuid_users = Relation("users", "table", [Column("id", "uuid", False)], primary_key=["id"])
    assert identity_cast_type(uuid_users) == "uuid"
    big_users = Relation("users", "table", [Column("id", "bigint", False)], primary_key=["id"])
    assert identity_cast_type(big_users) == "bigint"
    text_users = Relation("users", "table", [Column("id", "character varying", False)])
    assert identity_cast_type(text_users) == "text"
    assert identity_cast_type(None) == "uuid"  # safe default
