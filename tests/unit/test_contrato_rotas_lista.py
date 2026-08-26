"""
Rotas que recebem LISTA no corpo têm que declarar o tipo do item.

`payload: list` (sem genérico) faz o FastAPI tratar o corpo como
multipart/form-data: o JSON do cliente nunca chega e a rota devolve 422 com
`input: null`, sem nada de errado do lado de quem chama. Aconteceu no PUT de
passos do roteiro e só apareceu quando a tela tentou salvar.
"""
import pytest
from fastapi.openapi.utils import get_openapi

from app.main import app

ROTAS_COM_LISTA = [
    ("/api/v1/roteiros/{roteiro_id}/passos", "put"),
    ("/api/v1/templates/{template_id}/variacoes", "put"),
    ("/api/v1/campanhas-grupos/{campanha_id}/grupos", "put"),
    ("/api/v1/campanhas-grupos/{campanha_id}/anuncios", "put"),
]


@pytest.fixture(scope="module")
def spec():
    return get_openapi(title="t", version="1", routes=app.routes)


@pytest.mark.parametrize("caminho,metodo", ROTAS_COM_LISTA)
def test_corpo_de_lista_e_json_nao_form_data(spec, caminho, metodo):
    operacao = spec["paths"][caminho][metodo]
    tipos = list(operacao["requestBody"]["content"].keys())
    assert tipos == ["application/json"], (
        f"{metodo.upper()} {caminho} espera {tipos} — anote o tipo do item "
        "(ex.: list[PassoIn]) ou o JSON do cliente nunca chega."
    )
