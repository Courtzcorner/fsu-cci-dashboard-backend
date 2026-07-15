from pydantic import BaseModel


class RowError(BaseModel):
    row: int
    error: str


class ImportResult(BaseModel):
    organization: str
    created: int
    updated: int
    skipped: int
    failed: int
    row_errors: list[RowError]


class NormalizeLocationsResult(BaseModel):
    organization: str | None
    processed: int
    updated: int
    unchanged: int
    dry_run: bool
