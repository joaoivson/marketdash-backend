# Exemplos de Uso da API

> ⚠️ **Documento legado — não confira nada por aqui, e não acrescente endpoint
> novo.** Ele descreve uma API que mudou de forma:
> - o prefixo real é **`/api/v1`** (`API_V1_STR` em `core/config.py`);
> - a autenticação é **Supabase Auth no frontend** — o backend valida o token
>   com `auth.get_user()` e **não emite JWT próprio**.
>
> A fonte da verdade dos endpoints é o **`/openapi.json`** do próprio serviço
> (ou `/docs`), que nasce do código e não envelhece.

Este documento contém exemplos práticos de como usar a API do MarketDash Backend.

## 🔐 1. Autenticação

### Registrar Novo Usuário

```bash
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "usuario@example.com",
    "password": "senha123"
  }'
```

**Resposta:**
```json
{
  "id": 1,
  "email": "usuario@example.com",
  "is_active": true,
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Login

```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "email=usuario@example.com&password=senha123"
```

**Resposta:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Salve o token para usar nos próximos requests:**
```bash
export TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

## 📥 2. Upload de CSV

### Upload de Arquivo CSV

```bash
curl -X POST "http://localhost:8000/api/datasets/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@example_data.csv"
```

**Resposta:**
```json
{
  "id": 1,
  "user_id": 1,
  "filename": "example_data.csv",
  "uploaded_at": "2024-01-15T10:35:00Z"
}
```

### Listar Todos os Datasets

```bash
curl -X GET "http://localhost:8000/api/datasets" \
  -H "Authorization: Bearer $TOKEN"
```

**Resposta:**
```json
[
  {
    "id": 1,
    "user_id": 1,
    "filename": "example_data.csv",
    "uploaded_at": "2024-01-15T10:35:00Z"
  }
]
```

### Obter Dataset Específico

```bash
curl -X GET "http://localhost:8000/api/datasets/1" \
  -H "Authorization: Bearer $TOKEN"
```

### Atualizar Dataset (Refresh)

```bash
curl -X POST "http://localhost:8000/api/datasets/1/refresh" \
  -H "Authorization: Bearer $TOKEN"
```

## 📊 3. Dashboard

### Obter Dashboard Completo (sem filtros)

```bash
curl -X GET "http://localhost:8000/api/dashboard" \
  -H "Authorization: Bearer $TOKEN"
```

### Dashboard com Filtro de Data

```bash
curl -X GET "http://localhost:8000/api/dashboard?start_date=2024-01-01&end_date=2024-01-31" \
  -H "Authorization: Bearer $TOKEN"
```

### Dashboard com Filtro de Produto

```bash
curl -X GET "http://localhost:8000/api/dashboard?product=Produto%20A" \
  -H "Authorization: Bearer $TOKEN"
```

### Dashboard com Múltiplos Filtros

```bash
curl -X GET "http://localhost:8000/api/dashboard?start_date=2024-01-01&end_date=2024-01-31&product=Produto%20A&min_value=1000&max_value=5000" \
  -H "Authorization: Bearer $TOKEN"
```

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
    },
    {
      "period": "2024-01-02",
      "revenue": 2000.00,
      "cost": 800.00,
      "commission": 200.00,
      "profit": 1000.00,
      "row_count": 8
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
    },
    {
      "product": "Produto B",
      "revenue": 18000.00,
      "cost": 7200.00,
      "commission": 1800.00,
      "profit": 9000.00,
      "row_count": 30
    }
  ]
}
```

## 🐍 4. Exemplos em Python

### Usando requests

```python
import requests

BASE_URL = "http://localhost:8000/api"

# 1. Registrar usuário
response = requests.post(
    f"{BASE_URL}/auth/register",
    json={
        "email": "usuario@example.com",
        "password": "senha123"
    }
)
print(response.json())

# 2. Login
response = requests.post(
    f"{BASE_URL}/auth/login",
    data={
        "email": "usuario@example.com",
        "password": "senha123"
    }
)
token = response.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 3. Upload CSV
with open("example_data.csv", "rb") as f:
    files = {"file": ("example_data.csv", f, "text/csv")}
    response = requests.post(
        f"{BASE_URL}/datasets/upload",
        headers=headers,
        files=files
    )
    print(response.json())

# 4. Listar datasets
response = requests.get(
    f"{BASE_URL}/datasets",
    headers=headers
)
print(response.json())

# 5. Obter dashboard
response = requests.get(
    f"{BASE_URL}/dashboard",
    headers=headers,
    params={
        "start_date": "2024-01-01",
        "end_date": "2024-01-31"
    }
)
print(response.json())
```

## 📝 5. Formato do CSV

O CSV deve ter as seguintes colunas obrigatórias:

```csv
date,product,revenue,cost,commission
2024-01-01,Produto A,1000.00,500.00,100.00
2024-01-02,Produto B,2000.00,800.00,200.00
```

**Regras:**
- `date`: Formato YYYY-MM-DD
- `product`: String (nome do produto)
- `revenue`, `cost`, `commission`: Números decimais
- O campo `profit` é calculado automaticamente: `profit = revenue - cost - commission`

## 🔍 6. Acessando a Documentação Interativa

Após iniciar o servidor, acesse:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

Essas interfaces permitem testar a API diretamente no navegador!

