"""calibration steps of WBPP runs

Revision ID: b2e8f4a6c9d1
Revises: a7d3e9c1b5f8
Create Date: 2026-11-27 20:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2e8f4a6c9d1'
down_revision = 'a7d3e9c1b5f8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('wbpp_run', schema=None) as batch_op:
        batch_op.add_column(sa.Column('calibration', sa.JSON(), server_default='{}', nullable=False))
    op.execute("UPDATE wbpp_run SET size = -1")          # read the logs again at the next index run


def downgrade():
    with op.batch_alter_table('wbpp_run', schema=None) as batch_op:
        batch_op.drop_column('calibration')
