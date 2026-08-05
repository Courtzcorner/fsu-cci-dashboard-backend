from pydantic import BaseModel


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str

    model_config = {"from_attributes": True}


class AvailableContextOut(BaseModel):
    """One dashboard context (organization) the current user may switch
    into. Deliberately excludes any internal database ID - the frontend
    should only ever reference an organization by its slug."""

    slug: str
    display_name: str
    context_type: str
    role: str
    has_active_dataset: bool
    can_import: bool
    theme_key: str | None = None


class AvailableContextsResponse(BaseModel):
    contexts: list[AvailableContextOut]
