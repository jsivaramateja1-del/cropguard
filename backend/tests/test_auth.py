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
        session.query(models.ReportImage).delete()
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

    # And the resulting token cannot reach staff endpoints. 403, not 401: the account is
    # real and signed in, it simply has no staff role.
    token = response.json()["access_token"]
    assert client.get("/api/reports", headers=_auth(token)).status_code == 403


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
    # Authenticated but not staff: forbidden, not unauthenticated.
    assert response.status_code == 403


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
        # Staff creation is audited too now, so select the verification event itself.
        events = session.query(models.AuditEvent).filter_by(action="verify").all()
        assert len(events) == 1
        event = events[0]
        # Identity comes from the account, not the name in the body.
        assert event.actor_role == "expert"
        assert event.actor_name == "Dr Expert"
        assert event.report_id == report_id


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


# ---------------------------------------------------------------------------
# Expert approval workflow
# ---------------------------------------------------------------------------
def _apply(client, token, justification="I advise 40 farmers on tomato and rice in Andhra Pradesh."):
    return client.post(
        "/api/expert-requests",
        json={"justification": justification, "affiliation": "KVK Guntur", "region_name": "Guntur"},
        headers=_auth(token),
    )


def test_farmer_can_request_expert_access(client):
    token = _register(client).json()["access_token"]
    response = _apply(client, token)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["applicant_email"] is None, "the applicant does not need their own email echoed"

    mine = client.get("/api/expert-requests/me", headers=_auth(token))
    assert mine.status_code == 200
    assert len(mine.json()["applications"]) == 1


def test_applying_requires_an_account(client):
    response = client.post(
        "/api/expert-requests", json={"justification": "x" * 30}
    )
    assert response.status_code == 401


def test_justification_must_be_substantial(client):
    token = _register(client).json()["access_token"]
    response = _apply(client, token, justification="please")
    assert response.status_code == 422


def test_duplicate_pending_requests_are_refused(client):
    token = _register(client).json()["access_token"]
    assert _apply(client, token).status_code == 201
    second = _apply(client, token)
    assert second.status_code == 409
    assert "awaiting review" in second.json()["detail"]


def test_staff_do_not_need_to_apply(client):
    expert = _make_expert(client)
    response = _apply(client, expert)
    assert response.status_code == 409
    assert "already recognised" in response.json()["detail"]


def test_the_queue_is_admin_only(client):
    farmer = _register(client).json()["access_token"]
    _apply(client, farmer)

    # Anonymous
    assert client.get("/api/expert-requests").status_code == 401
    # An expert account is staff but not an admin.
    expert = _make_expert(client)
    assert client.get("/api/expert-requests", headers=_auth(expert)).status_code == 403
    # The applicant cannot see the queue either.
    assert client.get("/api/expert-requests", headers=_auth(farmer)).status_code == 403


def test_admin_sees_the_queue_with_applicant_details(client):
    farmer = _register(client).json()["access_token"]
    _apply(client, farmer)

    response = client.get("/api/expert-requests", headers=ADMIN)
    assert response.status_code == 200
    entries = response.json()["applications"]
    assert len(entries) == 1
    assert entries[0]["applicant_email"] == "ravi@example.com"
    assert entries[0]["applicant_name"] == "Ravi Kumar"


