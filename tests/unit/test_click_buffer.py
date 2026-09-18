"""Buffer de cliques no Redis e descarga em lote (app/services/click_buffer.py).
Run: pytest tests/unit/test_click_buffer.py -v
"""
from unittest.mock import Mock, patch

import pytest

from app.services import click_buffer as cb
from app.services.custom_link_service import CustomLinkService


class FakeRedis:
    """Só o que o buffer usa: hash, lista, set NX/EX, renamenx, pipeline."""

    def __init__(self):
        self.h = {}
        self.l = {}
        self.s = {}

    # --- comandos ---
    def hincrby(self, k, f, n):
        self.h.setdefault(k, {})
        self.h[k][f] = str(int(self.h[k].get(f, 0)) + int(n))
        return int(self.h[k][f])

    def rpush(self, k, *vals):
        self.l.setdefault(k, []).extend(vals)
        return len(self.l[k])

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.s:
            return None
        self.s[k] = v
        return True

    def delete(self, *ks):
        n = 0
        for k in ks:
            for d in (self.h, self.l, self.s):
                if k in d:
                    del d[k]
                    n += 1
        return n

    def renamenx(self, src, dst):
        for d in (self.h, self.l, self.s):
            if src in d:
                if dst in d:
                    return 0
                d[dst] = d.pop(src)
                return 1
        raise Exception("ERR no such key")

    def hgetall(self, k):
        return dict(self.h.get(k, {}))

    def lrange(self, k, a, b):
        return list(self.l.get(k, []))

    def llen(self, k):
        return len(self.l.get(k, []))

    def pipeline(self, transaction=False):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, r):
        self.r = r
        self.ops = []

    def __getattr__(self, name):
        def _enfileira(*a, **kw):
            self.ops.append((name, a, kw))
            return self
        return _enfileira

    def execute(self):
        return [getattr(self.r, n)(*a, **kw) for n, a, kw in self.ops]


@pytest.fixture
def redis():
    r = FakeRedis()
    with patch.object(cb, "get_client", return_value=r), \
         patch.object(cb.settings, "CLIQUES_BUFFER_REDIS", True), \
         patch.object(cb.settings, "CLIQUES_FLUSH_INTERVALO_S", 15):
        yield r


# ---- registrar --------------------------------------------------------------

def test_registrar_acumula_no_redis_e_agenda_uma_vez(redis):
    agendar = Mock()
    assert cb.registrar(7, 3, agendar=agendar)
    assert cb.registrar(7, 3, agendar=agendar)
    assert cb.registrar(9, 3, agendar=agendar)
    assert redis.h[cb.CHAVE_CONTAGEM] == {"7": "2", "9": "1"}
    assert len(redis.l[cb.CHAVE_EVENTOS]) == 3
    assert agendar.call_count == 1, "uma task por janela, não uma por clique"


def test_registrar_sem_redis_devolve_false():
    with patch.object(cb, "get_client", return_value=None):
        assert cb.registrar(7, 3) is False


def test_registrar_flag_desligada_devolve_false(redis):
    with patch.object(cb.settings, "CLIQUES_BUFFER_REDIS", False):
        assert cb.registrar(7, 3) is False


def test_registrar_redis_com_erro_devolve_false(redis):
    redis.pipeline = Mock(side_effect=Exception("connection refused"))
    assert cb.registrar(7, 3) is False


def test_falha_ao_agendar_libera_lock_para_proximo_clique(redis):
    agendar = Mock(side_effect=Exception("broker fora"))
    cb.registrar(7, 3, agendar=agendar)
    assert cb.CHAVE_AGENDADO not in redis.s
    cb.registrar(7, 3, agendar=agendar)
    assert agendar.call_count == 2


# ---- descarregar ------------------------------------------------------------

def _db_ok():
    db = Mock()
    db.bind = None
    return db


def test_descarregar_faz_um_update_por_link_e_insert_em_lote(redis):
    for _ in range(5):
        cb.registrar(7, 3, agendar=Mock())
    cb.registrar(9, 4, agendar=Mock())
    db = _db_ok()

    r = cb.descarregar(db)

    assert r.links_atualizados == 2 and r.eventos_gravados == 6 and r.restantes == 0
    updates = [c for c in db.execute.call_args_list if "UPDATE custom_links" in str(c.args[0])]
    inserts = [c for c in db.execute.call_args_list if "INSERT INTO custom_link_events" in str(c.args[0])]
    assert len(updates) == 2, "62 mil cliques viram 1 UPDATE, não 62 mil"
    assert {u.args[1]["id"]: u.args[1]["n"] for u in updates} == {7: 5, 9: 1}
    assert len(inserts) == 1 and len(inserts[0].args[1]) == 6
    db.commit.assert_called_once()
    # buffer e lote limpos
    assert cb.CHAVE_CONTAGEM not in redis.h and cb.CHAVE_EVENTOS not in redis.l
    assert not [k for k in list(redis.h) + list(redis.l) if ":lote:" in k]
    assert cb.CHAVE_AGENDADO not in redis.s


