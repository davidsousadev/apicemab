from pydantic import BaseModel, Field as PydanticField
from sqlmodel import SQLModel, Field
from typing import Optional


class BaseUser(SQLModel):
    token: str


class SignUpUserRequest(BaseUser):
    # Optional para manter clientes antigos compatíveis. Ao renovar token,
    # permite que a API mova os vínculos do token antigo para o novo.
    token_anterior: Optional[str] = None


class UserData(BaseUser):
    pass


class IncludeUser(BaseUser):
    pass


class UserM(IncludeUser, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class SignInUserRequest(SQLModel):
    pass


class UpdateUserRequest(BaseModel):
    pass


class UserResponse(BaseModel):
    id: int
    token: str

    class Config:
        from_attributes = True
