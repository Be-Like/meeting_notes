"""The users directory (D8): the first real service and the one P3 calls to validate attendee ids."""

from sqlalchemy import select
from sqlalchemy.sql import Select

import schemas
from errors import NotFound
from models import User
from services.base import BaseService
from tools import tool


class UserService(BaseService):
    name = "users"

    def scope(self, stmt: Select) -> Select:
        """Any authenticated user may read the whole directory (D8)."""
        return stmt

    @tool(title="List users", read_only=True, destructive=False, idempotent=True)
    def list(self) -> list[schemas.User]:
        """List every user in the directory, ordered by id. Any authenticated user may read the whole directory (D8)."""
        rows = self.session.scalars(self.scope(select(User)).order_by(User.id))
        return [schemas.User.model_validate(row) for row in rows]

    @tool(title="Get user", read_only=True, destructive=False, idempotent=True)
    def get(self, user_id: int) -> schemas.User:
        """Return one user from the directory. A user that does not exist is "user <id> not found"."""
        row = self.session.scalars(self.scope(select(User)).where(User.id == user_id)).first()
        if row is None:
            raise NotFound(f"user {user_id} not found")
        return schemas.User.model_validate(row)
