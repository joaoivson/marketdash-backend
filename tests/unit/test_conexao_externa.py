"""
Link de conexão externa (item 18 da spec).

A tela é PÚBLICA por necessidade — quem vai escanear o QR não tem login aqui.
Isso faz do token a única barreira, e é o que estes testes cobrem:

  * o token em claro NUNCA é gravado (só o hash);
  * expira, e o prazo é curto;
  * morre ao CONECTAR, não no fim do prazo — link ainda válido depois de pareado
    é um convite para outra pessoa trocar o número no lugar;
  * criar um novo revoga os anteriores da mesma sessão;
  * a página não revela nada além do QR.
"""
import uuid
from datetime import datetime, timedelta, timezone

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
    Sessao = sessionmaker(bind=ENGINE)

from app.models.conexao_convite import ConexaoConvite  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.whatsapp_grupos import WhatsappInstancia  # noqa: E402
from app.services.conexao_convite_service import (  # noqa: E402
    ConexaoConviteService, ConviteInvalido,
)


@pytest.fixture
def db():
    s = Sessao()
    yield s
    s.rollback()
    s.close()


def _cenario(db):
    suf = uuid.uuid4().hex[:8]
    u = User(email=f"cx-{suf}@x.com", hashed_password="x")
    db.add(u); db.flush()
    inst = WhatsappInstancia(user_id=u.id, nome_instancia=f"mkdcx{suf}",
                             status="criada")
    db.add(inst); db.flush()
    db.commit()
    return u, inst


def test_token_em_claro_nunca_e_gravado(db):
    """O segredo vive no link que ela mandou. Quem lê o banco não abre nada."""
    user, inst = _cenario(db)
    convite, token = ConexaoConviteService(db).criar(user.id, inst.id)
    db.commit()

    colunas = {c.name for c in ConexaoConvite.__table__.columns}
    assert "token" not in colunas
    assert token not in (convite.token_hash or "")
    assert len(token) >= 32


def test_token_certo_resolve_e_token_errado_nao(db):
    user, inst = _cenario(db)
    svc = ConexaoConviteService(db)
    _convite, token = svc.criar(user.id, inst.id)
    db.commit()

    assert svc.resolver(token).instancia_id == inst.id
    with pytest.raises(ConviteInvalido):
        svc.resolver(token + "x")


def test_convite_expirado_nao_resolve(db):
    user, inst = _cenario(db)
    svc = ConexaoConviteService(db)
    convite, token = svc.criar(user.id, inst.id)
    convite.expira_em = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(convite); db.commit()

    with pytest.raises(ConviteInvalido):
        svc.resolver(token)


def test_link_morre_ao_conectar_nao_no_fim_do_prazo(db):
    """
    O invariante que mais importa: link de pareamento ainda válido depois de
    pareado é um convite para outra pessoa conectar OUTRO número no lugar.
    """
    user, inst = _cenario(db)
    svc = ConexaoConviteService(db)
    convite, token = svc.criar(user.id, inst.id)
    db.commit()

    assert svc.resolver(token)          # antes de conectar, vale
    svc.marcar_usado(convite)
    with pytest.raises(ConviteInvalido):
        svc.resolver(token)             # depois de conectar, não vale mais


def test_criar_um_novo_revoga_os_anteriores_da_mesma_sessao(db):
    """Dois links vivos para o mesmo número significam que o primeiro — que ela
    talvez tenha mandado no grupo errado — continua funcionando."""
    user, inst = _cenario(db)
    svc = ConexaoConviteService(db)
    _c1, token_velho = svc.criar(user.id, inst.id)
    db.commit()
    _c2, token_novo = svc.criar(user.id, inst.id)
    db.commit()

    with pytest.raises(ConviteInvalido):
        svc.resolver(token_velho)
    assert svc.resolver(token_novo)
    assert len(svc.ativos_da_instancia(user.id, inst.id)) == 1


def test_revogar_mata_o_link(db):
    user, inst = _cenario(db)
    svc = ConexaoConviteService(db)
    convite, token = svc.criar(user.id, inst.id)
    db.commit()

    assert svc.revogar(user.id, convite.id) is True
    db.commit()
    with pytest.raises(ConviteInvalido):
        svc.resolver(token)


def test_revogar_convite_de_outra_afiliada_nao_faz_nada(db):
    user, inst = _cenario(db)
    outra, _i = _cenario(db)
    svc = ConexaoConviteService(db)
    convite, token = svc.criar(user.id, inst.id)
    db.commit()

    assert svc.revogar(outra.id, convite.id) is False
    db.commit()
    assert svc.resolver(token)          # continua valendo para a dona


