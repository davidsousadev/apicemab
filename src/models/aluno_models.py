from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, Field as PydanticField
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field


class Aluno(SQLModel, table=True):
    """Cadastro oficial de alunos usado para validar vínculos do CEMAB App."""
    __tablename__ = "aluno"

    id: Optional[int] = Field(default=None, primary_key=True)
    matricula: str = Field(index=True, unique=True, max_length=40)
    nome_completo: str = Field(max_length=160)
    ativo: bool = Field(default=True, index=True)


class AlunoDispositivo(SQLModel, table=True):
    """Relacionamento muitos-para-muitos entre alunos e dispositivos FCM."""
    __tablename__ = "aluno_dispositivo"
    __table_args__ = (UniqueConstraint("aluno_id", "user_id", name="uq_aluno_dispositivo_aluno_user"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    aluno_id: int = Field(foreign_key="aluno.id", index=True)
    user_id: int = Field(foreign_key="userm.id", index=True)
    criado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EventoPresenca(SQLModel, table=True):
    """Registro idempotente de um evento recebido da automação escolar."""
    __tablename__ = "cemab_evento_presenca"
    __table_args__ = (UniqueConstraint("external_id", name="uq_cemab_evento_external_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    external_id: str = Field(index=True, max_length=120)
    aluno_id: Optional[int] = Field(default=None, foreign_key="aluno.id", index=True)
    matricula: str = Field(index=True, max_length=40)
    nome_completo: str = Field(max_length=160)
    data_hora: str = Field(max_length=80)
    identificacao: Optional[str] = Field(default=None, max_length=80)
    codigo_usuario: Optional[str] = Field(default=None, max_length=80)
    autorizacao: Optional[str] = Field(default=None, max_length=80)
    envio_status: str = Field(default="pendente", index=True, max_length=40)
    tokens_enviados: int = Field(default=0)
    criado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class VincularAlunoRequest(BaseModel):
    matricula: str = PydanticField(min_length=1, max_length=40)
    nome_completo: str = PydanticField(min_length=3, max_length=160)
    # Token FCM deste aparelho: usado para criar o vínculo no servidor.
    token: str = PydanticField(min_length=10, max_length=4096)


class DesvincularAlunoRequest(BaseModel):
    matricula: str = PydanticField(min_length=1, max_length=40)
    token: str = PydanticField(min_length=10, max_length=4096)


class CadastrarAlunoRequest(BaseModel):
    matricula: str = PydanticField(min_length=1, max_length=40)
    nome_completo: str = PydanticField(min_length=3, max_length=160)
    ativo: bool = True


class ImportarAlunosRequest(BaseModel):
    alunos: list[CadastrarAlunoRequest] = PydanticField(min_length=1, max_length=5000)


class EventoPresencaRequest(BaseModel):
    """Evento da automação. Aceita campos normalizados ou os rótulos do relatório."""
    # Identificadores e matrículas podem chegar como números ao converter uma planilha.
    id_evento: str | int | None = PydanticField(
        default=None,
        validation_alias=AliasChoices("id_evento", "event_id", "id_evento_origem"),
    )
    id_log: str | int | None = PydanticField(
        default=None,
        validation_alias=AliasChoices("id_log", "log_id", "ID", "Código do Log"),
    )
    matricula: str | int = PydanticField(
        validation_alias=AliasChoices("matricula", "Matrícula (Usuário)", "Matrícula", "matricula_usuario"),
    )
    nome_completo: Optional[str] = PydanticField(
        default=None, max_length=160,
        validation_alias=AliasChoices("nome_completo", "Nome (Usuário)", "nome", "nome_aluno"),
    )
    data_hora: Any = PydanticField(
        ...,
        validation_alias=AliasChoices("data_hora", "Data e Hora (Logs de Acesso)", "timestamp", "dataHora"),
    )
    identificacao: str | int | None = PydanticField(
        default=None,
        validation_alias=AliasChoices("identificacao", "Identificação", "Identificacao"),
    )
    codigo_usuario: str | int | None = PydanticField(
        default=None,
        validation_alias=AliasChoices("codigo_usuario", "Código (Usuário)", "codigo"),
    )
    autorizacao: str | int | None = PydanticField(
        default=None,
        validation_alias=AliasChoices("autorizacao", "Autorização (Logs de Acesso)", "Autorização", "autorizacao_id"),
    )


class EventoPresencaLoteRequest(BaseModel):
    eventos: list[EventoPresencaRequest] = PydanticField(min_length=1, max_length=500)


class NotificacaoAdministrativa(SQLModel, table=True):
    """Histórico persistente de notificações enviadas pela área administrativa."""
    __tablename__ = "cemab_notificacao_admin"
    id: Optional[int] = Field(default=None, primary_key=True)
    external_id: str = Field(index=True, unique=True, max_length=140)
    tipo: str = Field(index=True, max_length=30)
    titulo: str = Field(max_length=120)
    mensagem: str = Field(max_length=1000)
    aluno_id: Optional[int] = Field(default=None, foreign_key="aluno.id", index=True)
    matricula: Optional[str] = Field(default=None, index=True, max_length=40)
    nome_completo: Optional[str] = Field(default=None, max_length=160)
    data_referencia: Optional[str] = Field(default=None, index=True, max_length=10)
    tokens_enviados: int = Field(default=0)
    criado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class ConfiguracaoAplicativo(SQLModel, table=True):
    """Configurações persistentes e pequenas do aplicativo CEMAB."""
    __tablename__ = "cemab_configuracao_app"

    chave: str = Field(primary_key=True, max_length=80)
    valor: str = Field(default="", max_length=1000)
