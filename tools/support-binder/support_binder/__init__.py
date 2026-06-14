"""support-binder — off-model local DB-scoping CLI.

Introspects a Postgres/Supabase database with an admin URL (transient, never
persisted), lets the operator pick which tables/views a support agent may read
and the owner column that scopes each table to a user, then emits reviewable SQL
that creates a least-privilege read-only role with column grants and row-level
security. No LLM is involved at any step; the real schema never leaves the machine.
"""

__version__ = "0.1.0"
