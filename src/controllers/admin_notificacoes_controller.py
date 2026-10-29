# -*- coding: utf-8 -*-
"""Área administrativa para faltas e notificações personalizadas do CEMAB."""
from datetime import date, datetime, timezone
from uuid import uuid4
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from decouple import config
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from firebase_admin import messaging

from src.database import get_engine
from src.firebase_service import ensure_firebase_app
from src.models.aluno_models import Aluno, AlunoDispositivo, EventoPresenca, NotificacaoAdministrativa, ConfiguracaoAplicativo
from src.models.users_models import UserM
from src.controllers.alunos_controller import normalizar_matricula, localizar_aluno_por_matricula

router = APIRouter()
FUSO = ZoneInfo("America/Fortaleza")


def validar_admin(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    segredo = config("ALUNOS_ADMIN_TOKEN", default="").strip()
    if not segredo:
        raise HTTPException(status_code=503, detail="Configure ALUNOS_ADMIN_TOKEN nas variáveis de ambiente da API.")
    if not x_admin_token or x_admin_token != segredo:
        raise HTTPException(status_code=401, detail="Token administrativo inválido.")


class EnviarFaltasRequest(BaseModel):
    data: date
    titulo: str = Field(default="Falta registrada", min_length=3, max_length=120)
    mensagem: str = Field(default="Não identificamos a passagem do aluno pelo leitor facial nesta data. Em caso de divergência, entre em contato com a escola.", min_length=5, max_length=1000)


class PersonalizadaRequest(BaseModel):
    titulo: str = Field(min_length=3, max_length=120)
    mensagem: str = Field(min_length=2, max_length=1000)
    destino: str = Field(default="todos", pattern="^(todos|aluno)$")
    matricula: str | None = Field(default=None, max_length=40)


def _tokens_aluno(session: Session, aluno_id: int) -> list[str]:
    links = session.exec(select(AlunoDispositivo).where(AlunoDispositivo.aluno_id == aluno_id)).all()
    tokens = []
    for link in links:
        user = session.get(UserM, link.user_id)
        if user and user.token and user.token not in tokens:
            tokens.append(user.token)
    return tokens


def _enviar(token: str, payload: dict, titulo: str, mensagem: str) -> bool:
    data = {str(k): str(v) for k, v in payload.items() if v is not None}
    msg = messaging.Message(
        notification=messaging.Notification(title=titulo, body=mensagem),
        data=data,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(sound="default", color="#176B52"),
        ),
        token=token,
    )
    messaging.send(msg)
    return True


