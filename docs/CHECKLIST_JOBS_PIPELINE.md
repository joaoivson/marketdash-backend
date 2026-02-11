# Checklist: Pipeline de Jobs (CSV Chunking + Object Storage)

## Visão geral

Com `USE_JOBS_PIPELINE=true`, o backend expõe as rotas `/api/v1/jobs` para upload de CSV via URL pré-assinada (presigned) e processamento assíncrono (Object Storage + Celery). O worker baixa o arquivo **uma vez** e processa em batches em memória (`process_job_from_storage`), sem gravar chunks no S3, reduzindo I/O e latência. O fluxo antigo (`POST /datasets/upload`, `POST /clicks/upload`) continua disponível; o rollback é imediato desativando a flag.

## Variáveis de ambiente

| Variável | Obrigatória (se pipeline ativa) | Descrição |
|----------|----------------------------------|-----------|
| `USE_JOBS_PIPELINE` | - | `true` para registrar rotas `/jobs` e usar a nova pipeline. Default: `false`. |
| `S3_BUCKET` | Sim | Nome do bucket (ex.: `uploads`). |
| `S3_ENDPOINT` | Sim | URL do endpoint S3 (ex.: Supabase Storage ou MinIO). |
| `S3_ACCESS_KEY` | Sim | Access key. |
| `S3_SECRET_KEY` | Sim | Secret key. |
| `S3_REGION` | Não | Região (default: `us-east-1`). |

## Ativar a pipeline

1. Aplicar a migration de tabelas `jobs` e `job_chunks`:
   ```bash
   python apply_migration_supabase.py migrations/007_jobs_and_job_chunks.sql
   ```
   (Ou executar o SQL no Supabase SQL Editor.)

2. Configurar Object Storage (Supabase Storage com compatibilidade S3 ou MinIO) e definir as variáveis `S3_*` acima.

3. Definir `USE_JOBS_PIPELINE=true` no ambiente da API e do worker (Coolify, docker-compose, etc.).

4. Reiniciar a aplicação e o worker Celery.

## Rollback

- Definir `USE_JOBS_PIPELINE=false` e fazer redeploy.
- As rotas `/api/v1/jobs` deixam de ser registradas (404).
- Os clientes que usam `POST /datasets/upload` e `POST /clicks/upload` continuam funcionando sem alteração.

## Limites e opcionais

- **Processamento**: uma task `process_job_from_storage` baixa o CSV do S3 e processa em batches em memória (Polars/pandas). Progresso: `chunks_done` e `total_chunks` refletem batches processados.
- **Legado**: as tasks `split_and_enqueue_chunks` e `process_chunk` (gravar chunks no S3 e N tasks) permanecem no código para uso opcional (ex.: arquivos enormes com threshold futuro).
- **Celery**: `process_job_from_storage` usa `soft_time_limit=3600`, `time_limit=3700`.

## Observabilidade

- Logs estruturados em `process_job_from_storage` (job_id, batches, duration_seconds).
- Progresso do job: `GET /api/v1/jobs/{job_id}` retorna `total_chunks`, `chunks_done` (batches) e `errors`.

## Desenvolvimento local com MinIO

No `docker-compose.yml` pode-se adicionar um serviço MinIO e expor as variáveis S3 para app e worker. Exemplo:

```yaml
  minio:
    image: minio/minio:latest
    command: server /data
    ports:
      - "9000:9000"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - minio_data:/data

# No service app e worker, adicionar quando USE_JOBS_PIPELINE=true:
#   S3_BUCKET: uploads
#   S3_ENDPOINT: http://minio:9000
#   S3_ACCESS_KEY: minioadmin
#   S3_SECRET_KEY: minioadmin
#   USE_JOBS_PIPELINE: "true"
```

Criar o bucket `uploads` no MinIO (Console em http://localhost:9000) antes de testar.
