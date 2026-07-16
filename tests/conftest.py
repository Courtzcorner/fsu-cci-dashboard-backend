import os
import sys
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent / "test.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production-use-only-testing"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:5173"
os.environ["ENVIRONMENT"] = "development"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
os.environ["LOGIN_RATE_LIMIT"] = "1000/minute"
os.environ["UPLOADS_DIR"] = str(Path(__file__).resolve().parent / "test_uploads")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402,F401
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.alumni import Alumni, AlumniOrganization  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.user import User  # noqa: E402
from app.security import hash_password  # noqa: E402

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "AdminPass123!"
ALUMNI_USERNAME = "jdoe"
ALUMNI_PASSWORD = "AlumniPass123!"
OTHER_ALUMNI_USERNAME = "asmith"
OTHER_ALUMNI_PASSWORD = "OtherPass123!"


@pytest.fixture(scope="function", autouse=True)
def _fresh_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def organization(db_session):
    org = Organization(name="FSU College of Communication and Information", slug="fsu-cci")
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture()
def other_organization(db_session):
    org = Organization(name="STARS National", slug="stars-national")
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture()
def admin_user(db_session, organization):
    user = User(username=ADMIN_USERNAME, password_hash=hash_password(ADMIN_PASSWORD), role="admin")
    db_session.add(user)
    db_session.commit()
    return user


def _make_alumni_with_user(db_session, organization, username, password, **alumni_overrides):
    defaults = dict(first_name="Jane", last_name="Doe", full_name="Jane Doe")
    defaults.update(alumni_overrides)
    alumni = Alumni(**defaults)
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    user = User(username=username, password_hash=hash_password(password), role="alumni", alumni_id=alumni.id)
    db_session.add(user)
    db_session.commit()
    return user, alumni


@pytest.fixture()
def alumni_user(db_session, organization):
    user, alumni = _make_alumni_with_user(
        db_session, organization, ALUMNI_USERNAME, ALUMNI_PASSWORD, first_name="Jordan", last_name="Doe", full_name="Jordan Doe"
    )
    return user


@pytest.fixture()
def alumni_record(db_session, organization, alumni_user):
    return db_session.get(Alumni, alumni_user.alumni_id)


@pytest.fixture()
def other_alumni_user(db_session, organization):
    user, alumni = _make_alumni_with_user(
        db_session, organization, OTHER_ALUMNI_USERNAME, OTHER_ALUMNI_PASSWORD,
        first_name="Alex", last_name="Smith", full_name="Alex Smith",
    )
    return user


def login(client, username, password):
    response = client.post("/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]