def _presente_na_data(evento: EventoPresenca, dia: date) -> bool:
    try:
        dt = datetime.fromisoformat(str(evento.data_hora).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(FUSO).date() == dia
    except (ValueError, TypeError, OverflowError):
        return False


def _salvar_notificacao(session, external_id, tipo, titulo, mensagem, aluno, dia, enviados):
    item = NotificacaoAdministrativa(
        external_id=external_id, tipo=tipo, titulo=titulo, mensagem=mensagem,
        aluno_id=aluno.id if aluno else None,
        matricula=aluno.matricula if aluno else None,
        nome_completo=aluno.nome_completo if aluno else None,
        data_referencia=dia.isoformat() if dia else None,
        tokens_enviados=enviados,
    )
    session.add(item)


class VersaoAplicativoRequest(BaseModel):
    version: str = Field(min_length=5, max_length=40)
    download_url: str = Field(default="", max_length=500)
    release_notes: str = Field(default="", max_length=500)


def _ler_configuracao(session: Session, chave: str, padrao: str = "") -> str:
    item = session.get(ConfiguracaoAplicativo, chave)
    return item.valor if item else padrao


def _gravar_configuracao(session: Session, chave: str, valor: str) -> None:
    item = session.get(ConfiguracaoAplicativo, chave)
    if item is None:
        item = ConfiguracaoAplicativo(chave=chave, valor=valor)
    else:
        item.valor = valor
    session.add(item)


@router.get("", response_class=HTMLResponse)
def painel_admin():
    return HTMLResponse(ADMIN_HTML)


@router.get("/api/app-version")
def obter_versao_aplicativo():
    """Configuração pública consultada pelo aplicativo para exibir atualização disponível."""
    with Session(get_engine()) as session:
        return {
            "success": True,
            "version": _ler_configuracao(session, "app_version", "1.1.0"),
            "download_url": _ler_configuracao(session, "app_download_url", ""),
            "release_notes": _ler_configuracao(session, "app_release_notes", ""),
            "updated_at": _ler_configuracao(session, "app_version_updated_at", ""),
        }


@router.put("/api/app-version", dependencies=[Depends(validar_admin)])
def atualizar_versao_aplicativo(dados: VersaoAplicativoRequest):
    version = dados.version.strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise HTTPException(status_code=422, detail="A versão deve usar o formato MAJOR.MINOR.PATCH, por exemplo 1.2.0.")
    download_url = dados.download_url.strip()
    if download_url:
        parsed = urlparse(download_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=422, detail="O link deve ser uma URL completa iniciada por https:// ou http://.")
    updated_at = datetime.now(timezone.utc).isoformat()
    with Session(get_engine()) as session:
        _gravar_configuracao(session, "app_version", version)
        _gravar_configuracao(session, "app_download_url", download_url)
        _gravar_configuracao(session, "app_release_notes", dados.release_notes.strip())
        _gravar_configuracao(session, "app_version_updated_at", updated_at)
        session.commit()
    return {"success": True, "version": version, "download_url": download_url, "release_notes": dados.release_notes.strip(), "updated_at": updated_at}


@router.get("/api/alunos", dependencies=[Depends(validar_admin)])
def listar_alunos():
    with Session(get_engine()) as session:
        alunos = session.exec(select(Aluno).where(Aluno.ativo == True).order_by(Aluno.nome_completo)).all()  # noqa: E712
        return {"success": True, "alunos": [{"matricula": a.matricula, "nome_completo": a.nome_completo, "ativo": a.ativo, "familia_id": a.familia_id} for a in alunos]}


@router.get("/api/resumo", dependencies=[Depends(validar_admin)])
def resumo(data: date = Query(default_factory=lambda: datetime.now(FUSO).date())):
    with Session(get_engine()) as session:
        alunos = session.exec(select(Aluno).where(Aluno.ativo == True)).all()  # noqa: E712
        eventos = session.exec(select(EventoPresenca)).all()
        presentes = {e.aluno_id for e in eventos if e.aluno_id and _presente_na_data(e, data)}
        enviados = session.exec(select(NotificacaoAdministrativa).where(NotificacaoAdministrativa.tipo == "falta").where(NotificacaoAdministrativa.data_referencia == data.isoformat())).all()
        return {"success": True, "data": data.isoformat(), "alunos_ativos": len(alunos), "presentes": len(presentes), "faltas_calculadas": sum(1 for a in alunos if a.id not in presentes), "faltas_ja_notificadas": len({n.aluno_id for n in enviados if n.aluno_id})}


@router.post("/api/faltas/enviar", dependencies=[Depends(validar_admin)])
def enviar_faltas(dados: EnviarFaltasRequest):
    with Session(get_engine()) as session:
        alunos = session.exec(select(Aluno).where(Aluno.ativo == True).order_by(Aluno.nome_completo)).all()  # noqa: E712
        eventos = session.exec(select(EventoPresenca)).all()
        presentes = {e.aluno_id for e in eventos if e.aluno_id and _presente_na_data(e, dados.data)}
        ja_enviadas = session.exec(select(NotificacaoAdministrativa).where(NotificacaoAdministrativa.tipo == "falta").where(NotificacaoAdministrativa.data_referencia == dados.data.isoformat())).all()
        notificados = {n.aluno_id for n in ja_enviadas if n.aluno_id}
        ausentes = [a for a in alunos if a.id not in presentes and a.id not in notificados]
        if not ausentes:
            return {"success": True, "data": dados.data.isoformat(), "faltas_identificadas": sum(1 for a in alunos if a.id not in presentes), "notificacoes_enviadas": 0, "sem_vinculo": 0, "ja_notificados": len(notificados), "message": "Não há novas faltas para notificar nesta data."}
        try:
            ensure_firebase_app()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Firebase não está configurado corretamente na API.") from exc
        total_enviados = sem_vinculo = 0
        resultados = []
        for aluno in ausentes:
            tokens = _tokens_aluno(session, aluno.id)
            enviados = 0
            for token in tokens:
                try:
                    _enviar(token, {"tipo":"falta", "evento_id":f"falta:{dados.data.isoformat()}:{aluno.id}", "open_notification":"1", "matricula":aluno.matricula, "nome_completo":aluno.nome_completo, "titulo":dados.titulo, "mensagem":dados.mensagem, "data_referencia":dados.data.isoformat()}, dados.titulo, f"{aluno.nome_completo}: {dados.mensagem}")
                    enviados += 1
                except Exception:
                    pass
            if not tokens:
                sem_vinculo += 1
            if enviados:
                total_enviados += enviados
                _salvar_notificacao(session, f"falta:{dados.data.isoformat()}:{aluno.id}", "falta", dados.titulo, dados.mensagem, aluno, dados.data, enviados)
            resultados.append({"matricula":aluno.matricula,"nome_completo":aluno.nome_completo,"tokens_enviados":enviados,"status":"enviado" if enviados else ("sem_vinculo" if not tokens else "erro_envio")})
        session.commit()
        return {"success": True, "data": dados.data.isoformat(), "faltas_identificadas": sum(1 for a in alunos if a.id not in presentes), "notificacoes_enviadas": total_enviados, "alunos_notificados": sum(1 for r in resultados if r["tokens_enviados"]), "sem_vinculo": sem_vinculo, "ja_notificados": len(notificados), "resultados": resultados}


@router.post("/api/personalizada/enviar", dependencies=[Depends(validar_admin)])
def enviar_personalizada(dados: PersonalizadaRequest):
    with Session(get_engine()) as session:
        if dados.destino == "aluno":
            if not dados.matricula:
                raise HTTPException(status_code=422, detail="Informe a matrícula do aluno.")
            aluno = localizar_aluno_por_matricula(session, normalizar_matricula(dados.matricula))
            if not aluno or not aluno.ativo:
                raise HTTPException(status_code=404, detail="Aluno ativo não encontrado para essa matrícula.")
            alunos = [aluno]
        else:
            alunos = session.exec(select(Aluno).where(Aluno.ativo == True).order_by(Aluno.nome_completo)).all()  # noqa: E712
        try:
            ensure_firebase_app()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Firebase não está configurado corretamente na API.") from exc
        total = sem_vinculo = alunos_notificados = 0
        falhas = []
        for aluno in alunos:
            tokens = _tokens_aluno(session, aluno.id)
            enviados = 0
            external_id = f"aviso:{uuid4().hex}"
            for token in tokens:
                try:
                    _enviar(token, {"tipo":"aviso", "evento_id":external_id, "open_notification":"1", "matricula":aluno.matricula, "nome_completo":aluno.nome_completo, "titulo":dados.titulo, "mensagem":dados.mensagem}, dados.titulo, dados.mensagem)
                    enviados += 1
                except Exception:
                    pass
            if not tokens:
                sem_vinculo += 1
            if enviados:
                total += enviados; alunos_notificados += 1
                _salvar_notificacao(session, external_id, "aviso", dados.titulo, dados.mensagem, aluno, None, enviados)
            else:
                falhas.append({"matricula":aluno.matricula,"nome_completo":aluno.nome_completo,"status":"sem_vinculo" if not tokens else "erro_envio"})
        session.commit()
        return {"success": True, "destino": dados.destino, "alunos_alvo": len(alunos), "alunos_notificados": alunos_notificados, "notificacoes_enviadas": total, "sem_vinculo": sem_vinculo, "falhas": falhas}


@router.get("/api/historico", dependencies=[Depends(validar_admin)])
def historico_admin(limite: int = Query(default=100, ge=1, le=500)):
    with Session(get_engine()) as session:
        rows = session.exec(select(NotificacaoAdministrativa).order_by(NotificacaoAdministrativa.criado_em.desc()).limit(limite)).all()
        return {"success": True, "notificacoes": [{"tipo":r.tipo,"titulo":r.titulo,"mensagem":r.mensagem,"matricula":r.matricula,"nome_completo":r.nome_completo,"data_referencia":r.data_referencia,"tokens_enviados":r.tokens_enviados,"criado_em":r.criado_em.isoformat()} for r in rows]}


ADMIN_HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0d2c7a"><title>CEMAB | Painel administrativo</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#172923;background:#f5f7fa;font-synthesis:none;--blue:#1e5bfa;--blue-dark:#11388c;--blue-light:#eaf2ff;--line:#e1e9e4;--muted:#6d7c75;--white:#fff;--red:#b42318;--shadow:0 10px 28px rgba(13,44,122,.06)}*{box-sizing:border-box}body{margin:0;min-height:100vh}button,input,select,textarea{font:inherit}button{cursor:pointer;border:0}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #9bbcff;outline-offset:2px}[hidden]{display:none!important}
.login-wrap{min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(ellipse at 15% 0%,#eaf2ff 0,transparent 45%),#f5f7fa}.login-card{width:min(440px,100%);padding:34px;background:white;border:1px solid var(--line);border-radius:24px;box-shadow:var(--shadow)}.logo-box{width:52px;height:52px;border-radius:16px;background:var(--blue-dark);color:white;display:grid;place-items:center;font-weight:900;font-size:23px;margin-bottom:24px}.eyebrow{font-size:11px;letter-spacing:.12em;font-weight:850;color:var(--blue);text-transform:uppercase}.login-card h1{font-size:27px;line-height:1.15;margin:9px 0}.muted{color:var(--muted);line-height:1.55;font-size:13px}.field{margin:16px 0}.field label{display:block;margin:0 0 7px;font-size:13px;font-weight:750;color:#172923}.field input,.field select,.field textarea{display:block;width:100%;border:1px solid #c7d5f2;border-radius:11px;background:#fff;padding:12px 13px;color:#172923}.field textarea{min-height:125px;resize:vertical}.field .hint{font-size:12px;color:var(--muted);margin:7px 0 0;line-height:1.45}.btn{display:inline-flex;justify-content:center;align-items:center;gap:8px;padding:11px 15px;border-radius:10px;background:var(--blue);color:white;font-size:13px;font-weight:800;transition:transform .15s,background .15s}.btn:hover{background:#11388c}.btn:active{transform:translateY(1px)}.btn:disabled{opacity:.55;cursor:wait}.btn-full{width:100%;margin-top:7px}.btn-light{background:#eaf2ff;color:#11388c}.btn-light:hover{background:#d6e5ff}.btn-outline{background:white;border:1px solid #c7d5f2;color:#11388c}.btn-danger{background:#b42318}.msg{margin-top:13px;padding:12px 13px;border-radius:10px;background:#eaf2ff;color:#11388c;font-size:13px;white-space:pre-wrap;line-height:1.5}.msg.error{background:#fce8e6;color:#9c1e15}.app-shell{min-height:100vh}.sidebar{position:fixed;z-index:20;inset:0 auto 0 0;width:252px;background:#0d2c7a;color:#f2f7ff;padding:25px 16px;display:flex;flex-direction:column}.brand{display:flex;align-items:center;gap:11px;padding:0 8px 28px;border-bottom:1px solid #ffffff1d}.brand-mark{width:42px;height:42px;display:grid;place-items:center;border-radius:13px;background:#1a4fb8;color:#fff;font-size:21px;font-weight:900}.brand strong{display:block;font-size:16px;letter-spacing:-.02em}.brand small{display:block;font-size:10px;letter-spacing:.09em;color:#b8ccf8;margin-top:4px}.nav-label{font-size:10px;letter-spacing:.14em;font-weight:850;color:#a9c2f5;margin:23px 11px 10px}.nav-btn{display:flex;align-items:center;gap:12px;text-align:left;width:100%;background:transparent;color:#d2def8;padding:12px;border-radius:10px;margin:3px 0;font-weight:680;font-size:13px}.nav-btn .ico{width:22px;text-align:center;font-size:16px}.nav-btn:hover{background:#ffffff0b;color:#fff}.nav-btn.active{background:#11388c;color:white;box-shadow:inset 3px 0 #d12f24}.sidebar-foot{margin-top:auto;padding:16px 9px 2px;border-top:1px solid #ffffff1d;color:#b8ccf8;font-size:11px;line-height:1.5}.sidebar-foot strong{color:#fff;display:block;font-size:12px;margin-bottom:3px}.main{margin-left:252px;min-height:100vh}.topbar{height:76px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 34px;gap:15px;position:sticky;top:0;z-index:10}.topbar-title small{display:block;font-size:11px;color:var(--muted);margin-bottom:3px}.topbar-title strong{font-size:17px;letter-spacing:-.02em}.profile{display:flex;align-items:center;gap:10px}.online-dot{width:8px;height:8px;border-radius:50%;background:#1e5bfa;box-shadow:0 0 0 4px #eaf2ff}.profile span{font-size:12px;color:#43516b;font-weight:750}.content{max-width:1400px;margin:0 auto;padding:30px 34px 50px}.view{display:none;animation:fadein .15s ease}.view.active{display:block}@keyframes fadein{from{opacity:.5;transform:translateY(2px)}to{opacity:1;transform:none}}.view-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:15px;margin-bottom:24px}.view-heading h1{font-size:26px;letter-spacing:-.04em;margin:6px 0 7px}.view-heading p{margin:0;color:var(--muted);font-size:13px;line-height:1.5}.tag{display:inline-flex;align-items:center;gap:6px;border-radius:100px;background:#eaf2ff;color:#11388c;padding:7px 10px;font-size:11px;font-weight:800;white-space:nowrap}.tag-dot{width:6px;height:6px;background:#1e5bfa;border-radius:50%}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin-bottom:19px}.metric{background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:var(--shadow)}.metric-top{display:flex;align-items:center;justify-content:space-between;gap:8px}.metric-label{color:var(--muted);font-size:12px;font-weight:700}.metric-icon{height:36px;width:36px;border-radius:11px;background:#eaf2ff;display:grid;place-items:center;font-size:17px}.metric-value{display:block;font-size:31px;font-weight:850;letter-spacing:-.05em;margin:15px 0 3px}.metric-foot{font-size:11px;color:var(--muted)}.grid-two{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:17px}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px;margin-bottom:17px;box-shadow:var(--shadow);min-width:0}.card-header{display:flex;justify-content:space-between;align-items:flex-start;gap:15px;margin-bottom:17px}.card h2{margin:0;font-size:16px;letter-spacing:-.02em}.card-sub{margin:6px 0 0;font-size:12px;line-height:1.5;color:var(--muted)}.notice{display:flex;align-items:flex-start;gap:12px;padding:15px;border-radius:12px;background:#fff5f5;border:1px solid #fce8e6;color:#9c1e15;margin:18px 0}.notice-icon{font-size:18px}.notice strong{display:block;font-size:12px;margin-bottom:5px}.notice p{font-size:12px;line-height:1.55;margin:0}.coverage-line{margin:16px 0}.coverage-meta{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:#6d7c75;margin-bottom:8px}.progress{height:8px;background:#e8edf5;border-radius:99px;overflow:hidden}.progress span{display:block;height:100%;border-radius:99px;background:#1e5bfa;width:0;transition:width .2s}.shortcut{display:flex;align-items:center;text-align:left;width:100%;gap:13px;padding:15px 0;background:#fff;border-bottom:1px solid #e8edf5;color:#172923}.shortcut:last-child{border-bottom:0;padding-bottom:0}.shortcut .shortcut-icon{width:39px;height:39px;border-radius:12px;background:#eaf2ff;display:grid;place-items:center;font-size:18px;flex:none}.shortcut strong{display:block;font-size:13px}.shortcut small{display:block;color:var(--muted);font-size:11px;margin-top:4px}.shortcut .arrow{margin-left:auto;color:#6d7c75;font-size:20px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 17px}.span-2{grid-column:1/-1}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:17px}.table-wrap{width:100%;overflow:auto}table{border-collapse:collapse;width:100%;min-width:620px}th,td{text-align:left;padding:13px 12px;border-bottom:1px solid #e8edf5;font-size:12px;vertical-align:top}th{color:#6d7c75;font-size:10px;text-transform:uppercase;letter-spacing:.08em;background:#f7f9fc}td strong{display:block;font-size:12px;color:#172923;margin-bottom:4px}td small{display:block;color:var(--muted);line-height:1.45;max-width:440px;overflow-wrap:anywhere}.status-chip{display:inline-block;border-radius:7px;padding:5px 7px;background:#eaf2ff;color:#11388c;font-size:10px;font-weight:800}.empty{padding:35px 15px;text-align:center;color:var(--muted);font-size:13px}.inline-row{display:flex;align-items:flex-end;gap:12px}.inline-row .field{flex:1}.footnote{font-size:11px;color:var(--muted);line-height:1.5;margin-top:14px}.version-preview{border:1px dashed #c7d5f2;border-radius:14px;padding:20px;background:#f7f9fc;margin-top:15px}.version-preview strong{display:block;margin-bottom:7px}.version-preview .example-banner{background:#fff5f5;border:1px solid #fce8e6;color:#9c1e15;border-radius:9px;padding:10px 12px;font-size:12px;font-weight:700}
@media(max-width:1100px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.grid-two{grid-template-columns:1fr}.content{padding:25px 22px}.topbar{padding:0 22px}}@media(max-width:760px){.sidebar{position:static;width:auto;height:auto;padding:12px;display:block}.brand{padding:0 6px 13px;border:0}.brand-mark{width:36px;height:36px}.brand strong{font-size:14px}.brand small{font-size:9px}.nav-label,.sidebar-foot{display:none}.nav-list{display:flex;gap:5px;overflow-x:auto;padding:8px 0 0}.nav-btn{width:auto;white-space:nowrap;padding:10px 11px;font-size:11px;margin:0}.nav-btn .ico{width:auto}.main{margin-left:0}.topbar{height:62px;padding:0 17px}.topbar-title strong{font-size:14px}.content{padding:20px 14px 36px}.view-heading{align-items:flex-start;flex-direction:column;margin-bottom:18px}.view-heading h1{font-size:23px}.metrics{gap:10px}.metric{padding:14px}.metric-value{font-size:26px;margin:12px 0 2px}.metric-label{font-size:11px}.metric-icon{height:30px;width:30px}.card{padding:17px}.form-grid{grid-template-columns:1fr}.span-2{grid-column:auto}.inline-row{display:block}.profile span{display:none}}@media(max-width:370px){.metrics{grid-template-columns:1fr}.nav-btn{font-size:10px;padding:9px}.card{padding:14px}}
</style></head><body>
<section id="loginView" class="login-wrap"><div class="login-card"><div class="logo-box">C</div><span class="eyebrow">CENTRO EDUCA MAIS ANNA BERNARDES</span><h1>Central de gestão</h1><p class="muted">Acesse o painel para gerenciar avisos, notificações de falta e a versão disponível do aplicativo CEMAB.</p><div class="field"><label for="adminToken">Token administrativo</label><input id="adminToken" type="password" autocomplete="current-password" placeholder="Informe o token de acesso"></div><button class="btn btn-full" id="connectButton">Entrar no painel <span aria-hidden="true">→</span></button><div id="loginMessage" class="msg" hidden></div><p class="muted" style="font-size:11px;margin:18px 0 0">Acesso protegido. O token fica apenas na memória desta página e não é salvo no navegador.</p></div></section>
<div id="dashboard" class="app-shell" hidden><aside class="sidebar"><div class="brand"><div class="brand-mark">C</div><div><strong>CEMAB <span style="color:#dbe8ff">Admin</span></strong><small>GESTÃO ESCOLAR</small></div></div><div class="nav-label">MENU PRINCIPAL</div><nav class="nav-list"><button class="nav-btn active" data-view="overview"><span class="ico">◫</span>Visão geral</button><button class="nav-btn" data-view="absences"><span class="ico">◷</span>Notificar faltas</button><button class="nav-btn" data-view="custom"><span class="ico">✉</span>Avisos personalizados</button><button class="nav-btn" data-view="version"><span class="ico">⬆</span>Versão do aplicativo</button><button class="nav-btn" data-view="history"><span class="ico">▤</span>Histórico</button></nav><div class="sidebar-foot"><strong>Centro Educa Mais Anna Bernardes</strong>Notificações e comunicação com as famílias.</div></aside>
<div class="main"><header class="topbar"><div class="topbar-title"><small id="crumb">PAINEL ADMINISTRATIVO</small><strong id="topTitle">Visão geral</strong></div><div class="profile"><span class="online-dot"></span><span>Sessão administrativa ativa</span></div></header><main class="content">
<section class="view active" id="view-overview"><div class="view-heading"><div><span class="eyebrow">ACOMPANHAMENTO ESCOLAR</span><h1>Visão geral</h1><p>Resumo das presenças recebidas e das faltas para a data selecionada.</p></div><div class="inline-row" style="min-width:min(100%,350px)"><div class="field" style="margin:0"><label for="overviewDate">Data de referência</label><input id="overviewDate" type="date"></div><button class="btn btn-light" id="refreshOverview">Atualizar</button></div></div>
<div class="metrics"><article class="metric"><div class="metric-top"><span class="metric-label">Alunos ativos</span><span class="metric-icon">👥</span></div><strong class="metric-value" id="mStudents">—</strong><span class="metric-foot">Cadastros ativos na API</span></article><article class="metric"><div class="metric-top"><span class="metric-label">Presenças registradas</span><span class="metric-icon">✓</span></div><strong class="metric-value" id="mPresent">—</strong><span class="metric-foot">Passagens recebidas no dia</span></article><article class="metric"><div class="metric-top"><span class="metric-label">Faltas calculadas</span><span class="metric-icon">◷</span></div><strong class="metric-value" id="mAbsences">—</strong><span class="metric-foot">Sem passagem registrada na data</span></article><article class="metric"><div class="metric-top"><span class="metric-label">Faltas já notificadas</span><span class="metric-icon">🔔</span></div><strong class="metric-value" id="mSent">—</strong><span class="metric-foot">Notificações registradas no histórico</span></article></div>
<div class="grid-two"><section class="card"><div class="card-header"><div><h2>Panorama do dia</h2><p class="card-sub">Proporção dos alunos com passagem identificada no relatório importado.</p></div><span class="tag" id="dateTag">Data selecionada</span></div><div class="coverage-line"><div class="coverage-meta"><span>Presenças registradas</span><strong id="coveragePresent">—</strong></div><div class="progress"><span id="coverageBar"></span></div></div><div class="coverage-line"><div class="coverage-meta"><span>Alunos sem passagem</span><strong id="coverageAbsence">—</strong></div><div class="progress"><span id="absenceBar" style="background:#d12f24"></span></div></div><div class="notice"><span class="notice-icon">⚠️</span><div><strong>Conferência obrigatória antes do envio</strong><p>Importe o relatório completo do Control iD e confirme que o script terminou sem erro. Se os registros ainda não chegaram à API, alunos presentes podem aparecer como faltosos.</p></div></div></section><section class="card"><div class="card-header"><div><h2>Ações rápidas</h2><p class="card-sub">Acesse diretamente as tarefas mais utilizadas.</p></div></div><button class="shortcut" data-jump="absences"><span class="shortcut-icon">🔔</span><span><strong>Enviar notificações de falta</strong><small>Notificar responsáveis sem passagem registrada</small></span><span class="arrow">›</span></button><button class="shortcut" data-jump="custom"><span class="shortcut-icon">✉️</span><span><strong>Criar aviso personalizado</strong><small>Envie comunicado para uma turma inteira ou um aluno</small></span><span class="arrow">›</span></button><button class="shortcut" data-jump="version"><span class="shortcut-icon">⬆️</span><span><strong>Publicar nova versão do app</strong><small>Configure a versão e o link para baixar o APK</small></span><span class="arrow">›</span></button></section></div></section>
<section class="view" id="view-absences"><div class="view-heading"><div><span class="eyebrow">ALERTA DE AUSÊNCIA</span><h1>Notificar faltas</h1><p>O sistema identifica alunos ativos sem registro de passagem na data selecionada.</p></div><span class="tag">Envio manual com confirmação</span></div><section class="card"><div class="card-header"><div><h2>Conferir faltas e preparar envio</h2><p class="card-sub">Uma notificação de falta é registrada por aluno e por data, evitando reenvios acidentais.</p></div></div><div class="form-grid"><div class="field"><label for="absenceDate">Data da falta</label><input id="absenceDate" type="date"></div><div class="field"><label for="absenceTitle">Título da notificação</label><input id="absenceTitle" maxlength="120" value="Falta registrada"></div><div class="field span-2"><label for="absenceMessage">Mensagem para os responsáveis</label><textarea id="absenceBody" maxlength="1000">Não identificamos a passagem do aluno pelo leitor facial nesta data. Em caso de divergência, entre em contato com a escola.</textarea><p class="hint">A mensagem será enviada junto ao nome do aluno em cada dispositivo vinculado.</p></div></div><div class="actions"><button class="btn btn-light" id="refreshAbsences">Atualizar resumo</button><button class="btn" id="sendAbsences">Conferir e enviar notificações</button></div><div id="absenceMessage" class="msg" hidden></div></section><section class="card"><h2>Resumo atual</h2><p id="absenceSummary" class="card-sub">Atualize o resumo para conferir os totais antes de enviar.</p></section></section>
<section class="view" id="view-custom"><div class="view-heading"><div><span class="eyebrow">COMUNICAÇÃO COM AS FAMÍLIAS</span><h1>Avisos personalizados</h1><p>Redija comunicados e envie-os para os responsáveis vinculados.</p></div></div><section class="card"><div class="card-header"><div><h2>Compor uma notificação</h2><p class="card-sub">Você pode enviar para todos os alunos ativos ou somente para um aluno selecionado.</p></div></div><div class="form-grid"><div class="field"><label for="customDestination">Destinatários</label><select id="customDestination"><option value="todos">Todos os alunos ativos vinculados</option><option value="aluno">Somente um aluno</option></select></div><div class="field" id="customStudentBox" hidden><label for="customStudent">Aluno</label><select id="customStudent"><option value="">Selecione um aluno</option></select></div><div class="field span-2"><label for="customTitle">Título</label><input id="customTitle" maxlength="120" placeholder="Ex.: Reunião de responsáveis"></div><div class="field span-2"><label for="customBody">Mensagem</label><textarea id="customBody" maxlength="1000" placeholder="Escreva o aviso que deseja enviar..."></textarea><p class="hint">Antes do envio, revise o título, o texto e os destinatários.</p></div></div><div class="actions"><button class="btn" id="sendCustom">Enviar notificação</button><button class="btn btn-light" id="clearCustom" type="button">Limpar formulário</button></div><div id="customMessage" class="msg" hidden></div></section></section>
<section class="view" id="view-version"><div class="view-heading"><div><span class="eyebrow">DISTRIBUIÇÃO DO CEMAB APP</span><h1>Versão do aplicativo</h1><p>Defina a versão recomendada e o link de instalação exibido no banner dentro do aplicativo.</p></div><span class="tag">Configuração pública para o app</span></div><section class="card"><div class="card-header"><div><h2>Informações da versão disponível</h2><p class="card-sub">Quando a versão instalada for inferior à publicada aqui, o app exibirá um banner discreto com o botão de download.</p></div></div><div class="form-grid"><div class="field"><label for="availableVersion">Versão disponível</label><input id="availableVersion" inputmode="decimal" maxlength="40" placeholder="Ex.: 1.2.0"><p class="hint">Use o formato MAJOR.MINOR.PATCH, por exemplo 1.2.0.</p></div><div class="field"><label for="downloadUrl">Link para baixar o APK</label><input id="downloadUrl" type="url" maxlength="500" placeholder="https://..." autocomplete="url"><p class="hint">Deve ser um link http:// ou https://. Pode apontar para a página ou arquivo de download.</p></div><div class="field span-2"><label for="releaseNotes">Mensagem opcional sobre a atualização</label><textarea id="releaseNotes" maxlength="500" placeholder="Ex.: Melhorias e correções de estabilidade."></textarea></div></div><div class="actions"><button class="btn" id="saveVersion">Salvar versão</button><button class="btn btn-light" id="reloadVersion">Recarregar configuração</button></div><div id="versionMessage" class="msg" hidden></div><div class="version-preview"><strong>Prévia do banner no aplicativo</strong><div class="example-banner" id="bannerPreview">Atualização disponível · versão 1.2.0 — Baixe a nova versão para continuar aproveitando os avisos do CEMAB.</div><p id="installedVersionNote" class="footnote">A versão instalada que o aplicativo compara fica definida no código do app e alinhada à versão do pacote Cordova.</p></div></section></section>
<section class="view" id="view-history"><div class="view-heading"><div><span class="eyebrow">AUDITORIA E CONSULTA</span><h1>Histórico de envios</h1><p>Consulte as notificações administrativas enviadas aos responsáveis.</p></div><button class="btn btn-light" id="refreshHistory">Atualizar histórico</button></div><section class="card"><div id="historyContainer" class="table-wrap"><div class="empty">Entre no painel para carregar o histórico.</div></div><div id="historyMessage" class="msg" hidden></div></section></section>
</main></div></div>
<script>
(function(){'use strict';const $=id=>document.getElementById(id);let adminToken='',students=[],lastSummary=null;const TITLES={overview:'Visão geral',absences:'Notificar faltas',custom:'Avisos personalizados',version:'Versão do aplicativo',history:'Histórico de envios'};const now=new Date();const today=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10);$('overviewDate').value=today;$('absenceDate').value=today;
function showMsg(id,text,error){const el=$(id);el.hidden=false;el.textContent=text;el.className='msg'+(error?' error':'')}
function clearMsg(id){const el=$(id);if(el){el.hidden=true;el.textContent='';el.className='msg'}}
async function api(path,method='GET',body){const opts={method,headers:{'X-Admin-Token':adminToken,'Accept':'application/json'}};if(body!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body)}const response=await fetch('/admin/api'+path,opts);let data={};try{data=await response.json()}catch(_){}if(!response.ok)throw new Error(data.detail||('Falha HTTP '+response.status));return data}
function nav(view){if(!TITLES[view])return;document.querySelectorAll('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.view===view));document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id==='view-'+view));$('topTitle').textContent=TITLES[view];$('crumb').textContent='PAINEL ADMINISTRATIVO / '+TITLES[view].toUpperCase();if(view==='history')loadHistory().catch(e=>alert(e.message));if(view==='version')loadVersion().catch(e=>showMsg('versionMessage',e.message,true));window.scrollTo({top:0,behavior:'smooth'})}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>nav(b.dataset.view)));document.querySelectorAll('[data-jump]').forEach(b=>b.addEventListener('click',()=>nav(b.dataset.jump)));
function option(text,value){const o=document.createElement('option');o.textContent=text;o.value=value;return o}
function fillStudents(){const sel=$('customStudent');sel.replaceChildren(option('Selecione um aluno',''));students.forEach(a=>sel.appendChild(option(a.nome_completo+' — '+a.matricula,a.matricula)))}
$('connectButton').addEventListener('click',async()=>{adminToken=$('adminToken').value.trim();if(!adminToken){showMsg('loginMessage','Informe o token administrativo.',true);return}const btn=$('connectButton');btn.disabled=true;clearMsg('loginMessage');try{const data=await api('/alunos');students=data.alunos||[];fillStudents();$('loginView').hidden=true;$('dashboard').hidden=false;await Promise.allSettled([loadSummary(),loadVersion(),loadHistory()]);}catch(e){showMsg('loginMessage',e.message,true)}finally{btn.disabled=false}});$('adminToken').addEventListener('keydown',e=>{if(e.key==='Enter')$('connectButton').click()});
function percent(part,total){return total?Math.max(0,Math.min(100,Math.round(part/total*100))):0}
async function loadSummary(){const day=$('overviewDate').value||today;const d=await api('/resumo?data='+encodeURIComponent(day));lastSummary=d;$('mStudents').textContent=d.alunos_ativos;$('mPresent').textContent=d.presentes;$('mAbsences').textContent=d.faltas_calculadas;$('mSent').textContent=d.faltas_ja_notificadas;$('dateTag').textContent=new Date(day+'T12:00:00').toLocaleDateString('pt-BR');$('coveragePresent').textContent=d.presentes+' ('+percent(d.presentes,d.alunos_ativos)+'%)';$('coverageAbsence').textContent=d.faltas_calculadas+' ('+percent(d.faltas_calculadas,d.alunos_ativos)+'%)';$('coverageBar').style.width=percent(d.presentes,d.alunos_ativos)+'%';$('absenceBar').style.width=percent(d.faltas_calculadas,d.alunos_ativos)+'%';$('absenceSummary').textContent=`Data ${new Date(day+'T12:00:00').toLocaleDateString('pt-BR')}: ${d.alunos_ativos} alunos ativos, ${d.presentes} com passagem registrada, ${d.faltas_calculadas} sem passagem e ${d.faltas_ja_notificadas} faltas já notificadas.`;return d}
$('refreshOverview').addEventListener('click',async()=>{try{await loadSummary()}catch(e){alert(e.message)}});$('overviewDate').addEventListener('change',async()=>{try{$('absenceDate').value=$('overviewDate').value;await loadSummary()}catch(e){alert(e.message)}});$('absenceDate').addEventListener('change',()=>{$('overviewDate').value=$('absenceDate').value});$('refreshAbsences').addEventListener('click',async()=>{try{const d=await loadSummary();showMsg('absenceMessage',`Resumo atualizado.\nAlunos ativos: ${d.alunos_ativos}\nPresenças registradas: ${d.presentes}\nFaltas calculadas: ${d.faltas_calculadas}\nFaltas já notificadas: ${d.faltas_ja_notificadas}`)}catch(e){showMsg('absenceMessage',e.message,true)}});
$('sendAbsences').addEventListener('click',async()=>{const day=$('absenceDate').value,title=$('absenceTitle').value.trim(),message=$('absenceBody').value.trim();if(!day||!title||!message){showMsg('absenceMessage','Informe a data, o título e a mensagem.',true);return}try{const summary=await loadSummary();const confirmed=confirm(`Confirma o envio para ${summary.faltas_calculadas-summary.faltas_ja_notificadas} possível(is) faltoso(s) em ${new Date(day+'T12:00:00').toLocaleDateString('pt-BR')}?\n\nVerifique se o relatório completo foi importado.`);if(!confirmed)return;const btn=$('sendAbsences');btn.disabled=true;const d=await api('/faltas/enviar','POST',{data:day,titulo:title,mensagem:message});showMsg('absenceMessage',`Processamento concluído.\nFaltas identificadas: ${d.faltas_identificadas}\nAlunos notificados: ${d.alunos_notificados||0}\nPush enviados a dispositivos: ${d.notificacoes_enviadas}\nSem dispositivo vinculado: ${d.sem_vinculo}\nJá notificados antes: ${d.ja_notificados}\n\n${d.message||''}`);await loadSummary();await loadHistory()}catch(e){showMsg('absenceMessage',e.message,true)}finally{$('sendAbsences').disabled=false}});
$('customDestination').addEventListener('change',()=>{$('customStudentBox').hidden=$('customDestination').value!=='aluno'});$('clearCustom').addEventListener('click',()=>{$('customTitle').value='';$('customBody').value='';$('customDestination').value='todos';$('customStudentBox').hidden=true;clearMsg('customMessage')});$('sendCustom').addEventListener('click',async()=>{const title=$('customTitle').value.trim(),body=$('customBody').value.trim(),dest=$('customDestination').value,mat=$('customStudent').value;if(!title||!body){showMsg('customMessage','Preencha o título e a mensagem.',true);return}if(dest==='aluno'&&!mat){showMsg('customMessage','Selecione o aluno que receberá o aviso.',true);return}if(!confirm(dest==='todos'?'Enviar este aviso para todos os alunos ativos com vínculo?':'Enviar este aviso para o aluno selecionado?'))return;const btn=$('sendCustom');btn.disabled=true;try{const d=await api('/personalizada/enviar','POST',{titulo:title,mensagem:body,destino:dest,matricula:dest==='aluno'?mat:null});showMsg('customMessage',`Envio concluído.\nAlunos no público-alvo: ${d.alunos_alvo}\nAlunos notificados: ${d.alunos_notificados}\nPush enviados a dispositivos: ${d.notificacoes_enviadas}\nSem vínculo: ${d.sem_vinculo}`);await loadHistory()}catch(e){showMsg('customMessage',e.message,true)}finally{btn.disabled=false}});
function td(text,small){const cell=document.createElement('td');if(small!==undefined){const strong=document.createElement('strong');strong.textContent=text||'—';const sub=document.createElement('small');sub.textContent=small||'';cell.append(strong,sub)}else cell.textContent=text||'—';return cell}
async function loadHistory(){const d=await api('/historico?limite=100');const container=$('historyContainer');container.replaceChildren();if(!d.notificacoes||!d.notificacoes.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='Nenhuma notificação administrativa enviada ainda.';container.appendChild(empty);return}const table=document.createElement('table'),thead=document.createElement('thead'),hr=document.createElement('tr');['Data e hora','Tipo','Destinatário','Mensagem','Dispositivos'].forEach(t=>{const th=document.createElement('th');th.textContent=t;hr.appendChild(th)});thead.appendChild(hr);const tbody=document.createElement('tbody');d.notificacoes.forEach(n=>{const tr=document.createElement('tr');const stamp=new Date(n.criado_em);tr.appendChild(td(Number.isNaN(stamp.getTime())?'—':stamp.toLocaleString('pt-BR')));const typ=td('');const chip=document.createElement('span');chip.className='status-chip';chip.textContent=n.tipo==='falta'?'Falta':n.tipo==='aviso'?'Aviso personalizado':n.tipo||'Notificação';typ.appendChild(chip);tr.appendChild(typ);tr.appendChild(td(n.nome_completo||'Todos os destinatários',n.matricula||''));tr.appendChild(td(n.titulo,n.mensagem));tr.appendChild(td(String(n.tokens_enviados||0)));tbody.appendChild(tr)});table.append(thead,tbody);container.appendChild(table)}$('refreshHistory').addEventListener('click',()=>loadHistory().catch(e=>showMsg('historyMessage',e.message,true)));
async function loadVersion(){const d=await api('/app-version');$('availableVersion').value=d.version||'1.1.0';$('downloadUrl').value=d.download_url||'';$('releaseNotes').value=d.release_notes||'';updatePreview();$('installedVersionNote').textContent='No Android, a versão instalada é consultada pelo plugin Cordova App Version. Em testes no navegador, o app usa APP_VERSION de www/js/config.js como fallback; mantenha também config.xml e package.json atualizados.'}
function updatePreview(){const v=$('availableVersion').value.trim()||'1.1.0';const note=$('releaseNotes').value.trim();$('bannerPreview').textContent='Atualização disponível · versão '+v+(note?' — '+note:' — Baixe a nova versão para receber os avisos atualizados.')}['availableVersion','releaseNotes'].forEach(id=>$(id).addEventListener('input',updatePreview));$('reloadVersion').addEventListener('click',()=>loadVersion().catch(e=>showMsg('versionMessage',e.message,true)));$('saveVersion').addEventListener('click',async()=>{const version=$('availableVersion').value.trim(),url=$('downloadUrl').value.trim(),notes=$('releaseNotes').value.trim();if(!/^\d+\.\d+\.\d+$/.test(version)){showMsg('versionMessage','Informe a versão no formato 1.2.0.',true);return}if(url&&!/^https?:\/\//i.test(url)){showMsg('versionMessage','O link deve começar com https:// ou http://.',true);return}const btn=$('saveVersion');btn.disabled=true;try{const d=await api('/app-version','PUT',{version,download_url:url,release_notes:notes});showMsg('versionMessage','Configuração publicada com sucesso. O app consultará essa versão ao abrir e ao voltar para primeiro plano.');updatePreview()}catch(e){showMsg('versionMessage',e.message,true)}finally{btn.disabled=false}});
$('adminToken').focus();})();
</script></body></html>'''