def test_prazo_e_curto(db):
    from app.core.config import settings

    user, inst = _cenario(db)
    convite, _t = ConexaoConviteService(db).criar(user.id, inst.id)
    db.commit()
    minutos = (convite.expira_em - datetime.now(timezone.utc)).total_seconds() / 60
    assert 0 < minutos <= settings.CONEXAO_CONVITE_MINUTOS + 1
    assert settings.CONEXAO_CONVITE_MINUTOS <= 60, "prazo longo demais para link público"


def test_pagina_publica_nao_revela_nada_da_conta(db):
    """
    O token é a única barreira. A página não pode contar nome, número nem a qual
    conta pertence — quem tem o link tem só o QR.
    """
    from app.main import _pagina_de_qr

    user, inst = _cenario(db)
    _convite, token = ConexaoConviteService(db).criar(user.id, inst.id)
    db.commit()

    html = _pagina_de_qr(token)
    assert inst.nome_instancia not in html
    assert user.email not in html
    assert str(user.id) not in html.replace(token, "")
    assert "noindex" in html          # link público não entra em buscador


def test_pagina_de_link_invalido_nao_diz_qual_e_o_problema():
    """Inexistente, expirado, usado e revogado mostram o MESMO texto: dizer qual
    deles é ajudar quem está tentando adivinhar."""
    from app.main import _pagina_simples

    html = _pagina_simples("Link expirado", "Peça um link novo para quem te enviou este.")
    for palavra in ("revogado", "já usado", "não existe", "inválido"):
        assert palavra not in html.lower()


def test_pagina_para_de_consultar_ao_terminar():
    """Sem `clearInterval`, a aba fica batendo no backend para sempre depois de
    conectar — de graça, e num endpoint público."""
    from app.main import _pagina_de_qr

    html = _pagina_de_qr("tok")
    assert "clearInterval" in html


def test_proxy_do_link_publico_existe_nos_dois_ambientes():
    """
    A página é servida pelo BACKEND, mas o link aponta para o host do FRONTEND —
    e ela consulta o backend em laço para atualizar o QR. Sem proxy das DUAS
    rotas, o link cai no SPA e o QR nunca aparece. Foi exatamente o que
    aconteceu com o `/g` na F6.
    """
    import pathlib

    front = pathlib.Path(__file__).resolve().parents[3] / "marketdash-frontend"
    nginx = (front / "nginx.conf").read_text()
    assert "/conectar/" in nginx, "nginx não encaminha a página de conexão"
    assert "/api/conectar/" in nginx, "nginx não encaminha o polling do QR"

    vite = (front / "vite.config.ts").read_text()
    assert "'/conectar'" in vite or '"/conectar"' in vite, \
        "vite não encaminha a página de conexão no dev"


def test_endpoint_publico_usa_a_chave_qrcode_do_contrato(db, monkeypatch):
    """
    `WhatsappInstanciaService.qr()` devolve a imagem em **`qrcode`** — é o mesmo
    contrato de `InstanciaQrOut`. A primeira versão desta rota lia `qr` e o
    código NUNCA aparecia: a página ficava em "Preparando o código…" para
    sempre, sem erro nenhum na tela ou no log.
    """
    from app import main
    from app.services.whatsapp_instancia_service import WhatsappInstanciaService

    user, inst = _cenario(db)
    _convite, token = ConexaoConviteService(db).criar(user.id, inst.id)
    db.commit()

    monkeypatch.setattr(
        WhatsappInstanciaService, "qr",
        lambda self, instancia: {"estado": "aguardando", "qrcode": "data:image/png;base64,AAA"},
    )
    resposta = main.qr_da_conexao_externa(token, db=db)
    assert resposta == {"estado": "aguardando", "qr": "data:image/png;base64,AAA"}


def test_conectar_consome_o_link_na_hora(db, monkeypatch):
    from app import main
    from app.services.whatsapp_instancia_service import WhatsappInstanciaService

    user, inst = _cenario(db)
    svc = ConexaoConviteService(db)
    _convite, token = svc.criar(user.id, inst.id)
    db.commit()

    monkeypatch.setattr(WhatsappInstanciaService, "qr",
                        lambda self, instancia: {"estado": "conectada", "qrcode": None})
    assert main.qr_da_conexao_externa(token, db=db) == {"estado": "conectado"}
    with pytest.raises(ConviteInvalido):
        svc.resolver(token)


def test_erro_do_waha_nao_vaza_diagnostico_para_a_pagina_publica(db, monkeypatch):
    from app import main
    from app.services.whatsapp_instancia_service import WhatsappInstanciaService

    user, inst = _cenario(db)
    _convite, token = ConexaoConviteService(db).criar(user.id, inst.id)
    db.commit()

    monkeypatch.setattr(
        WhatsappInstanciaService, "qr",
        lambda self, instancia: {"estado": "erro: sem_config", "qrcode": None},
    )
    resposta = main.qr_da_conexao_externa(token, db=db)
    assert resposta == {"estado": "indisponivel"}
    assert "sem_config" not in str(resposta)
