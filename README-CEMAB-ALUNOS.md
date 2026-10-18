Aqui está a documentação **atualizada e alinhada com a versão final** — com `id_log`, estrutura de lote confirmada, regras da automação e detalhes de idempotência corrigidos:

---

# CEMAB App — Alunos, Vínculos, Presenças e Notificações

Esta versão integra dispositivos FCM aos alunos e implementa os endpoints de recebimento de eventos de presença para automação externa. A ferramenta que processa o relatório do Control iD **é um sistema separado**: roda em computador dedicado, lê os registros, aplica deduplicação (1ª passagem do dia) e envia eventos formatados para os endpoints abaixo.

---

## 1. Variáveis de Ambiente

Configure na hospedagem da API (ex.: Vercel):

| Variável | Finalidade |
|---|---|
| `DATABASE_URL` | Banco de dados persistente (PostgreSQL recomendado) |
| `ALUNOS_ADMIN_TOKEN` | Chave exclusiva para importação/cadastro do cadastro oficial de alunos |
| `CEMAB_EVENT_TOKEN` | Chave **diferente da anterior**, usada SOMENTE pela automação de presença |
| Credenciais Firebase | Conjunto listado em `.env.example` para envio de notificações FCM |

> ⚠️ **Nunca** exponha `ALUNOS_ADMIN_TOKEN`, `CEMAB_EVENT_TOKEN` ou credenciais Firebase no código do aplicativo Cordova nem em repositório público. O `CEMAB_EVENT_TOKEN` deve residir **apenas** no ambiente da API e no computador que executa a automação.

As tabelas novas são criadas automaticamente pelo `SQLModel.metadata.create_all`:
- `aluno_dispositivo` → vínculo entre aluno e token FCM
- `cemab_evento_presenca` → histórico e idempotência de eventos enviados

---

## 2. Cadastro Oficial de Alunos

A base de alunos inicia vazia. Antes de vínculos ou eventos, importe o cadastro oficial.

### `POST /alunos/importar` — Administrativo
**Cabeçalho:** `X-Admin-Token: <ALUNOS_ADMIN_TOKEN>`

```json
{
  "alunos": [
    {"matricula": "12.345.678", "nome_completo": "NOME COMPLETO DO ALUNO", "ativo": true},
    {"matricula": "23.456.789", "nome_completo": "OUTRO ALUNO DE EXEMPLO", "ativo": true}
  ]
}
```

- Até **5.000 alunos** por requisição
- Matrícula normalizada: remove pontos, hífens e espaços
- Matrícula duplicada → atualiza `nome_completo` e `ativo`
- Duplicatas no mesmo lote → mantém a primeira ocorrência
- Consultas por nome ignoram acentuação, caixa e espaços extras

### `POST /alunos/cadastrar` — Administrativo
Cadastra um único aluno, protegido pelo mesmo cabeçalho `X-Admin-Token`.

---

## 3. Vínculo no Aplicativo

### `POST /users/cadastrar`
Registra ou atualiza token FCM. Compatível com formato simples:
```json
{ "token": "TOKEN_FCM" }
```
Ou com renovação de token:
```json
{ "token": "NOVO_TOKEN", "token_anterior": "TOKEN_ANTIGO" }
```
Atualiza vínculos existentes para não perder associações.

### `POST /alunos/vincular`
Valida dados contra o cadastro oficial e cria a associação aluno–dispositivo. O token deve ter sido registrado previamente.

```json
{
  "matricula": "12.345.678",
  "nome_completo": "NOME COMPLETO DO ALUNO",
  "token": "TOKEN_FCM_DO_DISPOSITIVO"
}
```

- Sucesso → `{success: true, vinculo_ativo: true, ...dados_do_aluno}`
- Dados não conferem → `404`
- Token não cadastrado → `409`

### `POST /alunos/desvincular`
Remove somente a associação entre aluno e aquele token:
```json
{"matricula": "12.345.678", "token": "TOKEN_FCM"}
```

### `GET /alunos/vinculo`
Retorna alunos vinculados ao dispositivo. Envie o token no cabeçalho:
```
X-FCM-Token: TOKEN_FCM
```
> Token na URL é evitado para não aparecer em logs.

---

## 4. Recebimento de Eventos de Presença

A automação externa é responsável por:
- Ler e interpretar o relatório do Control iD
- Resolver matrícula (coluna Matrícula → se vazia, usa Código do Usuário)
- Preservar **valores brutos** de `identificacao`, `codigo_usuario` e `autorizacao`
- Deduplicação: **apenas a primeira passagem do aluno no dia** é enviada
- Formatar `data_hora` em ISO 8601 com fuso `-03:00`
- Enviar em lote de até 500 eventos

