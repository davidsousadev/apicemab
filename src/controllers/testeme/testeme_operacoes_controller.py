import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timedelta, timezone
from typing import Annotated
from sqlmodel import Session, select
from passlib.context import CryptContext

from src.database import get_engine
from src.auth_utils import (
    get_logged_user,
    hash_password,
    SECRET_KEY,
    ALGORITHM,
    ACCESS_EXPIRES,
    REFRESH_EXPIRES
)

from src.models.testeme_user_models import (
    SignInUserRequest,
    SignUpUserRequest,
    User
)

from src.models.testeme_operacoes_models import (
    Operacoes,
    OperacoesResponse
)

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# =============================
# OPERACOES
# =============================

@router.get("/operacoes", response_model=list[OperacoesResponse])
def listar_operacoes(
    user: Annotated[User, Depends(get_logged_user)]
):
    with Session(get_engine()) as session:
        operacoes = session.exec(
            select(Operacoes)
            .where(Operacoes.nick == user.nick)
            .order_by(Operacoes.created_at.desc())
        ).all()

        return [OperacoesResponse.model_validate(op) for op in operacoes]