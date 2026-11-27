"""rig mount and guide camera

Revision ID: e8a3c6d2f4b1
Revises: d5b8e2f1a6c3
Create Date: 2026-11-27 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e8a3c6d2f4b1'
down_revision = 'd5b8e2f1a6c3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mount',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('payload_kg', sa.Double(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mount'))
    )
    with op.batch_alter_table('rig', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mount_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('guide_camera_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_rig_mount_id'), ['mount_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_rig_guide_camera_id'), ['guide_camera_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_rig_mount_id_mount'), 'mount', ['mount_id'], ['id'])
        batch_op.create_foreign_key(batch_op.f('fk_rig_guide_camera_id_camera'), 'camera', ['guide_camera_id'], ['id'])


def downgrade():
    with op.batch_alter_table('rig', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_rig_guide_camera_id_camera'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('fk_rig_mount_id_mount'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_rig_guide_camera_id'))
        batch_op.drop_index(batch_op.f('ix_rig_mount_id'))
        batch_op.drop_column('guide_camera_id')
        batch_op.drop_column('mount_id')
    op.drop_table('mount')
