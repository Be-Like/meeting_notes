"""Registration, login, and the HTTP dependencies that turn a bearer token into a user id."""

import hashlib
import os
import secrets

import bcrypt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import schemas
from database import get_db
from errors import Conflict, NoUser
from mesh import MeshRuntime, ServiceMesh
from models import User

LOGIN_FAILED = "invalid email or password"
TOKEN_FAILED = "invalid or missing bearer token"

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """bcrypt hash. The work factor is BCRYPT_ROUNDS, read per call, or the library default when unset."""
    rounds = os.environ.get("BCRYPT_ROUNDS")
    salt = bcrypt.gensalt(rounds=int(rounds)) if rounds else bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register(session: Session, data: schemas.UserCreate) -> schemas.User:
    row = User(name=data.name, email=data.email, password_hash=hash_password(data.password))
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise Conflict(f"a user with email {data.email} already exists") from None
    return schemas.User.model_validate(row)


def login(session: Session, email: str, password: str) -> str:
    """Rotating the token on every login is the whole revocation story."""
    row = session.scalars(select(User).where(User.email == email)).one_or_none()
    if row is None or not verify_password(password, row.password_hash):
        raise NoUser(LOGIN_FAILED)
    token = new_token()
    row.token_hash = hash_token(token)
    session.commit()
    return token


def authenticate(session: Session, token: str) -> int:
    user_id = session.scalars(select(User.id).where(User.token_hash == hash_token(token))).one_or_none()
    if user_id is None:
        raise NoUser(TOKEN_FAILED)
    return user_id


def get_runtime(request: Request) -> MeshRuntime:
    return request.app.state.mesh_runtime


def get_current_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> int:
    if creds is None:
        raise NoUser(TOKEN_FAILED)
    return authenticate(db, creds.credentials)


def get_mesh(user_id: int = Depends(get_current_user_id), runtime: MeshRuntime = Depends(get_runtime)) -> ServiceMesh:
    return runtime.build_mesh(user_id)
