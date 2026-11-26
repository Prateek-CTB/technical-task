# 0016 Migration policy and schema verification

**Status:** Accepted (refines ADR 0004)

**Context:** The P1 report claimed "migrated schema equals the models (no diff)". The test behind it, Alembic's `compare_metadata`, does not examine CHECK constraints, partial-index predicates, server defaults or foreign-key actions: a migration that silently dropped the WHERE clause of a partial unique index passed it (demonstrated in the P1 review). The claim was stronger than the evidence.

**Decision:**
* Schema equivalence is proven by **reflecting the real database** on both engines: the migrated database and one built directly from the models must have identical reflected columns, defaults, primary keys, foreign keys with actions, unique constraints, CHECKs and index predicates. Specific constraints are also asserted by name, and a negative control proves the comparison can fail (`tests/test_schema_equivalence.py`).
* **Before the first persistent deployment**, migrations 0001 and 0002 may be amended in place; no database outside test runs has been created from them. The P1 review amended 0001 to add `ck_review_analysis_current_requires_usable`.
* **From the first persistent deployment**, history is append-only. Every new migration must come with a test that upgrades a populated database and checks that the rows survive, because SQLite implements many `ALTER`s by rebuilding the table, and with foreign keys enabled a rebuild can cascade-delete child rows.
* Migrations never import application code.

**Consequences:** If anyone created `data/reviewhub.db` from the P1 code, delete it and run `reviewhub db upgrade` again. After deployment, schema changes are slower to make and safer.
