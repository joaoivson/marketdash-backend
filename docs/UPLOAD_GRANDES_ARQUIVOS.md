# Suporte a upload de arquivos grandes (500k+ linhas)

Para CSVs muito grandes (ex.: 500k linhas), o servidor pode travar se o arquivo inteiro for carregado na memória da API e enviado ao Redis. Este documento descreve como habilitar o suporte a arquivos grandes.

## 1. Gravar upload em disco (recomendado para 500k+)

Defina a variável de ambiente **`UPLOAD_TEMP_DIR`** no ambiente onde rodam a **API** e o **worker Celery**. Quando definida:

- A API **não** carrega o arquivo inteiro na memória: faz stream em chunks de 1 MB para um arquivo temporário.
- Apenas o **caminho do arquivo** (string) é enviado ao Redis para a task Celery.
- O worker lê o CSV do disco, processa e remove o arquivo ao final.

**Requisito:** API e worker precisam enxergar o **mesmo diretório** (ex.: volume compartilhado no Docker/Coolify).

### Exemplos

**Docker Compose (local):** no `docker-compose.yml`, para os serviços `app` e `worker`:

```yaml
environment:
  UPLOAD_TEMP_DIR: /app/uploads
volumes:
  - .:/app
```

Crie o diretório no host se quiser persistir: `mkdir -p uploads` (ou use `/app/uploads` dentro do container).

**Coolify / produção:** use um volume compartilhado entre o container da API e o do worker e defina o mesmo `UPLOAD_TEMP_DIR` (ex.: `/app/uploads`) em ambos. Monte esse volume no mesmo path nos dois serviços.

## 2. Limite de tamanho no proxy (nginx / Coolify)

O proxy à frente da API costuma limitar o tamanho do body (ex.: 1 MB). Para permitir arquivos de dezenas de MB (ex.: 100–200 MB):

- **Coolify:** verifique as configurações do proxy reverso (Traefik/nginx) e aumente o limite de body (ex.: `client_max_body_size` ou equivalente para **100m** ou **200m**).
- **Nginx:** `client_max_body_size 200m;`
- **Traefik:** middleware ou anotações para aumentar o limite de upload.

Sem isso, o proxy pode rejeitar o request com 413 (Request Entity Too Large) antes de chegar na API.

## 3. Time limit das tasks Celery

As tasks de processamento de CSV já estão configuradas com:

- `soft_time_limit=3600` (1 hora)
- `time_limit=3700`

Assim, arquivos muito grandes (ex.: 500k linhas) têm tempo suficiente para processar sem que o worker seja morto por timeout.

## 4. Resumo

| Objetivo | Ação |
|----------|------|
| Evitar travar a API e o Redis com arquivos grandes | Definir `UPLOAD_TEMP_DIR` (ex.: `/app/uploads`) na API e no worker e garantir volume compartilhado. |
| Permitir envio de arquivos de dezenas de MB | Aumentar limite de body no proxy (ex.: 100m ou 200m). |
| Evitar timeout do worker em processamento longo | Já configurado (1 h) nas tasks de CSV. |

Com `UPLOAD_TEMP_DIR` definido e o proxy permitindo o tamanho desejado, o fluxo suporta arquivos como o `WebsiteClickReport_500k.csv` sem carregar o arquivo inteiro na memória da API nem no Redis.
