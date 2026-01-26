# 🏗️ Arquitetura de Serviços - DashAds

## 📋 Visão Geral

Este documento descreve como os serviços estão organizados e quais tecnologias são usadas para cada funcionalidade.

---

## ✅ Serviços que PERMANECEM no FastAPI

### 1. **CSVService** (`app/services/csv_service.py`)
**Responsabilidade**: Processamento e validação de arquivos CSV

**O que faz:**
- Validação de encoding (UTF-8, Latin-1, ISO-8859-1)
- Normalização de colunas
- Validação de tipos (datas, números)
- Limpeza de dados inválidos
- Cálculo de profit (revenue - cost - commission)
- Tratamento de erros

**Por que fica no FastAPI:**
- Lógica customizada complexa com Pandas
- Processamento pesado de dados
- Validações específicas do domínio
- Melhor performance para data processing

**Status**: ✅ **NÃO PRECISA MUDAR**

---

### 2. **DashboardService** (`app/services/dashboard_service.py`)
**Responsabilidade**: Analytics e agregações de dados

**O que faz:**
- Cálculo de KPIs (totais, médias, contagens)
- Agregações por período (data)
- Agregações por produto
- Filtros dinâmicos complexos
- Queries SQL otimizadas

**Por que fica no FastAPI:**
- Queries SQL complexas e otimizadas
- Lógica de negócio específica
- Agregações customizadas
- Performance otimizada

**Status**: ✅ **NÃO PRECISA MUDAR**

---

## 🔄 Serviços que SERÃO MIGRADOS para Supabase

### 1. **Autenticação** (`app/api/routes/auth.py`)
**Responsabilidade**: Login, registro e gerenciamento de usuários

**Status Atual**: 
- Implementado com JWT customizado
- Usa tabela `users` no banco

**Status Futuro**:
- 🔄 Migrar para **Supabase Auth**
- Usar autenticação nativa do Supabase
- Frontend se integra diretamente com Supabase Auth
- Backend apenas valida tokens do Supabase (opcional)

**Benefícios da migração:**
- Autenticação pronta e segura
- OAuth integrado (Google, GitHub, etc)
- Magic links
- Email verification automático
- Menos código para manter

**Quando migrar**: Em breve (após deploy inicial)

---

## 🗄️ Banco de Dados - Supabase PostgreSQL

**Configuração Atual:**
- ✅ SQLAlchemy conectado ao Supabase PostgreSQL
- ✅ Tabelas: `users`, `datasets`, `dataset_rows`, `subscriptions`
- ✅ Connection pooling configurado

**O que acontece:**
1. FastAPI usa SQLAlchemy para acessar Supabase PostgreSQL
2. CSVService processa CSV e salva via SQLAlchemy
3. DashboardService lê dados via SQLAlchemy
4. Tudo funciona normalmente!

**Status**: ✅ **JÁ CONFIGURADO E FUNCIONANDO**

---

## 📊 Fluxo de Dados Atual

```
Frontend (React)
    ↓
Backend FastAPI
    ├─→ Auth Routes (atual - JWT customizado)
    │   └─→ SQLAlchemy → Supabase PostgreSQL
    │
    ├─→ CSV Routes
    │   ├─→ CSVService (Pandas) → processa CSV
    │   └─→ SQLAlchemy → Supabase PostgreSQL (salva dados)
    │
    └─→ Dashboard Routes
        ├─→ DashboardService → SQLAlchemy
        └─→ Supabase PostgreSQL → retorna dados calculados
```

---

## 🔄 Fluxo Futuro (com Supabase Auth)

```
Frontend (React)
    ├─→ Supabase Auth (login/registro) → Direto no Supabase
    │
    └─→ Backend FastAPI (com token do Supabase)
        ├─→ Valida token Supabase
        │
        ├─→ CSV Routes
        │   ├─→ CSVService (Pandas) → processa CSV
        │   └─→ SQLAlchemy → Supabase PostgreSQL
        │
        └─→ Dashboard Routes
            ├─→ DashboardService → SQLAlchemy
            └─→ Supabase PostgreSQL → retorna dados
```

---

## ✅ Resumo

| Serviço | Localização | Status | Mudança Necessária? |
|---------|-------------|--------|---------------------|
| **CSV Processing** | FastAPI (CSVService) | ✅ Funcionando | ❌ NÃO |
| **Analytics/Dashboard** | FastAPI (DashboardService) | ✅ Funcionando | ❌ NÃO |
| **Autenticação** | FastAPI (atual) → Supabase Auth (futuro) | 🔄 Migrar | ✅ SIM (futuro) |
| **Banco de Dados** | Supabase PostgreSQL | ✅ Configurado | ❌ NÃO |

---

## 🎯 Próximos Passos

1. ✅ **Deploy atual** - Tudo funciona como está
2. 🔄 **Migrar Auth para Supabase** - Quando estiver pronto
3. ✅ **Manter CSV e Dashboard no FastAPI** - Não mudar

---

## 📝 Notas Importantes

- **CSVService** e **DashboardService** continuam no FastAPI porque têm lógica complexa que não se encaixa no padrão do Supabase
- O banco já está no Supabase e funciona perfeitamente
- A migração de Auth é opcional e pode ser feita depois
- Os serviços atuais são otimizados e funcionam bem

**Conclusão**: A arquitetura híbrida está correta - Supabase para dados/auth, FastAPI para lógica de negócio complexa! 🚀

