from pydantic import BaseModel


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str

    model_config = {"from_attributes": True}
