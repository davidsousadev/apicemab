"""Webhook de presenças e histórico de notificações do CEMAB App.
A automação externa deve mandar eventos já tratados. Este módulo não lê nem
transforma arquivos/relatórios Control iD.
"""
import hashlib
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decouple import config
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlmodel import Session, select
from src.database import get_engine
from src.models.aluno_models import (
    Aluno, AlunoDispositivo, EventoPresenca, NotificacaoAdministrativa, EventoPresencaLoteRequest,
    EventoPresencaRequest,
)
from src.models.users_models import UserM
from src.controllers.alunos_controller import normalizar_matricula, localizar_aluno_por_matricula

router = APIRouter()

# Fuso de exibição (Fortaleza/Teresina/Brasília = UTC-3)
_FUSO_EXIBICAO = ZoneInfo("America/Fortaleza")
_UTC = timezone.utc


def validar_token_eventos(x_cemab_event_token: str | None = Header(default=None, alias="X-CEMAB-Event-Token")):
    segredo = config("CEMAB_EVENT_TOKEN", default="").strip() or config("PRESENCA_WEBHOOK_TOKEN", default="").strip()
    if not segredo:
        raise HTTPException(status_code=503, detail="Webhook de presenças não está configurado no servidor.")
    if not x_cemab_event_token or x_cemab_event_token != segredo:
        raise HTTPException(status_code=401, detail="Credencial do webhook inválida.")

def _data_hora_local(valor) -> datetime:
    """Aceita data ISO-8601 ou timestamp Unix. Retorna em UTC para o banco."""
    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, (int, float)):
        timestamp = float(valor)
        if abs(timestamp) > 100_000_000_000:
            timestamp /= 1000.0
        dt = datetime.fromtimestamp(timestamp, tz=_UTC)
    else:
        texto = str(valor).strip()
        if not texto:
            raise ValueError("data_hora não pode ser vazia")
        if re.fullmatch(r"\d{10,13}(?:\.\d+)?", texto):
            timestamp = float(texto)
            if abs(timestamp) > 100_000_000_000:
                timestamp /= 1000.0
            dt = datetime.fromtimestamp(timestamp, tz=_UTC)
        else:
            dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            if dt.tzinfo is None or dt.utcoffset() is None:
                dt = dt.replace(tzinfo=_FUSO_EXIBICAO)
    return dt.astimezone(_UTC)

def _texto(valor, limite=160):
    if valor is None:
        return None
    resultado = str(valor).strip()
    return resultado[:limite] if resultado else None

def _external_id(dados: EventoPresencaRequest, matricula: str, dt: datetime) -> str:
    origem = _texto(dados.id_evento, 110) or _texto(dados.id_log, 110)
    if origem:
        return ("controlid:" + origem)[:120]
    identificador = "|".join([
        matricula, dt.isoformat(), _texto(dados.identificacao, 80) or "",
        _texto(dados.codigo_usuario, 80) or "",
    ])
    return "sha256:" + hashlib.sha256(identificador.encode("utf-8")).hexdigest()

def _remover_token_fcm_invalido(session: Session, token: str) -> None:
    dispositivo = session.exec(select(UserM).where(UserM.token == token)).first()
    if not dispositivo:
        return
    vinculos = session.exec(
        select(AlunoDispositivo).where(AlunoDispositivo.user_id == dispositivo.id)
    ).all()
    for vinculo in vinculos:
        session.delete(vinculo)
    session.flush()
    session.delete(dispositivo)

