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

### 1. Produção (marketdash-prod)

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

### 1. Verificar DNS

```bash
# Verificar resolução DNS
dig api.marketdash.com.br
dig marketdash.com.br
dig api.hml.marketdash.com.br
dig marketdash.hml.com.br

# Verificar se apontam para IP correto da VPS
dig +short api.marketdash.com.br
```

### 2. Verificar SSL/HTTPS

```bash
# Backend Produção
curl -I https://api.marketdash.com.br/health
# Deve retornar: HTTP/2 200 ou HTTP/1.1 200 OK

# Backend Homologação
curl -I https://api.hml.marketdash.com.br/health

# Frontend Produção
curl -I https://marketdash.com.br
# Deve retornar: HTTP/2 200 ou HTTP/1.1 200 OK

# Frontend Homologação
curl -I https://marketdash.hml.com.br

# Verificar detalhes do certificado
echo | openssl s_client -connect api.marketdash.com.br:443 -servername api.marketdash.com.br 2>/dev/null | openssl x509 -noout -dates
```

### 3. Verificar Redirecionamento HTTP → HTTPS

```bash
# Deve redirecionar para HTTPS
curl -I http://api.marketdash.com.br/health
# Deve retornar: HTTP/1.1 301 Moved Permanently ou 308 Permanent Redirect
# Location header deve apontar para https://
```

### 4. Verificar no Coolify Dashboard

- [ ] Acessar Coolify Dashboard
- [ ] Para cada aplicação (Backend/Frontend, Produção/Homologação):
  - [ ] Ir em **Settings** → **Domains**
  - [ ] Verificar se domínio está configurado
  - [ ] Verificar se **SSL está habilitado** (toggle ON)
  - [ ] Verificar status do certificado (Válido, não expirado)
  - [ ] Verificar logs se houver erros de SSL

---

## 📋 Checklist Completo

### Código
- [x] Arquivos do backend atualizados
- [x] CORS configurado para HTTPS apenas (exceto localhost)
- [x] Fallbacks HTTP removidos do código

### Infraestrutura
- [ ] DNS configurado na Hostinger (registros A)
- [ ] DNS propagado (verificar com `dig`)
- [ ] Domínios configurados no Coolify
- [ ] **SSL habilitado no Coolify para todos os domínios** ⚠️ **CRÍTICO**
- [ ] Certificados SSL gerados com sucesso
- [ ] Status dos certificados: Válido (não expirado)

### Configurações Externas
- [ ] Site URL atualizado no Supabase
- [ ] Redirect URLs atualizados no Supabase (apenas HTTPS)

### Testes
- [ ] HTTPS funciona para backend produção
- [ ] HTTPS funciona para frontend produção
- [ ] HTTPS funciona para backend homologação
- [ ] HTTPS funciona para frontend homologação
- [ ] Redirecionamento HTTP → HTTPS funciona
- [ ] Certificados são válidos (não auto-assinados)
- [ ] Certificados não estão expirados

### Troubleshooting

Se SSL não estiver funcionando:
1. Consulte [TROUBLESHOOTING-SSL.md](./TROUBLESHOOTING-SSL.md) para diagnóstico completo
2. Verifique logs no Coolify Dashboard
3. Verifique se DNS está propagado corretamente
4. Verifique se porta 80 e 443 estão abertas no firewall

---

**Status**: ✅ Backend atualizado, aguardando configurações no Supabase e DNS!

