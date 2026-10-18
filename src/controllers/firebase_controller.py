from fastapi import APIRouter, HTTPException, status
from firebase_admin import messaging
from sqlmodel import Session, select

from src.database import get_engine
from src.firebase_service import ensure_firebase_app
from src.models.users_models import UserM

router = APIRouter()


@router.get("/send/{user_id}/{id_mensagem}")
def send_notification(user_id: int, id_mensagem: int):
    """Endpoint legado preservado para mensagens de outros projetos."""
    with Session(get_engine()) as session:
        user = session.exec(select(UserM).where(UserM.id == user_id)).first()
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        registration_token = user.token

    try:
        ensure_firebase_app()
        message = messaging.Message(
            notification=messaging.Notification(
                title="Promoção Exclusiva 🚀",
                body=f"Produto em promoção! ID {id_mensagem}",
            ),
            data={"tipo": "promocao", "id_mensagem": str(id_mensagem)},
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(sound="default", color="#176B52"),
            ),
            token=registration_token,
        )
        response = messaging.send(message)
        return {"success": True, "response": response}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao enviar notificação: {str(exc)}")
