# Integração Frontend - Assinatura Cakto

Este documento descreve como integrar o sistema de assinatura Cakto no frontend do MarketDash.

## 📋 Índice

1. [Visão Geral](#visão-geral)
2. [Fluxo de Assinatura](#fluxo-de-assinatura)
3. [Endpoints da API](#endpoints-da-api)
4. [Implementação no Frontend](#implementação-no-frontend)
5. [Tratamento de Erros](#tratamento-de-erros)
6. [Exemplos de Código](#exemplos-de-código)
7. [Boas Práticas](#boas-práticas)

---

## 🎯 Visão Geral

O MarketDash possui **dois ambientes principais**:

1. **Site Institucional** (Público) - Landing page com informações do produto
2. **Plataforma** (Autenticado) - Dashboard e funcionalidades para usuários assinantes

### Arquitetura do Sistema

```
Site Institucional (Público)
├── Botão "Entrar" → Redireciona para /login
└── Botão "Assinar" → Redireciona para checkout Cakto ou /subscription

Plataforma (Autenticado)
├── Requer login (JWT)
├── Requer assinatura ativa
└── Dashboard, Datasets, Analytics, etc.
```

### Fluxo Principal

1. **Usuário acessa site institucional** → Vê botões "Entrar" e "Assinar"
2. **Clica em "Assinar"** → Redireciona para checkout Cakto (com ou sem dados pré-preenchidos)
3. **Completa pagamento na Cakto** → PIX, Débito Recorrente ou Crédito
4. **Cakto envia webhook** → Backend cria/atualiza usuário e ativa assinatura
5. **Usuário retorna** → Pode fazer login e acessar a plataforma

### Pontos Importantes

- ✅ O site institucional é **público** (não requer autenticação)
- ✅ O botão "Assinar" pode redirecionar **diretamente para Cakto** ou para página de assinatura
- ✅ O usuário pode ser criado automaticamente via webhook da Cakto
- ✅ A assinatura é validada automaticamente a cada 30 dias
- ✅ APIs protegidas retornam `403` se a assinatura não estiver ativa
- ✅ O frontend deve verificar o status da assinatura periodicamente

---

## 🔄 Fluxos de Assinatura

### Cenário 1: Usuário Novo via Site Institucional (Recomendado)

**Fluxo Direto (Botão "Assinar" → Cakto):**

```
1. Usuário acessa site institucional (marketdash.com.br)
2. Clica no botão "Assinar" no header/hero
3. Frontend redireciona diretamente para checkout Cakto
   → GET /api/v1/cakto/checkout-url (sem autenticação)
   → URL gerada: https://pay.cakto.com.br/8e9qxyg_742442
4. Usuário preenche dados na Cakto (Nome, Email, CPF/CNPJ)
5. Usuário escolhe método de pagamento (PIX, Débito, Crédito)
6. Completa pagamento na Cakto
7. Cakto envia webhook → Backend cria usuário e ativa assinatura
8. Cakto redireciona de volta para site (callback URL)
9. Site mostra mensagem: "Assinatura confirmada! Faça login para acessar"
10. Usuário clica em "Entrar" → Faz login → Acessa plataforma
```

**Fluxo com Página de Assinatura (Alternativo):**

```
1. Usuário acessa site institucional
2. Clica no botão "Assinar"
3. Redireciona para /subscription (página de assinatura)
4. Usuário preenche formulário: Nome, Email, CPF/CNPJ
5. Clica em "Assinar Agora"
6. Frontend chama GET /api/v1/cakto/checkout-url (com dados preenchidos)
7. Redireciona para Cakto (dados já pré-preenchidos)
8. Usuário completa pagamento
9. Cakto envia webhook → Backend cria usuário e ativa assinatura
10. Retorna para /subscription/callback ou /login
```

---

### Cenário 2: Usuário Existente via Site Institucional

```
1. Usuário acessa site institucional
2. Clica no botão "Entrar"
3. Redireciona para /login
4. Faz login com email/senha
5. Se não tiver assinatura ativa:
   → Redireciona para /subscription
   → Mostra banner: "Renove sua assinatura"
6. Usuário clica em "Assinar"
7. Frontend chama GET /api/v1/cakto/checkout-url (com email do usuário logado)
8. Redireciona para Cakto (dados pré-preenchidos)
9. Usuário completa pagamento
10. Cakto envia webhook → Backend atualiza assinatura
11. Retorna → Assinatura ativa, pode acessar plataforma
```

---

### Cenário 3: Usuário com Assinatura Ativa

```
1. Usuário acessa site institucional
2. Clica no botão "Entrar"
3. Redireciona para /login
4. Faz login
5. Backend verifica assinatura → is_active: true
6. Redireciona para /dashboard (plataforma)
7. Usuário acessa todas as funcionalidades
```

---

### Cenário 4: Validação Automática (30 dias)

```
1. Usuário faz login na plataforma
2. Backend verifica: passou mais de 30 dias desde última validação?
3. Se sim → Valida com API da Cakto automaticamente
4. Se assinatura ativa → Permite acesso
5. Se assinatura inativa → Retorna 403
6. Frontend mostra mensagem: "Sua assinatura expirou. Renove agora"
7. Redireciona para /subscription
```

---

## 🔌 Endpoints da API

### Base URL

```
Produção: https://api.marketdash.com.br
Homologação: https://api.hml.marketdash.com.br
```

### 1. Listar Planos Disponíveis

**GET** `/api/v1/cakto/plans`

Retorna lista de todos os planos de assinatura disponíveis.

**Resposta:**
```json
{
  "plans": [
    {
      "id": "principal",
      "name": "Oferta Principal",
      "checkout_url": "https://pay.cakto.com.br/8e9qxyg_742442",
      "period": "mensal"
    },
    {
      "id": "trimestral",
      "name": "MarketDash Trimestral",
      "checkout_url": "https://pay.cakto.com.br/hi5cerw",
      "period": "trimestral"
    },
    {
      "id": "anual",
      "name": "MarketDash Anual",
      "checkout_url": "https://pay.cakto.com.br/6bpwn57",
      "period": "anual"
    }
  ]
}
```

**Exemplo de Requisição:**
```typescript
const response = await fetch(`${API_BASE_URL}/api/v1/cakto/plans`, {
  method: 'GET',
  headers: {
    'Content-Type': 'application/json',
  },
});

const data = await response.json();
// data.plans contém array com todos os planos disponíveis
```

---

### 2. Obter URL de Checkout

**GET** `/api/v1/cakto/checkout-url`

Gera URL de checkout da Cakto com dados pré-preenchidos para um plano específico.

**Query Parameters:**
- `email` (obrigatório): Email do usuário
- `name` (opcional): Nome do usuário
- `cpf_cnpj` (opcional): CPF ou CNPJ do usuário
- `plan` (opcional): ID do plano desejado. Valores: `"principal"`, `"trimestral"`, `"anual"`. Default: `"principal"`

**Resposta:**
```json
{
  "checkout_url": "https://pay.cakto.com.br/6bpwn57?email=usuario@example.com&name=João Silva&cpf_cnpj=12345678900"
}
```

**Exemplo de Requisição (Plano Anual):**
```typescript
const response = await fetch(
  `${API_BASE_URL}/api/v1/cakto/checkout-url?email=${email}&name=${name}&cpf_cnpj=${cpfCnpj}&plan=anual`,
  {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  }
);

const data = await response.json();
window.location.href = data.checkout_url;
```

**Exemplo de Requisição (Plano Principal - Default):**
```typescript
// Sem especificar plan, usa "principal" como padrão
const response = await fetch(
  `${API_BASE_URL}/api/v1/cakto/checkout-url?email=${email}&name=${name}&cpf_cnpj=${cpfCnpj}`,
  {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  }
);

const data = await response.json();
window.location.href = data.checkout_url;
```

---

### 3. Verificar Status da Assinatura

**GET** `/api/v1/subscription/status`

Retorna o status atual da assinatura do usuário autenticado.

**Headers:**
```
Authorization: Bearer {token}
```

**Resposta:**
```json
{
  "is_active": true,
  "plan": "marketdash",
  "expires_at": "2025-02-26T00:00:00Z",
  "last_validation_at": "2025-01-26T10:30:00Z",
  "cakto_customer_id": "customer_123",
  "needs_validation": false
}
```

**Campos:**
- `is_active` (boolean): Se a assinatura está ativa
- `plan` (string): Plano atual ("marketdash" ou "free")
- `expires_at` (string | null): Data de expiração (ISO 8601)
- `last_validation_at` (string | null): Última validação com Cakto
- `cakto_customer_id` (string | null): ID do cliente na Cakto
- `needs_validation` (boolean): Se precisa validar (passou 30 dias)

**Exemplo de Requisição:**
```typescript
const response = await fetch(`${API_BASE_URL}/api/v1/subscription/status`, {
  method: 'GET',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json',
  },
});

const subscription = await response.json();
```

---

### 4. Login

**POST** `/api/v1/auth/login`

Autentica o usuário e retorna token JWT.

**Body (form-data):**
```
email: string
password: string
```

**Resposta:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "name": "João Silva",
    "email": "joao@example.com",
    "cpf_cnpj": "12345678900",
    "is_active": true,
    "created_at": "2025-01-26T10:00:00Z"
  }
}
```

---

### 5. Registro (Opcional)

**POST** `/api/v1/auth/register`

Cria um novo usuário. **Nota:** Usuários também podem ser criados automaticamente via webhook da Cakto.

**Body (JSON):**
```json
{
  "name": "João Silva",
  "email": "joao@example.com",
  "cpf_cnpj": "12345678900",
  "password": "senha123"
}
```

---

## 💻 Implementação no Frontend

### 1. Estrutura de Pastas Recomendada

```
src/
├── services/
│   ├── api.ts              # Configuração base da API
│   ├── auth.service.ts     # Serviço de autenticação
│   ├── subscription.service.ts  # Serviço de assinatura
│   └── cakto.service.ts    # Serviço de integração Cakto
├── hooks/
│   ├── useAuth.ts          # Hook de autenticação
│   └── useSubscription.ts # Hook de assinatura
├── components/
│   ├── SubscriptionBanner.tsx
│   ├── SubscriptionModal.tsx
│   └── Header.tsx          # Header com botões "Entrar" e "Assinar"
├── pages/
│   ├── LandingPage.tsx     # Site institucional
│   ├── LoginPage.tsx       # Página de login
│   ├── SubscriptionPage.tsx
│   ├── CaktoCallback.tsx   # Callback após pagamento
│   └── Dashboard.tsx
└── routes/
    └── AppRoutes.tsx       # Configuração de rotas
```

---

### 2. Implementação do Site Institucional

O site institucional é **público** e não requer autenticação. Os botões "Entrar" e "Assinar" devem estar presentes no header.

#### Estratégias para o Botão "Assinar"

**Opção 1: Redirecionamento Direto (Recomendado)**
- Redireciona diretamente para a Cakto
- Menos fricção, conversão mais rápida
- Usuário preenche dados na própria Cakto

**Opção 2: Página de Assinatura Intermediária**
- Redireciona para `/subscription`
- Permite coletar dados antes (opcional)
- Mais controle sobre o fluxo

#### Exemplo de Implementação Rápida

```typescript
// No componente do Header ou Landing Page
import { caktoService } from '../services/cakto.service';

// Botão "Assinar" - Redirecionamento direto
const handleSubscribe = () => {
  caktoService.redirectToCheckoutDirect();
};

// Botão "Entrar" - Redireciona para login
const handleLogin = () => {
  navigate('/login');
};
```

---

### 3. Serviço de API Base

**`src/services/api.ts`**

```typescript
const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.hml.marketdash.com.br';

export const api = {
  baseURL: API_BASE_URL,
  
  /**
   * Faz requisição para a API
   * @param endpoint - Endpoint da API (ex: '/api/v1/cakto/checkout-url')
   * @param options - Opções da requisição (method, headers, body, etc.)
   * @param requireAuth - Se true, adiciona token de autenticação (padrão: true)
   */
  async request<T>(
    endpoint: string,
    options: RequestInit = {},
    requireAuth: boolean = true
  ): Promise<T> {
    const token = localStorage.getItem('token');
    
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
      ...options.headers,
    };
    
    // Adiciona token apenas se requireAuth for true e token existir
    if (requireAuth && token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers,
    });
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Erro desconhecido' }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    
    return response.json();
  },
  
  /**
   * Requisição pública (sem autenticação)
   * Útil para endpoints que não requerem login (ex: checkout-url)
   */
  async publicRequest<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    return this.request<T>(endpoint, options, false);
  },
};
```

---

### 4. Serviço de Assinatura

**`src/services/subscription.service.ts`**

```typescript
import { api } from './api';

export interface SubscriptionStatus {
  is_active: boolean;
  plan: string;
  expires_at: string | null;
  last_validation_at: string | null;
  cakto_customer_id: string | null;
  needs_validation: boolean;
}

export const subscriptionService = {
  /**
   * Obtém o status da assinatura do usuário autenticado
   */
  async getStatus(): Promise<SubscriptionStatus> {
    return api.request<SubscriptionStatus>('/api/v1/subscription/status');
  },
  
  /**
   * Verifica se o usuário tem assinatura ativa
   */
  async isActive(): Promise<boolean> {
    try {
      const status = await this.getStatus();
      return status.is_active;
    } catch (error) {
      console.error('Erro ao verificar assinatura:', error);
      return false;
    }
  },
};
```

---

### 5. Serviço Cakto

**`src/services/cakto.service.ts`**

```typescript
import { api } from './api';

export interface PlanInfo {
  id: string;
  name: string;
  checkout_url: string;
  period: string;  // "mensal", "trimestral", "anual"
}

export interface PlansResponse {
  plans: PlanInfo[];
}

export interface CheckoutUrlParams {
  email?: string;  // Opcional para site institucional
  name?: string;
  cpf_cnpj?: string;
  plan?: string;  // ID do plano: "principal", "trimestral", "anual"
}

export const caktoService = {
  /**
   * Obtém lista de planos disponíveis
   */
  async getPlans(): Promise<PlanInfo[]> {
    const response = await api.publicRequest<PlansResponse>('/api/v1/cakto/plans');
    return response.plans;
  },
  
  /**
   * Obtém URL de checkout da Cakto para um plano específico
   * Pode ser chamado sem autenticação (para site institucional)
   */
  async getCheckoutUrl(params: CheckoutUrlParams = {}): Promise<string> {
    const queryParams = new URLSearchParams();
    
    if (params.email) {
      queryParams.append('email', params.email);
    }
    
    if (params.name) {
      queryParams.append('name', params.name);
    }
    
    if (params.cpf_cnpj) {
      queryParams.append('cpf_cnpj', params.cpf_cnpj);
    }
    
    if (params.plan) {
      queryParams.append('plan', params.plan);
    }
    
    // Para site institucional, não precisa de token
    const endpoint = `/api/v1/cakto/checkout-url${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;
    
    // Usa publicRequest para não requerer autenticação
    const response = await api.publicRequest<{ checkout_url: string }>(endpoint);
    
    return response.checkout_url;
  },
  
  /**
   * Redireciona para página de checkout da Cakto
   * Usado no site institucional e na plataforma
   */
  async redirectToCheckout(params: CheckoutUrlParams = {}): Promise<void> {
    const checkoutUrl = await this.getCheckoutUrl(params);
    window.location.href = checkoutUrl;
  },
  
  /**
   * Redireciona diretamente para checkout do plano principal (sem pré-preenchimento)
   * Usado no botão "Assinar" do site institucional quando não há seleção de plano
   */
  redirectToCheckoutDirect(): void {
    const baseUrl = 'https://pay.cakto.com.br/8e9qxyg_742442';
    window.location.href = baseUrl;
  },
};
```

---

### 6. Hook de Assinatura

**`src/hooks/useSubscription.ts`**

```typescript
import { useState, useEffect } from 'react';
import { subscriptionService, SubscriptionStatus } from '../services/subscription.service';

export const useSubscription = () => {
  const [subscription, setSubscription] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const fetchStatus = async () => {
    try {
      setLoading(true);
      setError(null);
      const status = await subscriptionService.getStatus();
      setSubscription(status);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao carregar assinatura');
      setSubscription(null);
    } finally {
      setLoading(false);
    }
  };
  
  useEffect(() => {
    fetchStatus();
    
    // Atualizar status a cada 5 minutos
    const interval = setInterval(fetchStatus, 5 * 60 * 1000);
    
    return () => clearInterval(interval);
  }, []);
  
  return {
    subscription,
    loading,
    error,
    refetch: fetchStatus,
    isActive: subscription?.is_active ?? false,
  };
};
```

---

### 7. Componente de Banner de Assinatura

**`src/components/SubscriptionBanner.tsx`**

```typescript
import React from 'react';
import { useSubscription } from '../hooks/useSubscription';
import { caktoService } from '../services/cakto.service';
import { useAuth } from '../hooks/useAuth';

export const SubscriptionBanner: React.FC = () => {
  const { subscription, isActive } = useSubscription();
  const { user } = useAuth();
  
  if (isActive || !user) {
    return null;
  }
  
  const handleSubscribe = async () => {
    try {
      await caktoService.redirectToCheckout({
        email: user.email,
        name: user.name,
        cpf_cnpj: user.cpf_cnpj,
      });
    } catch (error) {
      console.error('Erro ao redirecionar para checkout:', error);
      alert('Erro ao acessar página de assinatura. Tente novamente.');
    }
  };
  
  return (
    <div className="subscription-banner">
      <div className="banner-content">
        <h3>Assinatura Necessária</h3>
        <p>Você precisa de uma assinatura ativa para acessar a plataforma.</p>
        <button onClick={handleSubscribe} className="btn-subscribe">
          Assinar Agora
        </button>
      </div>
    </div>
  );
};
```

---

### 8. Proteção de Rotas

**`src/components/ProtectedRoute.tsx`**

```typescript
import React, { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { useSubscription } from '../hooks/useSubscription';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
  const { isAuthenticated, loading: authLoading } = useAuth();
  const { isActive, loading: subscriptionLoading } = useSubscription();
  const navigate = useNavigate();
  
  useEffect(() => {
    if (!authLoading && !isAuthenticated) {
      navigate('/login');
    } else if (!subscriptionLoading && isAuthenticated && !isActive) {
      navigate('/subscription');
    }
  }, [isAuthenticated, isActive, authLoading, subscriptionLoading, navigate]);
  
  if (authLoading || subscriptionLoading) {
    return <div>Carregando...</div>;
  }
  
  if (!isAuthenticated || !isActive) {
    return null;
  }
  
  return <>{children}</>;
};
```

---

### 9. Interceptor de Requisições (Axios)

Se estiver usando Axios, configure um interceptor para tratar erros 403:

```typescript
import axios from 'axios';

const apiClient = axios.create({
  baseURL: process.env.REACT_APP_API_URL,
});

// Interceptor de requisição (adiciona token)
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Interceptor de resposta (trata erros)
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 403) {
      const message = error.response.data?.detail;
      if (message?.includes('Assinatura não está ativa')) {
        // Redirecionar para página de assinatura
        window.location.href = '/subscription';
      }
    }
    return Promise.reject(error);
  }
);
```

---

## ⚠️ Tratamento de Erros

### Erros Comuns

#### 1. Assinatura Não Ativa (403)

```typescript
try {
  await api.request('/api/v1/datasets');
} catch (error) {
  if (error.message.includes('Assinatura não está ativa')) {
    // Redirecionar para página de assinatura
    navigate('/subscription');
  }
}
```

#### 2. Token Expirado (401)

```typescript
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);
```

#### 3. Erro ao Obter Checkout URL

```typescript
try {
  await caktoService.redirectToCheckout({ email: user.email });
} catch (error) {
  console.error('Erro ao obter URL de checkout:', error);
  // Mostrar mensagem amigável ao usuário
  toast.error('Não foi possível acessar a página de assinatura. Tente novamente.');
}
```

---

## 📝 Exemplos de Código

### 1. Header do Site Institucional

**`src/components/Header.tsx`**

```typescript
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { caktoService } from '../services/cakto.service';
import { useAuth } from '../hooks/useAuth';

export const Header: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, user } = useAuth();
  
  const handleSubscribe = async () => {
    try {
      // Se usuário estiver logado, pré-preenche dados
      if (isAuthenticated && user) {
        await caktoService.redirectToCheckout({
          email: user.email,
          name: user.name,
          cpf_cnpj: user.cpf_cnpj,
        });
      } else {
        // Se não estiver logado, redireciona direto para Cakto
        // Ou pode redirecionar para página de assinatura
        // Opção 1: Direto para Cakto
        caktoService.redirectToCheckoutDirect();
        
        // Opção 2: Para página de assinatura (comentado)
        // navigate('/subscription');
      }
    } catch (error) {
      console.error('Erro ao redirecionar para checkout:', error);
      alert('Erro ao acessar página de assinatura. Tente novamente.');
    }
  };
  
  const handleLogin = () => {
    navigate('/login');
  };
  
  return (
    <header className="header">
      <div className="header-content">
        <div className="logo">MarketDash</div>
        <nav className="header-nav">
          {isAuthenticated ? (
            <>
              <button onClick={() => navigate('/dashboard')} className="btn-secondary">
                Dashboard
              </button>
              <button onClick={handleSubscribe} className="btn-primary">
                Assinar
              </button>
            </>
          ) : (
            <>
              <button onClick={handleLogin} className="btn-secondary">
                Entrar
              </button>
              <button onClick={handleSubscribe} className="btn-primary">
                Assinar
              </button>
            </>
          )}
        </nav>
      </div>
    </header>
  );
};
```

---

### 2. Página de Assinatura Completa

**`src/pages/SubscriptionPage.tsx`**

```typescript
import React, { useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import { useSubscription } from '../hooks/useSubscription';
import { caktoService } from '../services/cakto.service';

export const SubscriptionPage: React.FC = () => {
  const { user } = useAuth();
  const { subscription, loading } = useSubscription();
  const [submitting, setSubmitting] = useState(false);
  
  const handleSubscribe = async () => {
    if (!user) return;
    
    setSubmitting(true);
    try {
      await caktoService.redirectToCheckout({
        email: user.email,
        name: user.name,
        cpf_cnpj: user.cpf_cnpj,
      });
    } catch (error) {
      console.error('Erro:', error);
      alert('Erro ao acessar página de assinatura');
    } finally {
      setSubmitting(false);
    }
  };
  
  if (loading) {
    return <div>Carregando...</div>;
  }
  
  if (subscription?.is_active) {
    return (
      <div className="subscription-active">
        <h2>Assinatura Ativa</h2>
        <p>Sua assinatura está ativa até {new Date(subscription.expires_at!).toLocaleDateString()}</p>
      </div>
    );
  }
  
  return (
    <div className="subscription-page">
      <h1>Assine o MarketDash</h1>
      <p>Tenha acesso completo à plataforma de análise de dados</p>
      
      <div className="subscription-plans">
        <div className="plan-card">
          <h3>Plano MarketDash</h3>
          <p className="price">R$ 99,90/mês</p>
          <ul>
            <li>Análise ilimitada de dados</li>
            <li>Upload de CSVs</li>
            <li>Dashboard completo</li>
            <li>Suporte prioritário</li>
          </ul>
          <button 
            onClick={handleSubscribe} 
            disabled={submitting}
            className="btn-subscribe"
          >
            {submitting ? 'Redirecionando...' : 'Assinar Agora'}
          </button>
        </div>
      </div>
    </div>
  );
};
```

---

## ✅ Boas Práticas

### 1. Verificação Periódica

```typescript
// Verificar status da assinatura a cada 5 minutos
useEffect(() => {
  const interval = setInterval(() => {
    subscriptionService.getStatus();
  }, 5 * 60 * 1000);
  
  return () => clearInterval(interval);
}, []);
```

### 2. Cache do Status

```typescript
// Cachear status por 1 minuto para evitar requisições excessivas
let cachedStatus: SubscriptionStatus | null = null;
let cacheTimestamp = 0;
const CACHE_TTL = 60 * 1000; // 1 minuto

export const getCachedStatus = async (): Promise<SubscriptionStatus> => {
  const now = Date.now();
  if (cachedStatus && (now - cacheTimestamp) < CACHE_TTL) {
    return cachedStatus;
  }
  
  cachedStatus = await subscriptionService.getStatus();
  cacheTimestamp = now;
  return cachedStatus;
};
```

### 3. Feedback Visual

```typescript
// Mostrar loading durante redirecionamento
const [redirecting, setRedirecting] = useState(false);

const handleSubscribe = async () => {
  setRedirecting(true);
  try {
    await caktoService.redirectToCheckout({ email: user.email });
  } finally {
    // Não resetar redirecting, pois a página será redirecionada
  }
};
```

### 4. Tratamento de Retorno da Cakto

Após o pagamento, a Cakto pode redirecionar de volta. Configure uma página de callback:

**`src/pages/CaktoCallback.tsx`**

```typescript
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { useSubscription } from '../hooks/useSubscription';

export const CaktoCallback: React.FC = () => {
  const { isAuthenticated, user } = useAuth();
  const { refetch, isActive } = useSubscription();
  const navigate = useNavigate();
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  
  useEffect(() => {
    const processCallback = async () => {
      try {
        // Aguardar alguns segundos para o webhook processar
        await new Promise(resolve => setTimeout(resolve, 3000));
        
        // Se usuário estiver logado, verificar assinatura
        if (isAuthenticated) {
          await refetch();
          
          if (isActive) {
            setStatus('success');
            setTimeout(() => {
              navigate('/dashboard');
            }, 2000);
          } else {
            setStatus('error');
          }
        } else {
          // Se não estiver logado, mostrar mensagem para fazer login
          setStatus('success');
        }
      } catch (error) {
        console.error('Erro ao processar callback:', error);
        setStatus('error');
      }
    };
    
    processCallback();
  }, [isAuthenticated, refetch, isActive, navigate]);
  
  if (status === 'processing') {
    return (
      <div className="callback-page">
        <div className="callback-content">
          <div className="spinner"></div>
          <h2>Processando sua assinatura...</h2>
          <p>Aguarde enquanto confirmamos seu pagamento.</p>
        </div>
      </div>
    );
  }
  
  if (status === 'success') {
    return (
      <div className="callback-page">
        <div className="callback-content">
          <div className="success-icon">✓</div>
          <h2>Assinatura Confirmada!</h2>
          {isAuthenticated ? (
            <>
              <p>Sua assinatura foi ativada com sucesso.</p>
              <p>Redirecionando para o dashboard...</p>
            </>
          ) : (
            <>
              <p>Sua assinatura foi confirmada.</p>
              <p>Faça login para acessar a plataforma.</p>
              <button onClick={() => navigate('/login')} className="btn-primary">
                Fazer Login
              </button>
            </>
          )}
        </div>
      </div>
    );
  }
  
  return (
    <div className="callback-page">
      <div className="callback-content">
        <div className="error-icon">✗</div>
        <h2>Erro ao Processar Assinatura</h2>
        <p>Houve um problema ao confirmar sua assinatura.</p>
        <p>Entre em contato com o suporte ou tente novamente.</p>
        <button onClick={() => navigate('/subscription')} className="btn-primary">
          Tentar Novamente
        </button>
      </div>
    </div>
  );
};
```

---

### 5. Página do Site Institucional (Landing Page)

**`src/pages/LandingPage.tsx`**

```typescript
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Header } from '../components/Header';
import { caktoService, PlanInfo } from '../services/cakto.service';
import { useAuth } from '../hooks/useAuth';

export const LandingPage: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, user } = useAuth();
  
  const handleSubscribe = async () => {
    try {
      if (isAuthenticated && user) {
        // Usuário logado: pré-preenche dados
        await caktoService.redirectToCheckout({
          email: user.email,
          name: user.name,
          cpf_cnpj: user.cpf_cnpj,
        });
      } else {
        // Usuário não logado: redireciona direto para Cakto
        caktoService.redirectToCheckoutDirect();
      }
    } catch (error) {
      console.error('Erro ao redirecionar:', error);
      alert('Erro ao acessar página de assinatura.');
    }
  };
  
  const handleLogin = () => {
    navigate('/login');
  };
  
  return (
    <div className="landing-page">
      <Header />
      
      <section className="hero">
        <h1>MarketDash</h1>
        <p className="hero-subtitle">
          Plataforma completa para análise de dados e insights de negócio
        </p>
        <div className="hero-cta">
          <button onClick={handleSubscribe} className="btn-primary btn-large">
            Assinar Agora
          </button>
          <button onClick={handleLogin} className="btn-secondary btn-large">
            Entrar
          </button>
        </div>
      </section>
      
      <section className="features">
        <h2>Recursos</h2>
        <div className="features-grid">
          <div className="feature-card">
            <h3>Upload de CSVs</h3>
            <p>Importe seus dados facilmente</p>
          </div>
          <div className="feature-card">
            <h3>Dashboard Completo</h3>
            <p>Visualize seus dados em tempo real</p>
          </div>
          <div className="feature-card">
            <h3>Análises Avançadas</h3>
            <p>Insights poderosos para seu negócio</p>
          </div>
        </div>
      </section>
      
      <section className="pricing">
        <h2>Planos</h2>
        {loading ? (
          <div>Carregando planos...</div>
        ) : (
          <div className="pricing-grid">
            {plans.map((plan) => (
              <div key={plan.id} className="pricing-card">
                <h3>{plan.name}</h3>
                <p className="period">{plan.period}</p>
                <ul>
                  <li>Análise ilimitada de dados</li>
                  <li>Upload de CSVs</li>
                  <li>Dashboard completo</li>
                  <li>Suporte prioritário</li>
                </ul>
                <button 
                  onClick={() => handleSubscribe(plan.id)} 
                  className="btn-primary"
                >
                  Assinar {plan.name}
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
```

---

## 📦 Planos Disponíveis

O MarketDash oferece três planos de assinatura:

1. **Oferta Principal** (Mensal)
   - ID: `principal`
   - Checkout: `https://pay.cakto.com.br/8e9qxyg_742442`
   - Período: Mensal

2. **MarketDash Trimestral**
   - ID: `trimestral`
   - Checkout: `https://pay.cakto.com.br/hi5cerw`
   - Período: Trimestral

3. **MarketDash Anual**
   - ID: `anual`
   - Checkout: `https://pay.cakto.com.br/6bpwn57`
   - Período: Anual

### Exemplo: Exibir Planos no Frontend

```typescript
import { caktoService, PlanInfo } from '../services/cakto.service';

export const SubscriptionPlans: React.FC = () => {
  const [plans, setPlans] = useState<PlanInfo[]>([]);
  const [loading, setLoading] = useState(true);
  
  useEffect(() => {
    const fetchPlans = async () => {
      try {
        const plansList = await caktoService.getPlans();
        setPlans(plansList);
      } catch (error) {
        console.error('Erro ao carregar planos:', error);
      } finally {
        setLoading(false);
      }
    };
    
    fetchPlans();
  }, []);
  
  const handleSelectPlan = async (planId: string) => {
    try {
      await caktoService.redirectToCheckout({
        email: user?.email,
        name: user?.name,
        cpf_cnpj: user?.cpf_cnpj,
        plan: planId,
      });
    } catch (error) {
      console.error('Erro ao redirecionar:', error);
    }
  };
  
  if (loading) {
    return <div>Carregando planos...</div>;
  }
  
  return (
    <div className="plans-grid">
      {plans.map((plan) => (
        <div key={plan.id} className="plan-card">
          <h3>{plan.name}</h3>
          <p>Período: {plan.period}</p>
          <button onClick={() => handleSelectPlan(plan.id)}>
            Assinar {plan.name}
          </button>
        </div>
      ))}
    </div>
  );
};
```

---

## 🔗 URLs Importantes

### URLs do Site
- **Site Institucional (Produção):** `https://marketdash.com.br`
- **Site Institucional (Homologação):** `https://hml.marketdash.com.br`
- **Plataforma (Produção):** `https://app.marketdash.com.br`
- **Plataforma (Homologação):** `https://app.hml.marketdash.com.br`

### URLs da API
- **API Base (Produção):** `https://api.marketdash.com.br`
- **API Base (Homologação):** `https://api.hml.marketdash.com.br`
- **Webhook URL:** `https://api.marketdash.com.br/cakto/webhook`

### URLs da Cakto (Planos)
- **Oferta Principal:** `https://pay.cakto.com.br/8e9qxyg_742442`
- **Trimestral:** `https://pay.cakto.com.br/hi5cerw`
- **Anual:** `https://pay.cakto.com.br/6bpwn57`

### Rotas do Frontend
- `/` - Site institucional (Landing Page)
- `/login` - Página de login
- `/subscription` - Página de assinatura
- `/subscription/callback` - Callback após pagamento Cakto
- `/dashboard` - Dashboard da plataforma (protegido)

---

## 📞 Suporte

Em caso de dúvidas ou problemas na integração, consulte:

1. Documentação da API: `/docs` (Swagger UI)
2. Logs do backend para debug
3. Documentação da Cakto: [Guia Completo de Integração](./Guia_Completo_Integracao_Cakto.md)

---

---

## 🎨 Considerações de UX/UI

### Site Institucional

1. **Botão "Assinar"** deve ser destacado (cor primária, tamanho maior)
2. **Botão "Entrar"** deve ser secundário (cor secundária)
3. Ambos devem estar visíveis no header em todas as páginas do site institucional
4. Após clicar em "Assinar", mostrar feedback visual (loading, spinner)

### Fluxo de Assinatura

1. **Redirecionamento direto** é mais rápido e reduz fricção
2. **Página de assinatura** oferece mais contexto e informações
3. Escolha baseada na estratégia de conversão desejada

### Callback após Pagamento

1. Mostrar mensagem clara de sucesso/erro
2. Se usuário não estiver logado, direcionar para login
3. Se usuário estiver logado, redirecionar automaticamente para dashboard após 2-3 segundos

---

**Última atualização:** Janeiro 2025
