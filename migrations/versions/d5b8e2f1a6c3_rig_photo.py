"""rig photo

Revision ID: d5b8e2f1a6c3
Revises: c3e1a7d4b9f2
Create Date: 2026-11-27 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5b8e2f1a6c3'
down_revision = 'c3e1a7d4b9f2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('rig', schema=None) as batch_op:
        batch_op.add_column(sa.Column('photo', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('rig', schema=None) as batch_op:
        batch_op.drop_column('photo')
