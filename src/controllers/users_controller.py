from fastapi import APIRouter, HTTPException, status
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from src.database import get_engine
from src.models.users_models import (
    SignInUserRequest, SignUpUserRequest, UserM, UpdateUserRequest, UserResponse,
)
from src.models.aluno_models import AlunoDispositivo

router = APIRouter()


@router.options("", status_code=status.HTTP_200_OK)
async def options_Users():
    return {"methods": ["GET", "POST", "PATCH"]}


@router.get("/", response_model=list[UserResponse])
def listar_Users():
    with Session(get_engine()) as session:
        users = session.exec(select(UserM)).all()
        return [UserResponse.model_validate(user) for user in users]


def _migrar_vinculos(session: Session, user_anterior: UserM, user_atual: UserM) -> int:
    """Move as associações do token antigo para o registro do token atual."""
    migrados = 0
    links = session.exec(
        select(AlunoDispositivo).where(AlunoDispositivo.user_id == user_anterior.id)
    ).all()
    for link in links:
        existente = session.exec(
            select(AlunoDispositivo).where(
                AlunoDispositivo.aluno_id == link.aluno_id,
                AlunoDispositivo.user_id == user_atual.id,
            )
        ).first()
        if existente:
            session.delete(link)
        else:
            link.user_id = user_atual.id
            session.add(link)
            migrados += 1
    return migrados


@router.post("/cadastrar", status_code=status.HTTP_201_CREATED)
def cadastrar_users(user_data: SignUpUserRequest):
    """Registra o token FCM sem interromper clientes antigos.

    Quando token_anterior for informado, atualiza o registro antigo (ou transfere
    vínculos caso o token novo já esteja cadastrado), mantendo notificações ativas.
    """
    token = user_data.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="Token FCM obrigatório.")

    with Session(get_engine()) as session:
        atual = session.exec(select(UserM).where(UserM.token == token)).first()
        anterior = None
        if user_data.token_anterior and user_data.token_anterior.strip() != token:
            anterior = session.exec(
                select(UserM).where(UserM.token == user_data.token_anterior.strip())
            ).first()

        if anterior and anterior.id != (atual.id if atual else None):
            if atual:
                _migrar_vinculos(session, anterior, atual)
                session.delete(anterior)
                session.commit()
                return {"detail": "Token atualizado; vínculos preservados.", "id": atual.id}
            anterior.token = token
            session.add(anterior)
            session.commit()
            session.refresh(anterior)
            return {"detail": "Token atualizado; vínculos preservados.", "id": anterior.id}

        if atual:
            return {"detail": "Token Já cadastrado", "id": atual.id}

        user = UserM(token=token)
        session.add(user)
        try:
            session.commit()
            session.refresh(user)
        except IntegrityError:
            session.rollback()
            # Concorrência: outra requisição pode ter registrado o mesmo token.
            registro = session.exec(select(UserM).where(UserM.token == token)).first()
            if registro:
                return {"detail": "Token Já cadastrado", "id": registro.id}
            raise HTTPException(status_code=500, detail="Não foi possível registrar o token FCM.")
        return {"detail": "Token Cadastrado com sucesso.", "id": user.id}
