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


router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# =============================
# USERS
# =============================

@router.post('/cadastrar', status_code=status.HTTP_201_CREATED)
async def cadastrar_users(user_data: SignUpUserRequest):
    with Session(get_engine()) as session:
        user_exists = session.exec(
            select(User).where(User.nick == user_data.nick)
        ).first()

        if user_exists:
            raise HTTPException(status_code=400, detail='Nick já cadastrado!')

        user = User(
            nome=user_data.nome,
            nick=user_data.nick,
            password=hash_password(user_data.password),
            score=0
        )

        session.add(user)
        session.commit()

        return {"detail": "Usuário cadastrado com sucesso!"}


@router.post('/logar')
def logar_users(signin_data: SignInUserRequest):
    with Session(get_engine()) as session:
        user = session.exec(
            select(User).where(User.nick == signin_data.nick)
        ).first()

        if not user:
            raise HTTPException(status_code=400, detail='Nick inválido!')

        if not pwd_context.verify(signin_data.password, user.password):
            raise HTTPException(status_code=400, detail='Senha incorreta!')

        # 🔒 UTC absoluto
        agora_utc = datetime.now(timezone.utc)

        access_expires = agora_utc + timedelta(minutes=ACCESS_EXPIRES)
        refresh_expires = agora_utc + timedelta(minutes=REFRESH_EXPIRES)

        access_token = jwt.encode(
            {
                "sub": user.nick,
                "exp": int(access_expires.timestamp())  # 🔥 timestamp UTC
            },
            SECRET_KEY,
            algorithm=ALGORITHM
        )

        refresh_token = jwt.encode(
            {
                "sub": user.nick,
                "exp": int(refresh_expires.timestamp())  # 🔥 timestamp UTC
            },
            SECRET_KEY,
            algorithm=ALGORITHM
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token
        }


@router.get("/autenticar")
def autenticar_users(user: Annotated[User, Depends(get_logged_user)]):
    return user