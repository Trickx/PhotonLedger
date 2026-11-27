"""notes files (Notizen.md)

Revision ID: a7d3e9c1b5f8
Revises: f1c4a9b7e2d5
Create Date: 2026-11-27 18:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7d3e9c1b5f8'
down_revision = 'f1c4a9b7e2d5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('note',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(length=1000), nullable=False),
    sa.Column('folder', sa.String(length=1000), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('mtime', sa.Double(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('seen_run', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_note')),
    sa.UniqueConstraint('path', name=op.f('uq_note_path'))
    )
    with op.batch_alter_table('note', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_note_folder'), ['folder'], unique=False)
        batch_op.create_index(batch_op.f('ix_note_seen_run'), ['seen_run'], unique=False)


def downgrade():
    op.drop_table('note')
