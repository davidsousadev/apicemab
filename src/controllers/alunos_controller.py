import re
import unicodedata
from typing import Optional

from decouple import config
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from src.database import get_engine
from src.models.aluno_models import (
    Aluno, AlunoDispositivo, VincularAlunoRequest, DesvincularAlunoRequest,
    CadastrarAlunoRequest, ImportarAlunosRequest,
)
from src.models.users_models import UserM

router = APIRouter()


def normalizar_texto(valor: str) -> str:
    valor = unicodedata.normalize("NFKD", valor.strip())
    valor = "".join(ch for ch in valor if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", valor).casefold()


def normalizar_matricula(valor: str) -> str:
    # Ignora pontos, hífens e espaços (ex.: 24.154.518 == 24154518).
    return re.sub(r"[^A-Za-z0-9]", "", str(valor or "")).upper()


def localizar_aluno_por_matricula(session: Session, matricula: str) -> Optional[Aluno]:
    normalizada = normalizar_matricula(matricula)
    # A consulta canônica funciona para todos os cadastros importados por esta versão.
    aluno = session.exec(select(Aluno).where(Aluno.matricula == normalizada)).first()
    if aluno:
        return aluno
    # Compatibilidade com registros inseridos por versões anteriores, que podiam conter pontos.
    for candidato in session.exec(select(Aluno)).all():
        if normalizar_matricula(candidato.matricula) == normalizada:
            return candidato
    return None


def validar_admin(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    segredo = config("ALUNOS_ADMIN_TOKEN", default="").strip()
    if not segredo:
        raise HTTPException(status_code=503, detail="Cadastro administrativo de alunos não está configurado no servidor.")
    if not x_admin_token or x_admin_token != segredo:
        raise HTTPException(status_code=401, detail="Credencial administrativa inválida.")


def aluno_publico(aluno: Aluno) -> dict:
    return {"id": aluno.id, "matricula": aluno.matricula, "nome_completo": aluno.nome_completo}


def _irmaos(session: Session, aluno: Aluno) -> list[Aluno]:
    if aluno.familia_id is None:
        return [aluno]
    irmaos = session.exec(
        select(Aluno).where(Aluno.familia_id == aluno.familia_id, Aluno.ativo == True)  # noqa: E712
    ).all()
    return irmaos if irmaos else [aluno]


def _vincular_par(session: Session, aluno_id: int, user_id: int) -> None:
    vinculo = session.exec(
        select(AlunoDispositivo).where(
            AlunoDispositivo.aluno_id == aluno_id,
            AlunoDispositivo.user_id == user_id,
        )
    ).first()
    if not vinculo:
        vinculo = AlunoDispositivo(aluno_id=aluno_id, user_id=user_id)
        session.add(vinculo)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            vinculo = session.exec(
                select(AlunoDispositivo).where(
                    AlunoDispositivo.aluno_id == aluno_id,
                    AlunoDispositivo.user_id == user_id,
                )
            ).first()
            if not vinculo:
                raise HTTPException(status_code=500, detail="Não foi possível concluir o vínculo.")
    else:
        session.commit()


def _alunos_vinculados_dispositivo(session: Session, user_id: int) -> list[dict]:
    links = session.exec(select(AlunoDispositivo).where(AlunoDispositivo.user_id == user_id)).all()
    alunos = []
    vistos = set()
    for link in links:
        aluno = session.get(Aluno, link.aluno_id)
        if aluno and aluno.ativo and aluno.id not in vistos:
            vistos.add(aluno.id)
            alunos.append(aluno_publico(aluno))
    alunos.sort(key=lambda item: item["nome_completo"])
    return alunos


@router.post("/vincular", status_code=status.HTTP_200_OK)
def vincular_aluno(dados: VincularAlunoRequest):
    """Valida matrícula/nome e associa o token FCM ao(s) aluno(s) no banco.

    Aceita tanto o formato legado (matricula + nome_completo) quanto o novo
    formato com a lista `alunos`. Irmãos (mesma família) são vinculados
    automaticamente ao mesmo dispositivo.
    """
    token = (dados.token or "").strip()
    if not token:
        raise HTTPException(status_code=422, detail="Informe o token FCM.")

    itens = []
    if dados.alunos:
        for item in dados.alunos:
            itens.append((item.matricula, item.nome_completo))
    elif dados.matricula and dados.nome_completo:
        itens.append((dados.matricula, dados.nome_completo))
    else:
        raise HTTPException(status_code=422, detail="Informe matrícula, nome completo e token FCM.")

    with Session(get_engine()) as session:
        dispositivo = session.exec(select(UserM).where(UserM.token == token)).first()
        if not dispositivo:
            raise HTTPException(status_code=409, detail="Este dispositivo ainda não foi registrado. Feche e abra o aplicativo para configurar as notificações e tente novamente.")

        alunos_primarios = []
        for matricula_raw, nome_raw in itens:
            matricula = normalizar_matricula(matricula_raw)
            nome = normalizar_texto(nome_raw)
            if not matricula or not nome:
                raise HTTPException(status_code=422, detail="Informe matrícula, nome completo e token FCM.")
            aluno = localizar_aluno_por_matricula(session, matricula)
            if not aluno or not aluno.ativo or normalizar_texto(aluno.nome_completo) != nome:
                raise HTTPException(status_code=404, detail="Não foi possível confirmar os dados informados. Verifique a matrícula e o nome completo com a escola.")

            alunos_primarios.append(aluno)
            for alvo in _irmaos(session, aluno):
                _vincular_par(session, alvo.id, dispositivo.id)

        alunos = _alunos_vinculados_dispositivo(session, dispositivo.id)
        principal = aluno_publico(alunos_primarios[0]) if alunos_primarios else (alunos[0] if alunos else None)

        return {
            "success": True,
            "vinculo_ativo": True,
            "message": "Aluno validado e dispositivo vinculado com sucesso.",
            "aluno": principal,
            "alunos": alunos,
        }


@router.post("/desvincular", status_code=status.HTTP_200_OK)
def desvincular_aluno(dados: DesvincularAlunoRequest):
    """Remove somente o vínculo deste aluno com este dispositivo."""
    matricula = normalizar_matricula(dados.matricula)
    with Session(get_engine()) as session:
        aluno = localizar_aluno_por_matricula(session, matricula)
        dispositivo = session.exec(select(UserM).where(UserM.token == dados.token.strip())).first()
        if not aluno or not dispositivo:
            return {"success": True, "vinculado": False, "message": "O dispositivo já não possuía este vínculo.", "alunos": []}
        vinculo = session.exec(
            select(AlunoDispositivo).where(
                AlunoDispositivo.aluno_id == aluno.id,
                AlunoDispositivo.user_id == dispositivo.id,
            )
        ).first()
        if vinculo:
            session.delete(vinculo)
            session.commit()
        alunos = _alunos_vinculados_dispositivo(session, dispositivo.id)
        return {"success": True, "vinculado": False, "message": "Aluno desvinculado deste dispositivo.", "alunos": alunos}


@router.get("/vinculo", status_code=status.HTTP_200_OK)
def consultar_vinculos(token: str | None = Header(default=None, alias="X-FCM-Token", min_length=10, max_length=4096)):
    """Retorna alunos associados ao dispositivo (incluindo irmãos), sem expor o token na URL."""
    if not token:
        raise HTTPException(status_code=401, detail="Token FCM obrigatório.")
    with Session(get_engine()) as session:
        dispositivo = session.exec(select(UserM).where(UserM.token == token.strip())).first()
        if not dispositivo:
            return {"success": True, "alunos": []}
        alunos = _alunos_vinculados_dispositivo(session, dispositivo.id)
        return {"success": True, "alunos": alunos}


@router.post("/cadastrar", status_code=status.HTTP_201_CREATED, dependencies=[Depends(validar_admin)])
def cadastrar_aluno(dados: CadastrarAlunoRequest):
    """Cadastra um aluno oficial; protegido por X-Admin-Token."""
    matricula = normalizar_matricula(dados.matricula)
    nome = re.sub(r"\s+", " ", dados.nome_completo.strip())
    if not matricula or not nome:
        raise HTTPException(status_code=422, detail="Matrícula e nome completo são obrigatórios.")
    with Session(get_engine()) as session:
        existente = localizar_aluno_por_matricula(session, matricula)
        if existente:
            raise HTTPException(status_code=409, detail="Já existe um aluno cadastrado com essa matrícula.")
        aluno = Aluno(matricula=matricula, nome_completo=nome, ativo=dados.ativo, familia_id=dados.familia_id)
        session.add(aluno)
        try:
            session.commit()
            session.refresh(aluno)
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail="Já existe um aluno cadastrado com essa matrícula.")
        return {"success": True, "message": "Aluno cadastrado com sucesso.", "aluno": aluno_publico(aluno)}


@router.post("/importar", status_code=status.HTTP_200_OK, dependencies=[Depends(validar_admin)])
def importar_alunos(dados: ImportarAlunosRequest):
    """Importa/atualiza até 5.000 alunos. Matrículas são normalizadas sem pontuação."""
    criados = 0
    atualizados = 0
    with Session(get_engine()) as session:
        existentes = session.exec(select(Aluno)).all()
        por_matricula = {normalizar_matricula(a.matricula): a for a in existentes}
        recebidas = set()
        for item in dados.alunos:
            matricula = normalizar_matricula(item.matricula)
            nome = re.sub(r"\s+", " ", item.nome_completo.strip())
            if not matricula or not nome or matricula in recebidas:
                continue
            recebidas.add(matricula)
            aluno = por_matricula.get(matricula)
            if aluno:
                aluno.matricula = matricula
                aluno.nome_completo = nome
                aluno.ativo = item.ativo
                aluno.familia_id = item.familia_id
                atualizados += 1
            else:
                aluno = Aluno(matricula=matricula, nome_completo=nome, ativo=item.ativo, familia_id=item.familia_id)
                session.add(aluno)
                por_matricula[matricula] = aluno
                criados += 1
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail="Não foi possível importar a lista por conflito de matrículas.")
    return {"success": True, "message": "Importação concluída.", "criados": criados, "atualizados": atualizados, "total_recebido": len(dados.alunos), "total_processado": len(recebidas)}


@router.get("/health", status_code=status.HTTP_200_OK)
def health_alunos():
    return {"success": True, "service": "alunos", "message": "Serviço de alunos disponível."}
