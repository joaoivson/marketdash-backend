"""
Sync de grupos — as decisões que não podem regredir:

1. o sync só DESCOBRE: sub_id + custom_link nascem na ATIVAÇÃO do grupo
   (spec §6.2 — whatsapp_grupo_service.definir_ativado), nunca aqui;
2. grupo que some vira ativo=False, NUNCA é deletado — e o sync jamais
   escreve em `ativado` (toggle da usuária);
3. sub_id de grupo ativado é PERPÉTUO: sumir/reaparecer não regenera.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.custom_link import CustomLink
from app.models.sync_run import SyncRun
from app.models.whatsapp_grupos import (
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.services.whatsapp_grupo_sync_service import (
    WhatsappGrupoSyncService, base36, sub_id_do_grupo,
)

USUARIA = 1


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for t in (CustomLink.__table__, WhatsappInstancia.__table__,
              WhatsappGrupo.__table__, WhatsappGrupoInstancia.__table__):
        t.create(engine)
    sessao = sessionmaker(bind=engine)()
    # sync_runs tem JSONB (que o SQLite não compila) — DDL manual equivalente.
    sessao.execute(text("""
        CREATE TABLE sync_runs (
            id INTEGER PRIMARY KEY,
            source VARCHAR, trigger VARCHAR, user_id INTEGER,
            days_back INTEGER, empty_attempt BOOLEAN,
            status VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
            records_fetched INTEGER, records_upserted INTEGER,
            is_suspected_partial BOOLEAN, error_message TEXT, details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    sessao.commit()
    yield sessao
    sessao.close()


class _FakeWaha:
    def __init__(self, paginas):
        self.paginas = paginas   # lista de listas (uma por offset)

    def sessao_info(self):
        return {"me": {"id": "5511999998888@c.us"}}

    def listar_grupos(self, limite=100, offset=0):
        indice = offset // 100
        return self.paginas[indice] if indice < len(self.paginas) else []

    def convite_do_grupo(self, jid):
        return f"https://chat.whatsapp.com/conv-{jid[:6]}"


def _instancia(db, numero="5511999998888"):
    inst = WhatsappInstancia(
        user_id=USUARIA, nome_instancia="mkdtestu1xabcd",
        numero=numero, status="conectada",
    )
    db.add(inst)
    db.commit()
    return inst


def _grupo(jid="120363000000000001@g.us", nome="Achadinhos", admin=True, announce=False):
    participantes = [
        {"id": "5511999998888@c.us", "role": "admin" if admin else "participant"},
        {"id": "5521888887777@c.us", "role": "participant"},
    ]
    return {"id": jid, "subject": nome, "participants": participantes,
            "announce": announce}


def _ativar(db, grupo):
    """Liga o toggle da usuária — é a ativação que cria sub_id/custom_link."""
    from app.services.whatsapp_grupo_service import WhatsappGrupoService

    return WhatsappGrupoService(db, plan_limit_grupos=-1).definir_ativado(
        grupo, True)


def test_base36_estavel():
    assert base36(0) == "0"
    assert base36(35) == "z"
    assert base36(36) == "10"
    assert sub_id_do_grupo(123) == "wg3f"


def test_grupo_novo_nasce_sem_atribuicao_e_desativado(db):
    """O sync só descobre (spec §6.2): sub_id/custom_link ficam para a
    ativação, e `ativado` nasce False — quem liga é a usuária."""
    inst = _instancia(db)
    svc = WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]]))
    r = svc.sincronizar(inst)

    assert r == {"vistos": 1, "novos": 1, "atualizados": 0, "desativados": 0,
                 "membros": 0, "telefones": 0,
                 "ignorados": 0, "convites": 1}
    grupo = db.query(WhatsappGrupo).one()
    assert grupo.sub_id is None
    assert grupo.custom_link_id is None
    assert grupo.ativado is False
    assert db.query(CustomLink).count() == 0
    assert grupo.participantes == 2
    assert grupo.sou_admin is True
    assert grupo.permite_envio is True
    assert grupo.link_convite and grupo.link_convite.startswith("https://chat.whatsapp.com/")


