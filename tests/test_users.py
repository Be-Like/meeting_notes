import pytest

import auth
import schemas
from errors import NotFound
from schemas import UserCreate
from services.base import DEFAULT_REGISTRY, is_read_only
from services.users import UserService


@pytest.fixture
def seeded(db_session) -> list[schemas.User]:
    """Two users registered through auth on db_engine, the engine the runtime fixture's sessions are bound to."""
    return [
        auth.register(db_session, UserCreate(name="Ada", email="ada@example.com", password="correct horse")),
        auth.register(db_session, UserCreate(name="Grace", email="grace@example.com", password="correct horse")),
    ]


def test_list_returns_users_in_id_order_and_get_returns_one(runtime, seeded):
    ada, grace = seeded
    mesh = runtime.build_mesh(ada.id)

    users = mesh.users.list()

    assert all(isinstance(user, schemas.User) for user in users)
    assert users == [ada, grace]
    assert [user.id for user in users] == sorted(user.id for user in users)
    found = mesh.users.get(user_id=grace.id)
    assert isinstance(found, schemas.User)
    assert found == grace


def test_get_unknown_id_raises_not_found_naming_it(runtime, seeded):
    mesh = runtime.build_mesh(seeded[0].id)

    with pytest.raises(NotFound) as raised:
        mesh.users.get(user_id=999)

    assert "999" in raised.value.message


def test_user_service_reads_are_marked_and_registered():
    assert is_read_only(UserService.list)
    assert is_read_only(UserService.get)
    assert "users" in DEFAULT_REGISTRY
    assert DEFAULT_REGISTRY.get("users") is UserService


def test_get_users_lists_every_registered_user_and_get_returns_one(client, headers_for):
    headers = headers_for("ada@example.com", name="Ada")
    headers_for("grace@example.com", name="Grace")

    listed = client.get("/users/", headers=headers)

    assert listed.status_code == 200
    users = listed.json()
    assert [(user["name"], user["email"]) for user in users] == [
        ("Ada", "ada@example.com"),
        ("Grace", "grace@example.com"),
    ]
    found = client.get(f"/users/{users[1]['id']}", headers=headers)
    assert found.status_code == 200
    assert found.json() == users[1]


def test_get_unknown_user_returns_404_with_detail(client, headers_for):
    response = client.get("/users/999", headers=headers_for("ada@example.com"))

    assert response.status_code == 404
    assert "detail" in response.json()


def test_repeat_list_by_one_user_is_a_cache_hit_and_another_user_is_not(client, runtime, headers_for):
    ada = headers_for("ada@example.com")
    grace = headers_for("grace@example.com")

    client.get("/users/", headers=ada)
    client.get("/users/", headers=ada)
    assert len(runtime.transport.envelopes) == 1

    client.get("/users/", headers=grace)
    assert len(runtime.transport.envelopes) == 2


def test_registration_between_two_lists_appears_in_the_second(client, headers_for):
    headers = headers_for("ada@example.com")
    before = client.get("/users/", headers=headers).json()

    client.post("/users/", json={"name": "Grace", "email": "grace@example.com", "password": "correct horse"})
    after = client.get("/users/", headers=headers).json()

    assert "grace@example.com" not in [user["email"] for user in before]
    assert "grace@example.com" in [user["email"] for user in after]


def test_no_response_body_leaks_password_or_hashes(client, headers_for):
    headers = headers_for("ada@example.com")
    responses = [
        client.post("/users/", json={"name": "Grace", "email": "grace@example.com", "password": "correct horse"}),
        client.post("/auth/login", json={"email": "grace@example.com", "password": "correct horse"}),
        client.get("/users/", headers=headers),
        client.get("/users/1", headers=headers),
        client.get("/users/", headers={"Authorization": "Bearer nope"}),
        client.get("/users/999", headers=headers),
    ]

    assert [r.status_code for r in responses] == [201, 200, 200, 200, 401, 404]
    for response in responses:
        assert "password" not in response.text
        assert "token_hash" not in response.text
