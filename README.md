# CLACS Escola: backend

API de gestão escolar da Talatona Kids em **FastAPI + SQLAlchemy 2 + Alembic**. Corre em SQLite para
desenvolvimento e em **PostgreSQL no Render** em produção.

## Arranque local

Dentro desta pasta (`clacs-backend`):

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows (PowerShell ou cmd); em Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head              # cria as 66 tabelas do esquema do cliente
python -m scripts.seed --demo     # perfis, administrador e dados de demonstração do protótipo
uvicorn app.main:app --reload
```

- Nas vezes seguintes basta activar o ambiente (`.venv\Scripts\activate`) e correr `uvicorn app.main:app --reload`.
- Documentação interactiva da API: http://localhost:8000/api/docs
- Testes: `pytest` (36 testes; usam uma base SQLite temporária. Para testar em PostgreSQL:
  `TEST_DATABASE_URL=postgresql://... pytest`, numa base vazia dedicada a testes)

O frontend está em [../clacs-frontend](../clacs-frontend/README.md).

Para usar o PostgreSQL local em vez de SQLite, crie a base (`createdb clacs`) e defina `DATABASE_URL` no `.env`.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `app/models.py` | Os 66 modelos, secção a secção do documento do esquema (núcleo, académico, cadastro, segurança, propinas, refeitório, Primavera, comunicação, pedagógico, alertas, importação) |
| `app/routers/` | Endpoints por módulo (auth, académico, cadastro, financeiro, refeitório, comunicação, pedagógico, painel, primavera, importação) |
| `app/services/` | Regras de negócio: cobranças e pagamentos, alertas, motor de regras e fila de mensagens, médias, PDFs, fecho do ano, relatórios, tarefas periódicas |
| `app/integracoes/` | Ligações externas: e-mail (SMTP), WhatsApp (Meta), SMS (Twilio), Primavera, gateway de pagamentos |
| `app/agendador.py` | Agendador interno (APScheduler) para as tarefas diárias, a fila e os relatórios |
| `app/armazenamento.py`, `app/pdf.py` | Ficheiros enviados/gerados e documentos PDF |
| `alembic/versions/` | `0001` esquema inicial (em PostgreSQL também vistas `vw_*`, triggers e índice de pesquisa); `0002` segredo da verificação em dois passos |
| `scripts/seed.py` | Perfis, permissões, administrador e dados de demonstração |
| `scripts/migrar_dados.py` | Cópia completa de uma base para outra (ex.: local → Render) |
| `scripts/tarefas_diarias.py` | As mesmas tarefas do agendador, para correr por cron fora da API |

## Módulos da API (prefixo `/api`)

- **Autenticação e utilizadores**: login JWT com bloqueio após tentativas falhadas, verificação em dois passos (TOTP: Google Authenticator, Microsoft Authenticator…), recuperação de senha por e-mail, perfis (`admin`, `direccao`, `secretaria`, `financeiro`, `coordenacao`, `professor`, `encarregado`) e permissões por módulo/acção guardadas em `perfil_permissao`. Todas as consultas ficam limitadas ao estabelecimento do utilizador; o professor só vê as turmas onde lecciona ou é director de turma.
- **Académico**: anos lectivos (com trimestres automáticos e um único ano activo), níveis, classes, plano curricular, salas, disciplinas, funcionários, turmas com ocupação e vagas, atribuição de professores, **fecho do ano** (média final, aprovado/reprovado, exclusão por faltas; simulação antes de gravar) e **passagem** dos alunos para o ano seguinte, com os serviços.
- **Cadastro**: alunos (número de processo automático), encarregados, ligação aluno–encarregado (principal / responsável financeiro), documentos, matrículas, mudança de turma com histórico, **visão 360°**.
- **Propinas e cobranças**: serviços, tabela de preços por ano/classe, planos, multas, descontos, métodos de pagamento; geração mensal idempotente; pagamentos parciais e por encarregado (salda as mais antigas primeiro); confirmar/rejeitar comprovativos; devedores; exportação Excel/CSV.
- **Refeitório**: adesões (alimentação vs. só utilização), ementas, registo diário de refeições.
- **Comunicação e regras**: modelos de mensagem com variáveis, regras sem programação (antes/depois do vencimento, dívida acima de X dias → alerta à Direcção, ocorrências), fila de mensagens sem repetições, comunicados por escola/classe/turma.
- **Pedagógico**: avaliações e notas com pesos, pautas por período (e fecho em `resultado_periodo`), assiduidade, ocorrências, registo diário de comportamento.
- **Painel, alertas e assistente**: KPIs e cobrança por turma; alertas de risco de incumprimento, queda de desempenho, faltas, comportamento e Primavera; assistente em linguagem natural (só leitura, registado em `ia_consulta_log`); relatórios e auditoria.
- **Primavera**: estado da sincronização, divergências (resolver regista o pagamento com a referência do documento), configuração.
- **Importação**: CSV de alunos, encarregados, cobranças e pagamentos do software actual, com relatório de erros por linha.
- **Documentos e ficheiros**: PDFs de recibo, extracto, boletim, pauta e declaração de matrícula; envio de documentos, fotos e comprovativos (PDF/JPG/PNG/WEBP, até 10 MB, conteúdo verificado).
- **Portal do encarregado** (`/api/portal`): educandos, cobranças, notas, boletim e extracto em PDF, pagamento por referência ou envio de comprovativo, mensagens, preferências de contacto. A escola cria a conta em `POST /api/encarregados/{id}/conta`.
- **Relatórios agendados**: devedores, cobranças, cobrança por turma e refeitório, em Excel, CSV ou PDF, enviados por e-mail segundo uma agenda cron.

