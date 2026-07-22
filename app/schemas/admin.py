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
    # whether the uploaded spreadsheet's headers were actually recognized
    # and how many rows ended up with each key field populated. Safe to
    # remove once header-mapping issues are no longer a concern.
    recognized_headers: list[str] = []
    unrecognized_headers: list[str] = []
    rows_with_graduation_year: int = 0
    rows_with_major: int = 0
    rows_with_university: int = 0
    rows_with_job_title: int = 0
    rows_with_company: int = 0
    rows_with_location: int = 0
    rows_with_city: int = 0
    rows_with_state: int = 0

    # --- Additional temporary debugging fields (first data row only) ---
    first_row_original: dict = {}
    first_row_normalized: dict = {}
    selected_company_column: str | None = None
    selected_location_column: str | None = None
    selected_university_column: str | None = None
    selected_degree_column: str | None = None
    selected_major_column: str | None = None
    selected_graduation_year_column: str | None = None


class NormalizeLocationsResult(BaseModel):
    organization: str | None
    processed: int
    updated: int
    unchanged: int
    dry_run: bool
