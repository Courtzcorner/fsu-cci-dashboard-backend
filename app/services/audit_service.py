"""Helper for writing durable AuditLog rows. Always call this inside the
same DB transaction as the change it describes, so the audit trail and the
underlying data are committed together."""
import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def record_audit_log(
    db: Session,
    *,
    user_id: Optional[str],
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user_id,
        organization_id=organization_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details_json=json.dumps(details) if details is not None else None,
    )
    db.add(entry)
    return entry
