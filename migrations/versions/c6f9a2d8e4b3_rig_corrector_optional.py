"""optional corrector of a rig

Revision ID: c6f9a2d8e4b3
Revises: b2e8f4a6c9d1
Create Date: 2026-11-28 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6f9a2d8e4b3'
down_revision = 'b2e8f4a6c9d1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('rig', schema=None) as batch_op:
        batch_op.add_column(sa.Column('corrector_optional', sa.Boolean(), server_default='0', nullable=False))


def downgrade():
    with op.batch_alter_table('rig', schema=None) as batch_op:
        batch_op.drop_column('corrector_optional')
