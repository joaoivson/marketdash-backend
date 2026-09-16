# 🚀 Guia de Deploy - MarketDash Backend

## Deploy no Coolify

### Pré-requisitos

1. VPS com Coolify instalado
2. Projeto Supabase configurado
3. Domínio configurado

### Variáveis de Ambiente no Coolify

Configure as seguintes variáveis no Coolify:

```env
DATABASE_URL=postgresql://postgres.rsejwvxealraianensoz:[SENHA_URL_ENCODED]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
SUPABASE_URL=https://rsejwvxealraianensoz.supabase.co
SUPABASE_KEY=sb_publishable_wn-jD_u50_800ku-syYsxQ_WhI3j_6X
SUPABASE_SERVICE_KEY=sb_secret_6cY091QlTEH1g2gZZyxLkw_frvldCnq
JWT_SECRET=[GERE_UMA_CHAVE_FORTE_AQUI]
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
ENVIRONMENT=production
```

**Importante**: 
- Substitua `[SENHA_URL_ENCODED]` pela senha do banco com URL encoding (ex: `@` vira `%40`)
- Gere um `JWT_SECRET` diferente para produção usando: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`

### Configuração no Coolify

> ⚠️ **Desde 16/09/2026 o Coolify NÃO constrói mais.** Quem constrói é o GitHub
> Actions; o VPS só puxa a imagem pronta do GHCR. O motivo está em
> `docs/PROMOCAO_PARA_PRODUCAO.md` §10 — build dentro do servidor que serve
> produção derrubou produção duas vezes em cinco dias.

1. **Source**: **Docker Image** (não Git Repository)
   - Image: `ghcr.io/joaoivson/marketdash-backend` (API) ou
     `…/marketdash-backend-worker` (worker Celery)
   - Tag: o **SHA completo** do commit — quem grava é o CI, não a mão
   - Imagens públicas: o host não precisa de `docker login`

2. **Build Pack**: `dockerimage`
   - Se estiver `dockerfile`, o `deploy-imagem.sh` **recusa disparar** o deploy:
     um POST nesse estado mandaria o VPS compilar
   - A API do Coolify recusa trocar isso por PATCH (422); a migração foi por
     `UPDATE` direto no `coolify-db`, com `pg_dump` antes

3. **Auto Deploy: DESLIGADO.** Ligado, o webhook do GitHub App dispara build no
   VPS pelas costas do CI — e foi assim que 5 builds simultâneos derrubaram
   produção em 11/09.

4. **Port**: `8000`

5. **Domain**: `api.marketdash.com.br` (produção)
   - **SSL: Enabled (Let's Encrypt)** - ⚠️ **IMPORTANTE**: Certifique-se de que SSL está habilitado
   - Coolify gerencia certificados SSL automaticamente via Let's Encrypt
   - Certificados são renovados automaticamente a cada 90 dias

### Repositórios

- **Backend**: https://github.com/joaoivson/dash
- **Frontend**: https://github.com/joaoivson/insight-spark

### Exemplo de .env para Desenvolvimento Local

```env
# Database (Supabase PostgreSQL - Connection Pooling)
DATABASE_URL=postgresql://postgres.rsejwvxealraianensoz:[SENHA_URL_ENCODED]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres

# Supabase Configuration
SUPABASE_URL=https://rsejwvxealraianensoz.supabase.co
SUPABASE_KEY=sb_publishable_wn-jD_u50_800ku-syYsxQ_WhI3j_6X
SUPABASE_SERVICE_KEY=sb_secret_6cY091QlTEH1g2gZZyxLkw_frvldCnq

# JWT Configuration
JWT_SECRET=your-secret-key-min-32-chars-change-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# Environment
ENVIRONMENT=development
```

### Gerar JWT_SECRET

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### URL Encoding de Senhas

Se sua senha contém caracteres especiais (como `@`), você precisa fazer URL encoding:
- `@` → `%40`
- `#` → `%23`
- `%` → `%25`
- `&` → `%26`
- etc.

Ou use uma ferramenta online de URL encoding.

### Health Check

O endpoint `/health` está disponível para verificação:

```bash
curl https://api.marketdash.com.br/health
# {"status":"healthy","database":"connected","version":"<sha do commit no ar>"}
```

O campo `version` vem de `APP_VERSION`, gravado na imagem em build-time
(`ARG GIT_SHA`). É a prova de QUAL commit está servindo — o CI compara esse
valor com o SHA empurrado antes de ficar verde. `{"status":"healthy"}` sozinho
diz que o processo subiu, não que o código novo está no ar.

### Configuração de SSL/HTTPS

#### Habilitar SSL no Coolify

1. **Acesse o Coolify Dashboard**
2. **Para cada aplicação (Backend e Frontend, Produção e Homologação)**:
   - Vá em **Settings** → **Domains**
   - Adicione o domínio se ainda não estiver configurado
   - **Ative o toggle de SSL** (Let's Encrypt)
   - Aguarde alguns minutos para geração do certificado

3. **Domínios a configurar**:
   - **Backend Produção**: `api.marketdash.com.br`
   - **Frontend Produção**: `marketdash.com.br`
   - **Backend Homologação**: `api.hml.marketdash.com.br` (ou variante)
   - **Frontend Homologação**: `marketdash.hml.com.br` ou `hml.marketdash.com.br`

4. **Verificar SSL**:
   ```bash
   # Testar Backend Produção
   curl -I https://api.marketdash.com.br/health
   
   # Testar Frontend Produção
   curl -I https://marketdash.com.br
   ```

5. **Se SSL não funcionar**:
   - Verifique logs no Coolify
   - Verifique se DNS está propagado corretamente
   - Consulte [TROUBLESHOOTING-SSL.md](./TROUBLESHOOTING-SSL.md) para diagnóstico completo

#### Mecanismo de Rollback de Emergência

Em caso de problemas críticos com SSL, é possível usar HTTP temporariamente:

**Backend** (variável de ambiente no Coolify):
```env
FORCE_HTTP_FALLBACK=true
```

**Frontend** (variável de ambiente no build):
```env
VITE_FORCE_HTTP_FALLBACK=true
```

⚠️ **ATENÇÃO**: 
- Use apenas em emergências críticas
- Remova assim que SSL for corrigido
- Logs mostrarão warnings quando ativo
- Não é recomendado para produção

### Documentação da API

Após o deploy, a documentação interativa estará disponível em:
- Swagger UI: `https://api.marketdash.com.br/docs`
- ReDoc: `https://api.marketdash.com.br/redoc`

### Troubleshooting

- **Problemas com SSL/HTTPS**: Consulte [TROUBLESHOOTING-SSL.md](./TROUBLESHOOTING-SSL.md)
- **Problemas com deploy**: Verifique logs no Coolify Dashboard
- **Problemas com banco de dados**: Verifique variável `DATABASE_URL` e conexão com Supabase