# 0004 SQLAlchemy 2 + Alembic; PostgreSQL and SQLite

**Status:** Accepted (closes D3). Schema verification refined by [ADR 0016](0016-migration-policy.md).

**Context:** "Ease of setup" and "production-grade data model" are both assessed.

**Decision:** SQLAlchemy 2 typed models and Alembic migrations (written and reviewed by hand, with a test that the migrated schema equals the models). PostgreSQL 16 is the production profile (docker-compose); SQLite is the zero-setup local default. Every migration and constraint test runs on both engines, locally and in CI. SQL stays portable: partial unique indexes and composite foreign keys are used because both engines support them; there is no `tsvector` or JSONB-specific syntax. SQLite connections enable `foreign_keys`, WAL and a busy timeout. Timestamps are stored in UTC through a `UTCDateTime` type that rejects naive values.

**Consequences:** Two engines to keep green. Portability limits some PostgreSQL features, which is acceptable at this volume.
