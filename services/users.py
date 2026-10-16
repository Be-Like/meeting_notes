"""The users directory (D8): the first real service and the one P3 calls to validate attendee ids."""

from sqlalchemy import select
from sqlalchemy.sql import Select

import schemas
from errors import NotFound
from models import User
from services.base import BaseService, read_only


class UserService(BaseService):
    name = "users"

    def scope(self, stmt: Select) -> Select:
        """Any authenticated user may read the whole directory (D8)."""
        return stmt

    @read_only
    def list(self) -> list[schemas.User]:
        rows = self.session.scalars(self.scope(select(User)).order_by(User.id))
        return [schemas.User.model_validate(row) for row in rows]

    @read_only
    def get(self, user_id: int) -> schemas.User:
        row = self.session.scalars(self.scope(select(User)).where(User.id == user_id)).first()
        if row is None:
            raise NotFound(f"user {user_id} not found")
        return schemas.User.model_validate(row)
