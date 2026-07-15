"""
Seed the `organizations` table (fsu-cci, fsu-stars, stars-national) and the
`location_aliases` table.

Usage:
    python scripts/seed_organizations.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.seed.seed_data import seed_location_aliases, seed_organizations  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        organizations = seed_organizations(db)
        aliases_inserted = seed_location_aliases(db)
        print("Organizations available:")
        for org in organizations:
            print(f"  - {org.slug}: {org.name}")
        print(f"Inserted {aliases_inserted} new location aliases.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
