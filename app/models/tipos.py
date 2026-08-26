"""Tipos de coluna compartilhados.

`JSON_PORTATIL` é JSONB no Postgres (o banco real) e JSON no SQLite, que é o
que os testes unitários usam. Sem a variante, qualquer teste que crie uma
tabela com JSONB quebra na compilação do DDL — e o teste que quebra costuma
ser de OUTRA feature, o que torna a causa difícil de achar.
"""
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

JSON_PORTATIL = JSONB().with_variant(JSON(), "sqlite")
