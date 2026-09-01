"""
Pool de servidores WAHA (migration 071) — o que faz "adicionar caixa" virar INSERT.

Invariantes que estes testes travam:

  * **fallback nunca quebra ambiente antigo**: sem pool cadastrado, tudo resolve
    para `settings.WAHA_URL` exatamente como antes da 071;
  * **a alocação é por afinidade**: os 3 chips da mesma afiliada caem no mesmo
    servidor, e quem não tem afinidade vai para o de MENOR ocupação;
  * **`max_sessoes` conta só instância viva** — removida não segura vaga, senão
    o pool enche de fantasma (mesmo bug que a contagem do proxy já evitou);
  * **o cap global é a soma do pool**, com o env como trava que GRITA quando
    está segurando o teto (senão "adicionei servidor e nada mudou" é mistério);
  * **`aceita_novas=False` drena sem derrubar**: para de receber, mantém as que
    já estão lá.
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
        # 071: create_all não altera tabela existente — espelho do gotcha de
        # produção que a própria migration documenta.
        _conn.execute(text(
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS servidor_id INTEGER"
        ))
    Sessao = sessionmaker(bind=ENGINE)

from app.core.encryption import encrypt_value  # noqa: E402
from app.models.waha_servidores import SERVIDOR_OK, WahaServidor  # noqa: E402
from app.models.whatsapp_grupos import (  # noqa: E402
    INSTANCIA_CONECTADA, INSTANCIA_REMOVIDA, WhatsappInstancia,
)
from app.services import waha_servidor_service as svc  # noqa: E402


@pytest.fixture
def db():
    s = Sessao()
    svc.limpar_cache()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        svc.limpar_cache()


def _servidor(db, rotulo=None, max_sessoes=10, ativo=True, aceita_novas=True,
              base_url=None, status=SERVIDOR_OK):
    s = WahaServidor(
        rotulo=rotulo or f"waha-{uuid.uuid4().hex[:8]}",
        base_url=base_url or f"http://{uuid.uuid4().hex[:8]}:3000",
        api_key_cifrada=encrypt_value("chave-secreta"),
        max_sessoes=max_sessoes,
        ativo=ativo,
        aceita_novas=aceita_novas,
        status=status,
    )
    db.add(s)
    db.flush()
    return s


def _instancia(db, user_id, servidor=None, status=INSTANCIA_CONECTADA):
    i = WhatsappInstancia(
        user_id=user_id,
        nome_instancia=f"mkdtst{uuid.uuid4().hex[:10]}",
        status=status,
        servidor_id=servidor.id if servidor else None,
    )
    db.add(i)
    db.flush()
    return i


# --------------------------------------------------------------------------
# Fallback — a garantia de que a 071 não é um degrau que quebra o que existe
# --------------------------------------------------------------------------

def test_sessao_sem_servidor_resolve_para_o_env_de_antes(db, monkeypatch):
    """Sessão anterior ao pool continua falando com WAHA_URL. Sem isso, a
    migration sozinha derrubaria todas as sessões vivas."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "WAHA_URL", "http://antigo:3000")
    monkeypatch.setattr(settings, "WAHA_API_KEY", "key-antiga")
    svc.limpar_cache()

    i = _instancia(db, user_id=9001, servidor=None)
    assert svc.endereco_da_sessao(i.nome_instancia, db) == ("http://antigo:3000", "key-antiga")