def test_admin_approval_promotes_the_account(client):
    farmer_token = _register(client).json()["access_token"]
    application_id = _apply(client, farmer_token).json()["id"]

    response = client.post(
        f"/api/expert-requests/{application_id}/approve",
        json={"notes": "Verified with the local KVK."},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    assert response.json()["reviewed_by"] == "Test Administrator"

    # The token issued while they were a farmer must no longer work: approval bumps the
    # token version precisely so an old farmer-scoped token cannot become an expert one.
    assert client.get("/api/auth/me", headers=_auth(farmer_token)).status_code == 401

    # Signing in again yields the expert role and access to the review queue.
    login = client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "expert"
    new_token = login.json()["access_token"]
    assert client.get("/api/reports", headers=_auth(new_token)).status_code == 200


def test_approval_is_audited_without_a_report(client):
    farmer_token = _register(client).json()["access_token"]
    application_id = _apply(client, farmer_token).json()["id"]
    client.post(f"/api/expert-requests/{application_id}/approve", json={}, headers=ADMIN)

    with database.SessionLocal() as session:
        event = session.query(models.AuditEvent).filter_by(action="approve_expert").one()
        assert event.report_id is None
        assert event.actor_role == "admin"
        assert "expert" in event.new_state


def test_a_request_cannot_be_approved_twice(client):
    farmer_token = _register(client).json()["access_token"]
    application_id = _apply(client, farmer_token).json()["id"]
    assert client.post(
        f"/api/expert-requests/{application_id}/approve", json={}, headers=ADMIN
    ).status_code == 200
    again = client.post(
        f"/api/expert-requests/{application_id}/approve", json={}, headers=ADMIN
    )
    assert again.status_code == 409


def test_admin_can_reject_with_notes_and_the_role_is_unchanged(client):
    farmer_token = _register(client).json()["access_token"]
    application_id = _apply(client, farmer_token).json()["id"]

    response = client.post(
        f"/api/expert-requests/{application_id}/reject",
        json={"notes": "Could not verify the affiliation."},
        headers=ADMIN,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["review_notes"] == "Could not verify the affiliation."

    me = client.get("/api/auth/me", headers=_auth(farmer_token))
    assert me.status_code == 200
    assert me.json()["role"] == "farmer", "rejection must not change the role"

    # And they may apply again after a rejection.
    assert _apply(client, farmer_token).status_code == 201


def test_an_expert_cannot_approve_requests(client):
    farmer_token = _register(client).json()["access_token"]
    application_id = _apply(client, farmer_token).json()["id"]
    expert = _make_expert(client)
    response = client.post(
        f"/api/expert-requests/{application_id}/approve", json={}, headers=_auth(expert)
    )
    assert response.status_code == 403


def test_approving_an_unknown_request_is_a_clean_404(client):
    response = client.post("/api/expert-requests/9999/approve", json={}, headers=ADMIN)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Password reset (Brevo email)
# ---------------------------------------------------------------------------
def _configure_email(monkeypatch, sender="no-reply@cropguard.test"):
    """Pretend Brevo is configured, so the flow runs without a real API key."""
    from config import settings

    monkeypatch.setattr(settings, "brevo_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "email_from", sender, raising=False)
    monkeypatch.setattr(settings, "public_app_url", "https://cropguard.test", raising=False)


def _capture_email(monkeypatch):
    """Replace the Brevo call and record what would have been sent."""
    sent = []

    class Result:
        delivered = True
        detail = "accepted"
        message_id = "test"

        def as_dict(self):
            return {"delivered": True, "detail": "accepted"}

    def fake_send(to_address, subject, html, text):
        sent.append({"to": to_address, "subject": subject, "html": html, "text": text})
        return Result()

    import emailer

    monkeypatch.setattr(emailer, "send_email", fake_send)
    return sent


def _reset_token_from(sent, index: int = -1) -> str:
    """Extract the token from a captured email. Defaults to the most recent one."""
    import re

    match = re.search(r"token=([A-Za-z0-9_\-]+)", sent[index]["text"])
    assert match, "the email should contain a reset link"
    return match.group(1)


def test_forgot_password_sends_a_branded_email(client, monkeypatch):
    _register(client)
    _configure_email(monkeypatch)
    sent = _capture_email(monkeypatch)

    response = client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    assert response.status_code == 202
    assert len(sent) == 1
    assert sent[0]["to"] == "ravi@example.com"
    assert sent[0]["subject"] == "Reset your CropGuard password"

    html = sent[0]["html"]
    # The email carries the product name and a description, and a working link.
    assert "CropGuard" in html
    assert "What is CropGuard?" in html
    assert "crop disease screening" in html.lower()
    assert "reset-password?token=" in html
    assert "30 minutes" in html
    # And a plain-text alternative for clients that do not render HTML.
    assert "Choose a new password" in sent[0]["text"] or "reset your password" in sent[0]["text"].lower()


def test_forgot_password_does_not_reveal_whether_the_account_exists(client, monkeypatch):
    _register(client)
    _configure_email(monkeypatch)
    _capture_email(monkeypatch)

    known = client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json()["detail"] == unknown.json()["detail"]


def test_reset_link_sets_a_new_password_and_signs_sessions_out(client, monkeypatch):
    token = _register(client).json()["access_token"]
    _configure_email(monkeypatch)
    sent = _capture_email(monkeypatch)

    client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    reset_token = _reset_token_from(sent)

    response = client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": "a-brand-new-password-1"},
    )
    assert response.status_code == 200
    assert response.json()["signed_out_everywhere"] is True

    # Old sessions are dead, old password rejected, new one works.
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
    assert client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": PASSWORD}
    ).status_code == 401
    assert client.post(
        "/api/auth/login", json={"email": "ravi@example.com", "password": "a-brand-new-password-1"}
    ).status_code == 200


def test_reset_token_is_single_use(client, monkeypatch):
    _register(client)
    _configure_email(monkeypatch)
    sent = _capture_email(monkeypatch)
    client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    reset_token = _reset_token_from(sent)

    assert client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": "first-new-password-1"},
    ).status_code == 200
    second = client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": "second-new-password-2"},
    )
    assert second.status_code == 400
    assert "invalid or has expired" in second.json()["detail"]


