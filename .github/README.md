# GitHub Actions Workflows - Backend

Este diretório contém os workflows do GitHub Actions para deploy automático do backend MarketDash.

## Workflows Disponíveis

### 1. Deploy to Production (`deploy-production.yml`)

**Trigger**: Push para a branch `main`

**Processo**:
1. **Validação**: 
   - Verifica sintaxe Python
   - Valida imports principais
   - Verifica dependências
2. **Deploy**: 
   - Aciona webhook do Coolify para deploy em produção
   - Aplicação: `marketdash-backend:main`
   - Domínio: `api.marketdash.com.br`

### 2. Deploy to Homologation (`deploy-homologation.yml`)

**Trigger**: Push para a branch `develop`

**Processo**:
1. **Validação**: 
   - Verifica sintaxe Python
   - Valida imports principais
   - Verifica dependências
2. **Deploy**: 
   - Aciona webhook do Coolify para deploy em homologação
   - Aplicação: `marketdash-backend-hml`
   - Domínio: `api.hml.marketdash.com.br`

## Configuração de Secrets

Configure os seguintes secrets no GitHub:

1. Acesse: `https://github.com/joaoivson/marketdash-backend/settings/secrets/actions`
2. Adicione:
   - **Name**: `COOLIFY_WEBHOOK_URL`
   - **Value**: `http://31.97.22.173:8000/webhooks/source/github/events/manual`

## Validações Implementadas

- ✅ Verificação de sintaxe Python (`python -m py_compile`)
- ✅ Validação de imports principais (`app`, `app.main`, `app.core.config`, `app.core.security`)
- ✅ Verificação de dependências instaladas

## Próximas Melhorias

- [ ] Adicionar testes unitários com `pytest`
- [ ] Adicionar verificação de formatação com `black` ou `ruff`
- [ ] Adicionar verificação de segurança com `bandit`
- [ ] Adicionar notificações (Slack, Discord, Email)

## Troubleshooting

### Workflow não executa

- Verifique se o push foi feito para a branch correta (`main` ou `develop`)
- Verifique se os arquivos modificados não estão em `paths-ignore`

### Validação falha

- Verifique os logs do job `validate` para identificar o erro
- Execute as validações localmente antes de fazer push

### Deploy não é acionado

- Verifique se o secret `COOLIFY_WEBHOOK_URL` está configurado corretamente
- Verifique se o Coolify está acessível e funcionando
- Verifique os logs do job `deploy` para identificar erros

## Referências

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Coolify Documentation](https://coolify.io/docs)
- [WEBHOOKS_SSL_CONFIG.md](../../WEBHOOKS_SSL_CONFIG.md)
