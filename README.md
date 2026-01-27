# MarketDash Backend - SaaS de Análise de Dados

Backend completo para um SaaS de análise de dados, focado em ingestão de CSV, armazenamento acumulativo, agregações analíticas e exposição de APIs para consumo por um frontend React.

## 🚀 Tecnologias

- **Python 3.11+**
- **FastAPI** - Framework web moderno e rápido
- **SQLAlchemy 2.0** - ORM para PostgreSQL
- **PostgreSQL** - Banco de dados relacional
- **Pandas** - Processamento e validação de CSV
- **Pydantic** - Validação de dados e schemas
- **JWT** - Autenticação baseada em tokens
- **Docker & Docker Compose** - Containerização

## 📁 Estrutura do Projeto

```
backend/
├── app/
│   ├── main.py                 # Aplicação FastAPI principal
│   ├── core/
│   │   ├── config.py           # Configurações e variáveis de ambiente
│   │   └── security.py         # JWT e hash de senhas
│   ├── db/
│   │   ├── session.py          # Sessão do banco de dados
│   │   └── base.py             # Base declarativa SQLAlchemy
│   ├── models/
│   │   ├── user.py             # Modelo de usuário
│   │   ├── dataset.py          # Modelo de dataset (upload)
│   │   ├── dataset_row.py      # Modelo de linhas do CSV
│   │   └── subscription.py    # Modelo de assinatura
│   ├── schemas/
│   │   ├── user.py             # Schemas Pydantic para usuários
│   │   ├── dataset.py          # Schemas Pydantic para datasets
│   │   └── dashboard.py        # Schemas Pydantic para dashboard
│   ├── services/
│   │   ├── csv_service.py      # Serviço de processamento de CSV
│   │   └── dashboard_service.py # Serviço de analytics e agregações
│   ├── api/
│   │   ├── deps.py             # Dependências (autenticação)
│   │   └── routes/
│   │       ├── auth.py         # Endpoints de autenticação
│   │       ├── datasets.py     # Endpoints de datasets
│   │       └── dashboard.py    # Endpoints de dashboard
│   └── utils/
├── tests/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## 🗄️ Modelo de Dados

### Usuários (users)
- `id`: ID único
- `email`: Email único do usuário
- `hashed_password`: Senha criptografada
- `is_active`: Status ativo/inativo
- `created_at`: Data de criação

### Datasets (datasets)
- `id`: ID único
- `user_id`: ID do usuário proprietário
- `filename`: Nome do arquivo CSV original
- `uploaded_at`: Data de upload

### Linhas do Dataset (dataset_rows)
- `id`: ID unico
- `dataset_id`: ID do dataset
- `user_id`: ID do usuario proprietario
- `date`: Data da transacao (normalizada)
- `transaction_date`: Data original (quando aplicavel)
- `time`: Hora da transacao (quando aplicavel)
- `product`: Produto normalizado
- `product_name`: Nome original do produto (quando aplicavel)
- `platform`: Plataforma/origem
- `status`: Status da transacao
- `category`: Categoria
- `sub_id1`: Sub ID
- `mes_ano`: Mes/ano de referencia
- `gross_value`: Valor bruto
- `commission_value`: Valor de comissao
- `net_value`: Valor liquido
- `quantity`: Quantidade
- `revenue`: Receita
- `cost`: Custo
- `commission`: Comissao
- `profit`: Lucro (calculado: revenue - cost - commission)
- `raw_data`: JSON bruto da linha (quando aplicavel)

**Índices criados para otimização:**
- `user_id`
- `date`
- `product`
- `(user_id, date)`
- `(user_id, product)`
- `(user_id, date, product)`

## 🚀 Como Executar

### Pré-requisitos

- Docker e Docker Compose instalados
- Python 3.11+ (se executar localmente)

### Opção 1: Docker Compose (Recomendado)

1. Clone o repositório:
```bash
git clone <repository-url>
cd marketdash
```

2. Crie um arquivo `.env` na raiz do projeto (opcional, valores padrão no docker-compose.yml):
```env
DATABASE_URL=postgresql://marketdash_user:marketdash_password@db:5432/marketdash_db
JWT_SECRET=your-secret-key-change-in-production-min-32-chars
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
```

3. Execute o projeto:
```bash
docker compose up
```

4. Acesse a documentação da API:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Opção 2: Execução Local

1. Instale as dependências:
```bash
pip install -r requirements.txt
```

2. Configure as variáveis de ambiente:
```bash
export DATABASE_URL=postgresql://user:password@localhost:5432/marketdash_db
export JWT_SECRET=your-secret-key-change-in-production-min-32-chars
```

3. Execute o servidor:
```bash
uvicorn app.main:app --reload
```

## 📡 Endpoints da API

### 🏥 Health Check

#### Verificar Status da Aplicação
```http
GET /health
```

**Resposta (healthy):**
```json
{
    "status": "healthy",
    "environment": "production",
    "timestamp": "2024-01-15T10:30:00Z",
    "database": "connected",
    "redis": "connected"
}
```

**Resposta (unhealthy):**
```json
{
    "status": "unhealthy",
    "environment": "production",
    "timestamp": "2024-01-15T10:30:00Z",
    "database": "disconnected",
    "redis": "not_configured"
}
```

**Códigos de Status HTTP:**
- `200 OK`: Aplicação saudável (database conectado)
- `503 Service Unavailable`: Aplicação com problemas (database desconectado, etc.)

**O que o Health Check verifica:**
- Conexão com o banco de dados (PostgreSQL/Supabase)
- Status do Redis (se configurado)
- Ambiente atual (production/staging/development)
- Timestamp UTC da verificação

### 🔐 Autenticação

#### Registrar Usuario
```http
POST /api/v1/auth/register
Content-Type: application/json