def test_requesting_again_invalidates_the_previous_link(client, monkeypatch):
    _register(client)
    _configure_email(monkeypatch)
    sent = _capture_email(monkeypatch)

    client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    first = _reset_token_from(sent, 0)
    client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    second = _reset_token_from(sent, 1)
    assert first != second

    # The older link must not work any more.
    assert client.post(
        "/api/auth/reset-password",
        json={"token": first, "new_password": "another-password-here-1"},
    ).status_code == 400


def test_expired_token_is_refused(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    _register(client)
    _configure_email(monkeypatch)
    sent = _capture_email(monkeypatch)
    client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    reset_token = _reset_token_from(sent)

    with database.SessionLocal() as session:
        record = session.query(models.PasswordReset).one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()

    response = client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": "another-password-here-1"},
    )
    assert response.status_code == 400


def test_reset_token_is_stored_only_as_a_hash(client, monkeypatch):
    _register(client)
    _configure_email(monkeypatch)
    sent = _capture_email(monkeypatch)
    client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    reset_token = _reset_token_from(sent)

    with database.SessionLocal() as session:
        record = session.query(models.PasswordReset).one()
        assert record.token_hash != reset_token
        import security

        assert record.token_hash == security.hash_token(reset_token)


def test_weak_new_password_is_refused(client, monkeypatch):
    _register(client)
    _configure_email(monkeypatch)
    sent = _capture_email(monkeypatch)
    client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    reset_token = _reset_token_from(sent)

    response = client.post(
        "/api/auth/reset-password", json={"token": reset_token, "new_password": "short"}
    )
    assert response.status_code == 422


def test_reset_says_so_when_email_is_not_configured(client):
    """No fake success: without Brevo configured, the API admits it cannot send."""
    _register(client)
    response = client.post("/api/auth/forgot-password", json={"email": "ravi@example.com"})
    assert response.status_code == 503
    assert "email delivery is not configured" in response.json()["detail"]


def test_forgot_password_is_rate_limited(client, monkeypatch):
    _configure_email(monkeypatch)
    _capture_email(monkeypatch)
    for _ in range(9):
        client.post("/api/auth/forgot-password", json={"email": "spam@example.com"})
    blocked = client.post("/api/auth/forgot-password", json={"email": "spam@example.com"})
    assert blocked.status_code == 429


# ---------------------------------------------------------------------------
# Retiring the legacy shared staff token
# ---------------------------------------------------------------------------
def test_legacy_staff_token_works_while_enabled(client, leaf):
    from config import settings

    assert settings.allow_legacy_staff_token is True
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
    ).json()
    response = client.patch(
        f"/api/reports/{created['report']['id']}/verify",
        json={"action": "confirm", "expert_name": "Dr Legacy"},
        headers={"Authorization": "Bearer test-expert-token"},
    )
    assert response.status_code == 200


def test_legacy_token_is_refused_once_disabled(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "allow_legacy_staff_token", False, raising=False)
    assert client.get(
        "/api/reports", headers={"Authorization": "Bearer test-expert-token"}
    ).status_code == 401


def test_account_tokens_still_work_when_the_legacy_token_is_disabled(client, monkeypatch):
    """The point of the migration: disabling the shared secret must not lock out experts."""
    from config import settings

    expert = _make_expert(client)
    monkeypatch.setattr(settings, "allow_legacy_staff_token", False, raising=False)

    assert client.get("/api/reports", headers=_auth(expert)).status_code == 200


def test_health_reports_whether_the_legacy_token_is_enabled(client):
    body = client.get("/health").json()
    assert body["checks"]["auth"]["legacy_staff_token_enabled"] is True
