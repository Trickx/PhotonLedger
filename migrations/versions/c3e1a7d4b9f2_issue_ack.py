"""acknowledged issues

Revision ID: c3e1a7d4b9f2
Revises: a2f54276191e
Create Date: 2026-11-26 16:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e1a7d4b9f2'
down_revision = 'a2f54276191e'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('issue_ack',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=1100), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_issue_ack')),
    sa.UniqueConstraint('key', name=op.f('uq_issue_ack_key'))
    )


def downgrade():
    op.drop_table('issue_ack')
