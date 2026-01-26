# 🔧 Solução SSL/HTTPS - Coolify

## Data: 23/01/2026

## Problema Identificado

O Coolify não está gerando automaticamente as labels HTTPS do Traefik para habilitar SSL/HTTPS. As labels atuais incluem apenas:
- Router HTTP (`traefik.http.routers.http-0-*.entryPoints=http`)
- Redirecionamento HTTP→HTTPS configurado
- **MAS FALTA**: Router HTTPS com certificados Let's Encrypt

## Configurações Verificadas

### ✅ Porta 443 configurada no Proxy
- O docker-compose.yml do proxy já tem `- '443:443'` configurado
- Porta 443 está disponível

### ❌ Labels HTTPS faltando
As labels do Traefik não incluem:
- `traefik.http.routers.https-0-*.entryPoints=https`
- `traefik.http.routers.https-0-*.tls.certresolver=letsencrypt`

### ⚠️ Mistura de Traefik e Caddy
- Há labels do Traefik E do Caddy simultaneamente
- Isso pode estar causando conflitos

## Solução Necessária

### Opção 1: Configurar Let's Encrypt no Traefik (Recomendado)

Adicionar configuração dinâmica do Traefik para Let's Encrypt:

1. Acessar: `http://31.97.22.173:8000/server/zkgg000sw4g4swcc48gc4ock/proxy/dynamic`
2. Adicionar configuração do certificado resolver:

```yaml
certificatesResolvers:
  letsencrypt:
    acme:
      email: seu-email@example.com
      storage: /data/coolify/proxy/acme.json
      httpChallenge:
        entryPoint: web
```

### Opção 2: Verificar se há configuração global de SSL

No Coolify v4, o SSL deveria ser automático. Verificar:
1. Configurações do servidor em Settings
2. Se há email configurado para Let's Encrypt
3. Se há alguma opção para habilitar SSL globalmente

### Opção 3: Forçar regeneração de labels

1. Salvar configuração da aplicação
2. Fazer redeploy completo (não apenas restart)
3. Verificar se Coolify gera labels HTTPS automaticamente

## Ações Imediatas

1. ✅ Verificar porta 443 no proxy - **CONFIRMADO: JÁ ESTÁ CONFIGURADA**
2. ⏳ Verificar configurações dinâmicas do Traefik para Let's Encrypt
3. ⏳ Verificar se há email configurado para Let's Encrypt
4. ⏳ Fazer redeploy completo das aplicações
5. ⏳ Testar HTTPS após redeploy

## Domínios Identificados

- `api.marketdash.com.br` (Backend Produção) - **Labels HTTPS FALTANDO**
- `marketdash.com.br` (Frontend Produção) - **Labels HTTPS FALTANDO**
- `api.hml.marketdash.com.br` (Backend Homologação) - **A VERIFICAR**
- Frontend Homologação - **A VERIFICAR**

## Testes Realizados (23/01/2026)

### Resultados dos Testes HTTPS

| Domínio | HTTP (80) | HTTPS (443) | Status SSL |
|---------|-----------|-------------|------------|
| `api.marketdash.com.br` | ❌ 404 | ❌ Erro SSL/TLS | **NÃO FUNCIONA** |
| `marketdash.com.br` | ✅ 200 OK | ❌ Erro SSL/TLS | **NÃO FUNCIONA** |

**Conclusão dos Testes:**
- ✅ HTTP está funcionando para o frontend
- ❌ HTTPS **NÃO está funcionando** para nenhum domínio
- ❌ Certificados SSL não estão configurados ou não são válidos
- ⚠️ Problema confirmado: Coolify não está gerando certificados Let's Encrypt

**Detalhes dos Erros:**
- Mensagem de erro SSL/TLS: "A conexão subjacente estava fechada: Não foi possível estabelecer relação de confiança para o canal seguro de SSL/TLS"
- Isso indica que não há certificado válido configurado na porta 443

## Próximos Passos

1. ✅ Verificar configurações dinâmicas do Traefik - **CONFIRMADO: Há arquivo `default_redirect_503.yaml` com `certResolver: letsencrypt`**
2. ⏳ Verificar se há configuração do Let's Encrypt no docker-compose.yml do proxy
3. ⏳ Verificar se há email configurado para Let's Encrypt
4. ⏳ Verificar se há opção "Generate labels only for Traefik" que pode estar afetando
5. ⏳ Adicionar configuração completa do Let's Encrypt nas configurações dinâmicas do Traefik
6. ⏳ Fazer redeploy de todas as aplicações após correções
7. ⏳ Aguardar alguns minutos para geração dos certificados
8. ⏳ Re-executar os testes HTTPS após correções

## Observações Importantes

- O arquivo `default_redirect_503.yaml` menciona `certResolver: letsencrypt`, indicando que o Let's Encrypt está parcialmente configurado
- Todas as aplicações têm apenas router HTTP, sem router HTTPS
- As labels estão em modo "Readonly", impedindo edição manual
- Há mistura de labels Traefik e Caddy, o que pode causar conflitos

## Verificação no Coolify (23/01/2026)

- **Proxy (Traefik):** Coolify usa Traefik automaticamente; não é "sem proxy". Ver [COOLIFY-PROXY-MARKETDASH.md](../../COOLIFY-PROXY-MARKETDASH.md).
- **Porta 443:** Na tela **Server → Proxy → Configuration**, o `docker-compose` do proxy exibia apenas `'80:80'`. Se ainda for assim, é preciso adicionar `'443:443'` e configurar Let's Encrypt.
- **Override default request handler:** Em **Proxy → Configuration**, "Override default request handler" está ativo e "Redirect to" = `https://app.coolify.io`. Pedidos não tratados por nenhum app podem ser redirecionados para o Coolify; isso pode explicar 404 ou redirecionamentos estranhos. Ver o guia acima.
