# Simulador de urna eletrônica

Aplicação em português com Flask, SQLite e JavaScript. Não exige serviços pagos nem coleta identidade de eleitores. **Simulador — sem vínculo com a Justiça Eleitoral.**

## Executar

Requisitos: Python 3.12 ou superior.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Acesse a rota `/` para votar e `/admin` para administrar no servidor da aplicação (porta 5000). Primeiro acesso: usuário **admin**, senha **admin**. Altere a senha no painel antes de disponibilizar o sistema.

O arquivo `.env.example` documenta as variáveis. Elas devem ser exportadas no ambiente: o aplicativo não carrega `.env` automaticamente. `DATA_DIR` define o diretório persistente (padrão `instance/` junto ao projeto). `PORT` configura o servidor de desenvolvimento; `COOKIE_SECURE=1` exige HTTPS para o cookie. Não ative essa opção em acesso HTTP local.

Para produção, use HTTPS e um servidor WSGI, por exemplo:

```sh
COOKIE_SECURE=1 .venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 'app:create_app()'
```

O servidor de desenvolvimento não é indicado para publicação na Internet. O diretório `DATA_DIR` deve estar em um volume persistente, compartilhado pelos processos da mesma instância. Para backup consistente, utilize a API de backup do SQLite e copie também `photos/` e `session.key`. Proteja esse diretório contra acesso público. A chave de sessão é gerada automaticamente, permanece nesse diretório e não deve ser versionada.

## Fluxo

1. Entre no painel e altere a senha inicial.
2. Cadastre candidatos com nome, número textual e foto (JPEG, PNG ou WebP de até 5 MB e 20 megapixels). As fotos são recodificadas em JPEG sem metadados.
3. Crie um turno, configure um único cargo, de 1 a 10 dígitos e os participantes compatíveis.
4. Abra o turno. A lista e os dados selecionados são congelados nesse momento. Um único turno pode ficar aberto.
5. Na urna, use os números, BRANCO, CORRIGE e CONFIRMA. Teclado físico: números, B, Backspace/Escape e Enter. Um número completo desconhecido registra voto nulo. Após resposta bem-sucedida do servidor, a urna emite som, mostra FIM e reinicia para outro eleitor.
6. Consulte a apuração e exporte resultados ou histórico em CSV. Encerre o turno com confirmação. Crie outro para uma apuração independente.

## Persistência e segurança

A migração idempotente `migrations/001_initial.sql` é aplicada na inicialização. `urna.db` conserva candidatos, turnos, snapshots e votos; `photos/` conserva fotos inclusive as usadas pelo histórico. Nenhum dado de negócio depende de localStorage.

Senhas usam scrypt por meio de Werkzeug. A sessão usa cookie assinado HttpOnly e SameSite Strict; operações administrativas, incluindo login e logout, exigem token CSRF. Todas as APIs de cadastro, administração, resultados e CSV verificam autenticação no backend. A página de entrada é pública. Dados públicos de votação incluem somente turno aberto e candidatos; fotos de candidatos são públicas.

`BEGIN IMMEDIATE` serializa abertura, encerramento e inserção de votos. A conferência de turno aberto e a gravação ocorrem na mesma transação. A chave de idempotência tem restrição única e vinculação ao conteúdo; reenvios retornam o mesmo ID, inclusive após encerrar, sem nova gravação. O próximo eleitor recebe uma nova chave. Votos armazenam UUID, instante UTC, turno e escolha, sem identidade, IP ou dispositivo. Logs HTTP do servidor não fazem parte da tabela de votos; configure a retenção de logs da infraestrutura conforme seu uso.

O painel mostra horários em America/Sao_Paulo; o histórico CSV inclui esse fuso e o resumo CSV identifica timestamps UTC. Percentuais são calculados apenas sobre votos válidos. Empates incluem candidatos com o mesmo número de votos, inclusive zero. Campos de texto em CSV neutralizam prefixos de fórmulas.

O modelo permite inspecionar escolhas individuais e serve para simulações; não reproduz todas as garantias de sigilo de uma eleição oficial. Não limita votos por pessoa porque não coleta identidade.

## Validação

```sh
.venv/bin/python -m pytest -q
node --check static/admin.js
node --check static/vote.js
```

Os testes usam banco e fotos temporários e cobrem login, APIs sem sessão, CSRF, senha, upload e validações, turno único, votos válido/branco/nulo, idempotência concorrente, encerramento concorrente, rejeição de novos votos, resultados independentes e histórico persistente. Testes de navegador ficam em `tests/browser.cjs` (Playwright deve estar instalado e ter Chromium disponível); executá-los contra uma instância com `DATA_DIR` temporário para não modificar dados reais.

No ambiente preparado, Chromium está disponível em `/usr/bin/chromium`: use `CHROMIUM_PATH=/usr/bin/chromium node tests/browser.cjs` contra a instância temporária. Em outra máquina, instale Playwright e execute `npx playwright install chromium`.
