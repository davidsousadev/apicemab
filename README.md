# API CEMAB

API FastAPI/SQLModel para o fluxo de vínculo e presença do **CEMAB App**.

## Configuração e execução local

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # preencha os valores reais; nunca comite o .env
uvicorn src.main:app --reload
```

Configure `DATABASE_URL` com um banco persistente no ambiente de produção, todas as variáveis de Firebase Admin, `ALUNOS_ADMIN_TOKEN` e `CEMAB_EVENT_TOKEN`. Não use SQLite local efêmero em um ambiente serverless como banco definitivo.

## CEMAB App

Veja [`README-CEMAB-ALUNOS.md`](README-CEMAB-ALUNOS.md) para os contratos completos. Principais rotas:

- `POST /users/cadastrar`: cadastro do token FCM; preserva compatibilidade e migra associações quando recebe `token_anterior`.
- `POST /alunos/vincular`: valida matrícula e nome e relaciona o dispositivo ao aluno.
- `POST /alunos/desvincular`: remove a associação entre este token e o aluno.
- `POST /alunos/importar` e `POST /alunos/cadastrar`: operações administrativas protegidas por `X-Admin-Token`.
- `POST /presencas/evento` e `POST /presencas/eventos`: webhook protegido por `X-CEMAB-Event-Token`, envia push somente a dispositivos vinculados ao aluno e deduplica eventos.
- `GET /presencas/historico`: fornece ao app o histórico associado ao token por meio do cabeçalho `X-FCM-Token`.

A automação que trata o relatório Control iD será construída separadamente. A API recebe JSON de eventos já tratados; não lê planilhas nem corrige os timestamps de origem.

## Testes de configuração

Depois de publicar, confirme `/alunos/health`, importe o cadastro oficial, vincule um dispositivo num telefone com permissões FCM habilitadas e envie um evento de teste usando o segredo de webhook. Nunca exponha o segredo administrativo, o segredo do webhook ou as credenciais do Firebase.
