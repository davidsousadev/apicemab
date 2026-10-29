# API CEMAB

API FastAPI/SQLModel para o fluxo de vinculo, presenca e notificacoes do **CEMAB App**.

## Configuracao e execucao local

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn src.main:app --reload
```

Configure `DATABASE_URL` (ou `DB_*`) com um banco persistente em producao, as variaveis de Firebase Admin, `ALUNOS_ADMIN_TOKEN` e `CEMAB_EVENT_TOKEN`. Nao use SQLite local efemero em ambiente serverless como banco definitivo.

Tabelas novas sao criadas automaticamente pelo `SQLModel.metadata.create_all`.

## Variaveis de ambiente

| Variavel | Finalidade |
|---|---|
| `DATABASE_URL` | Banco persistente (PostgreSQL recomendado) |
| `DB_USERNAME`, `DB_PASSWORD`, `DB_NAME`, `DB_HOST`, `DB_PORT` | Alternativa de conexao PostgreSQL |
| `SQLITE_URL` | SQLite local (padrao `sqlite:///apisme.db`) |
| `ALUNOS_ADMIN_TOKEN` | Chave para endpoints administrativos de alunos |
| `CEMAB_EVENT_TOKEN` (ou `PRESENCA_WEBHOOK_TOKEN`) | Chave do webhook de presencas |
| Credenciais Firebase (`FIREBASE_*`) | Envio de notificacoes FCM |

## Modelo de dados (principais tabelas)

- `aluno` — cadastro oficial de alunos (`id`, `matricula`, `nome_completo`, `ativo`, `familia_id`).
- `userm` — tokens FCM cadastrados (`id`, `token`).
- `aluno_dispositivo` — vinculo N:N entre aluno e dispositivo (`aluno_id`, `user_id`), permite varios filhos por responsavel.
- `cemab_evento_presenca` — eventos de presenca recebidos (idempotente por `external_id`).
- `cemab_notificacao_admin` — historico de notificacoes administrativas (falta/aviso), filtrado por aluno.
- `cemab_configuracao_app` — configuracoes do app (versao, URL, notas).

## Suporte a varios filhos por responsavel (familia)

- Um dispositivo (token FCM) pode estar vinculado a varios alunos via `aluno_dispositivo`.
- Alunos com o mesmo `familia_id` sao irmaos; ao vincular um deles, os demais da mesma familia sao vinculados automaticamente.
- `/alunos/desvincular` remove apenas o vinculo de uma matricula especifica.
- Notificacoes e historico sao filtrados por aluno/matricula, evitando mistura entre filhos.

## Rotas da API

Base: os routers sao montados em `src/main.py`:
- `/` (root)
- `/users`
- `/fire`
- `/alunos`
- `/presencas`
- `/admin`

### Root

| Metodo | Rota | Descricao |
|---|---|---|
| GET | `/` | Informacoes basicas / link da documentacao |
| OPTIONS | `/` | Metodos disponiveis |

### Usuarios (tokens FCM)

| Metodo | Rota | Descricao |
|---|---|---|
| GET | `/users/` | Lista usuarios/tokens cadastrados |
| OPTIONS | `/users/` | Metodos disponiveis |
| POST | `/users/cadastrar` | Registra/atualiza token FCM. Aceita `{token}` ou `{token, token_anterior}` para migrar vinculos. |

### Firebase (legado)

| Metodo | Rota | Descricao |
|---|---|---|
| GET | `/fire/send/{user_id}/{id_mensagem}` | Envia notificacao promocional legada a um usuario. |

### Alunos e vinculos

| Metodo | Rota | Auth | Descricao |
|---|---|---|---|
| GET | `/alunos/health` | — | Health check do servico |
| POST | `/alunos/vincular` | — | Valida matricula/nome e vincula o dispositivo ao(s) aluno(s). Aceita formato legado (`matricula`, `nome_completo`, `token`) ou novo (`alunos: [{matricula, nome_completo}]`, `token`). Retorna `aluno` e `alunos`. |
| POST | `/alunos/desvincular` | — | Remove o vinculo individual de uma matricula com o token. Body: `{matricula, token}`. Retorna `alunos` restantes. |
| GET | `/alunos/vinculo` | Header `X-FCM-Token` | Retorna alunos vinculados ao dispositivo. |
| POST | `/alunos/cadastrar` | Header `X-Admin-Token` | Cadastra um aluno. Body: `{matricula, nome_completo, ativo?, familia_id?}`. |
| POST | `/alunos/importar` | Header `X-Admin-Token` | Importa/atualiza ate 5.000 alunos. Body: `{alunos: [{matricula, nome_completo, ativo?, familia_id?}]}`. |

### Presencas e historico

| Metodo | Rota | Auth | Descricao |
|---|---|---|---|
| POST | `/presencas/evento` | Header `X-CEMAB-Event-Token` | Recebe um evento individual de presenca. |
| POST | `/presencas/eventos` | Header `X-CEMAB-Event-Token` | Recebe lote de eventos (ate 500). |
| GET | `/presencas/historico` | Header `X-FCM-Token` | Retorna historico de notificacoes administrativas dos alunos vinculados ao dispositivo. Query: `?limite=100`. |


### Administracao (painel HTML + API)

| Metodo | Rota | Auth | Descricao |
|---|---|---|---|
| GET | `/admin` | — | Painel administrativo (HTML) |
| GET | `/admin/api/app-version` | — | Versao publicada do app |
| PUT | `/admin/api/app-version` | Header `X-Admin-Token` | Atualiza versao/URL/notas do app |
| GET | `/admin/api/alunos` | Header `X-Admin-Token` | Lista alunos ativos |
| GET | `/admin/api/resumo` | Header `X-Admin-Token` | Resumo de presenca/faltas por data. Query: `?data=YYYY-MM-DD` |
| POST | `/admin/api/faltas/enviar` | Header `X-Admin-Token` | Envia notificacoes de falta para ausentes. Body: `{data, titulo?, mensagem?}` |
| POST | `/admin/api/personalizada/enviar` | Header `X-Admin-Token` | Envia aviso personalizado. Body: `{titulo, mensagem, destino, matricula?}` |
| GET | `/admin/api/historico` | Header `X-Admin-Token` | Historico administrativo. Query: `?limite=100` |

## Notas

- Matriculas sao normalizadas (remocao de pontos, hifens e espacos) e comparadas sem acentuacao/caixa.
- Tokens FCM invalidos/expirados tem o vinculo removido automaticamente no processamento de eventos.
- Idempotencia de eventos por `external_id`/`id_log`.
- A automacao externa (Control iD) e responsavel por enviar eventos ja tratados.

> Nunca exponha `ALUNOS_ADMIN_TOKEN`, `CEMAB_EVENT_TOKEN` ou credenciais Firebase no codigo do app ou em repositorio publico.
