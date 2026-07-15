"""
Seed data for organizations and location aliases. Invoked by
`scripts/seed_organizations.py` - kept out of route files per the project
spec ("do not hardcode every alias permanently inside route files").
"""
from sqlalchemy.orm import Session

from app.models.location_alias import LocationAlias
from app.models.organization import Organization
from app.services.location_aliases_seed_data import LOCATION_ALIAS_SEED_DATA

ORGANIZATIONS_SEED_DATA = [
    {"name": "FSU College of Communication and Information", "slug": "fsu-cci"},
    {"name": "FSU STARS", "slug": "fsu-stars"},
    {"name": "STARS National", "slug": "stars-national"},
]


def seed_organizations(db: Session) -> list[Organization]:
    created_or_existing: list[Organization] = []
    for entry in ORGANIZATIONS_SEED_DATA:
        organization = db.query(Organization).filter(Organization.slug == entry["slug"]).first()
        if organization is None:
            organization = Organization(name=entry["name"], slug=entry["slug"])
            db.add(organization)
        created_or_existing.append(organization)
    db.commit()
    return created_or_existing


def seed_location_aliases(db: Session) -> int:
    inserted = 0
    for entry in LOCATION_ALIAS_SEED_DATA:
        existing = db.query(LocationAlias).filter(LocationAlias.alias == entry["alias"]).first()
        if existing is not None:
            continue
        db.add(
            LocationAlias(
                alias=entry["alias"],
                canonical_city=entry.get("canonical_city"),
                canonical_state=entry.get("canonical_state"),
                state_code=entry.get("state_code"),
                canonical_country=entry.get("canonical_country"),
                metro_area=entry.get("metro_area"),
                latitude=entry.get("latitude"),
                longitude=entry.get("longitude"),
            )
        )
        inserted += 1
    db.commit()
    return inserted