def test_descarregar_buffer_vazio_nao_toca_o_banco(redis):
    db = _db_ok()
    r = cb.descarregar(db)
    assert r.links_atualizados == 0
    db.execute.assert_not_called()
    db.commit.assert_not_called()


def test_descarregar_banco_falha_devolve_lote_ao_buffer(redis):
    for _ in range(3):
        cb.registrar(7, 3, agendar=Mock())
    db = _db_ok()
    db.execute.side_effect = Exception("canceling statement due to statement timeout")

    with pytest.raises(Exception):
        cb.descarregar(db)

    db.rollback.assert_called_once()
    assert redis.h[cb.CHAVE_CONTAGEM] == {"7": "3"}, "nenhum clique perdido"
    assert len(redis.l[cb.CHAVE_EVENTOS]) == 3


def test_descarregar_evento_malformado_e_ignorado_sem_derrubar_lote(redis):
    cb.registrar(7, 3, agendar=Mock())
    redis.rpush(cb.CHAVE_EVENTOS, "lixo")
    db = _db_ok()
    r = cb.descarregar(db)
    assert r.eventos_gravados == 1 and r.links_atualizados == 1


# ---- integração com o serviço de redirect ------------------------------------

@pytest.fixture
def servico():
    link = Mock(id=42, user_id=3, is_active=True, expires_at=None,
                original_url="https://shopee.com.br/produto", slug="x")
    repo = Mock()
    repo.get_by_slug.return_value = link
    repo.increment_click_count = Mock()
    return CustomLinkService(repo), link


def test_redirect_com_redis_usa_buffer_e_nao_escreve_no_banco(servico):
    svc, _ = servico
    with patch("app.services.custom_link_service.is_bot", return_value=False), \
         patch("app.services.custom_link_service.should_count", return_value=True), \
         patch("app.services.custom_link_service.click_buffer.registrar", return_value=True) as reg, \
         patch("app.core.cache.cache_get", return_value=None), patch("app.core.cache.cache_set"):
        assert svc.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0") == {"url": "https://shopee.com.br/produto"}
    reg.assert_called_once_with(42, 3)
    svc.repository.increment_click_count.assert_not_called()


def test_redirect_sem_redis_cai_no_incremento_atomico(servico):
    svc, link = servico
    with patch("app.services.custom_link_service.is_bot", return_value=False), \
         patch("app.services.custom_link_service.should_count", return_value=True), \
         patch("app.services.custom_link_service.click_buffer.registrar", return_value=False), \
         patch("app.core.cache.cache_get", return_value=None), patch("app.core.cache.cache_set"):
        svc.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0")
    svc.repository.increment_click_count.assert_called_once_with(link)
    svc.repository.get_by_slug.assert_called_once()


def test_redirect_usa_decisao_em_cache_sem_consultar_banco(servico):
    svc, _ = servico
    cached = {"url": "https://shopee.com.br/produto", "link_id": 42, "user_id": 3}
    with patch("app.services.custom_link_service.is_bot", return_value=False), \
         patch("app.services.custom_link_service.should_count", return_value=True), \
         patch("app.services.custom_link_service.click_buffer.registrar", return_value=True), \
         patch("app.core.cache.cache_get", return_value=cached):
        assert svc.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0")["url"] == cached["url"]
    svc.repository.get_by_slug.assert_not_called()


def test_redirect_erro_em_cache_e_respeitado(servico):
    svc, _ = servico
    with patch("app.core.cache.cache_get", return_value={"error": "Este link está desativado", "status_code": 403}):
        r = svc.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0")
    assert r["status_code"] == 403
    svc.repository.get_by_slug.assert_not_called()


def test_contagem_nunca_impede_redirect_no_fallback(servico):
    svc, _ = servico
    svc.repository.increment_click_count.side_effect = Exception("lock_timeout")
    with patch("app.services.custom_link_service.is_bot", return_value=False), \
         patch("app.services.custom_link_service.should_count", return_value=True), \
         patch("app.services.custom_link_service.click_buffer.registrar", return_value=False), \
         patch("app.core.cache.cache_get", return_value=None), patch("app.core.cache.cache_set"):
        assert svc.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0") == {"url": "https://shopee.com.br/produto"}
    svc.repository.db.rollback.assert_called_once()
