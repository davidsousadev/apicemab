# CEMAB — painel administrativo, notificações de falta e controle de versões

## Painel

A interface administrativa fica em:

`https://apisme.vercel.app/admin`

O painel inclui:

- Visão geral das passagens recebidas e das faltas calculadas por data;
- envio confirmado de notificações para alunos sem registro de passagem na data escolhida;
- criação de avisos personalizados para todos os alunos ativos vinculados ou para um aluno específico;
- histórico dos envios administrativos;
- configuração da versão disponível do CEMAB App, link do APK e mensagem curta da atualização.

## Autenticação e configuração

Defina `ALUNOS_ADMIN_TOKEN` nas variáveis de ambiente da API. As operações administrativas exigem esse token por meio de `X-Admin-Token`. O navegador mantém o token apenas em memória durante a sessão.

Mantenha as credenciais `FIREBASE_*` já usadas pelo Firebase Admin e configure `CEMAB_EVENT_TOKEN` para validar o envio do script Control iD. Em produção, `DATABASE_URL` deve apontar para banco persistente. A tabela `cemab_configuracao_app` armazena a configuração de versão; ela é criada pelo `SQLModel.metadata.create_all()` quando a API inicializa.

## Funcionamento das presenças e faltas

`POST /presencas/eventos` continua recebendo o JSON gerado pelo script Control iD, mas a API **somente registra as passagens**. Não é enviado push quando o aluno passa pelo leitor. Os registros servem ao cálculo de faltas no painel administrativo.

Passos recomendados para enviar faltas:

1. Importe o relatório completo do Control iD no script local.
2. Confira se o script terminou e se todos os lotes foram aceitos pela API.
3. No painel, selecione a data do relatório e atualize o resumo.
4. Confira o número de presentes e possíveis faltosos.
5. Somente depois da conferência, clique em **Conferir e enviar notificações**.

A API não tem como saber se o relatório está incompleto. Se você enviar faltas antes da importação completa, alunos presentes poderão ser tratados como ausentes. O envio de faltas é manual e explícito pelo painel; não ocorre automaticamente durante a importação.

## Atualização do aplicativo fora da Google Play

No painel, entre em **Versão do aplicativo** e configure:

- **Versão disponível**: por exemplo, `1.2.0`;
- **Link para baixar o APK**: URL pública `https://...` ou `http://...`;
- **Mensagem opcional**: texto curto mostrado junto ao banner.

O endpoint público `GET /admin/api/app-version` fornece somente esses metadados para o app. A alteração exige autenticação: `PUT /admin/api/app-version`.

O aplicativo Cordova usa `cordova-plugin-app-version` para consultar a versão nativa instalada, compara com a versão publicada e mostra um banner apenas quando a versão publicada é maior e existe URL de download. Se o app instalado for anterior à implantação da funcionalidade de atualização, ele ainda não tem o código que consulta esse endpoint; esses usuários precisam instalar manualmente uma versão que inclua o verificador pelo menos uma vez.

## Rotas administrativas

- `GET /admin`: dashboard web;
- `GET /admin/api/app-version`: metadados públicos para o banner do app;
- `PUT /admin/api/app-version`: grava a versão/link/mensagem (requer `X-Admin-Token`);
- `GET /admin/api/alunos`: alunos ativos;
- `GET /admin/api/resumo?data=AAAA-MM-DD`: resumo de presença/falta;
- `POST /admin/api/faltas/enviar`: envia notificações de falta calculadas para a data;
- `POST /admin/api/personalizada/enviar`: aviso personalizado;
- `GET /admin/api/historico`: histórico de envios;
- `POST /presencas/eventos`: registra passagens; não envia push positivo;
- `GET /presencas/historico`: histórico de notificações administrativas dos dispositivos vinculados.
