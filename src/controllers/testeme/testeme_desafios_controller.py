
from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timedelta, timezone
from typing import Annotated
from sqlmodel import Session, select
from passlib.context import CryptContext

from src.database import get_engine

from src.models.testeme_desafio_models import (
    CreateDesafioRequest,
    DesafioResponse,
    Desafio
)

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# =============================
# DESAFIOS
# =============================

@router.get("/desafios", response_model=list[DesafioResponse])
def listar_desafios():
    with Session(get_engine()) as session:
        desafios = session.exec(select(Desafio)).all()
        return [DesafioResponse.model_validate(d) for d in desafios]


@router.post("/desafios", response_model=DesafioResponse, status_code=status.HTTP_201_CREATED)
def criar_desafio(desafio_data: CreateDesafioRequest):
    with Session(get_engine()) as session:
        desafio = Desafio(
            title=desafio_data.title,
            desc=desafio_data.desc
            # created_at já é UTC timezone-aware pelo model
        )

        session.add(desafio)
        session.commit()
        session.refresh(desafio)

        return DesafioResponse.model_validate(desafio)