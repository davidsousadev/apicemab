import os
from functools import lru_cache

from decouple import config
from sqlmodel import SQLModel, create_engine


@lru_cache(maxsize=1)
def get_engine():
    """Cria e reutiliza o engine do banco de dados.

    Em produção, configure DATABASE_URL. Também aceita as variáveis DB_*
    usadas anteriormente pelo projeto. Sem configuração, usa SQLite local.
    """
    database_url = config("DATABASE_URL", default="").strip()

    if database_url:
        # Alguns provedores ainda fornecem postgres:// em vez de postgresql://.
        if database_url.startswith("postgres://"):
            database_url = "postgresql://" + database_url[len("postgres://"): ]
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)

    db_username = config("DB_USERNAME", default="")
    db_password = config("DB_PASSWORD", default="")
    db_name = config("DB_NAME", default="")
    db_host = config("DB_HOST", default="")
    db_port = config("DB_PORT", default="5432")
    if all([db_username, db_password, db_name, db_host]):
        from sqlalchemy.engine import URL
        url = URL.create(
            "postgresql+psycopg2", username=db_username, password=db_password,
            host=db_host, port=int(db_port), database=db_name,
            query={"sslmode": "require"},
        )
        return create_engine(url, pool_pre_ping=True)

    sqlite_url = config("SQLITE_URL", default="sqlite:///apisme.db")
    return create_engine(sqlite_url, connect_args={"check_same_thread": False} if sqlite_url.startswith("sqlite") else {}, pool_pre_ping=True)


def init_db():
    # Importa os modelos para registrar suas tabelas no metadata antes do create_all.
    from src.models.users_models import UserM  # noqa: F401
    
    from src.models.aluno_models import Aluno, AlunoDispositivo, EventoPresenca, NotificacaoAdministrativa, ConfiguracaoAplicativo  # noqa: F401

    SQLModel.metadata.create_all(get_engine())
    print("Banco de dados inicializado com sucesso!")
