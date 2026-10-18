from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# Importação de todos controladores
from .controllers.index_controller import router as index_router

from .controllers.alunos_controller import router as alunos_router
from .controllers.presencas_controller import router as presencas_router
from .controllers.admin_notificacoes_controller import router as admin_notificacoes_router
# Importa o modelo para registrar a tabela no SQLModel.metadata antes do init_db.
from .models.aluno_models import Aluno, AlunoDispositivo, EventoPresenca, NotificacaoAdministrativa, ConfiguracaoAplicativo  # noqa: F401

from .database import init_db

def create_app():

    # Inicializa a aplicação
    app = FastAPI(
        title="API CEMAB",
        description="API CEMAB: serviços gerais, Teste Me e notificações escolares do CEMAB App.",
        version="1.1.0",
    )

    # Configuração de CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # ! Restringir em produção
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Middleware TrustedHost sem parâmetros incorretos
    app.add_middleware(
        TrustedHostMiddleware, 
        allowed_hosts=["*"],  # ! Restringir em produção
    )  

    # Registro das rotas
    # /
    app.include_router(index_router, prefix="", tags=["root"])
    app.include_router(alunos_router, prefix="/alunos", tags=["CEMAB App - Alunos"])
    app.include_router(presencas_router, prefix="/presencas", tags=["CEMAB App - Presenças e notificações"])
    app.include_router(admin_notificacoes_router, prefix="/admin", tags=["CEMAB - Administração de notificações"])
    # Inicialização do banco de dados
    init_db()

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    import os

    # Porta dinâmica para compatibilidade com serviços do Vercel
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=True)
    