def _processar_evento(dados: EventoPresencaRequest) -> dict:
    matricula = normalizar_matricula(dados.matricula)
    if not matricula or len(matricula) > 40:
        return {"success": False, "status": "matricula_invalida", "matricula": str(dados.matricula)[:40]}
    
    try:
        dt = _data_hora_local(dados.data_hora)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        return {"success": False, "status": "data_hora_invalida", "matricula": matricula, "detail": str(exc)}
    
    external_id = _external_id(dados, matricula, dt)
    data_iso = dt.isoformat()  # Banco continua igual
    identificacao = _texto(dados.identificacao, 80)
    codigo_usuario = _texto(dados.codigo_usuario, 80)
    autorizacao = _texto(dados.autorizacao, 80)
    
    with Session(get_engine()) as session:
        existente = session.exec(select(EventoPresenca).where(EventoPresenca.external_id == external_id)).first()
        
        if existente and existente.envio_status in {"enviado", "parcial", "presenca_registrada"}:
            # Registros antigos podem ter status de push da implementação anterior;
            # este endpoint agora só registra passagens e nunca dispara push de presença.
            return {
                "success": True,
                "duplicado": True,
                "evento_id": existente.external_id,
                "status": "duplicado",
                "tokens_enviados": 0,
            }
        
        aluno = localizar_aluno_por_matricula(session, matricula)
        nome_evento = _texto(dados.nome_completo, 160)
        nome_oficial = aluno.nome_completo if aluno else (nome_evento or "Aluno não identificado")
        
        if existente:
            evento = existente
            evento.matricula = matricula
            evento.nome_completo = nome_oficial
            evento.data_hora = data_iso
            evento.identificacao = identificacao
            evento.codigo_usuario = codigo_usuario
            evento.autorizacao = autorizacao
            evento.aluno_id = aluno.id if aluno else None
            evento.envio_status = "pendente"
            evento.tokens_enviados = 0
        else:
            evento = EventoPresenca(
                external_id=external_id,
                aluno_id=aluno.id if aluno else None,
                matricula=matricula,
                nome_completo=nome_oficial,
                data_hora=data_iso,
                identificacao=identificacao,
                codigo_usuario=codigo_usuario,
                autorizacao=autorizacao,
                envio_status="pendente",
                tokens_enviados=0,
            )
            session.add(evento)
        
        session.commit()
        session.refresh(evento)
        
        if not aluno or not aluno.ativo:
            evento.envio_status = "aluno_nao_cadastrado"
            session.add(evento)
            session.commit()
            return {"success": False, "evento_id": external_id, "matricula": matricula, "status": "aluno_nao_cadastrado", "detail": "A matrícula não consta no cadastro de alunos ativos."}

        # A API registra as passagens, mas NÃO envia mais push de presença.
        # As notificações serão de falta ou avisos personalizados pela área administrativa.
        evento.envio_status = "presenca_registrada"
        evento.tokens_enviados = 0
        session.add(evento)
        session.commit()
        return {
            "success": True, "evento_id": external_id, "matricula": aluno.matricula,
            "status": "presenca_registrada", "tokens_destino": 0, "tokens_enviados": 0,
        }

@router.post("/evento", status_code=status.HTTP_200_OK, dependencies=[Depends(validar_token_eventos)])
def receber_evento(dados: EventoPresencaRequest):
    """Recebe e registra uma passagem; não envia push de presença."""
    return _processar_evento(dados)

@router.post("/eventos", status_code=status.HTTP_200_OK, dependencies=[Depends(validar_token_eventos)])
def receber_lote(dados: EventoPresencaLoteRequest):
    """Recebe e registra um lote de passagens para cálculo posterior de faltas."""
    resultados = []
    for evento in dados.eventos:
        try:
            resultados.append(_processar_evento(evento))
        except HTTPException as exc:
            resultados.append({"success": False, "status": "erro", "matricula": evento.matricula, "detail": exc.detail})
    return {
        "success": all(item.get("success", False) or item.get("status") in {"sem_vinculo", "duplicado", "presenca_registrada"} for item in resultados),
        "total_recebido": len(dados.eventos),
        # Mantém o campo legado por compatibilidade, mas ele é sempre zero:
        # o endpoint não envia notificações de presença.
        "notificados": 0,
        "presencas_registradas": sum(1 for item in resultados if item.get("status") in {"presenca_registrada", "duplicado"}),
        "resultados": resultados,
    }

@router.get("/historico", status_code=status.HTTP_200_OK)
def historico_notificacoes(
    token: str | None = Header(default=None, alias="X-FCM-Token", min_length=10, max_length=4096),
    limite: int = Query(default=100, ge=1, le=300),
):
    """Retorna apenas faltas e avisos enviados administrativamente, nunca passagens positivas."""
    if not token:
        raise HTTPException(status_code=401, detail="Token FCM obrigatório para consultar o histórico.")

    with Session(get_engine()) as session:
        user = session.exec(select(UserM).where(UserM.token == token.strip())).first()
        if not user:
            return {"success": True, "notificacoes": []}
        links = session.exec(select(AlunoDispositivo).where(AlunoDispositivo.user_id == user.id)).all()
        aluno_ids = [link.aluno_id for link in links]
        if not aluno_ids:
            return {"success": True, "notificacoes": []}

        rows = session.exec(
            select(NotificacaoAdministrativa)
            .where(NotificacaoAdministrativa.aluno_id.in_(aluno_ids))
            .order_by(NotificacaoAdministrativa.criado_em.desc())
            .limit(limite)
        ).all()
        notificacoes = []
        for item in rows:
            criado = item.criado_em
            if criado.tzinfo is None:
                criado = criado.replace(tzinfo=_UTC)
            notificacoes.append({
                "evento_id": item.external_id,
                "tipo": item.tipo,
                "titulo": item.titulo,
                "mensagem": item.mensagem,
                "matricula": item.matricula or "",
                "nome_completo": item.nome_completo or "",
                "data_hora": "",
                "hora_aproximada": "",
                "data_referencia": item.data_referencia or "",
                "recebida_em": criado.astimezone(_UTC).isoformat(),
                "lida": False,
            })
        return {"success": True, "notificacoes": notificacoes}
