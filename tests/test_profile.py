import io

from app.models.alumni import Alumni
from app.models.legal_name import LegalNameChangeRequest
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALUMNI_PASSWORD,
    ALUMNI_USERNAME,
    OTHER_ALUMNI_PASSWORD,
    OTHER_ALUMNI_USERNAME,
    login,
)


def test_get_my_profile_returns_own_alumni_record(client, alumni_user, alumni_record, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get("/me/profile", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == alumni_record.id
    assert body["full_name"] == alumni_record.full_name


def test_admin_without_alumni_link_gets_404_on_profile(client, admin_user, organization):
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = client.get("/me/profile", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_patch_my_profile_updates_allowed_fields(client, alumni_user, alumni_record, organization, db_session):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.patch(
        "/me/profile",
        json={"job_title": "Senior Engineer", "company": "Acme Corp", "bio": "Building things.", "job_location": "Brooklyn, NY"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job_title"] == "Senior Engineer"
    assert body["company"] == "Acme Corp"
    assert body["bio"] == "Building things."
    assert body["city"] == "Brooklyn"
    assert body["job_location"] == "Brooklyn, NY"

    db_session.refresh(alumni_record)
    assert alumni_record.job_title == "Senior Engineer"
    assert alumni_record.city == "Brooklyn"


def test_patch_my_profile_rejects_disallowed_fields(client, alumni_user, alumni_record, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    for forbidden_field, value in [
        ("role", "admin"),
        ("verification_status", "verified"),
        ("legal_name_verified", True),
        ("id", "some-other-id"),
        ("organization_id", "some-other-org"),
    ]:
        response = client.patch(
            "/me/profile",
            json={forbidden_field: value},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422, f"expected rejection for field '{forbidden_field}'"


def test_alumni_cannot_edit_another_alumni_profile(client, alumni_user, other_alumni_user, organization, db_session):
    """There is no endpoint that takes a target alumni id from an alumni
    role - PATCH /me/profile always resolves to the caller's own record."""
    other_alumni = db_session.get(Alumni, other_alumni_user.alumni_id)
    original_job_title = other_alumni.job_title

    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.patch(
        "/me/profile",
        json={"job_title": "Hacked Title"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    db_session.refresh(other_alumni)
    assert other_alumni.job_title == original_job_title

    other_token = login(client, OTHER_ALUMNI_USERNAME, OTHER_ALUMNI_PASSWORD)
    other_response = client.get("/me/profile", headers={"Authorization": f"Bearer {other_token}"})
    assert other_response.json()["job_title"] != "Hacked Title"


def test_profile_photo_upload_validates_file_type(client, alumni_user, alumni_record, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.post(
        "/me/profile/photo",
        files={"file": ("not-an-image.txt", io.BytesIO(b"hello"), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_profile_photo_upload_succeeds_for_valid_image(client, alumni_user, alumni_record, organization, db_session):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    tiny_png = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
        "de0000000c4944415478da6360000002000155e621bc0000000049454e44ae426082"
    )
    response = client.post(
        "/me/profile/photo",
        files={"file": ("selfie.png", io.BytesIO(tiny_png), "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    url = response.json()["profile_image_url"]
    assert url.startswith("http")
    assert "/uploads/profile-photos/" in url

    db_session.refresh(alumni_record)
    assert alumni_record.profile_image_url == url


def test_legal_name_change_request_flow(client, alumni_user, alumni_record, admin_user, organization, db_session):
    alumni_token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    create_response = client.post(
        "/me/legal-name-change-request",
        json={"requested_legal_name": "Jordan Alexander Doe", "reason": "Legal name change"},
        headers={"Authorization": f"Bearer {alumni_token}"},
    )
    assert create_response.status_code == 201
    request_id = create_response.json()["id"]
    assert create_response.json()["status"] == "pending_review"

    db_session.refresh(alumni_record)
    assert alumni_record.legal_name_verification_status == "change_requested"

    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    list_response = client.get(
        "/admin/legal-name-requests", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert list_response.status_code == 200
    assert any(r["id"] == request_id for r in list_response.json())

    approve_response = client.post(
        f"/admin/legal-name-requests/{request_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"

    db_session.refresh(alumni_record)
    assert alumni_record.verified_legal_name == "Jordan Alexander Doe"
    assert alumni_record.legal_name_verified is True
    assert alumni_record.legal_name_verification_status == "verified"

    request = db_session.get(LegalNameChangeRequest, request_id)
    assert request.reviewed_by_user_id == admin_user.id


def test_alumni_cannot_review_legal_name_requests(client, alumni_user, organization):
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    response = client.get("/admin/legal-name-requests", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