def test_nome_desconhecido_resolve_para_o_env(db, monkeypatch):
    """Sessão que não existe no banco (órfã, resumo diário) também cai no env."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "WAHA_URL", "http://antigo:3000")
    monkeypatch.setattr(settings, "WAHA_API_KEY", "key-antiga")
    svc.limpar_cache()

    assert svc.endereco_da_sessao("mkdnaoexiste", db) == ("http://antigo:3000", "key-antiga")


def test_sessao_com_servidor_resolve_para_a_caixa_dela(db):
    s = _servidor(db, base_url="http://waha-02:3000")
    i = _instancia(db, user_id=9002, servidor=s)

    base_url, chave = svc.endereco_da_sessao(i.nome_instancia, db)
    assert base_url == "http://waha-02:3000"
    assert chave == "chave-secreta", "a api_key precisa vir DECIFRADA"


def test_api_key_nunca_fica_em_claro_no_banco(db):
    s = _servidor(db)
    assert s.api_key_cifrada != "chave-secreta"
    assert svc.api_key(s) == "chave-secreta"


# --------------------------------------------------------------------------
# Alocação
# --------------------------------------------------------------------------

def test_afinidade_mantem_os_chips_da_mesma_afiliada_juntos(db):
    """Os 3 números da mesma pessoa na mesma caixa — simplifica debug e o
    roteiro de shard morto."""
    a = _servidor(db, max_sessoes=10)
    b = _servidor(db, max_sessoes=10)
    _instancia(db, user_id=7777, servidor=a)
    # `b` está mais vazio, mas a afinidade tem precedência sobre ocupação.
    _instancia(db, user_id=8888, servidor=b)
    _instancia(db, user_id=8888, servidor=b)

    assert svc.escolher(db, user_id=7777).id == a.id


def test_sem_afinidade_vai_para_o_menos_ocupado(db):
    a = _servidor(db, max_sessoes=10)
    b = _servidor(db, max_sessoes=10)
    for _ in range(3):
        _instancia(db, user_id=1234, servidor=a)

    assert svc.escolher(db, user_id=5555).id == b.id


def test_servidor_cheio_nao_recebe_e_o_pool_esgotado_devolve_none(db):
    a = _servidor(db, max_sessoes=1)
    _instancia(db, user_id=1111, servidor=a)

    # Sem outro servidor com vaga, não há para onde mandar.
    assert svc.escolher(db, user_id=2222) is None


def test_instancia_removida_nao_segura_vaga(db):
    """Contar histórico deixaria o pool cheio de vaga fantasma."""
    a = _servidor(db, max_sessoes=1)
    _instancia(db, user_id=1111, servidor=a, status=INSTANCIA_REMOVIDA)

    assert svc.escolher(db, user_id=2222).id == a.id


def test_aceita_novas_false_drena_sem_derrubar(db):
    """Marcar para drenar para de RECEBER; quem já está lá continua resolvendo."""
    a = _servidor(db, aceita_novas=False, base_url="http://drenando:3000")
    i = _instancia(db, user_id=3333, servidor=a)

    assert svc.escolher(db, user_id=4444) is None, "não deve receber sessão nova"
    assert svc.endereco_da_sessao(i.nome_instancia, db)[0] == "http://drenando:3000", \
        "a sessão que já vive lá continua sendo atendida"


def test_servidor_inativo_nao_recebe(db):
    _servidor(db, ativo=False)
    assert svc.escolher(db, user_id=4444) is None


# --------------------------------------------------------------------------
# Cap global
# --------------------------------------------------------------------------

def test_pool_vazio_usa_o_env(db, monkeypatch):
    """Ambiente que ainda não cadastrou servidor não pode ter capacidade zero —
    isso travaria a criação de números no instante do deploy."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "WHATSAPP_MAX_INSTANCIAS_GLOBAL", 60)
    # Nenhum servidor criado nesta transação; ignora o que já houver no banco
    # zerando via filtro de ativos é impossível, então o teste vale só quando
    # a tabela está vazia — garantimos limpando os ativos da transação.
    db.query(WahaServidor).update({WahaServidor.ativo: False})
    db.flush()
    assert svc.capacidade_global(db) == 60


def test_env_segura_o_teto_e_avisa(db, monkeypatch, caplog):
    """Adicionar servidor e esquecer de subir o env não pode ser silencioso."""
    from app.core.config import settings
    db.query(WahaServidor).update({WahaServidor.ativo: False})
    db.flush()
    monkeypatch.setattr(settings, "WHATSAPP_MAX_INSTANCIAS_GLOBAL", 60)
    _servidor(db, max_sessoes=100)
    _servidor(db, max_sessoes=100)

    with caplog.at_level("WARNING"):
        assert svc.capacidade_global(db) == 60, "o env é trava de segurança"
    assert any("segurando o teto" in r.getMessage() for r in caplog.records), \
        "precisa avisar que o env está segurando"


def test_capacidade_e_a_soma_do_pool_quando_o_env_permite(db, monkeypatch):
    """O ponto inteiro da 071: mais uma linha = mais capacidade."""
    from app.core.config import settings
    db.query(WahaServidor).update({WahaServidor.ativo: False})
    db.flush()
    monkeypatch.setattr(settings, "WHATSAPP_MAX_INSTANCIAS_GLOBAL", 1000)

    _servidor(db, max_sessoes=100)
    assert svc.capacidade_global(db) == 100
    _servidor(db, max_sessoes=100)
    assert svc.capacidade_global(db) == 200, "adicionar caixa tem que virar capacidade"


# --------------------------------------------------------------------------
# Cache — a otimização que só é segura porque a alocação é definitiva
# --------------------------------------------------------------------------

def test_cache_evita_uma_query_por_mensagem(db):
    s = _servidor(db, base_url="http://cacheado:3000")
    i = _instancia(db, user_id=6001, servidor=s)

    primeiro = svc.endereco_da_sessao(i.nome_instancia, db)
    # Sem sessão nenhuma: se fosse ao banco de novo, quebraria.
    segundo = svc.endereco_da_sessao(i.nome_instancia, None)
    assert primeiro == segundo == ("http://cacheado:3000", "chave-secreta")
