"""camera role (main or guide camera)

Revision ID: f1c4a9b7e2d5
Revises: e8a3c6d2f4b1
Create Date: 2026-11-27 15:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1c4a9b7e2d5'
down_revision = 'e8a3c6d2f4b1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('camera', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role', sa.String(length=10), server_default='main', nullable=False))
    # cameras already chosen as guide camera of a rig (and never as its main camera) are guide cameras
    op.execute("UPDATE camera SET role = 'guide' WHERE id IN (SELECT guide_camera_id FROM rig) "
               "AND id NOT IN (SELECT camera_id FROM rig)")


def downgrade():
    with op.batch_alter_table('camera', schema=None) as batch_op:
        batch_op.drop_column('role')
