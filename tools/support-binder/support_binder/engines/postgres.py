"""Postgres / Supabase adapter. Uses psycopg (v3). Introspection runs against an admin
connection; the verification methods run while connected as the new read-only role.
"""
from __future__ import annotations

from ..introspect import looks_secret
from .base import Column, ForeignKey, Relation, Routine, SchemaSnapshot, ScopingPlan


class PostgresEngine:
    name = "postgres"

    def __init__(self, schema: str = "public"):
        self.schema = schema
        self._conn = None

    # ---- connection -------------------------------------------------------
    def connect(self, url: str) -> None:
        import psycopg  # lazy: only needed when this engine is used
        self._conn = psycopg.connect(url, autocommit=True)

    def _fetch(self, sql: str, params: tuple = ()) -> list[tuple]:
        if self._conn is None:
            raise RuntimeError("not connected")
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # ---- introspection (admin connection) --------------------------------
    def introspect(self) -> SchemaSnapshot:
        rels: dict[str, Relation] = {}

        for name, ttype in self._fetch(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY table_name",
            (self.schema,),
        ):
            rels[name] = Relation(name=name, kind="view" if ttype == "VIEW" else "table")

        for table, col, dtype, nullable in self._fetch(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_schema = %s "
            "ORDER BY table_name, ordinal_position",
            (self.schema,),
        ):
            if table in rels:
                rels[table].columns.append(
                    Column(name=col, type=dtype, nullable=(nullable == "YES"),
                           looks_secret=looks_secret(col))
                )

        for table, col, ref_table, ref_col in self._fetch(
            "SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "  AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON ccu.constraint_name = tc.constraint_name "
            "  AND ccu.table_schema = tc.table_schema "
            "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %s",
            (self.schema,),
        ):
            if table in rels:
                rels[table].foreign_keys.append(
                    ForeignKey(column=col, ref_table=ref_table, ref_column=ref_col)
                )

        for table, col in self._fetch(
            "SELECT tc.table_name, kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "  AND tc.table_schema = kcu.table_schema "
            "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = %s "
            "ORDER BY tc.table_name, kcu.ordinal_position",
            (self.schema,),
        ):
            if table in rels:
                rels[table].primary_key.append(col)

        routines = [
            Routine(name=name, kind=rtype.lower())
            for name, rtype in self._fetch(
                "SELECT routine_name, routine_type FROM information_schema.routines "
                "WHERE routine_schema = %s ORDER BY routine_name",
                (self.schema,),
            )
        ]

        return SchemaSnapshot(relations=list(rels.values()), routines=routines)

    def emit_scoping_sql(self, plan: ScopingPlan) -> str:
        from ..sqlgen import build_sql
        return build_sql(plan)

    # ---- verification (read-only connection) ------------------------------
    def list_visible_relations(self) -> list[str]:
        # Column-level grants surface in column_privileges, not table_privileges.
        rows = self._fetch(
            "SELECT DISTINCT table_name FROM information_schema.column_privileges "
            "WHERE grantee = current_user AND privilege_type = 'SELECT' "
            "AND table_schema = %s ORDER BY table_name",
            (self.schema,),
        )
        return [r[0] for r in rows]

    def write_blocked(self, relation: str) -> bool:
        qualified = f"{self.schema}.{relation}"
        rows = self._fetch(
            "SELECT has_table_privilege(%s, 'INSERT') "
            "OR has_table_privilege(%s, 'UPDATE') "
            "OR has_table_privilege(%s, 'DELETE')",
            (qualified, qualified, qualified),
        )
        return not rows[0][0]

    def rows_without_identity(self, relation: str) -> int:
        rows = self._fetch(f'SELECT count(*) FROM "{self.schema}"."{relation}"')
        return int(rows[0][0])
