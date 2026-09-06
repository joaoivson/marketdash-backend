"""
`template_id` vem do cliente e o id é sequencial.

Sem checagem de dono, apontar um passo para o template de OUTRA usuária fazia o
texto dela sair nos grupos de quem copiou o id — o motor resolvia a variação
filtrando só por `template_id`.

Duas barreiras, e este teste cobre as duas: recusar ao SALVAR o passo, e
filtrar por dono na hora do DISPARO (defesa em profundidade: um passo antigo,
salvo antes da correção, não pode vazar).
"""
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PG_URL = "postgresql://dashads_user:dashads_password@localhost:5434/dashads_db"
try:
    _p = create_engine(PG_URL, pool_pre_ping=True)
    with _p.connect() as _c:
        _c.execute(text("SELECT 1"))
    PG_OK = True
except Exception:
    PG_OK = False

pytestmark = pytest.mark.skipif(not PG_OK, reason="Postgres local (5434) indisponível")

if PG_OK:
    from app.db.base import Base
    import app.models  # noqa: F401
    ENGINE = create_engine(PG_URL)
    Base.metadata.create_all(ENGINE)
    with ENGINE.begin() as _conn:
        # 082: `create_all` cria tabela nova (passo_blocos) mas não altera
        # tabela existente — mesmo gotcha de produção.
        for _alter in (
            "ALTER TABLE roteiro_passos ADD COLUMN IF NOT EXISTS offset_segundos INTEGER",
            "ALTER TABLE roteiro_passos ADD COLUMN IF NOT EXISTS offset_unidade VARCHAR(10)",
            "ALTER TABLE roteiro_passos ADD COLUMN IF NOT EXISTS "
            "acao_descontinuada BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE roteiro_mensagens ADD COLUMN IF NOT EXISTS "
            "blocos_enviados INTEGER NOT NULL DEFAULT 0",
        ):
            _conn.execute(text(_alter))
    Sessao = sessionmaker(bind=ENGINE)

from types import SimpleNamespace  # noqa: E402

from app.models.roteiro import (  # noqa: E402
    Roteiro, RoteiroPasso, TemplateMensagem, TemplateVariacao,
)
from app.models.user import User  # noqa: E402
from app.services.roteiro_service import RoteiroInvalido, RoteiroService  # noqa: E402


@pytest.fixture
def db():
    s = Sessao()
    yield s
    s.rollback()
    s.close()


def _usuaria_com_template(db, corpo):
    suf = uuid.uuid4().hex[:8]
    u = User(email=f"tpl-{suf}@x.com", hashed_password="x")
    db.add(u); db.flush()
    t = TemplateMensagem(user_id=u.id, nome=f"t-{suf}", tipo="livre")
    db.add(t); db.flush()
    db.add(TemplateVariacao(template_id=t.id, corpo=corpo, peso=1, ativa=True))
    db.commit()
    return u, t


def _passo_in(template_id):
    from datetime import date as _date, time as _time
    bloco = SimpleNamespace(tipo="texto", conteudo="oi", legenda=None,
                            template_id=template_id)
    return SimpleNamespace(
        id=None, ordem=1, tipo_tempo="ancora", hora_fixa=_time(9, 0),
        data_fixa=_date(2099, 1, 1), offset_valor=None, offset_unidade=None,
        tipo_conteudo="mensagem", blocos=[bloco], texto=None, midia_url=None,
        oferta_url=None, template_id=template_id, acao=None, acao_parametro=None,
        grupos_alvo="todos", grupos_alvo_ids=None, marcar_todos="nunca",
    )


def test_salvar_passo_com_template_alheio_e_recusado(db):
    minha, _t = _usuaria_com_template(db, "meu texto")
    _outra, alheio = _usuaria_com_template(db, "SEGREDO DA CONCORRENTE")

    roteiro = Roteiro(user_id=minha.id, nome="r", status="rascunho", origem="editor")
    db.add(roteiro); db.flush(); db.commit()

    with pytest.raises(RoteiroInvalido):
        RoteiroService(db).definir_passos(roteiro, [_passo_in(alheio.id)])


def test_disparo_nao_usa_variacao_de_template_alheio(db):
    """Segunda barreira: passo gravado ANTES da correção não pode vazar."""
    from app.services.roteiro_envio_service import RoteiroEnvioService

    minha, _t = _usuaria_com_template(db, "meu texto")
    _outra, alheio = _usuaria_com_template(db, "SEGREDO DA CONCORRENTE")

    roteiro = Roteiro(user_id=minha.id, nome="r", status="pronto", origem="editor")
    db.add(roteiro); db.flush()
    # Grava direto no banco, como estaria um passo salvo antes da checagem.
    passo = RoteiroPasso(roteiro_id=roteiro.id, ordem=1, tipo_conteudo="mensagem",
                         template_id=alheio.id, grupos_alvo="todos")
    db.add(passo); db.flush(); db.commit()

    svc = RoteiroEnvioService(db, dormir=lambda s: None,
                              cliente_factory=lambda nome: None)
    svc._passo_cache = {passo.id: passo}
    execucao = SimpleNamespace(user_id=minha.id)
    mensagem = SimpleNamespace(passo_id=passo.id, short_link=None, grupo_id=None)

    saida = svc._preparar_saida(execucao, mensagem, passo)
    texto = "\n".join(b["texto"] or "" for b in saida)
    assert "SEGREDO DA CONCORRENTE" not in texto
