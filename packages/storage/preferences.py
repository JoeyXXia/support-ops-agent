"""Version defaults explicitly confirmed by each actor."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from .schema import user_preferences


class PreferenceRepository:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def owner_filter(owner):
        return sa.and_(
            user_preferences.c.tenant_id == owner.tenant_id,
            user_preferences.c.user_id == owner.user_id,
        )

    def get(self, owner):
        with self.engine.connect() as connection:
            return connection.scalar(
                sa.select(user_preferences.c.product_version).where(self.owner_filter(owner))
            )

    def set(self, owner, version):
        statement = insert(user_preferences).values(
            tenant_id=owner.tenant_id, user_id=owner.user_id, product_version=version
        )
        with self.engine.begin() as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["tenant_id", "user_id"], set_={"product_version": version}
                )
            )

    def delete(self, owner):
        with self.engine.begin() as connection:
            connection.execute(user_preferences.delete().where(self.owner_filter(owner)))
