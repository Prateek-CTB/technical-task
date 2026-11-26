"""Seed the four Azzurro Hotels properties.

Reference data, not sample data: the properties are fixed by the brief.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (slug, name used by the operations team)
PROPERTIES = [
    ("olympic-paddington", "Olympic Hotel Paddington"),
    ("venus-potts-point-sydney", "Potts Point"),
    ("venus-surry-hills", "Central Sydney"),
    ("chateau-de-venus", "Darling Harbour"),
]

property_table = sa.table(
    "property",
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column("booking_url", sa.String),
    sa.column("timezone", sa.String),
    sa.column("active", sa.Boolean),
)


def upgrade() -> None:
    op.bulk_insert(
        property_table,
        [
            {
                "slug": slug,
                "name": name,
                "booking_url": f"https://www.booking.com/hotel/au/{slug}.en-gb.html",
                "timezone": "Australia/Sydney",
                "active": True,
            }
            for slug, name in PROPERTIES
        ],
    )


def downgrade() -> None:
    slugs = [slug for slug, _ in PROPERTIES]
    op.execute(property_table.delete().where(property_table.c.slug.in_(slugs)))
