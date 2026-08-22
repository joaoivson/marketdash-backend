# Resumo diário no WhatsApp — design

**Status:** aprovado em 07/08/2026.

## O que é

Toda manhã às 9h de Brasília, a afiliada Pro ou Max que optou por receber ganha
uma mensagem no WhatsApp com os números do dia anterior e, quando houver, um
alerta de campanha abaixo do ponto de equilíbrio. A mensagem sai do número do
MarketDash — a afiliada não conecta o WhatsApp dela em lugar nenhum.

## Decisões

| Questão | Decisão |
|---|---|
| Conteúdo | Resumo fixo + linha de alerta quando alguma campanha cai abaixo do breakeven |
| Quem recebe | Pro e Max, com opt-in explícito nas Configurações |
| Horário | Fixo, 9h BRT (12h UTC) |
| Número | Um número dedicado do MarketDash, conectado uma vez pelo admin |
| Provedor | Evolution API auto-hospedada no Coolify |

## Por que essas escolhas

**Número do MarketDash, não o da afiliada.** Conectar o WhatsApp pessoal dela
exigiria QR code por usuária, sessão por usuária e suporte para cada
desconexão. Com um número só, o custo operacional é fixo — e o risco também
fica concentrado num lugar que a gente controla.

**Opt-in com confirmação por código.** A afiliada digita o número no site,
recebe um código no WhatsApp e o digita de volta. Sem essa volta, qualquer
erro de digitação vira mensagem para um desconhecido, e mensagem não
solicitada é a via mais rápida para o número ser denunciado e banido.

**Evolution, não API oficial.** A oficial (Cloud API da Meta) cobra por
conversa e exige template aprovado para mensagem iniciada por nós — que é
exatamente o nosso caso. Para o volume atual, self-hosted sai muito mais
barato. A troca é assumir o risco de banimento, tratado abaixo.

## Anti-banimento

O que derruba número é volume alto, conteúdo repetido e gente denunciando.
Cinco medidas, em ordem de importância:

1. **Só quem confirmou recebe.** Opt-in duplo, sempre.
2. **Saída fácil.** Toda mensagem termina com "Responda SAIR para parar". Um
   webhook escuta a resposta e desliga na hora, sem passar pelo site.
3. **Ritmo humano.** Intervalo aleatório entre envios, nunca em rajada.
4. **Teto diário.** Acima dele o lote para e o resto fica para o dia seguinte.
5. **Disjuntor.** Falhas consecutivas param o lote inteiro — número
   desconectado ou banido não deve gastar mais tentativas.

## Estrutura

```
Banco (migration 045)
  whatsapp_optins    uma linha por usuária: número, status, código
  whatsapp_envios    log de cada envio; índice único garante 1 resumo/dia

Backend
  services/evolution_client.py     única fronteira HTTP com a Evolution
  services/whatsapp_optin_service.py   registrar, confirmar, desligar
  services/whatsapp_resumo_service.py  monta o texto a partir do KpiService
  services/whatsapp_envio_service.py   percorre o lote com as travas acima
  api/v1/routes/whatsapp.py        opt-in da afiliada + webhook da Evolution
  api/v1/routes/internal.py        + /cron/whatsapp-resumo
  api/v1/routes/admin_panel.py     + estado da instância e QR code

Frontend
  Configurações → card "Resumo diário no WhatsApp"
  Admin → conectar o número (QR code)
```

Os números vêm do `KpiService`, o mesmo que alimenta a tela.
Nenhuma conta nova é feita aqui: se o resumo do WhatsApp e o dashboard
divergirem, é bug de um só lugar.

## Fora deste escopo

- Automação de Instagram
- Conversa de ida e volta (o webhook só entende SAIR)
- Resumo semanal ou mensal
- Escolha de horário pela afiliada
