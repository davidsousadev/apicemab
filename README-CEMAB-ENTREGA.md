# CEMAB App — pacote de atualização

Este pacote reúne a API APIS ME com painel administrativo, o projeto Cordova CEMAB App e o script Control iD atualizado.

## O que mudou

- **API/dashboard:** painel administrativo responsivo com resumo do dia, conferência e envio manual de faltas, avisos personalizados para todos os alunos ativos com vínculo ou para uma matrícula, histórico de envios e configuração de versão/link/mensagem do APK.
- **Presenças:** `POST /presencas/eventos` continua recebendo as passagens do Control iD, porém não envia notificação de presença. O histórico do aplicativo apresenta notificações administrativas de falta e avisos personalizados.
- **Atualização do app:** o app consulta a versão nativa instalada e, se for inferior à publicada no painel, mostra um banner com versão, mensagem e link configurado para baixar o APK.
- **Termos de Uso:** checkbox obrigatória no formulário de vínculo. Sem marcar o aceite, o app não faz a requisição para vincular o aluno. O texto permanece como marcador para inserção manual pelo responsável pelo projeto.
- **Créditos:** nova aba com crédito a David Sousa da Silva, área de contato do desenvolvedor configurável e link de WhatsApp da escola configurável.
- **Script:** preserva o fluxo local da planilha; envia registros de passagem à API sem disparar push positivo.

## Implantação da API

1. Publique a pasta `apisme` na implantação atual.
2. Configure `ALUNOS_ADMIN_TOKEN` para proteger a administração.
3. Mantenha/configure as variáveis `FIREBASE_*` necessárias à conta de serviço e `CEMAB_EVENT_TOKEN` igual ao valor usado no script.
4. Em produção, configure `DATABASE_URL` para um banco persistente. A tabela de configuração da versão é criada pelo `SQLModel.metadata.create_all()` na inicialização.
5. Após implantar, abra `https://apisme.vercel.app/admin`, autentique-se com `ALUNOS_ADMIN_TOKEN` e acesse **Versão do aplicativo** para configurar versão disponível, link público do APK e mensagem opcional.

O painel calcula as faltas com base nos eventos recebidos e no cadastro de alunos ativos. **Importe o relatório completo e confirme os lotes antes de enviar faltas**: a API não consegue inferir se o arquivo terminou de ser importado. O envio de faltas é manual e pede confirmação; não é disparado automaticamente durante a importação.

## Preparar e compilar o aplicativo

Na pasta `cemabapp`:

```bash
npm install
cordova platform add android@13
cordova plugin add cordova-plugin-app-version@0.1.14
cordova prepare android
cordova build android --release
```

O plugin de versão está referenciado no `config.xml` e no `package.json`; o código-fonte dele é restaurado pelo comando `cordova plugin add` acima quando o ambiente tem acesso ao registro npm. Se o projeto já tem a plataforma Android configurada, não é necessário adicioná-la novamente.

Antes de distribuir o APK, conclua as configurações abaixo:

- **Termos de Uso:** edite o conteúdo de `termsModalContent` em `cemabapp/www/index.html` e coloque os termos oficiais aprovados pela escola.
- **WhatsApp e contato do desenvolvedor:** configure `SCHOOL_WHATSAPP` e `DEVELOPER_CONTACT_URL` em `cemabapp/www/js/config.js`. Os valores estão vazios para não inventar dados de contato.
- **Versão do APK:** mantenha a mesma versão em `config.xml`, `package.json` e `www/js/config.js`; gere o APK e hospede-o em um endereço acessível ao celular. Depois configure essa versão e URL em **Versão do aplicativo** no painel administrativo.

### Limitação da primeira atualização

Um APK antigo que não contém o verificador de versão não consegue mostrar o banner retroativamente. Quem tiver instalado uma versão anterior precisará instalar manualmente este primeiro APK atualizado (`1.1.0`). Depois disso, versões futuras poderão ser anunciadas no banner.

## Usar o script Control iD

Coloque `Controle_Presenca.xlsx` na mesma pasta do arquivo `Controle_Presenca_CEMAB_v3_5_5.py`, execute o script e importe o relatório completo. O processamento da planilha continua local e preserva as abas já previstas. Após confirmar que todos os lotes foram aceitos pela API, use o dashboard para revisar e enviar faltas.

## Testes e limitações desta entrega

Foram executadas verificações estáticas de sintaxe em Python (`compileall`) e em todos os JavaScript do app e do dashboard (`node --check`), além de uma verificação estrutural dos IDs do HTML. Não foi possível executar a integração real contra Firebase/produção neste ambiente, pois não havia dependências de runtime e credenciais de produção disponíveis. Teste primeiro com poucos alunos e um dispositivo antes de enviar notificações para todos.
