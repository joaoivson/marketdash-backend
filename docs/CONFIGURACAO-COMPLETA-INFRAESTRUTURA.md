# 📚 Documentação Completa - Configuração de Infraestrutura MarketDash

**Data de Criação:** 25/01/2026  
**Última Atualização:** 25/01/2026  
**Versão:** 1.0

---

## 📋 Índice

1. [Visão Geral da Arquitetura](#visão-geral-da-arquitetura)
2. [Configuração Hostinger VPS](#configuração-hostinger-vps)
3. [Configuração Cloudflare DNS](#configuração-cloudflare-dns)
4. [Configuração Coolify](#configuração-coolify)
5. [Configuração Traefik Proxy](#configuração-traefik-proxy)
6. [Configuração de Ambientes](#configuração-de-ambientes)
7. [Configuração SSL/HTTPS](#configuração-sslhttps)
8. [URLs e Endpoints](#urls-e-endpoints)
9. [Troubleshooting](#troubleshooting)
10. [Checklist de Verificação](#checklist-de-verificação)

---

## 🏗️ Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│                    Cloudflare DNS                        │
│  • marketdash.com.br                                    │
│  • api.marketdash.com.br                                │
│  • hml.marketdash.com.br                                │
│  • api.hml.marketdash.com.br                            │
│  • Proxy: DESABILITADO (DNS Only)                       │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Hostinger VPS (31.97.22.173)               │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │         Coolify (Porta 8000)                     │  │
│  │  • Dashboard: http://31.97.22.173:8000          │  │
│  │  • Gerenciamento de aplicações                   │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │         Traefik Proxy (Portas 80/443)           │  │
│  │  • Reverse Proxy                                │  │
│  │  • SSL/TLS Termination                          │  │
│  │  • Let's Encrypt (ACME)                         │  │
│  │  • Redirecionamento HTTP → HTTPS                │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │         Aplicações Docker                        │  │
│  │  • Backend Produção (FastAPI)                    │  │
│  │  • Backend Homologação (FastAPI)                 │  │
│  │  • Frontend Produção (React + Nginx)             │  │
│  │  • Frontend Homologação (React + Nginx)          │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                    Supabase Cloud                        │
│  • PostgreSQL Database                                  │
│  • Authentication                                       │
│  • Storage                                              │
└─────────────────────────────────────────────────────────┘
```

---

## 🖥️ Configuração Hostinger VPS

### Informações do Servidor

- **IP Público:** `31.97.22.173`
- **Tipo:** VPS KVM 2 ⚠️ **NÃO CONFIRMADO — ver aviso abaixo**
- **Especificações:**
  - 2 vCPUs
  - 2GB RAM
  - 40GB SSD

> ⚠️ **Conflito aberto (31/08/2026).** Esta seção é de 25/01/2026 e diz KVM 2
> (2 vCPU / 2 GB). O `docs/PLANO_ESCALA_100_USUARIAS.md` (27/08) afirma
> **KVM 4 — 4 vCPU / 16 GB** como o servidor atual. Os dois não podem estar
> certos, e a diferença decide se o teto de 60 sessões WAHA
> (`WHATSAPP_MAX_INSTANCIAS_GLOBAL`) é capacidade real ou fantasia: com 2 GB,
> compartilhados com API, worker, Redis e frontend, o teto real fica perto de
> 10–15 sessões.
>
> **Como resolver em 30 segundos** (precisa de acesso SSH, que não está
> disponível por chave — pede senha):
>
> ```bash
> ssh root@31.97.22.173 'nproc; free -g; df -h /'
> ```
>
> Ou pelo painel do Coolify. Quem medir, corrige aqui e apaga este aviso.
- **Sistema Operacional:** Ubuntu 22.04 LTS
- **Região:** Próxima ao Brasil

### Acesso SSH

```bash
# Conexão SSH
ssh root@31.97.22.173
# ou
ssh marketdash@31.97.22.173
```

### Portas Abertas

- **Porta 22:** SSH
- **Porta 80:** HTTP (Traefik)
- **Porta 443:** HTTPS (Traefik)
- **Porta 8000:** Coolify Dashboard

### Software Instalado

- **Docker:** Versão mais recente
- **Docker Compose:** Versão mais recente
- **Coolify:** v4.0.0-beta.462
- **Traefik:** v3.6 (via Coolify)

### Estrutura de Diretórios

```
/data/coolify/
├── proxy/
│   ├── docker-compose.yml          # Configuração do Traefik
│   └── acme.json                    # Certificados Let's Encrypt
└── [outros diretórios do Coolify]
```

---

## ☁️ Configuração Cloudflare DNS

### Domínio Principal

- **Domínio:** `marketdash.com.br`
- **Registrador:** Hostinger
- **DNS Management:** Cloudflare
- **Proxy Status:** **DESABILITADO** (DNS Only - Gray Cloud)

### Registros DNS Configurados

#### Produção

| Tipo | Nome | Valor | TTL | Proxy | Descrição |
|------|------|-------|-----|-------|-----------|
| A | @ | 31.97.22.173 | 3600 | ❌ Off | Frontend Produção |
| A | api | 31.97.22.173 | 3600 | ❌ Off | Backend Produção |

#### Homologação

| Tipo | Nome | Valor | TTL | Proxy | Descrição |
|------|------|-------|-----|-------|-----------|
| A | hml | 31.97.22.173 | 3600 | ❌ Off | Frontend Homologação |
| A | api.hml | 31.97.22.173 | 3600 | ❌ Off | Backend Homologação |

### Configurações Cloudflare

- **SSL/TLS Mode:** Full ou Full (strict) - **NÃO usado** (proxy desabilitado)
- **Always Use HTTPS:** N/A (proxy desabilitado)
- **Automatic HTTPS Rewrites:** N/A (proxy desabilitado)
- **Minimum TLS Version:** N/A (proxy desabilitado)

**⚠️ IMPORTANTE:** O proxy do Cloudflare está **DESABILITADO** (gray cloud) para permitir que o Let's Encrypt valide os domínios diretamente no servidor.

### Verificação DNS

```bash
# Verificar resolução DNS
dig marketdash.com.br
dig api.marketdash.com.br
dig hml.marketdash.com.br
dig api.hml.marketdash.com.br

# Verificar IP retornado
dig +short marketdash.com.br
# Deve retornar: 31.97.22.173
```

---

## 🚀 Configuração Coolify

### Informações do Servidor Coolify

- **URL Dashboard:** `http://31.97.22.173:8000`
- **Versão:** v4.0.0-beta.462
- **Server ID:** `zkgg000sw4g4swcc48gc4ock`
- **Server Name:** `localhost`
- **Status Proxy:** Running

### Estrutura de Projetos

#### Projeto: App Frontend
- **Project ID:** `locc4kc0s80cws8gko8sowk0`
- **Environments:**
  - **Produção:** `kowoow44084oksw484ccwcgs`
  - **Homologação:** `bggssk4wwgooswc08w4wkcsc`

#### Projeto: Backend
- **Project ID:** `owocs8cgosw44sco0o0wg0o4`
- **Environments:**
  - **Produção:** `zk8c0c8kg4ckws08ckc40kgk`
  - **Homologação:** `fo8wsggkg4k8ksksgss8sgcw`

### Aplicações Configuradas

#### Backend Produção
- **Application ID:** `toow0co8g40gkc44w84c4skw`
- **Nome:** `marketdash-backend:main`
- **Status:** Running (healthy)
- **Domínio:** `api.marketdash.com.br`
- **Porta Interna:** 8000
- **Build Pack:** Dockerfile

#### Backend Homologação
- **Application ID:** `r448swsggoock0wg80csws0k`
- **Nome:** `marketdash-backend-hml`
- **Status:** Running
- **Domínio:** `api.hml.marketdash.com.br`
- **Porta Interna:** 8000
- **Build Pack:** Dockerfile

#### Frontend Produção
- **Application ID:** `qs0404g4g40gk80csg4gwo8c`
- **Nome:** `marketdash-frontend:main`
- **Status:** Running
- **Domínio:** `marketdash.com.br`
- **Porta Interna:** 80
- **Build Pack:** Dockerfile

#### Frontend Homologação
- **Application ID:** `mws0c0g4kkw00cwg88o00kw4`
- **Nome:** `marketdash-frontend-hml`
- **Status:** Running
- **Domínio:** `hml.marketdash.com.br`
- **Porta Interna:** 80
- **Build Pack:** Dockerfile

### Configurações Avançadas do Proxy

- **Generate labels only for Traefik:** ✅ Habilitado
- **Override default request handler:** ❌ Desabilitado
- **Proxy Type:** Traefik (Coolify Proxy)

### URLs de Acesso no Coolify

- **Dashboard:** `http://31.97.22.173:8000/`
- **Proxy Configuration:** `http://31.97.22.173:8000/server/zkgg000sw4g4swcc48gc4ock/proxy`
- **Proxy Logs:** `http://31.97.22.173:8000/server/zkgg000sw4g4swcc48gc4ock/proxy/logs`
- **Proxy Dynamic Config:** `http://31.97.22.173:8000/server/zkgg000sw4g4swcc48gc4ock/proxy/dynamic`

---

## 🔄 Configuração Traefik Proxy

### Informações do Traefik

- **Versão:** v3.6
- **Container Name:** `coolify-proxy`
- **Image:** `traefik:v3.6`
- **Status:** Running
- **Network:** `coolify` (Docker network)

### Docker Compose Configuration

**Arquivo:** `/data/coolify/proxy/docker-compose.yml`

```yaml
name: coolify-proxy
networks:
  coolify:
    external: true
services:
  traefik:
    container_name: coolify-proxy
    image: 'traefik:v3.6'
    restart: unless-stopped
    extra_hosts:
      - 'host.docker.internal:host-gateway'
    networks:
      - coolify
    ports:
      - '80:80'
      - '443:443'
      - '443:443/udp'
    volumes:
      - '/var/run/docker.sock:/var/run/docker.sock:ro'
      - '/data/coolify/proxy:/etc/traefik'
    command:
      - '--api.dashboard=false'
      - '--api.insecure=false'
      - '--entrypoints.http.address=:80'
      - '--entrypoints.https.address=:443'
      - '--entrypoints.http.http.redirections.entrypoint.to=https'
      - '--entrypoints.http.http.redirections.entrypoint.scheme=https'
      - '--entrypoints.http.http.redirections.entrypoint.permanent=true'
      - '--providers.docker=true'
      - '--providers.docker.exposedbydefault=false'
      - '--certificatesresolvers.letsencrypt.acme.httpchallenge=true'
      - '--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=http'
      - '--certificatesresolvers.letsencrypt.acme.email=joaoivsonn@gmail.com'
      - '--certificatesresolvers.letsencrypt.acme.storage=/etc/traefik/acme.json'
```

### Configurações Principais

#### Entrypoints

- **HTTP (Porta 80):**
  - Redireciona automaticamente para HTTPS
  - Permite acesso ao endpoint `.well-known/acme-challenge` para validação Let's Encrypt
  - Redirecionamento permanente (301)

- **HTTPS (Porta 443):**
  - Terminação SSL/TLS
  - Certificados Let's Encrypt automáticos

#### Let's Encrypt (ACME)

- **Email:** `joaoivsonn@gmail.com`
- **Challenge Type:** HTTP-01
- **Storage:** `/etc/traefik/acme.json`
- **Resolver Name:** `letsencrypt`
- **Entrypoint para Challenge:** `http` (porta 80)

#### Docker Provider

- **Auto-discovery:** Habilitado
- **Exposed by Default:** Desabilitado (apenas containers com labels Traefik)
- **Network:** `coolify`

### Labels Traefik Geradas pelo Coolify

#### Exemplo: Frontend Homologação

```yaml
traefik.enable=true
traefik.http.middlewares.gzip.compress=true
traefik.http.middlewares.redirect-to-https.redirectscheme.scheme=https
traefik.http.routers.http-0-mws0c0g4kkw00cwg88o00kw4.entryPoints=http
traefik.http.routers.http-0-mws0c0g4kkw00cwg88o00kw4.middlewares=redirect-to-https
traefik.http.routers.http-0-mws0c0g4kkw00cwg88o00kw4.rule=Host(`hml.marketdash.com.br`) && PathPrefix(`/`)
traefik.http.routers.http-0-mws0c0g4kkw00cwg88o00kw4.service=http-0-mws0c0g4kkw00cwg88o00kw4
traefik.http.routers.https-0-mws0c0g4kkw00cwg88o00kw4.entryPoints=https
traefik.http.routers.https-0-mws0c0g4kkw00cwg88o00kw4.middlewares=gzip
traefik.http.routers.https-0-mws0c0g4kkw00cwg88o00kw4.rule=Host(`hml.marketdash.com.br`) && PathPrefix(`/`)
traefik.http.routers.https-0-mws0c0g4kkw00cwg88o00kw4.service=https-0-mws0c0g4kkw00cwg88o00kw4
traefik.http.routers.https-0-mws0c0g4kkw00cwg88o00kw4.tls.certresolver=letsencrypt
```

**⚠️ IMPORTANTE:** A label `traefik.http.routers.https-*.tls.certresolver=letsencrypt` é **ESSENCIAL** para habilitar SSL. Sem ela, o Traefik não solicitará certificados do Let's Encrypt.

### Middlewares

- **gzip:** Compressão de resposta
- **redirect-to-https:** Redirecionamento HTTP → HTTPS

### Logs do Traefik

**Localização:** Acessível via Coolify Dashboard → Server → Proxy → Logs

**Comandos úteis:**
```bash
# Ver logs do container Traefik
docker logs coolify-proxy -f

# Ver logs filtrados por SSL/ACME
docker logs coolify-proxy 2>&1 | grep -i "acme\|ssl\|certificate\|letsencrypt"
```

---

## 🌍 Configuração de Ambientes

### Ambiente: Produção

#### Backend Produção

- **Domínio:** `https://api.marketdash.com.br`
- **Health Check:** `https://api.marketdash.com.br/health`
- **API Docs:** `https://api.marketdash.com.br/docs`
- **ReDoc:** `https://api.marketdash.com.br/redoc`
- **Porta Interna:** 8000
- **Status:** Running (healthy)

**Variáveis de Ambiente:**
```env
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.rsejwvxealraianensoz.supabase.co:6543/postgres?sslmode=require
ENVIRONMENT=production
JWT_SECRET=[SECRET_KEY]
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
```

#### Frontend Produção

- **Domínio:** `https://marketdash.com.br`
- **Porta Interna:** 80
- **Status:** Running

**Variáveis de Ambiente (Build):**
```env
VITE_API_URL=https://api.marketdash.com.br
VITE_SUPABASE_URL=https://rsejwvxealraianensoz.supabase.co
VITE_SUPABASE_ANON_KEY=[ANON_KEY]
```

### Ambiente: Homologação

#### Backend Homologação

- **Domínio:** `https://api.hml.marketdash.com.br`
- **Health Check:** `https://api.hml.marketdash.com.br/health`
- **API Docs:** `https://api.hml.marketdash.com.br/docs`
- **Porta Interna:** 8000
- **Status:** Running

**Variáveis de Ambiente:**
```env
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[HML_PROJECT].supabase.co:6543/postgres?sslmode=require
ENVIRONMENT=homologation
JWT_SECRET=[SECRET_KEY]
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
```

#### Frontend Homologação

- **Domínio:** `https://hml.marketdash.com.br`
- **Porta Interna:** 80
- **Status:** Running

**Variáveis de Ambiente (Build):**
```env
VITE_API_URL=https://api.hml.marketdash.com.br
VITE_SUPABASE_URL=https://[HML_PROJECT].supabase.co
VITE_SUPABASE_ANON_KEY=[HML_ANON_KEY]
```

---

## 🔐 Configuração SSL/HTTPS

### Status Atual

- ✅ **Porta 443:** Configurada no Traefik
- ✅ **Let's Encrypt:** Configurado no Traefik
- ✅ **Redirecionamento HTTP → HTTPS:** Habilitado
- ⚠️ **Certificados:** Sendo gerados (pode levar alguns minutos)

### Configuração Let's Encrypt

- **Email de Contato:** `joaoivsonn@gmail.com`
- **Challenge Type:** HTTP-01
- **Storage:** `/data/coolify/proxy/acme.json`
- **Renovação Automática:** Sim (a cada 90 dias)

### Verificação de Certificados

```bash
# Verificar certificado do backend produção
echo | openssl s_client -connect api.marketdash.com.br:443 -servername api.marketdash.com.br 2>/dev/null | openssl x509 -noout -dates -subject -issuer

# Verificar certificado do frontend produção
echo | openssl s_client -connect marketdash.com.br:443 -servername marketdash.com.br 2>/dev/null | openssl x509 -noout -dates -subject -issuer
```

### Troubleshooting SSL

#### Problema: "Site não seguro" no navegador

**Causas possíveis:**
1. Certificados ainda sendo gerados (aguardar 5-10 minutos)
2. Label `certresolver` faltando nos labels Traefik
3. Endpoint `.well-known/acme-challenge` não acessível
4. DNS não propagado corretamente

**Solução:**
1. Verificar se a label `traefik.http.routers.https-*.tls.certresolver=letsencrypt` está presente
2. Reiniciar o proxy Traefik
3. Aguardar alguns minutos
4. Limpar cache do navegador

#### Problema: Erro "Cannot retrieve the ACME challenge"

**Causa:** O Let's Encrypt não consegue acessar o endpoint de validação.

**Solução:**
1. Verificar se o DNS está apontando corretamente para o IP do servidor
2. Verificar se o proxy do Cloudflare está desabilitado (gray cloud)
3. Verificar se a porta 80 está acessível publicamente
4. Verificar logs do Traefik para mais detalhes

---

## 🔗 URLs e Endpoints

### Produção

| Serviço | URL | Status |
|---------|-----|--------|
| Frontend | `https://marketdash.com.br` | ✅ Ativo |
| Backend API | `https://api.marketdash.com.br` | ✅ Ativo |
| Health Check | `https://api.marketdash.com.br/health` | ✅ Ativo |
| API Docs (Swagger) | `https://api.marketdash.com.br/docs` | ✅ Ativo |
| API Docs (ReDoc) | `https://api.marketdash.com.br/redoc` | ✅ Ativo |

### Homologação

| Serviço | URL | Status |
|---------|-----|--------|
| Frontend | `https://hml.marketdash.com.br` | ✅ Ativo |
| Backend API | `https://api.hml.marketdash.com.br` | ✅ Ativo |
| Health Check | `https://api.hml.marketdash.com.br/health` | ✅ Ativo |
| API Docs (Swagger) | `https://api.hml.marketdash.com.br/docs` | ✅ Ativo |
| API Docs (ReDoc) | `https://api.hml.marketdash.com.br/redoc` | ✅ Ativo |

### Infraestrutura

| Serviço | URL | Descrição |
|---------|-----|-----------|
| Coolify Dashboard | `http://31.97.22.173:8000` | Gerenciamento de aplicações |
| Traefik Dashboard | Desabilitado | Por segurança |

---

## 🔧 Troubleshooting

### Problema: 404 Page Not Found

**Possíveis causas:**
1. Aplicação não está rodando
2. Domínio não configurado corretamente no Coolify
3. Labels Traefik incorretas
4. "Override default request handler" habilitado no Coolify

**Solução:**
1. Verificar status da aplicação no Coolify
2. Verificar configuração de domínios
3. Verificar labels Traefik geradas
4. Desabilitar "Override default request handler"

### Problema: SSL não funciona

**Possíveis causas:**
1. Porta 443 não configurada
2. Label `certresolver` faltando
3. Certificados ainda sendo gerados
4. DNS não propagado

**Solução:**
1. Verificar configuração do docker-compose.yml do proxy
2. Verificar labels Traefik (especialmente `certresolver`)
3. Aguardar alguns minutos após reiniciar o proxy
4. Verificar resolução DNS

### Problema: Redirecionamento HTTP → HTTPS não funciona

**Possíveis causas:**
1. Configuração de redirecionamento faltando no Traefik
2. Middleware de redirecionamento não aplicado

**Solução:**
1. Verificar configuração do entrypoint HTTP no Traefik
2. Verificar se o middleware `redirect-to-https` está aplicado

### Comandos Úteis

```bash
# Verificar containers rodando
docker ps

# Ver logs do Traefik
docker logs coolify-proxy -f

# Ver logs de uma aplicação específica
docker logs [container_name] -f

# Reiniciar proxy
# Via Coolify Dashboard → Server → Proxy → Restart Proxy

# Verificar DNS
dig marketdash.com.br
dig api.marketdash.com.br

# Testar HTTPS
curl -I https://api.marketdash.com.br/health
curl -I https://marketdash.com.br

# Verificar certificado SSL
echo | openssl s_client -connect api.marketdash.com.br:443 -servername api.marketdash.com.br 2>/dev/null | openssl x509 -noout -text
```

---

## ✅ Checklist de Verificação

### Infraestrutura

- [x] VPS Hostinger configurada
- [x] Docker e Docker Compose instalados
- [x] Coolify instalado e rodando
- [x] Traefik configurado como proxy
- [x] Portas 80 e 443 abertas no firewall

### DNS

- [x] Domínios configurados no Cloudflare
- [x] Registros A apontando para IP correto (31.97.22.173)
- [x] Proxy Cloudflare desabilitado (gray cloud)
- [x] DNS propagado (verificado com `dig`)

### Coolify

- [x] Projetos criados (Frontend e Backend)
- [x] Ambientes criados (Produção e Homologação)
- [x] Aplicações configuradas
- [x] Domínios configurados nas aplicações
- [x] Variáveis de ambiente configuradas

### Traefik

- [x] Docker Compose configurado corretamente
- [x] Portas 80 e 443 mapeadas
- [x] Entrypoints HTTP e HTTPS configurados
- [x] Redirecionamento HTTP → HTTPS configurado
- [x] Let's Encrypt configurado
- [x] Email do Let's Encrypt configurado
- [x] Storage do ACME configurado

### SSL/HTTPS

- [x] Certificados sendo gerados
- [x] Labels `certresolver` presentes nos routers HTTPS
- [x] Endpoint `.well-known/acme-challenge` acessível
- [x] Certificados válidos (não auto-assinados)

### Aplicações

- [x] Backend Produção rodando e saudável
- [x] Backend Homologação rodando
- [x] Frontend Produção rodando
- [x] Frontend Homologação rodando
- [x] Health checks respondendo corretamente

### Testes

- [x] HTTPS funcionando para backend produção
- [x] HTTPS funcionando para frontend produção
- [x] HTTPS funcionando para backend homologação
- [x] HTTPS funcionando para frontend homologação
- [x] Redirecionamento HTTP → HTTPS funcionando
- [x] Certificados válidos no navegador

---

## 📝 Notas Importantes

### Segurança

1. **Coolify Dashboard:** Acessível apenas via IP interno. Considere adicionar autenticação adicional se necessário.
2. **Traefik Dashboard:** Desabilitado por padrão (recomendado para produção).
3. **Certificados:** Renovação automática a cada 90 dias via Let's Encrypt.
4. **Firewall:** Apenas portas necessárias devem estar abertas (22, 80, 443, 8000).

### Manutenção

1. **Logs:** Verificar logs regularmente via Coolify Dashboard.
2. **Backups:** Configurar backups regulares do arquivo `acme.json` (certificados).
3. **Atualizações:** Manter Coolify e Traefik atualizados.
4. **Monitoramento:** Configurar monitoramento de saúde das aplicações.

### Limitações Conhecidas

1. **Rate Limits Let's Encrypt:** Máximo de 50 certificados por domínio registrado por semana.
2. **Propagação DNS:** Pode levar até 24 horas (geralmente 1-2 horas).
3. **Geração de Certificados:** Pode levar 5-10 minutos após configuração inicial.

---

## 📞 Contatos e Suporte

- **Email Let's Encrypt:** joaoivsonn@gmail.com
- **Documentação Coolify:** https://coolify.io/docs
- **Documentação Traefik:** https://doc.traefik.io/traefik/
- **Documentação Let's Encrypt:** https://letsencrypt.org/docs/

---

## 📅 Histórico de Alterações

| Data | Versão | Alteração | Autor |
|------|--------|-----------|-------|
| 25/01/2026 | 1.0 | Criação da documentação completa | Sistema |

---

**Fim da Documentação**
