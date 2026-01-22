# ✅ Checklist - Atualização de Domínio para marketdash.com.br

## 📝 Arquivos Atualizados

Todos os arquivos do backend foram atualizados com o novo domínio:

✅ **app/core/config.py** - CORS_ORIGINS atualizado
✅ **README-DEPLOY.md** - Documentação de deploy atualizada  
✅ **etapas.md** - Guia completo atualizado
✅ **README.md** - Documentação atualizada

---

## 🔧 Configurações no Supabase

Você precisa atualizar no Supabase Dashboard:

### 1. Produção (dashads-prod)

Acesse: https://supabase.com/dashboard/project/rsejwvxealraianensoz

**Authentication → Settings:**
- **Site URL**: `https://marketdash.com.br`
- **Redirect URLs**: Adicione/atualize:
  ```
  https://marketdash.com.br/**
  https://marketdash.hml.com.br/**
  http://localhost:3000/**
  http://localhost:5173/**
  http://localhost:8080/**
  ```

### 2. Staging (se tiver projeto separado)

Mesmas configurações acima, mas no projeto de staging.

---

## 🌐 Domínios Finais

### Produção:
- **Frontend**: `https://marketdash.com.br`
- **Backend API**: `https://api.marketdash.com.br`
- **Documentação**: `https://api.marketdash.com.br/docs`

### Homologação:
- **Frontend**: `https://marketdash.hml.com.br`
- **Backend API**: `https://api.marketdash.hml.com.br`
- **Documentação**: `https://api.marketdash.hml.com.br/docs`

---

## 🔐 Configuração de DNS

No painel da Hostinger, configure os registros A:

```
Tipo: A
Nome: @ (ou deixe em branco para o domínio raiz)
Valor: [IP_DA_VPS]
TTL: 3600
Descrição: marketdash.com.br (frontend produção)

Tipo: A
Nome: api
Valor: [IP_DA_VPS]
TTL: 3600
Descrição: api.marketdash.com.br (backend produção)

Tipo: A
Nome: @ (ou deixe em branco)
Valor: [IP_DA_VPS]
TTL: 3600
Descrição: marketdash.hml.com.br (frontend homologação)

Tipo: A
Nome: api
Valor: [IP_DA_VPS]
TTL: 3600
Descrição: api.marketdash.hml.com.br (backend homologação)
```

**Nota**: Para os domínios de homologação (`marketdash.hml.com.br` e `api.marketdash.hml.com.br`), você precisará criar um subdomínio `hml` primeiro na Hostinger, ou configurar como domínio separado se `hml.com.br` for um domínio diferente.

---

## ✅ Verificação

Após atualizar tudo, verifique:

```bash
# Backend Produção
curl https://api.marketdash.com.br/health

# Backend Homologação
curl https://api.marketdash.hml.com.br/health

# Frontend Produção
curl https://marketdash.com.br

# Frontend Homologação
curl https://marketdash.hml.com.br
```

---

## 📋 Checklist Completo

- [x] Arquivos do backend atualizados
- [ ] Site URL atualizado no Supabase
- [ ] Redirect URLs atualizados no Supabase
- [ ] DNS configurado na Hostinger
- [ ] Domínios configurados no Coolify
- [ ] SSL gerado automaticamente
- [ ] Testes de acesso funcionando

---

**Status**: ✅ Backend atualizado, aguardando configurações no Supabase e DNS!

