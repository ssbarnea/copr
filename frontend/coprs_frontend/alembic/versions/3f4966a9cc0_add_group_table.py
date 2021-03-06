"""Add group table

Revision ID: 3f4966a9cc0
Revises: 3b4cfc666d14
Create Date: 2015-09-24 10:14:06.291886

"""

# revision identifiers, used by Alembic.
revision = '3f4966a9cc0'
down_revision = '3b4cfc666d14'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table('group',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=127), nullable=True),
        sa.Column('fas_name', sa.String(length=127), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.add_column('copr',
        sa.Column('group_id', sa.Integer(), nullable=True)
    )


def downgrade():
    op.drop_table('group')
    op.drop_column('copr', 'group_id')
