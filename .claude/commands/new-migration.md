---
name: new-migration
description: Criar e aplicar uma migration SQL no MarketDash (sem Alembic), de forma idempotente e aditiva.
---

Crie uma migration SQL seguindo o fluxo do projeto.

> **Este projeto não usa Alembic** e **não tem tabela de controle de versão de
> schema**. Migration é `.sql` solto em `migrations/`, aplicada à mão. Isso
> torna idempotência obrigatória, não opcional.

## Input esperado

O usuário descreve a alteração (ex.: "adicionar coluna `placement` em
`ad_spends`").

## Passos

### 1. Ver o que já existe

Liste `migrations/` e identifique o padrão de nome em uso. Confira se a
alteração já não foi feita — sem tabela de controle, o histórico do
diretório é a única pista.

### 2. Escrever a migration

Arquivo novo em `migrations/`, nome descritivo em snake_case.

**Aditiva e idempotente, sempre:**

```sql
ALTER TABLE ad_spends ADD COLUMN IF NOT EXISTS placement text;
CREATE INDEX IF NOT EXISTS idx_ad_spends_user_date ON ad_spends (user_id, date);
```

**Proibido sem decisão registrada:** `DROP TABLE`, `DROP COLUMN`,
`TRUNCATE`. Dado de comissão é financeiro; a perda é irreversível e não
detectável.

Coluna que vai entrar em `WHERE` nasce com índice — e as consultas quentes
deste projeto filtram por `(user_id, date)`.

### 3. Espelhar no model

Se a coluna é lida pela aplicação, adicione em `app/models/`. Model e schema
divergentes dão erro só em runtime, no ambiente real.

### 4. Aplicar em homologação primeiro

```bash
python3 scripts/apply_migrations.py
```

Confirme o objeto criado (coluna, índice, constraint) antes de seguir.

### 5. Produção

Só depois de hml validado. Migration que faz **`UPDATE` em dado real**
merece atenção redobrada: reaplicar mexe em dado bom. Verifique se já rodou
**antes** de rodar.

### 6. Registrar

- `.claude/memoria/DIARIO.md` — o que mudou e **por quê**
- `.claude/memoria/DECISOES.md` — se a mudança de schema carrega decisão
  (ex.: por que a coluna e não um join)
- `CHANGELOG.md` da raiz se muda comportamento visível