def test_membro_comum_de_grupo_so_admin_nao_permite_envio(db):
    inst = _instancia(db)
    dados = _grupo(admin=False, announce=True)   # "só admins podem enviar" ligado
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[dados]])).sincronizar(inst)

    grupo = db.query(WhatsappGrupo).one()
    assert grupo.sou_admin is False
    assert grupo.permite_envio is False


def test_grupo_que_some_desativa_sem_deletar(db):
    inst = _instancia(db)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)
    _ativar(db, db.query(WhatsappGrupo).one())    # a usuária ligou o grupo
    # Segundo sync: o grupo sumiu do retorno.
    r = WhatsappGrupoSyncService(db, cliente=_FakeWaha([[]])).sincronizar(inst)

    assert r["desativados"] == 1
    grupo = db.query(WhatsappGrupo).one()   # a linha continua existindo
    assert grupo.ativo is False
    assert grupo.sub_id is not None         # atribuição histórica preservada
    assert grupo.ativado is True            # toggle é da usuária — sync não mexe


def test_grupo_que_reaparece_revive_com_o_mesmo_sub_id(db):
    inst = _instancia(db)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)
    sub_id_original = _ativar(db, db.query(WhatsappGrupo).one()).sub_id
    assert sub_id_original                     # a ativação criou a atribuição
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[]])).sincronizar(inst)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)

    grupo = db.query(WhatsappGrupo).one()
    assert grupo.ativo is True
    assert grupo.sub_id == sub_id_original     # NUNCA regenerar
    assert db.query(CustomLink).count() == 1   # não cria segundo link


def test_mesmo_grupo_em_duas_instancias_e_n_para_n(db):
    inst_a = _instancia(db)
    inst_b = WhatsappInstancia(user_id=USUARIA, nome_instancia="mkdtestu1xefgh",
                               numero="5511888887777", status="conectada")
    db.add(inst_b)
    db.commit()

    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst_a)
    # O segundo número é membro comum do MESMO grupo.
    dados_b = _grupo(admin=False)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[dados_b]])).sincronizar(inst_b)

    assert db.query(WhatsappGrupo).count() == 1   # UNIQUE(user_id, jid)
    vinculos = db.query(WhatsappGrupoInstancia).all()
    assert {(v.instancia_id, v.sou_admin) for v in vinculos} == {
        (inst_a.id, True), (inst_b.id, False),
    }


def test_sync_grava_sync_run_para_o_painel_admin(db):
    inst = _instancia(db)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)

    run = db.query(SyncRun).one()
    assert run.source == "whatsapp_grupos"
    assert run.status == "success"
    assert run.records_fetched == 1


def test_payload_sem_flags_de_anuncio_nao_assume_grupo_aberto(db):
    inst = _instancia(db)
    dados = {"id": "120363000000000002@g.us", "subject": "Sem flags",
             "participants": [{"id": "5511999998888@c.us", "role": "participant"}]}
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[dados]])).sincronizar(inst)

    grupo = db.query(WhatsappGrupo).one()
    # Membro comum + payload omisso: marcar "aberto" no chute mandaria o lote
    # contra grupos que devolvem 403.
    assert grupo.permite_envio is False


def test_link_de_grupo_fica_fora_de_meus_links_pela_fk_nao_pela_tag(db):
    from app.repositories.custom_link_repository import CustomLinkRepository

    inst = _instancia(db)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)
    _ativar(db, db.query(WhatsappGrupo).one())    # o link interno nasce aqui
    # A usuária cria um link PESSOAL com a tag "whatsapp" — precisa continuar
    # visível e contando no limite (tag é texto livre, não marcador interno).
    pessoal = CustomLink(user_id=USUARIA, name="meu link", original_url="https://x",
                         slug="pessoal1", tag="whatsapp", is_active=True)
    db.add(pessoal)
    db.commit()

    ids_grupo = CustomLinkRepository(db).ids_de_links_de_grupo(USUARIA)
    grupo = db.query(WhatsappGrupo).one()
    assert ids_grupo == {grupo.custom_link_id}
    assert pessoal.id not in ids_grupo


# --- o formato do engine GOWS, e a falha que ficou invisível -----------------

