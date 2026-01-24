# MarketDash - Visao Detalhada do Produto

## 1. Visao geral
MarketDash e uma plataforma SaaS para analise de dados de vendas digitais. O objetivo e transformar dados brutos (CSV e fontes externas) em KPIs, graficos e relatorios acionaveis, com foco em rapidez, confiabilidade e simplicidade de uso.

## 2. Publico alvo
- Produtores digitais e afiliados.
- Operadores de trafego e gestores de campanhas.
- Equipes de marketing e vendas que precisam de KPIs diarios.

## 3. Problemas que resolvemos
- Dados dispersos em planilhas e plataformas diferentes.
- Dificuldade em comparar periodos e produtos.
- Falta de visibilidade sobre lucro real e custos de anuncios.
- Tempo alto para preparar relatorios.

## 4. Proposta de valor
- Upload rapido de CSV e processamento automatico.
- KPIs e dashboards prontos em minutos.
- Controle de investimento (Ad Spends) integrado.
- Filtros avancados e relatorios exportaveis.

## 5. Principais funcionalidades
### 5.1 Autenticacao e seguranca
- Registro, login e sessao via JWT.
- Isolamento total dos dados por usuario.

### 5.2 Ingestao de dados
- Upload de CSV com validacao e normalizacao.
- Suporte futuro para Excel e JSON.
- Atualizacao via API externa (pipeline de refresh).

### 5.3 Dados e analise
- KPIs: receita, custo, comissao, lucro, quantidade, linhas.
- Agregacoes por periodo e por produto.
- Filtros por data, produto e valores.

### 5.4 Investimentos (Ad Spends)
- CRUD de gastos com anuncios.
- Importacao em lote via template.
- Aplicacao de gastos no dataset mais recente.

### 5.5 Relatorios e exportacao
- Relatorios detalhados e exportacao CSV/PDF.
- Nome de arquivo com periodo e dataset.

## 6. Jornada do usuario
1. Usuario cria conta e faz login.
2. Faz upload do CSV de vendas.
3. Visualiza KPIs e graficos no dashboard.
4. Adiciona gastos de anuncios.
5. Filtra e exporta relatorios.

## 7. Fontes de dados e integracoes
- CSV (principal).
- API externa (planejado): Hotmart, Eduzz, Kiwify, Monetizze.
- Webhook Cakto para assinaturas.

## 8. Requisitos nao funcionais
- Desempenho: agregacoes otimizadas por indices.
- Seguranca: JWT, validacao, hash de senha.
- Confiabilidade: validacao robusta e logs estruturados.
- Escalabilidade: API stateless + banco PostgreSQL.

## 9. Metricas de sucesso
- Tempo medio para gerar dashboard apos upload.
- Taxa de uso de exportacao.
- Retencao mensal de usuarios ativos.
- Reducao de tempo de preparo de relatorios.

## 10. Roadmap resumido
Curto prazo:
- Integracao completa frontend-backend.
- Exportacao CSV/PDF.
- Filtros avancados.

Medio prazo:
- Multiplos datasets e comparacao.
- Integracao com APIs externas.
- Migracao para Supabase Auth.

Longo prazo:
- Modulos avancados de IA.
- Sistema de pagamentos e assinaturas.
