"""Remove build.ended_on, build.started_on columns

Revision ID: 13af46c70227
Revises: 22c5f7a954ce
Create Date: 2016-03-04 09:14:03.088234

"""

# revision identifiers, used by Alembic.
revision = '13af46c70227'
down_revision = '22c5f7a954ce'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.drop_column('build', 'ended_on')
    op.drop_column('build', 'started_on')
    op.create_index('build_canceled', 'build', ['canceled'], unique=False)


def downgrade():
    op.drop_index('build_canceled', table_name='build')
    op.add_column('build', sa.Column('started_on', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('build', sa.Column('ended_on', sa.INTEGER(), autoincrement=False, nullable=True))
    op.create_index('build_ended_on_canceled_started_on', 'build', ['ended_on', 'canceled', 'started_on'], unique=False)