---

### `POST /presencas/eventos` — Lote de Eventos ✅ Formato Oficial

**Cabeçalho obrigatório:**
```
X-CEMAB-Event-Token: <CEMAB_EVENT_TOKEN>
Content-Type: application/json
```

**Corpo da requisição:**
```json
{
  "eventos": [
    {
      "id_log": "log-exemplo-0001",
      "matricula": "12345678",
      "nome_completo": "NOME COMPLETO DO ALUNO",
      "data_hora": "2026-10-11T07:35:00-03:00",
      "identificacao": "1717658368",
      "codigo_usuario": "156",
      "autorizacao": "7"
    }
  ]
}
```

#### Detalhes dos campos:
| Campo | Descrição |
|---|---|
| **`id_log`** | Identificador único do evento (ex.: `linha_5_dia_2026-10-14`). Usado para idempotência — reenvio com mesmo valor é ignorado |
| `matricula` | Sem pontos/hífens, normalizada pela automação; chave de busca no cadastro |
| `nome_completo` | Nome do aluno, capitalizado e normalizado |
| `data_hora` | Horário **real da passagem** em ISO 8601 com fuso `-03:00`; não fixado no início do dia |
| `identificacao` | Valor bruto do campo "Identificação" do Control iD — preservado integralmente |
| `codigo_usuario` | Valor bruto do "Código (Usuário)" — sem transformação |
| `autorizacao` | Valor bruto do status de autorização — **não filtrado, não descartado**, enviado exatamente recebido |

#### Comportamento da API:
- ✅ `id_log` já registrado → evento ignorado (idempotência)
- ✅ Aluno ativo + dispositivos vinculados → notificação FCM enviada para cada aparelho
- ⚠️ Aluno sem vínculo ativo → registra sem erro, sem envio de push
- ⚠️ Matrícula não encontrada → resposta com motivo, sem inferência por nome
- ⚠️ Token FCM inválido/expirado → vínculo removido automaticamente
- Lotes maiores que 500 → divididos pela automação

---

### `POST /presencas/evento` — Evento Individual (compatibilidade)
Mantido para compatibilidade; aceita os mesmos campos de objeto. Preferir `/presencas/eventos` para processamento em massa.

---

### `GET /presencas/historico`
Recupera eventos para sincronização do aplicativo.

**Cabeçalho:** `X-FCM-Token: TOKEN_FCM`
**Parâmetro opcional:** `?limite=100`

Retorna apenas eventos:
- De alunos vinculados ao dispositivo
- Que tiveram pelo menos uma tentativa de notificação enviada
- Sem estado de leitura (gerenciado localmente no app)

---

## 5. Fluxo Completo

```
1. App → solicita permissão de notificação → obtém token FCM
   → POST /users/cadastrar

2. Responsável → informa matrícula e nome no app
   → POST /alunos/vincular → valida e grava vínculo

3. Automação (computador dedicado):
   ├─ Lê relatório Control iD
   ├─ Resolve matrícula (coluna 6 → coluna 3)
   ├─ Filtra → 1ª passagem do aluno no dia
   ├─ Preserva todos os campos brutos
   └─ POST /presencas/eventos → X-CEMAB-Event-Token

4. API → consulta vínculos → envia FCM aos dispositivos associados

5. App → recebe notificação → exibe em primeiro plano
   └─ Sincroniza histórico ao reabrir (GET /presencas/historico)
```

---

## 6. Implantação e Validação

1. Fazer backup do código e do banco de dados
2. Configurar todas as variáveis de ambiente na hospedagem
3. Publicar a API — tabelas novas são criadas automaticamente
4. Importar cadastro oficial via `/alunos/importar`
5. Instalar e vincular o aplicativo em dispositivos reais
6. **Teste de integração:**
   ```bash
   POST /presencas/eventos
   X-CEMAB-Event-Token: <token>
   Body: lote com 1 evento de exemplo
   ```
   Confirmar: resposta de sucesso + recebimento da notificação no app
7. Validar cenários: app em primeiro plano, segundo plano, fechado e reabertura

> ⚠️ Se `CEMAB_EVENT_TOKEN` não estiver configurado, a API retorna **`503 Service Unavailable`** para requisições de presença. A validação de matrícula e nome não constitui, por si só, comprovação de identidade do responsável — avalie etapas adicionais de confirmação conforme política da escola.

---
