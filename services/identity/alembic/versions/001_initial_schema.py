"""Initial schema — tables created via SQLAlchemy create_all on startup.

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

This migration is intentionally empty. Tables are created by the lifespan
`create_all` call during development. For production schema evolution, run
`alembic revision --autogenerate -m "description"` to generate real migrations.
"""
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