{
  "email": "usuario@example.com",
  "password": "senha123"
}
```

#### Login
```http
POST /api/v1/auth/login
Content-Type: application/x-www-form-urlencoded
#### Obter Usuario Atual
```http
GET /api/v1/auth/me
Authorization: Bearer {token}
```

#### Atualizar Usuario
```http
PUT /api/v1/auth/users/{user_id}
Authorization: Bearer {token}
```

#### Excluir Usuario
```http
DELETE /api/v1/auth/users/{user_id}
Authorization: Bearer {token}
```

email=usuario@example.com&password=senha123
```

**Resposta:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### 📥 Datasets

#### Upload de CSV
```http
POST /api/v1/datasets/upload
Authorization: Bearer {token}
Content-Type: multipart/form-data

file: [arquivo.csv]
```

**Formato esperado do CSV:**
```csv
date,product,revenue,cost,commission
2024-01-01,Produto A,1000.00,500.00,100.00
2024-01-02,Produto B,2000.00,800.00,200.00
```

**Colunas obrigatórias:**
- `date`: Data (formato: YYYY-MM-DD)
- `product`: Nome do produto
- `revenue`: Receita (número)
- `cost`: Custo (número)
- `commission`: Comissão (número)

**Resposta:**
```json
{
  "id": 1,
  "user_id": 1,
  "filename": "dados.csv",
  "uploaded_at": "2024-01-15T10:30:00Z"
}
```

#### Listar Datasets
```http
GET /api/v1/datasets
Authorization: Bearer {token}
```

#### Obter Dataset Especifico
```http
GET /api/v1/datasets/{dataset_id}
Authorization: Bearer {token}
```

#### Linhas do Dataset Mais Recente
```http
GET /api/v1/datasets/latest/rows?limit=100&offset=0
Authorization: Bearer {token}
```

#### Todas as Linhas (paginado)
```http
GET /api/v1/datasets/all/rows?limit=100&offset=0
Authorization: Bearer {token}
```

#### Linhas de um Dataset
```http
GET /api/v1/datasets/{dataset_id}/rows
Authorization: Bearer {token}
```

#### Aplicar Ad Spend no Dataset Mais Recente
```http
POST /api/v1/datasets/latest/ad_spend
Authorization: Bearer {token}
```

#### Atualizar Dataset (Refresh)
```http
POST /api/v1/datasets/{dataset_id}/refresh
Authorization: Bearer {token}
```

> **Nota:** Este endpoint está preparado para integração futura com API externa.

### 📊 Dashboard

#### Obter Dashboard Completo
```http
GET /api/v1/dashboard?start_date=2024-01-01&end_date=2024-01-31&product=Produto A
Authorization: Bearer {token}
```
### 💸 Ad Spends

#### Listar Ad Spends
```http
GET /api/v1/ad_spends?limit=100&offset=0
Authorization: Bearer {token}
```

#### Criar Ad Spend
```http
POST /api/v1/ad_spends
Authorization: Bearer {token}
```

#### Criar Ad Spends em Lote
```http
POST /api/v1/ad_spends/bulk
Authorization: Bearer {token}
```

#### Atualizar Ad Spend
```http
PATCH /api/v1/ad_spends/{ad_spend_id}
Authorization: Bearer {token}
```

#### Excluir Ad Spend
```http
DELETE /api/v1/ad_spends/{ad_spend_id}
Authorization: Bearer {token}
```

#### Template de Importacao
```http
GET /api/v1/ad_spends/template
Authorization: Bearer {token}
```

### 🔗 Cakto

#### Webhook de Assinaturas
```http
POST /api/v1/cakto/webhook
```

**Parâmetros de Query (todos opcionais):**
- `start_date`: Data inicial (YYYY-MM-DD)
- `end_date`: Data final (YYYY-MM-DD)
- `product`: Nome do produto (busca parcial)
- `min_value`: Valor mínimo
- `max_value`: Valor máximo

**Resposta:**
```json
{
  "kpis": {
    "total_revenue": 50000.00,
    "total_cost": 20000.00,
    "total_commission": 5000.00,
    "total_profit": 25000.00,
    "total_rows": 100
  },
  "period_aggregations": [
    {
      "period": "2024-01-01",
      "revenue": 1000.00,
      "cost": 500.00,
      "commission": 100.00,
      "profit": 400.00,
      "row_count": 5
    }
  ],
  "product_aggregations": [
    {
      "product": "Produto A",
      "revenue": 20000.00,
      "cost": 8000.00,
      "commission": 2000.00,
      "profit": 10000.00,
      "row_count": 50
    }
  ]
}
```

## 🔒 Segurança

- **Autenticação JWT**: Todos os endpoints (exceto auth) requerem token JWT
- **Isolamento de Dados**: Usuários só acessam seus próprios dados
- **Validação de Dados**: Pydantic valida todos os inputs
- **Hash de Senhas**: Bcrypt para armazenamento seguro de senhas

## 📊 Características

### Processamento de CSV
- Validação automática de colunas obrigatórias
- Normalização de dados (datas, números)
- Cálculo automático de lucro (profit = revenue - cost - commission)
- Tratamento de erros e validações robustas
- Suporte a múltiplos encodings (UTF-8, Latin-1, ISO-8859-1)

### Analytics
- Agregações SQL otimizadas
- Filtros flexíveis por data, produto e valores
- KPIs calculados em tempo real
- Agregações por período e por produto
- Queries otimizadas com índices

### Arquitetura
- Separação de responsabilidades (Services, Models, Schemas)
- Código escalável e manutenível
- Preparado para integração com APIs externas
- Base sólida para crescimento do SaaS

## 🧪 Testes

Para executar testes (quando implementados):
```bash
pytest tests/
```

## 🔄 Migrações de Banco de Dados

O projeto usa SQLAlchemy com criação automática de tabelas. Para migrações mais avançadas, considere usar Alembic:

```bash
alembic init migrations
alembic revision --autogenerate -m "Initial migration"
alembic upgrade head
```

## 📝 Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `DATABASE_URL` | URL de conexão com PostgreSQL | - |
| `JWT_SECRET` | Chave secreta para JWT | - |
| `JWT_ALGORITHM` | Algoritmo JWT | HS256 |
| `JWT_EXPIRATION_HOURS` | Horas de expiração do token | 24 |
| `CAKTO_WEBHOOK_SECRET` | Chave secreta para validação de webhooks Cakto | - |

## 🚀 Próximos Passos

- [ ] Implementar testes unitários e de integração
- [ ] Adicionar rate limiting
- [ ] Implementar cache para queries frequentes
- [ ] Adicionar suporte a exportação de dados
- [ ] Integração com API externa para atualização de dados
- [ ] Dashboard com mais métricas e visualizações
- [ ] Suporte a múltiplos formatos de arquivo (Excel, JSON)
- [ ] Sistema de notificações
- [ ] Integração com Supabase Auth

## 📄 Licença

Este projeto é proprietário.

## 👥 Contribuindo

Este é um projeto interno. Para sugestões e melhorias, entre em contato com a equipe de desenvolvimento.

---

**Desenvolvido com ❤️ para análise de dados eficiente e escalável**

