import os
import sys
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent / "test.db"
TEST_USERS_CSV_PATH = Path(__file__).resolve().parent / "test_users.csv"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["USERS_CSV_PATH"] = str(TEST_USERS_CSV_PATH)
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production-use-only-testing"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:5173"
os.environ["ENVIRONMENT"] = "development"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
os.environ["LOGIN_RATE_LIMIT"] = "1000/minute"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402,F401
from app.csv_user_store import CsvUserRecord, save_users  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.security import hash_password  # noqa: E402

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "AdminPass123!"
ALUMNI_USERNAME = "jdoe"
ALUMNI_PASSWORD = "AlumniPass123!"


@pytest.fixture(scope="function", autouse=True)
def _fresh_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    save_users(
        {
            ADMIN_USERNAME: CsvUserRecord(
                username=ADMIN_USERNAME, password_hash=hash_password(ADMIN_PASSWORD), role="admin"
            ),
            ALUMNI_USERNAME: CsvUserRecord(
                username=ALUMNI_USERNAME, password_hash=hash_password(ALUMNI_PASSWORD), role="alumni"
            ),
        }
    )
    yield
    Base.metadata.drop_all(bind=engine)
    TEST_USERS_CSV_PATH.unlink(missing_ok=True)


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


def login(client, username=ADMIN_USERNAME, password=ADMIN_PASSWORD):
    response = client.post("/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def admin_token(client):
    return login(client, ADMIN_USERNAME, ADMIN_PASSWORD)


@pytest.fixture()
def alumni_token(client):
    return login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
