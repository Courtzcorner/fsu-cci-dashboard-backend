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

# Phase 1 multi-institution context metadata (see app.models.organization
# context_type/theme_key). Keyed by slug so seed_organizations() below can
# apply it idempotently to ONLY these three known, already-seeded
# organizations - never renaming/merging/deleting anything, and never
# touching `name`/`slug` for an already-existing row. `fsu-cci` keeps a
# null theme_key: it is a legacy organization whose production data
# ownership has not yet been confirmed (see architecture analysis), so no
# frontend theme is assigned to it in this phase.
ORGANIZATION_CONTEXT_METADATA = {
    "stars-national": {"context_type": "national", "theme_key": "stars-national"},
    "fsu-stars": {"context_type": "institution", "theme_key": "stars-fsu"},
    "fsu-cci": {"context_type": "institution", "theme_key": None},
}


def seed_organizations(db: Session) -> list[Organization]:
    created_or_existing: list[Organization] = []
    for entry in ORGANIZATIONS_SEED_DATA:
        organization = db.query(Organization).filter(Organization.slug == entry["slug"]).first()
        if organization is None:
            organization = Organization(name=entry["name"], slug=entry["slug"])
            db.add(organization)

        metadata = ORGANIZATION_CONTEXT_METADATA.get(entry["slug"])
        if metadata is not None:
            organization.context_type = metadata["context_type"]
            if metadata["theme_key"] is not None:
                organization.theme_key = metadata["theme_key"]

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
