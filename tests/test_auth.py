import pytest
from sqlalchemy import func, select

import auth
import errors
import main
import schemas
from cache import cache_key
from errors import Conflict, NoUser
from models import User
from schemas import UserCreate


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("a" * 7, id="seven-characters"),
        pytest.param("a" * 73, id="73-ascii-bytes"),
        pytest.param("é" * 40, id="40-characters-80-bytes"),
    ],
)
def test_register_refuses_password_outside_bcrypt_limits(client, password):
    response = client.post("/users/", json={"name": "Ada", "email": "ada@example.com", "password": password})

    assert response.status_code == 422


def test_register_accepts_72_byte_password(client):
    response = client.post("/users/", json={"name": "Ada", "email": "ada@example.com", "password": "a" * 72})

    assert response.status_code == 201


def test_hash_password_round_trips_and_rejects_wrong_password():
    password_hash = auth.hash_password("correct horse")

    assert auth.verify_password("correct horse", password_hash)
    assert not auth.verify_password("wrong horse", password_hash)


def test_new_token_is_random_and_hash_token_is_sha256_hex():
    token = auth.new_token()

    assert token != auth.new_token()
    digest = auth.hash_token(token)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    assert digest != token


def test_register_returns_user_and_stores_bcrypt_hash_without_token(db_session):
    user = auth.register(db_session, UserCreate(name="Ada", email="ada@example.com", password="correct horse"))

    assert isinstance(user, schemas.User)
    row = db_session.get(User, user.id)
    assert row.password_hash.startswith("$2b$")
    assert row.token_hash is None


def test_register_twice_with_one_email_raises_conflict_and_keeps_one_row(db_session):
    data = UserCreate(name="Ada", email="ada@example.com", password="correct horse")
    auth.register(db_session, data)

    with pytest.raises(Conflict):
        auth.register(db_session, data)

    assert db_session.scalar(select(func.count()).select_from(User)) == 1


def test_login_returns_token_that_authenticates_to_user_id(db_session):
    user = auth.register(db_session, UserCreate(name="Ada", email="ada@example.com", password="correct horse"))

    token = auth.login(db_session, "ada@example.com", "correct horse")

    assert auth.authenticate(db_session, token) == user.id


@pytest.mark.parametrize(
    ("email", "password"),
    [
        pytest.param("nobody@example.com", "correct horse", id="unknown-email"),
        pytest.param("ada@example.com", "wrong horse", id="wrong-password"),
    ],
)
def test_login_failure_raises_no_user_with_one_message(db_session, email, password):
    auth.register(db_session, UserCreate(name="Ada", email="ada@example.com", password="correct horse"))

    with pytest.raises(NoUser) as excinfo:
        auth.login(db_session, email, password)

    assert excinfo.value.message == auth.LOGIN_FAILED


def test_second_login_revokes_first_token(db_session):
    user = auth.register(db_session, UserCreate(name="Ada", email="ada@example.com", password="correct horse"))
    first = auth.login(db_session, "ada@example.com", "correct horse")

    second = auth.login(db_session, "ada@example.com", "correct horse")

    with pytest.raises(NoUser):
        auth.authenticate(db_session, first)
    assert auth.authenticate(db_session, second) == user.id


def test_authenticate_unknown_token_raises_no_user(db_session):
    with pytest.raises(NoUser) as excinfo:
        auth.authenticate(db_session, auth.new_token())

    assert excinfo.value.message == auth.TOKEN_FAILED


ADA = {"name": "Ada", "email": "ada@example.com", "password": "correct horse"}


def test_every_mesh_error_class_has_an_exception_handler():
    assert all(cls in main.app.exception_handlers for cls in errors.ERROR_TYPES.values())


def test_post_users_returns_201_with_id_name_email_only(client):
    response = client.post("/users/", json=ADA)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "name", "email"}
    assert (body["name"], body["email"]) == ("Ada", "ada@example.com")
    login = client.post("/auth/login", json={"email": ADA["email"], "password": ADA["password"]}).json()
    read_back = client.get(f"/users/{body['id']}", headers={"Authorization": f"Bearer {login['access_token']}"})
    assert read_back.status_code == 200
    assert read_back.json() == body


def test_post_users_with_taken_email_returns_409_naming_email(client):
    client.post("/users/", json=ADA)

    response = client.post("/users/", json=ADA)

    assert response.status_code == 409
    assert "ada@example.com" in response.json()["detail"]


def test_post_users_clears_users_cache(client, runtime):
    key = cache_key("users", "list", 1, {})
    runtime.cache.set(key, ["stale"])

    client.post("/users/", json=ADA)

    assert runtime.cache.get(key) is None


def test_login_returns_200_with_bearer_token(client):
    client.post("/users/", json=ADA)

    response = client.post("/auth/login", json={"email": ADA["email"], "password": ADA["password"]})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    listed = client.get("/users/", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert listed.status_code == 200


def test_login_failures_return_identical_401_bodies(client):
    client.post("/users/", json=ADA)

    wrong_password = client.post("/auth/login", json={"email": ADA["email"], "password": "wrong horse"})
    unknown_email = client.post("/auth/login", json={"email": "nobody@example.com", "password": ADA["password"]})

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == {"detail": auth.LOGIN_FAILED}
    assert wrong_password.headers["WWW-Authenticate"] == "Bearer"


def test_second_login_over_http_revokes_first_token(client, db_session):
    created = client.post("/users/", json=ADA).json()
    credentials = {"email": ADA["email"], "password": ADA["password"]}
    first = client.post("/auth/login", json=credentials).json()["access_token"]

    second = client.post("/auth/login", json=credentials).json()["access_token"]

    with pytest.raises(NoUser):
        auth.authenticate(db_session, first)
    assert auth.authenticate(db_session, second) == created["id"]
    assert client.get("/users/", headers={"Authorization": f"Bearer {first}"}).status_code == 401
    assert client.get("/users/", headers={"Authorization": f"Bearer {second}"}).status_code == 200


def test_headers_for_returns_bearer_header_for_a_live_token(headers_for, db_session):
    headers = headers_for("ada@example.com")

    scheme, token = headers["Authorization"].split(" ")
    assert scheme == "Bearer"
    assert auth.authenticate(db_session, token)


@pytest.mark.parametrize("path", ["/users/", "/meetings/"])
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic abc"}, {"Authorization": "Token x"}, {"Authorization": "Bearer nope"}],
    ids=["no-header", "basic", "token", "unknown-bearer"],
)
def test_guarded_route_without_valid_bearer_returns_401(client, path, headers):
    response = client.get(path, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": auth.TOKEN_FAILED}


def test_public_routes_need_no_token(client):
    assert client.get("/").status_code == 200
    assert client.post("/users/", json=ADA).status_code == 201
    assert client.post("/auth/login", json={"email": ADA["email"], "password": ADA["password"]}).status_code == 200


def test_openapi_lists_bearer_security_on_guarded_routes_only(client):
    document = client.get("/openapi.json").json()
    paths = document["paths"]

    assert "HTTPBearer" in document["components"]["securitySchemes"]
    assert paths["/users/"]["get"]["security"] == [{"HTTPBearer": []}]
    assert paths["/meetings/"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in paths["/users/"]["post"]
    assert "security" not in paths["/auth/login"]["post"]
    assert "security" not in paths["/"]["get"]


def test_stub_route_with_valid_token_answers_as_before(client, headers_for):
    response = client.get("/meetings/", headers=headers_for("ada@example.com"))

    assert response.status_code == 200
    assert response.json() == []