## Deploy no Render (PostgreSQL)

1. Coloque a pasta `clacs-escola` inteira (backend + frontend + `render.yaml`) num repositório GitHub/GitLab.
2. No Render: **New + → Blueprint**, escolha o repositório. O `render.yaml` (na raiz do repositório) cria a base `clacs-db`, a API `clacs-escola` e o site estático `clacs-frontend`.
   Depois do primeiro deploy, confirme que `VITE_API_URL` (frontend) tem o endereço real da API e ponha em `CORS_ORIGINS` (API) o endereço do frontend.
3. Em cada arranque o serviço corre `alembic upgrade head` e o seed (idempotentes), e depois a API.
4. A senha inicial do administrador é gerada pelo Render: veja `ADMIN_PASSWORD` em *Environment* e altere-a após o primeiro login (`POST /api/auth/senha`).
5. Para carregar os dados de demonstração, defina `SEED_DEMO=true` antes do primeiro arranque.

### Migrar dados de uma base local para o Render

Copie a **External Database URL** da base no painel do Render e corra:

```bash
python -m scripts.migrar_dados --origem sqlite:///./clacs.db --destino "postgresql://...external-url..."
```

O script cria o esquema no destino, copia as tabelas pela ordem das chaves estrangeiras e acerta as sequências de
identidade. Recusa copiar para uma base com dados; `--limpar` apaga o destino primeiro. (Alternativa só entre
PostgreSQL: `pg_dump --data-only` + `psql`.)

## Integrações externas: estado

Todas funcionam em modo simulado (registam no log e o resto do sistema segue igual) até as credenciais estarem no
`.env`. A lista completa de variáveis está em `.env.example`.

| Integração | Código | Para activar |
|---|---|---|
| E-mail | `app/integracoes/email.py` | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` (pode ser o Gmail da escola com senha de aplicação) |
| WhatsApp | `app/integracoes/mensagens.py` | Conta WhatsApp Business na Meta: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` e um modelo aprovado em `WHATSAPP_TEMPLATE` |
| SMS | `app/integracoes/mensagens.py` | Conta Twilio com remetente para Angola: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` |
| Primavera | `app/integracoes/primavera.py` | Versão e endereço da Web API do Primavera; validar os caminhos em `CAMINHOS`; credenciais `PRIMAVERA_*`; activar em `PUT /api/primavera/config` |
| Pagamentos por referência | `app/integracoes/pagamentos.py` | Contrato com um gateway (ProxyPay, EMIS, banco); escrever a classe desse gateway; `PAGAMENTOS_GATEWAY` e `PAGAMENTOS_WEBHOOK_SEGREDO` |

## Notas de produção

- **Agendador**: com `AGENDADOR_ACTIVO=true` corre dentro da API. Use uma só instância. No plano gratuito do Render a API adormece e as tarefas não correm enquanto dorme.
- **Ficheiros**: no Render o disco apaga-se a cada deploy; para guardar documentos e comprovativos é preciso um disco persistente (plano pago) ou um armazenamento externo (S3/R2).
- **Bloqueio de login**: fica em memória; com várias instâncias passa a contar por instância.
- **Assistente**: responde por regras fixas (dívidas, turmas, taxa, recebidos, número de alunos, refeitório).

## Diferenças em relação ao SQL original

- Chaves `GENERATED BY DEFAULT AS IDENTITY` (em vez de `ALWAYS`) para permitir importar IDs do software actual.
- Tabelas no esquema `public` (sem `escola.`), que é o padrão no Render.
- `comunicado_alvo` ganhou uma coluna `id`, porque o ORM precisa de chave primária.
