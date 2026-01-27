# Integração Frontend - Fluxo "Definir Senha"

Este documento descreve como integrar o fluxo de "Definir Senha" no frontend do MarketDash.

## 📋 Índice

1. [Visão Geral](#visão-geral)
2. [Fluxo Completo](#fluxo-completo)
3. [Endpoints da API](#endpoints-da-api)
4. [Implementação no Frontend](#implementação-no-frontend)
5. [Tratamento de Erros](#tratamento-de-erros)
6. [Exemplos de Código](#exemplos-de-código)

---

## 🎯 Visão Geral

Quando um usuário assina pela primeira vez na Cakto:

1. **Backend cria usuário** automaticamente via webhook
2. **Backend envia email** com link para definir senha
3. **Usuário clica no link** do email
4. **Frontend exibe página** para definir senha
5. **Usuário define senha** e pode fazer login

### Pontos Importantes

- ✅ O usuário **não recebe senha por email** (mais seguro)
- ✅ O link do email contém um **token único** válido por 24 horas
- ✅ O token pode ser usado **apenas uma vez**
- ✅ Após definir senha, o usuário pode fazer login normalmente

---

## 🔄 Fluxo Completo

```
1. Usuário assina na Cakto
   ↓
2. Cakto envia webhook → Backend cria usuário
   ↓
3. Backend gera token único e envia email
   ↓
4. Usuário recebe email com link
   ↓
5. Usuário clica no link → Frontend: /auth/set-password?token=xxx
   ↓
6. Frontend exibe formulário para definir senha
   ↓
7. Usuário preenche senha e confirma
   ↓
8. Frontend chama API: POST /api/v1/auth/set-password
   ↓
9. Backend valida token e atualiza senha
   ↓
10. Frontend redireciona para /login (ou faz login automático)
   ↓
11. Usuário faz login com email e senha definida
```

---

## 🔌 Endpoints da API

### Definir Senha

**POST** `/api/v1/auth/set-password`

Define a senha do usuário usando o token recebido por email.

**Body (JSON):**
```json
{
  "token": "abc123def456...",
  "password": "senhaSegura123"
}
```

**Resposta de Sucesso (200):**
```json
{
  "message": "Senha definida com sucesso",
  "user": {
    "id": 1,
    "email": "usuario@example.com",
    "name": "João Silva"
  }
}
```

**Respostas de Erro:**

**400 - Token inválido ou expirado:**
```json
{
  "detail": "Token inválido ou expirado"
}
```

**400 - Senha muito fraca:**
```json
{
  "detail": "A senha deve ter no mínimo 8 caracteres"
}
```

**400 - Token já utilizado:**
```json
{
  "detail": "Este link já foi utilizado"
}
```

**Exemplo de Requisição:**
```typescript
const response = await fetch(`${API_BASE_URL}/api/v1/auth/set-password`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    token: tokenFromUrl,
    password: newPassword,
  }),
});

if (!response.ok) {
  const error = await response.json();
  throw new Error(error.detail || 'Erro ao definir senha');
}

const data = await response.json();
```

---

## 💻 Implementação no Frontend

### 1. Estrutura de Pastas Recomendada

```
src/
├── pages/
│   └── auth/
│       └── SetPasswordPage.tsx    # Página para definir senha
├── services/
│   └── auth.service.ts            # Serviço de autenticação (atualizar)
└── routes/
    └── AppRoutes.tsx              # Adicionar rota /auth/set-password
```

---

### 2. Serviço de Autenticação (Atualizar)

**`src/services/auth.service.ts`**

Adicionar método para definir senha:

```typescript
import { api } from './api';

export interface SetPasswordRequest {
  token: string;
  password: string;
}

export interface SetPasswordResponse {
  message: string;
  user: {
    id: number;
    email: string;
    name: string;
  };
}

export const authService = {
  // ... outros métodos existentes

  /**
   * Define senha do usuário usando token recebido por email
   */
  async setPassword(token: string, password: string): Promise<SetPasswordResponse> {
    return api.publicRequest<SetPasswordResponse>('/api/v1/auth/set-password', {
      method: 'POST',
      body: JSON.stringify({ token, password }),
    });
  },
};
```

---

### 3. Página de Definir Senha

**`src/pages/auth/SetPasswordPage.tsx`**

```typescript
import React, { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { authService } from '../../services/auth.service';

export const SetPasswordPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  
  const token = searchParams.get('token');
  
  useEffect(() => {
    // Verificar se token existe na URL
    if (!token) {
      setError('Token inválido ou ausente. Verifique o link do email.');
    }
  }, [token]);
  
  const validatePassword = (pwd: string): string | null => {
    if (pwd.length < 8) {
      return 'A senha deve ter no mínimo 8 caracteres';
    }
    if (!/(?=.*[a-z])/.test(pwd)) {
      return 'A senha deve conter pelo menos uma letra minúscula';
    }
    if (!/(?=.*[A-Z])/.test(pwd)) {
      return 'A senha deve conter pelo menos uma letra maiúscula';
    }
    if (!/(?=.*\d)/.test(pwd)) {
      return 'A senha deve conter pelo menos um número';
    }
    return null;
  };
  
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    
    if (!token) {
      setError('Token inválido');
      return;
    }
    
    // Validações
    if (password !== confirmPassword) {
      setError('As senhas não coincidem');
      return;
    }
    
    const validationError = validatePassword(password);
    if (validationError) {
      setError(validationError);
      return;
    }
    
    setLoading(true);
    
    try {
      await authService.setPassword(token, password);
      setSuccess(true);
      
      // Redirecionar para login após 2 segundos
      setTimeout(() => {
        navigate('/login', { 
          state: { message: 'Senha definida com sucesso! Faça login para continuar.' }
        });
      }, 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao definir senha. Tente novamente.');
    } finally {
      setLoading(false);
    }
  };
  
  if (!token) {
    return (
      <div className="set-password-page">
        <div className="error-container">
          <h2>Token Inválido</h2>
          <p>O link que você acessou é inválido ou expirou.</p>
          <p>Por favor, solicite um novo link ou entre em contato com o suporte.</p>
          <button onClick={() => navigate('/login')} className="btn-primary">
            Ir para Login
          </button>
        </div>
      </div>
    );
  }
  
  if (success) {
    return (
      <div className="set-password-page">
        <div className="success-container">
          <div className="success-icon">✓</div>
          <h2>Senha Definida com Sucesso!</h2>
          <p>Redirecionando para a página de login...</p>
        </div>
      </div>
    );
  }
  
  return (
    <div className="set-password-page">
      <div className="set-password-container">
        <div className="logo-container">
          <img src="/logo/logo.png" alt="MarketDash" />
        </div>
        
        <h1>Definir Senha</h1>
        <p>Por favor, defina uma senha para acessar sua conta.</p>
        
        {error && (
          <div className="error-message">
            {error}
          </div>
        )}
        
        <form onSubmit={handleSubmit} className="set-password-form">
          <div className="form-group">
            <label htmlFor="password">Nova Senha</label>
            <input
              type="password"
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Mínimo 8 caracteres"
              required
              minLength={8}
            />
            <small>
              A senha deve conter: mínimo 8 caracteres, letra maiúscula, minúscula e número
            </small>
          </div>
          
          <div className="form-group">
            <label htmlFor="confirmPassword">Confirmar Senha</label>
            <input
              type="password"
              id="confirmPassword"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Digite a senha novamente"
              required
              minLength={8}
            />
          </div>
          
          <button 
            type="submit" 
            disabled={loading || !password || !confirmPassword}
            className="btn-primary btn-large"
          >
            {loading ? 'Definindo senha...' : 'Definir Senha'}
          </button>
        </form>
        
        <div className="help-text">
          <p>
            <small>
              Este link expira em 24 horas. Se você não solicitou este email, 
              pode ignorá-lo com segurança.
            </small>
          </p>
        </div>
      </div>
    </div>
  );
};
```

---

### 4. Configuração de Rotas

**`src/routes/AppRoutes.tsx`**

Adicionar rota para definir senha:

```typescript
import { SetPasswordPage } from '../pages/auth/SetPasswordPage';

// Dentro do componente de rotas:
<Route path="/auth/set-password" element={<SetPasswordPage />} />
```

---

### 5. Tratamento de Erros Específicos

```typescript
try {
  await authService.setPassword(token, password);
} catch (error) {
  const errorMessage = error instanceof Error ? error.message : 'Erro desconhecido';
  
  if (errorMessage.includes('Token inválido') || errorMessage.includes('expirado')) {
    // Token inválido ou expirado
    setError('Este link expirou ou é inválido. Por favor, entre em contato com o suporte.');
    // Opcional: oferecer opção de reenviar email
  } else if (errorMessage.includes('já foi utilizado')) {
    // Token já usado
    setError('Este link já foi utilizado. Você já pode fazer login com sua senha.');
    navigate('/login');
  } else if (errorMessage.includes('mínimo 8 caracteres')) {
    // Senha muito fraca
    setError('A senha deve ter no mínimo 8 caracteres.');
  } else {
    // Outro erro
    setError('Erro ao definir senha. Tente novamente ou entre em contato com o suporte.');
  }
}
```

---

## ⚠️ Tratamento de Erros

### Erros Comuns

#### 1. Token Inválido ou Expirado

```typescript
if (errorMessage.includes('Token inválido') || errorMessage.includes('expirado')) {
  // Mostrar mensagem amigável
  // Opcional: oferecer contato com suporte ou reenvio de email
}
```

#### 2. Token Já Utilizado

```typescript
if (errorMessage.includes('já foi utilizado')) {
  // Informar que o link já foi usado
  // Redirecionar para login
  navigate('/login', { 
    state: { message: 'Você já definiu sua senha. Faça login para continuar.' }
  });
}
```

#### 3. Senha Muito Fraca

```typescript
// Validação no frontend antes de enviar
const validationError = validatePassword(password);
if (validationError) {
  setError(validationError);
  return;
}
```

---

## 📝 Exemplos de Código

### Validação de Senha no Frontend

```typescript
const validatePassword = (password: string): { valid: boolean; errors: string[] } => {
  const errors: string[] = [];
  
  if (password.length < 8) {
    errors.push('Mínimo 8 caracteres');
  }
  if (!/(?=.*[a-z])/.test(password)) {
    errors.push('Pelo menos uma letra minúscula');
  }
  if (!/(?=.*[A-Z])/.test(password)) {
    errors.push('Pelo menos uma letra maiúscula');
  }
  if (!/(?=.*\d)/.test(password)) {
    errors.push('Pelo menos um número');
  }
  if (!/(?=.*[@$!%*?&])/.test(password)) {
    errors.push('Pelo menos um caractere especial (@$!%*?&)');
  }
  
  return {
    valid: errors.length === 0,
    errors,
  };
};
```

### Feedback Visual de Força da Senha

```typescript
const getPasswordStrength = (password: string): 'weak' | 'medium' | 'strong' => {
  if (password.length < 8) return 'weak';
  
  let strength = 0;
  if (/(?=.*[a-z])/.test(password)) strength++;
  if (/(?=.*[A-Z])/.test(password)) strength++;
  if (/(?=.*\d)/.test(password)) strength++;
  if (/(?=.*[@$!%*?&])/.test(password)) strength++;
  
  if (strength <= 2) return 'weak';
  if (strength === 3) return 'medium';
  return 'strong';
};

// No componente:
const passwordStrength = getPasswordStrength(password);

<div className={`password-strength ${passwordStrength}`}>
  <div className="strength-bar" />
  <span>
    {passwordStrength === 'weak' && 'Senha fraca'}
    {passwordStrength === 'medium' && 'Senha média'}
    {passwordStrength === 'strong' && 'Senha forte'}
  </span>
</div>
```

---

## ✅ Boas Práticas

### 1. Validação no Frontend

- Validar senha antes de enviar para API
- Mostrar feedback visual em tempo real
- Indicar força da senha (fraca/média/forte)

### 2. UX/UI

- Mostrar loading durante o processo
- Mensagens de erro claras e específicas
- Redirecionar automaticamente após sucesso
- Design consistente com o resto da aplicação

### 3. Segurança

- Não armazenar token em localStorage
- Limpar token da URL após uso (opcional)
- Validar token no frontend antes de mostrar formulário
- HTTPS obrigatório em produção

### 4. Acessibilidade

- Labels descritivos nos campos
- Mensagens de erro acessíveis
- Navegação por teclado
- Contraste adequado

---

## 🔗 URLs e Rotas

- **Rota do Frontend:** `/auth/set-password?token=xxx`
- **Endpoint da API:** `POST /api/v1/auth/set-password`
- **Redirecionamento após sucesso:** `/login`

---

## 📞 Suporte

Em caso de dúvidas ou problemas na integração:

1. Verificar se o token está presente na URL
2. Verificar se o token não expirou (24 horas)
3. Verificar se o token não foi usado anteriormente
4. Consultar logs do backend para mais detalhes
5. Documentação da API: `/docs` (Swagger UI)

---

**Última atualização:** Janeiro 2025