def _grupo_gows(jid="120363412019840927@g.us", nome="Achadinhos SP", admin=True):
    """
    Como o GOWS devolve: structs do whatsmeow serializadas pelo Go, com as
    embutidas achatadas e tudo em PascalCase. Chaves reais, colhidas do log de
    homologação em 26/08 — o dia em que 499 grupos viraram zero.
    """
    return {
        "JID": jid,
        "Name": nome,
        "IsAnnounce": False,
        "AnnounceVersionID": "1724668800",
        "DisappearingTimer": 0,
        "IsDefaultSubGroup": False,
        "GroupCreated": "2026-01-15T12:00:00Z",
        "CreatorCountryCode": "55",
        "AddressingMode": "pn",
        "Participants": [
            {"JID": "5511999998888@s.whatsapp.net", "IsAdmin": admin,
             "IsSuperAdmin": False},
            {"JID": "5521888887777@s.whatsapp.net", "IsAdmin": False,
             "IsSuperAdmin": False},
        ],
    }


def test_gows_os_grupos_sao_gravados_de_verdade(db):
    """O caso que falhava: sync 'com sucesso' e nenhuma linha no banco."""
    inst = _instancia(db)
    svc = WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo_gows()]]))
    r = svc.sincronizar(inst)

    assert r["vistos"] == 1 and r["novos"] == 1 and r["ignorados"] == 0
    grupo = db.query(WhatsappGrupo).one()
    assert grupo.jid == "120363412019840927@g.us"
    assert grupo.nome == "Achadinhos SP"      # veio de `Name`, não de `subject`
    assert grupo.participantes == 2
    assert grupo.sou_admin is True            # veio de `IsAdmin`, não de `role`
    assert grupo.sub_id is None               # atribuição só na ativação


def test_pagina_toda_ignorada_FALHA_em_vez_de_dar_sucesso_com_zero(db):
    """
    A regressão que custou dias: o WAHA devolvia 100 itens por página, o parser
    não reconhecia nenhum, e o sync terminava `success` com `vistos=0`. Nada na
    tela, nada no `sync_runs` — só uma tela vazia dizendo "nenhum grupo ainda".
    """
    from app.services.waha_client import ErroWhatsapp

    inst = _instancia(db)
    formato_alien = [{"WhateverNovo": "x", "OutroCampo": 1} for _ in range(7)]
    svc = WhatsappGrupoSyncService(db, cliente=_FakeWaha([formato_alien]))

    with pytest.raises(ErroWhatsapp) as e:
        svc.sincronizar(inst)
    assert e.value.motivo == "formato"

    run = db.query(SyncRun).order_by(SyncRun.id.desc()).first()
    assert run.status == "failed"
    assert "sem JID reconhecível" in (run.error_message or "")


def test_alguns_itens_ilegiveis_no_meio_nao_derrubam_o_sync(db):
    """Ignorar parcial é tolerável; ignorar tudo é contrato quebrado."""
    inst = _instancia(db)
    pagina = [_grupo_gows(), {"CampoDesconhecido": 1}, _grupo_gows("120363000000000002@g.us")]
    r = WhatsappGrupoSyncService(db, cliente=_FakeWaha([pagina])).sincronizar(inst)

    assert r["vistos"] == 2 and r["ignorados"] == 1
    assert db.query(WhatsappGrupo).count() == 2


def test_convites_respeitam_o_orcamento_e_o_resto_espera_o_proximo_sync(db, monkeypatch):
    """
    Convite é UMA chamada HTTP por grupo. Com centenas de grupos isso estourava
    o tempo do request e o proxy cortava a conexão ("Failed to fetch") — e como
    o commit vinha só no fim, a afiliada perdia o sync inteiro.
    """
    import app.services.whatsapp_grupo_sync_service as mod

    inst = _instancia(db)
    pagina = [_grupo_gows(f"12036300000000{i:04d}@g.us") for i in range(5)]
    monkeypatch.setattr(mod, "ORCAMENTO_CONVITES_S", -1)   # orçamento já vencido

    r = WhatsappGrupoSyncService(db, cliente=_FakeWaha([pagina])).sincronizar(inst)

    # os grupos entraram; só o enriquecimento ficou pra depois
    assert r["vistos"] == 5 and r["novos"] == 5 and r["convites"] == 0
    assert db.query(WhatsappGrupo).count() == 5
    assert all(g.link_convite is None for g in db.query(WhatsappGrupo))


