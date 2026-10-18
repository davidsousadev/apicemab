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

from src.models.testeme_desafio_models import (
    CreateDesafioRequest,
    DesafioResponse,
    Desafio
)

from src.models.testeme_operacoes_models import (
    Operacoes,
    OperacoesResponse
)

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# =============================
# COLETAR RECOMPENSA
# =============================

@router.post("/desafios/{id}/coletar")
def coletar_recompensa(
    id: int,
    user: Annotated[User, Depends(get_logged_user)]
):
    with Session(get_engine()) as session:
        desafio = session.get(Desafio, id)

        if not desafio:
            raise HTTPException(status_code=404, detail="Desafio não encontrado")

        # 🚫 Impede coleta duplicada
        if desafio.coletado:
            raise HTTPException(status_code=400, detail="Recompensa já coletada.")

        # 🔒 UTC absoluto
        agora = datetime.now(timezone.utc)

        created_at = desafio.created_at

        # 🔒 Se banco devolver naive (extra segurança)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        tempo_passado = (agora - created_at).total_seconds()

        if tempo_passado < 0:
            tempo_passado = 0
        # 🚫 Expira após 240 segundos
        if tempo_passado >= 240:
            raise HTTPException(
                status_code=400,
                detail="Tempo expirado! Nenhuma recompensa disponível."
            )

        # 🎯 Pontuação linear (100 → 0)
        pontos = int(100 * (1 - (tempo_passado / 240)))
        pontos = max(pontos, 0)

        # Atualiza usuário
        db_user = session.exec(
            select(User).where(User.nick == user.nick)
        ).first()

        if not db_user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        db_user.score += pontos
        desafio.coletado = True

        # Registrar operação (UTC 00 garantido pelo model)
        nova_operacao = Operacoes(
            nick=user.nick,
            valor=pontos,
            tipo="recompensa",
            descricao=f"Recompensa do desafio {desafio.title}"
            # NÃO precisa passar created_at manualmente
        )

        session.add_all([nova_operacao, db_user, desafio])
        session.commit()

        return {
            "message": "Recompensa coletada com sucesso!",
            "pontos_ganhos": pontos,
            "novo_score": db_user.score,
            "tempo_passado_segundos": int(tempo_passado)
        }