# 🔍 Diagnóstico SSL/HTTPS - Coolify

## Data: 23/01/2026

## Situação Encontrada

### Aplicações Identificadas no Coolify

#### Backend
- **Produção**: `marketdash-backend:main`
  - Domínio: `api.marketdash.com.br`
  - Ambiente: production
  - Status: Running (healthy)
  
- **Homologação**: `marketdash-backend-hml`
  - Domínio: `api.hml.marketdash.com.br`
  - Ambiente: homologacao
  - Status: Running

#### Frontend
- **Produção**: `marketdash-frontend:main`
  - Domínio: `marketdash.com.br`
  - Ambiente: production
  - Status: Running

- **Homologação**: (precisa verificar)

### Configuração do Proxy

- **Proxy**: Traefik v3.6
- **Status**: Running
- **Porta 80**: ✅ Configurada
- **Porta 443**: ⚠️ **PROBLEMA**: Não está visível na configuração do docker-compose.yml

### Problemas Identificados

1. **Porta 443 não configurada no docker-compose.yml do proxy**
   - Apenas porta 80 está configurada: `- '80:80'`
   - Falta: `- '443:443'` para HTTPS

2. **SSL não está sendo gerado automaticamente**
   - Coolify deveria gerar certificados Let's Encrypt automaticamente
   - Mas os certificados não estão sendo gerados

3. **Configuração de Let's Encrypt pode estar faltando**
   - Traefik precisa de configuração para Let's Encrypt
   - Pode estar faltando no docker-compose.yml ou nas configurações dinâmicas

## Ações Necessárias

### 1. Verificar e Adicionar Porta 443 no Proxy

O docker-compose.yml do proxy precisa incluir a porta 443:

```yaml
ports:
  - '80:80'
  - '443:443'  # ADICIONAR ESTA LINHA
```

### 2. Verificar Configuração do Let's Encrypt no Traefik

O Traefik precisa ter configuração para Let's Encrypt. Verificar se há:

- EntryPoint para HTTPS (porta 443)
- Certificados Resolver configurado para Let's Encrypt
- Email configurado para Let's Encrypt

### 3. Verificar DNS

Confirmar que todos os domínios estão resolvendo corretamente:
- `api.marketdash.com.br`
- `marketdash.com.br`
- `api.hml.marketdash.com.br`
- `marketdash.hml.com.br` (ou variante)

### 4. Forçar Regeneração de Certificados

Após corrigir a configuração:
1. Reiniciar o proxy
2. Aguardar alguns minutos para geração dos certificados
3. Verificar logs do Traefik para erros de SSL

## Próximos Passos

1. Editar docker-compose.yml do proxy para adicionar porta 443
2. Verificar configurações dinâmicas do Traefik para Let's Encrypt
3. Reiniciar aplicações para forçar regeneração de certificados
4. Testar HTTPS em todos os domínios

## URLs do Coolify

- Dashboard: http://31.97.22.173:8000/
- Proxy Config: http://31.97.22.173:8000/server/zkgg000sw4g4swcc48gc4ock/proxy
- Backend Prod: http://31.97.22.173:8000/project/owocs8cgosw44sco0o0wg0o4/environment/zk8c0c8kg4ckws08ckc40kgk/application/toow0co8g40gkc44w84c4skw
- Backend HML: http://31.97.22.173:8000/project/owocs8cgosw44sco0o0wg0o4/environment/fo8wsggkg4k8ksksgss8sgcw/application/r448swsggoock0wg80csws0k
- Frontend Prod: http://31.97.22.173:8000/project/locc4kc0s80cws8gko8sowk0/environment/kowoow44084oksw484ccwcgs/application/qs0404g4g40gk80csg4gwo8c