def test_ponta_a_ponta_499_grupos_do_GOWS_pelo_cliente_real(db, monkeypatch):
    """
    Reprodução do incidente de 26/08 com o `WahaClient` de verdade no caminho:
    5 páginas de 100, PascalCase do whatsmeow, e a última página curta fechando
    a paginação. Antes da correção este cenário gravava ZERO e reportava sucesso.

    Vai pelo cliente real (MockTransport) de propósito: o bug morava na junção
    entre `_pedir` → `_lista_de_grupos` → `jid_do_grupo`, e um fake de serviço
    pularia justamente a costura que falhou.
    """
    import httpx

    from app.services.waha_client import WahaClient
    import app.services.whatsapp_grupo_sync_service as mod

    TOTAL = 499

    def responder(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/api/sessions/"):
            return httpx.Response(200, json={
                "status": "WORKING",
                "me": {"id": "5511999998888@s.whatsapp.net", "pushName": "Eu"},
            })
        if req.url.path.endswith("/invite-code"):
            return httpx.Response(200, json={"code": "AbCdEf"})
        # /api/{sessao}/groups
        offset = int(req.url.params.get("offset", 0))
        limite = int(req.url.params.get("limit", 100))
        fatia = range(offset, min(offset + limite, TOTAL))
        return httpx.Response(200, json=[
            _grupo_gows(f"1203630000000{i:05d}@g.us", f"Grupo {i}", admin=(i % 50 == 0))
            for i in fatia
        ])

    cliente = WahaClient("http://waha:3000", "chave", "mkdtestu1xabcd")
    cliente._transport = httpx.MockTransport(responder)

    inst = _instancia(db)
    monkeypatch.setattr(mod, "ORCAMENTO_CONVITES_S", 30)  # tempo de sobra no teste
    r = WhatsappGrupoSyncService(db, cliente=cliente).sincronizar(inst)

    assert r["vistos"] == TOTAL
    assert r["novos"] == TOTAL
    assert r["ignorados"] == 0
    assert db.query(WhatsappGrupo).count() == TOTAL

    # o sync NÃO atribui: nenhum sub_id, nenhum link interno — isso é da
    # ativação (spec §6.2), e criar 499 links no sync era justamente o custo
    # que a mudança removeu
    assert db.query(WhatsappGrupo).filter(WhatsappGrupo.sub_id.isnot(None)).count() == 0
    assert db.query(CustomLink).count() == 0

    # só os 10 grupos onde somos admin buscam convite
    admins = db.query(WhatsappGrupo).filter(WhatsappGrupo.sou_admin.is_(True)).all()
    assert len(admins) == 10 and r["convites"] == 10
    assert all(g.link_convite for g in admins)


def test_convites_nao_ficam_presos_nos_mesmos_grupos_que_falham(db, monkeypatch):
    """
    O WAHA devolve 500 em parte dos grupos (medido em hml: 169 de admin, e uma
    fatia falha sempre). A lista chega sempre na mesma ordem, então sem
    embaralhar os mesmos grupos quebrados consumiriam o orçamento em toda
    rodada — e os que funcionariam nunca seriam tentados.
    """
    from app.services.waha_client import ErroWhatsapp
    import app.services.whatsapp_grupo_sync_service as mod

    quebrados = {f"1203630000000{i:05d}@g.us" for i in range(0, 8)}

    class _WahaParcial(_FakeWaha):
        def __init__(self, paginas):
            super().__init__(paginas)
            self.tentados = []

        def convite_do_grupo(self, jid):
            self.tentados.append(jid)
            if jid in quebrados:
                raise ErroWhatsapp("convite", "status 500: boom")
            return f"https://chat.whatsapp.com/ok-{jid[:8]}"

    pagina = [_grupo_gows(f"1203630000000{i:05d}@g.us", f"G{i}") for i in range(10)]
    cliente = _WahaParcial([pagina])
    monkeypatch.setattr(mod, "ORCAMENTO_CONVITES_S", 30)

    r = WhatsappGrupoSyncService(db, cliente=cliente).sincronizar(_instancia(db))

    # os 2 sadios são resolvidos, e os 8 quebrados não derrubam o sync
    assert r["vistos"] == 10 and r["convites"] == 2
    assert db.query(WhatsappGrupo).filter(
        WhatsappGrupo.link_convite.isnot(None)).count() == 2
