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
    # True count of this organization's alumni rows in the database,
    # re-queried after commit - not an in-memory counter - so a caller
    # can independently confirm the import actually persisted.
    database_total: int
    row_errors: list[RowError]
    csv_import_id: str | None = None

    # --- Temporary CSV-mapping diagnostics ---
    # These make it possible to see, directly from the import response,
    # whether the uploaded