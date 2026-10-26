"""Account authentication tests.

Covers registration, sign-in, role boundaries, token lifecycle and the interaction
between accounts and report ownership. The point of most of these is that a farmer
account must never be able to reach staff capability, and that revoking a token actually
revokes it.
"""

from __future__ import annotations

import io
import time

import jwt
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import auth
import database
import main
import models

ADMIN = {"Authorization": "Bearer test-admin-token"}

PASSWORD = "field-demo-2026"


@pytest.fixture()
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean():
    with database.SessionLocal() as session:
        session.query(models.FollowUp).delete()
        session.query(models.AuditEvent).delete()
        session.query(models.Report).delete()
        session.query(models.User).delete()
        session.commit()
    auth.login_throttle().clear()
    yield


@pytest.fixture()
def leaf() -> bytes:
    rng = np.random.default_rng(3)
    array = rng.integers(60, 180, (320, 320, 3), dtype=np.uint8)
    array[:, :, 1] = np.clip(array[:, :, 1] + 40, 0, 255)
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="JPEG")
    return buffer.getvalue()


def _register(client, email="ravi@example.com", password=PASSWORD, name="Ravi Kumar"):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": name},
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_expert(client, email="expert@example.com"):
    """Create an expert through the admin path, the only route that grants elevation."""
    response = client.post(
        "/api/admin/users",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Dr Expert",
            "role": "expert",
        },
        headers=ADMIN,
    )
    assert response.status_code == 201, response.text
    login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_register_creates_a_farmer_and_signs_them_in(client):
    response = _register(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["role"] == "farmer"
    assert body["user"]["email"] == "ravi@example.com"
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0

    me = client.get("/api/auth/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "ravi@example.com"


def test_password_is_never_stored_in_plaintext(client):
    _register(client)
    with database.SessionLocal() as session:
        user = session.query(models.User).filter_by(email="ravi@example.com").one()
        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$2b$")
        assert auth.verify_password(PASSWORD, user.password_hash)


def test_registration_cannot_grant_staff_role(client):
    """A role in the request body must be ignored, not honoured."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": "sneaky@example.com",
            "password": PASSWORD,
            "display_name": "Sneaky",
            "role": "admin",
        },
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "farmer"

    # And the resulting token cannot reach staff endpoints.
    token = response.json()["access_token"]
    assert client.get("/api/reports", headers=_auth(token)).status_code == 401


def test_duplicate_email_is_refused_without_confirming_it_exists(client):
    _register(client)
    again = _register(client)
    assert again.status_code == 409
    assert "already have an account" in again.json()["detail"]


def test_short_password_is_refused(client):
    response = _register(client, password="short")
    assert response.status_code == 422
    assert "at least" in response.json()["detail"]


def test_invalid_email_is_refused(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": PASSWORD, "display_name": "X Y"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------
def test_login_with_correct_password(client):
    _register(client)
    response = client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_wrong_password_is_generic(client):
    _register(client)
    wrong_password = client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": "wrong-password-1"}
    )
    unknown_email = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    # Identical messages: the response cannot be used to enumerate accounts.
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


def test_repeated_failures_are_throttled(client):
    _register(client)
    for _ in range(8):
        client.post("/api/auth/login", json={"email": "ravi@example.com", "password": "nope-nope-nope"})

    blocked = client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": PASSWORD}
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_successful_login_clears_the_failure_counter(client):
    _register(client)
    for _ in range(3):
        client.post("/api/auth/login", json={"email": "ravi@example.com", "password": "nope-nope-nope"})
    assert client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": PASSWORD}
    ).status_code == 200
    # Counter reset: a fresh burst is allowed again.
    assert auth.login_throttle().blocked("email:ravi@example.com")[0] is False


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------
def test_garbage_and_tampered_tokens_are_rejected(client):
    _register(client)
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401

    login = client.post("/api/auth/login", json={"email": "ravi@example.com", "password": PASSWORD})
    token = login.json()["access_token"]
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    assert client.get("/api/auth/me", headers=_auth(tampered)).status_code == 401


def test_expired_token_is_rejected(client):
    _register(client)
    with database.SessionLocal() as session:
        user = session.query(models.User).filter_by(email="ravi@example.com").one()
        expired = jwt.encode(
            {
                "sub": str(user.id),
                "role": "farmer",
                "ver": user.token_version,
                "iat": int(time.time()) - 7200,
                "exp": int(time.time()) - 3600,
            },
            auth._signing_key(),
            algorithm=auth.ALGORITHM,
        )
    assert client.get("/api/auth/me", headers=_auth(expired)).status_code == 401


def test_logout_revokes_existing_tokens(client):
    token = _register(client).json()["access_token"]
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200

    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


def test_password_change_revokes_tokens_and_updates_the_hash(client):
    token = _register(client).json()["access_token"]
    response = client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "another-good-one-9"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401

    assert client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": PASSWORD}
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "ravi@example.com", "password": "another-good-one-9"},
    ).status_code == 200


def test_password_change_requires_the_current_password(client):
    token = _register(client).json()["access_token"]
    response = client.post(
        "/api/auth/password",
        json={"current_password": "wrong-password-x", "new_password": "another-good-one-9"},
        headers=_auth(token),
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
def test_farmer_cannot_verify_reports(client, leaf):
    token = _register(client).json()["access_token"]
    created = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Ravi Kumar",
            "latitude": "16.5",
            "longitude": "80.6",
        },
        headers=_auth(token),
    )
    assert created.status_code == 200
    report_id = created.json()["report"]["id"]

    response = client.patch(
        f"/api/reports/{report_id}/verify",
        json={"action": "confirm", "expert_name": "Dr Rao"},
        headers=_auth(token),
    )
    assert response.status_code == 401


def test_expert_account_can_verify(client, leaf):
    expert = _make_expert(client)
    farmer = _register(client).json()["access_token"]
    created = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Ravi Kumar",
            "latitude": "16.5",
            "longitude": "80.6",
            "request_expert_review": "true",
        },
        headers=_auth(farmer),
    )
    report_id = created.json()["report"]["id"]

    response = client.patch(
        f"/api/reports/{report_id}/verify",
        json={"action": "confirm", "expert_name": "Dr Expert", "expert_notes": "Agreed"},
        headers=_auth(expert),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Expert_Confirmed"

    with database.SessionLocal() as session:
        event = session.query(models.AuditEvent).one()
        # Identity comes from the account, not the name in the body.
        assert event.actor_role == "expert"
        assert event.actor_name == "Dr Expert"


def test_expert_account_can_read_the_queue(client):
    expert = _make_expert(client)
    assert client.get("/api/reports", headers=_auth(expert)).status_code == 200


def test_only_an_admin_can_create_staff(client):
    expert = _make_expert(client)
    response = client.post(
        "/api/admin/users",
        json={
            "email": "second@example.com",
            "password": PASSWORD,
            "display_name": "Second",
            "role": "admin",
        },
        headers=_auth(expert),
    )
    assert response.status_code == 403


def test_admin_route_requires_authentication(client):
    response = client.post(
        "/api/admin/users",
        json={
            "email": "x@example.com",
            "password": PASSWORD,
            "display_name": "X Y",
            "role": "expert",
        },
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Accounts and report ownership
# ---------------------------------------------------------------------------
def test_signed_in_farmer_owns_their_report(client, leaf):
    token = _register(client).json()["access_token"]
    created = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Ravi Kumar",
            "latitude": "16.5",
            "longitude": "80.6",
        },
        headers=_auth(token),
    ).json()

    report_id = created["report"]["id"]
    with database.SessionLocal() as session:
        report = session.get(models.Report, report_id)
        assert report.owner_user_id is not None

    # Readable with the account token alone, no access code needed.
    assert client.get(f"/api/reports/{report_id}", headers=_auth(token)).status_code == 200
    # And by reference too.
    assert client.get(
        f"/api/reports/by-reference/{created['report']['reference']}", headers=_auth(token)
    ).status_code == 200


def test_one_farmer_cannot_read_another_farmers_report(client, leaf):
    owner = _register(client, email="owner@example.com").json()["access_token"]
    intruder = _register(client, email="intruder@example.com").json()["access_token"]

    report_id = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Owner",
            "latitude": "16.5",
            "longitude": "80.6",
        },
        headers=_auth(owner),
    ).json()["report"]["id"]

    assert client.get(f"/api/reports/{report_id}", headers=_auth(intruder)).status_code == 404
    assert client.get(f"/api/reports/{report_id}", headers=_auth(owner)).status_code == 200


def test_anonymous_submission_still_works(client, leaf):
    """The access-code path must keep working: not everyone will make an account."""
    created = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Anonymous",
            "latitude": "16.5",
            "longitude": "80.6",
        },
    ).json()
    assert created["access_token"]
    assert client.get(
        f"/api/reports/{created['report']['id']}",
        headers={"X-Report-Token": created["access_token"]},
    ).status_code == 200


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------
def test_deleting_an_account_anonymises_its_reports(client, leaf):
    token = _register(client).json()["access_token"]
    report_id = client.post(
        "/api/analyze",
        files={"image": ("leaf.jpg", leaf, "image/jpeg")},
        data={
            "crop_type": "Tomato",
            "farmer_name": "Ravi Kumar",
            "latitude": "16.5",
            "longitude": "80.6",
        },
        headers=_auth(token),
    ).json()["report"]["id"]

    response = client.request(
        "DELETE", "/api/auth/account", json={"password": PASSWORD}, headers=_auth(token)
    )
    assert response.status_code == 200

    with database.SessionLocal() as session:
        report = session.get(models.Report, report_id)
        assert report is not None, "the report is kept for the outbreak map"
        assert report.farmer_name == "Deleted account"
        assert report.owner_user_id is None

    # The account can no longer sign in, and the old token is dead.
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
    assert client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": PASSWORD}
    ).status_code == 401


def test_deleting_an_account_requires_the_password(client):
    token = _register(client).json()["access_token"]
    response = client.request(
        "DELETE", "/api/auth/account", json={"password": "wrong-password-x"}, headers=_auth(token)
    )
    assert response.status_code == 401
