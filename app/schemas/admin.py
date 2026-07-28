from datetime import datetime

from pydantic import BaseModel


class UserAdminOut(BaseModel):
    """Admin-facing user listing. Never includes `password_hash` or any
    other credential material - only the current login name and
    credential-setup state, so an admin can see a temporary account's
    updated username without asking the user directly."""

    id: str
    username: str
    role: str
    is_active: bool
    alumni_id: str | None = None
    must_change_credentials: bool
    temporary_account_created_at: datetime | None = None
    credentials_updated_at: datetime | None = None
    previous_username: str | None = None
    username_changed_at: datetime | None = None


class RowError(BaseModel):
    row: int
    error: str


class ImportResult(BaseModel):
    organization: str
    filename: str | None = None
    created: int
    updated: int
    # Existing alumni row matched by this import but with no field changes.
    unchanged: int = 0
    # Previously-active alumni NOT present in this upload; deactivated
    # (is_active=False), never physically deleted. "archived" is a legacy
    # alias for the same count.
    deactivated: int = 0
    archived: int = 0
    skipped: int
    failed: int
    # Legacy alias for active_database_total, kept for backward compatibility.
    # Always equals the ACTIVE dataset count after a successful replace-mode
    # import - never the cumulative historical row count.
    database_total: int
    # True count of ACTIVE alumni for this organization after this import
    # (re-queried after commit, not an in-memory counter) - this is the
    # single source of truth the dashboard/analytics must match exactly.
    active_database_total: int = 0
    # Total alumni_organizations rows for this organization regardless of
    # is_active (includes archived history). Never use as the dashboard total.
    historical_database_total: int = 0
    row_errors: list[RowError]
    csv_import_id: str | None = None
    status: str = "complete"

    # --- Import accounting (replace-mode single-dataset behavior) ---
    # csv_physical_lines / csv_header_rows / csv_data_rows describe the raw
    # uploaded file itself (e.g. 250 physical lines incl. 1 header -> 249
    # data rows), independent of how many of those rows parsed cleanly.
    csv_physical_lines: int = 0
    csv_header_rows: int = 0
    csv_data_rows: int = 0
    csv_valid_rows: int = 0
    csv_invalid_rows: int = 0
    csv_duplicate_rows: int = 0
    # Legacy aliases for the same counts, kept for backward compatibility.
    csv_rows_received: int = 0
    csv_rows_valid: int = 0
    csv_rows_invalid: int = 0
    # Canonical short names (dynamic - always reflect the actual uploaded
    # file, never hardcoded).
    rows_received: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    duplicate_rows: int = 0

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
    rows_with_raw_city: int = 0
    rows_with_raw_state: int = 0
    rows_with_constructed_location: int = 0

    # --- Additional temporary debugging fields (first data row only) ---
    first_row_original: dict = {}
    first_row_normalized: dict = {}
    selected_company_column: str | None = None
    selected_location_column: str | None = None
    selected_city_column: str | None = None
    selected_state_column: str | None = None
    selected_university_column: str | None = None
    selected_degree_column: str | None = None
    selected_major_column: str | None = None
    selected_graduation_year_column: str | None = None

    # --- Duplicate-matching audit (temporary) ---
    # One entry per newly created alumni row with normalized identifiers
    # and why matching failed. Used to audit recurring create-on-reimport.
    newly_created_identifiers: list[dict] = []
    duplicate_candidates_found: int = 0
    # Temporary deploy marker: must equal "replace-v2" when Render is
    # running the new replacement import path.
    import_logic_version: str = "replace-v2"


class CurrentImportOut(BaseModel):
    """Metadata describing the single authoritative dataset currently
    powering the dashboard - i.e. the latest successfully committed CSV
    import for the (single, default) organization."""

    import_logic_version: str = "replace-v2"
    csv_import_id: str | None = None
    filename: str | None = None
    uploaded_at: str | None = None
    imported_at: str | None = None  # alias for uploaded_at
    uploaded_by_user_id: str | None = None
    csv_data_rows: int = 0
    csv_rows_received: int = 0  # legacy alias for csv_data_rows
    rows_received: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    active_database_total: int = 0
    status: str | None = None


class NormalizeLocationsResult(BaseModel):
    organization: str | None
    processed: int
    updated: int
    unchanged: int
    dry_run: bool
