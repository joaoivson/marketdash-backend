# 🔒 Troubleshooting SSL/HTTPS - MarketDash

Este guia ajuda a diagnosticar e resolver problemas de SSL/HTTPS nos ambientes de produção e homologação.

## 📋 Índice

1. [Verificação Rápida](#verificação-rápida)
2. [Diagnóstico no Coolify](#diagnóstico-no-coolify)
3. [Problemas Comuns e Soluções](#problemas-comuns-e-soluções)
4. [Comandos de Teste](#comandos-de-teste)
5. [Renovação de Certificados](#renovação-de-certificados)

---

## Verificação Rápida

### 1. Teste Rápido de HTTPS

Execute estes comandos para verificar se HTTPS está funcionando:

```bash
# Backend Produção
curl -I https://api.marketdash.com.br/health

# Frontend Produção
curl -I https://marketdash.com.br

# Backend Homologação
curl -I https://api.hml.marketdash.com.br/health

# Frontend Homologação
curl -I https://marketdash.hml.com.br
```

**Resultado esperado:**
- Status `200 OK` ou `301/302 Redirect` (não erro de SSL)
- Sem mensagens de "certificate verify failed" ou "SSL connection error"

**Se houver erro:**
- Continue com o diagnóstico abaixo

---

## Diagnóstico no Coolify

### Passo 1: Acessar Coolify Dashboard

1. Faça login no Coolify Dashboard
2. Localize as aplicações:
   - Backend Produção
   - Frontend Produção
   - Backend Homologação
   - Frontend Homologação

### Passo 2: Verificar Configuração de Domínios

Para cada aplicação:

1. Acesse **Settings** → **Domains** (ou aba similar)
2. Verifique se os domínios estão configurados:
   - **Backend Produção**: `api.marketdash.com.br`
   - **Frontend Produção**: `marketdash.com.br`
   - **Backend Homologação**: `api.hml.marketdash.com.br` (ou variante)
   - **Frontend Homologação**: `marketdash.hml.com.br` ou `hml.marketdash.com.br`

3. Verifique o status de SSL:
   - Procure por um **toggle/switch de SSL**
   - Verifique se está **ativado** (ON/Enabled)
   - Verifique o status do certificado:
     - ✅ **Válido** - Certificado está funcionando
     - ⚠️ **Pendente** - Certificado está sendo gerado
     - ❌ **Erro** - Falha na geração do certificado
     - ⏰ **Expirado** - Certificado expirou

### Passo 3: Habilitar SSL (se não estiver habilitado)

1. Se SSL não estiver habilitado:
   - Ative o **toggle de SSL** para cada domínio
   - Coolify deve automaticamente:
     - Gerar certificado via Let's Encrypt
     - Configurar Nginx/Traefik para usar HTTPS
     - Configurar redirecionamento HTTP → HTTPS

2. Aguarde alguns minutos (2-5 minutos) para geração do certificado

3. Verifique se aparece mensagem de sucesso ou erro

### Passo 4: Verificar Logs de SSL

1. No Coolify, acesse **Logs** da aplicação
2. Procure por mensagens relacionadas a SSL/Let's Encrypt
3. Verifique se há erros como:
   - `Failed to obtain certificate`
   - `DNS challenge failed`
   - `Domain verification failed`
   - `Rate limit exceeded`

### Passo 5: Forçar Regeneração (se necessário)

Se certificado existir mas estiver com problemas:

1. **Desabilitar SSL temporariamente**:
   - Desative o toggle de SSL
   - Aguarde 10-15 segundos

2. **Reabilitar SSL**:
   - Ative o toggle de SSL novamente
   - Isso força regeneração do certificado

3. Aguarde alguns minutos para nova geração

---

## Problemas Comuns e Soluções

### Problema 1: SSL não está habilitado

**Sintomas:**
- Aplicação funciona apenas com HTTP
- Erro ao acessar HTTPS

**Solução:**
1. Acesse Coolify Dashboard
2. Vá em Settings → Domains
3. Ative o toggle de SSL
4. Aguarde geração do certificado

### Problema 2: Certificado não é gerado

**Sintomas:**
- SSL está habilitado mas certificado não é gerado
- Logs mostram erro de geração

**Possíveis causas e soluções:**

#### A) DNS não propagado
```bash
# Verificar DNS
dig api.marketdash.com.br
nslookup api.marketdash.com.br

# Verificar se aponta para IP correto da VPS
```

**Solução:**
- Aguardar propagação DNS (pode levar até 48h, geralmente 1-2h)
- Verificar registros A na Hostinger
- Garantir que todos os subdomínios apontam para IP correto

#### B) Rate limit do Let's Encrypt
**Sintomas:**
- Erro "Rate limit exceeded" nos logs

**Solução:**
- Let's Encrypt tem limite de 50 certificados por domínio por semana
- Aguardar 7 dias ou usar certificado existente
- Verificar se há outros certificados para o mesmo domínio

#### C) Porta 80 bloqueada
**Sintomas:**
- Let's Encrypt precisa de porta 80 para validação HTTP-01

**Solução:**
- Verificar se porta 80 está aberta no firewall
- Verificar se Nginx/Traefik está escutando na porta 80

### Problema 3: Certificado expirado

**Sintomas:**
- Certificado válido mas expirou
- Navegador mostra aviso de certificado expirado

**Solução:**
1. Forçar regeneração (Passo 5 acima)
2. Verificar se renovação automática está configurada
3. Coolify geralmente renova automaticamente, mas pode falhar

### Problema 4: Certificado inválido ou auto-assinado

**Sintomas:**
- Navegador mostra aviso de certificado não confiável
- Certificado não é do Let's Encrypt

**Solução:**
1. Verificar se está usando certificado do Let's Encrypt
2. Se não, desabilitar e reabilitar SSL para gerar novo certificado
3. Verificar se não há certificado customizado configurado

### Problema 5: Mixed Content (HTTP e HTTPS)

**Sintomas:**
- Site carrega mas alguns recursos (imagens, scripts) não carregam
- Console do navegador mostra erros de mixed content

**Solução:**
1. Verificar se todas as URLs no código usam HTTPS
2. Verificar variável `VITE_API_URL` no frontend
3. Verificar CORS no backend (deve permitir apenas HTTPS em produção)

---

## Comandos de Teste

### Teste Básico de HTTPS

```bash
# Testar Backend Produção
curl -I https://api.marketdash.com.br/health

# Testar Frontend Produção
curl -I https://marketdash.com.br

# Testar Backend Homologação
curl -I https://api.hml.marketdash.com.br/health

# Testar Frontend Homologação
curl -I https://marketdash.hml.com.br
```

### Verificar Certificado Detalhado

```bash
# Ver detalhes do certificado
openssl s_client -connect api.marketdash.com.br:443 -servername api.marketdash.com.br

# Ver data de expiração
echo | openssl s_client -connect api.marketdash.com.br:443 -servername api.marketdash.com.br 2>/dev/null | openssl x509 -noout -dates
```

### Verificar DNS

```bash
# Verificar resolução DNS
dig api.marketdash.com.br
dig marketdash.com.br
nslookup api.marketdash.com.br

# Verificar se aponta para IP correto
dig +short api.marketdash.com.br
```

### Testar Redirecionamento HTTP → HTTPS

```bash
# Deve redirecionar para HTTPS
curl -I http://api.marketdash.com.br/health

# Verificar se Location header aponta para HTTPS
```

---

## Renovação de Certificados

### Renovação Automática

Coolify geralmente gerencia renovação automática de certificados Let's Encrypt. Certificados Let's Encrypt expiram em **90 dias** e são renovados automaticamente.

### Verificar Renovação

1. No Coolify, verifique logs de renovação
2. Certificados são renovados automaticamente quando faltam 30 dias para expirar
3. Verifique se há erros nos logs de renovação

### Renovação Manual

Se renovação automática falhar:

1. Desabilitar SSL temporariamente
2. Aguardar 10-15 segundos
3. Reabilitar SSL
4. Isso força regeneração do certificado

---

## Checklist de Verificação

Use este checklist para verificar se tudo está configurado corretamente:

### Infraestrutura

- [ ] DNS configurado corretamente na Hostinger
- [ ] Registros A apontam para IP correto da VPS
- [ ] DNS propagado (verificar com `dig` ou `nslookup`)
- [ ] Porta 80 aberta no firewall
- [ ] Porta 443 aberta no firewall

### Coolify

- [ ] Domínios configurados no Coolify
- [ ] SSL habilitado para todos os domínios
- [ ] Certificados gerados com sucesso
- [ ] Status do certificado: Válido (não expirado, não com erro)
- [ ] Logs não mostram erros de SSL

### Testes

- [ ] HTTPS funciona para backend produção
- [ ] HTTPS funciona para frontend produção
- [ ] HTTPS funciona para backend homologação
- [ ] HTTPS funciona para frontend homologação
- [ ] Redirecionamento HTTP → HTTPS funciona
- [ ] Certificados são válidos (não auto-assinados)
- [ ] Certificados não estão expirados

### Código

- [ ] `VITE_API_URL` usa HTTPS em produção/homologação
- [ ] CORS no backend permite apenas HTTPS (exceto localhost)
- [ ] Sem fallbacks HTTP no código (exceto desenvolvimento local)

---

## Suporte Adicional

Se após seguir este guia o problema persistir:

1. Verifique logs detalhados no Coolify
2. Verifique logs do Nginx/Traefik na VPS
3. Verifique configuração de firewall
4. Verifique se há múltiplos certificados para o mesmo domínio
5. Consulte documentação do Coolify: https://coolify.io/docs

---

## Referências

- [Coolify Documentation](https://coolify.io/docs)
- [Let's Encrypt Documentation](https://letsencrypt.org/docs/)
- [SSL Labs SSL Test](https://www.ssllabs.com/ssltest/) - Teste de qualidade do certificado
