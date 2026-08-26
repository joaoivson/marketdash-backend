"""
Módulo de Grupos — F2: Campanhas (conjuntos de grupos). Tudo MAX-only.

Rota fina: validação Pydantic + service. Ownership como dependency — a
próxima rota da fase herda o guard em vez de lembrar de copiá-lo.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_plan
from app.core.plans import normalize_plan, plan_limit
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.campanhas_grupos import (
    AnunciosDaCampanhaOut, CampanhaAtualizar, CampanhaCriar, CampanhaDetalheOut,
    CampanhaOut, GrupoDaCampanhaItem, GrupoDaCampanhaOut, ResultadosOut,
    ResumoConsolidadoOut, VinculosDeAnuncioOut,
)
from app.services.campanha_grupos_service import (
    CampanhaGruposService, GrupoInvalido, LimiteDeCampanhas,
)
from app.services.campanha_link_service import CampanhaLinkService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["campanhas-grupos"])

# Teto do bloco consolidado do Dashboard. Ver `resumo_consolidado`.
MAX_CAMPANHAS_NO_RESUMO = 20


def _servico(db: Session, user: User | None = None) -> CampanhaGruposService:
    """`user` só no caminho que CRIA (o único que lê o limite do plano)."""
    limite = -1
    if user is not None:
        sub = SubscriptionRepository(db).get_by_user_id(user.id)
        limite = plan_limit(normalize_plan(sub.plan if sub else None), "campanhas_grupos")
    return CampanhaGruposService(db, plan_limit_campanhas=limite)


def campanha_da_usuaria(
    campanha_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    campanha = CampanhaGruposService(db).obter(current_user.id, campanha_id)
    if not campanha:
        raise HTTPException(status_code=404, detail="Campanha não encontrada.")
    return campanha


def _out(campanha, total_grupos: int) -> CampanhaOut:
    return CampanhaOut(
        id=campanha.id, nome=campanha.nome, descricao=campanha.descricao,
        status=campanha.status, estrategia_entrada=campanha.estrategia_entrada,
        abertura_automatica=campanha.abertura_automatica,
        reabertura_automatica=campanha.reabertura_automatica,
        prefixo=campanha.prefixo, sufixo=campanha.sufixo,
        modo_imagem=campanha.modo_imagem, total_grupos=total_grupos,
        criado_em=campanha.criado_em,
    )


def _grupos_out(servico: CampanhaGruposService, campanha) -> list[GrupoDaCampanhaOut]:
    return [
        GrupoDaCampanhaOut(
            grupo_id=g.id, posicao=v.posicao, aberto=v.aberto,
            nome=g.nome, participantes=g.participantes,
            permite_envio=g.permite_envio, ativo=g.ativo, sub_id=g.sub_id,
        )
        for v, g in servico.grupos_da_campanha(campanha)
    ]


@router.get("/vinculos-de-anuncio", response_model=VinculosDeAnuncioOut)
def vinculos_de_anuncio(
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """campaign_id (Meta) → campanha de grupos vinculada.

    Endpoint separado de propósito: a tela de Anúncios só precisa do SELO, e
    embutir isso no CampaignResponse mexeria no cálculo de KPIs que já está
    no ar. Uma request barata contra o risco de regressão na tela principal.
    """
    from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository

    return {"vinculos": CampanhaAnuncioRepository(db).campanha_por_campaign(current_user.id)}


@router.get("/resumo", response_model=ResumoConsolidadoOut)
def resumo_consolidado(
    inicio: str | None = None,
    fim: str | None = None,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """
    Totais somados de TODAS as campanhas ativas — o bloco do Dashboard.

    Declarada antes de `/{campanha_id}` de propósito: o FastAPI casa na ordem
    de declaração e "resumo" seria engolido como id (422 em vez de 200).
    """
    from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository
    from app.services.campanha_resultado_service import CampanhaResultadoService

    d_ini, d_fim = _periodo(inicio, fim)
    campanhas, _contagens = _servico(db).listar(current_user.id, incluir_arquivadas=False)
    ativas = [c for c in campanhas if c.status != "arquivada"]
    # O plano MAX não limita campanhas e cada uma custa uma volta de queries.
    # O corte protege o Dashboard, mas NÃO é silencioso: `campanhas_omitidas`
    # vai na resposta para a tela poder dizer que não somou tudo.
    total_ativas = len(ativas)
    ativas = ativas[:MAX_CAMPANHAS_NO_RESUMO]
    if total_ativas > len(ativas):
        logger.info("resumo de grupos truncado: %d de %d campanhas (user=%s)",
                    len(ativas), total_ativas, current_user.id)

    servico = CampanhaResultadoService(db)
    repo_anuncio = CampanhaAnuncioRepository(db)

    gasto_bruto = 0.0
    gasto_com_imposto = 0.0
    leads_total = None          # None enquanto NENHUMA campanha reportar lead
    por_campanha = []
    # O MESMO grupo pode estar em N campanhas — é decisão de desenho da F2.
    # Somar os totais campanha a campanha contava esse grupo (e a comissão, e
    # os participantes) uma vez por campanha: 1 grupo virava 200 participantes
    # e R$200 onde havia R$100. Aqui o recorte é por GRUPO, uma vez só.
    linhas_por_grupo = {}

    for c in ativas:
        dados = servico.por_grupo(current_user.id, c, d_ini, d_fim)
        t = dados["totais"]
        for linha in dados["linhas"]:
            linhas_por_grupo.setdefault(linha["grupo_id"], linha)
        m = repo_anuncio.metricas(current_user.id, c.id, d_ini, d_fim)
        # Gasto NÃO deduplica: cada campanha tem os anúncios dela, e os dois
        # gastos são reais mesmo quando enchem o mesmo grupo.
        gasto_bruto += m["gasto"]
        gasto_com_imposto += m["gasto_com_imposto"]
        if m["leads"] is not None:
            leads_total = (leads_total or 0) + m["leads"]
        por_campanha.append({
            "campanha_id": c.id, "nome": c.nome,
            "grupos": len(dados["linhas"]),
            "participantes": t["participantes"], "entradas": t["entradas"],
            "comissao_liquida": t["comissao_liquida"], "lucro": t["lucro"],
            "lucro_por_pessoa": t["lucro_por_pessoa"],
        })

    investimento_com_imposto = round(gasto_com_imposto, 2)
    totais = {"participantes": 0, "entradas": 0, "saidas": 0, "ficaram": 0,
              "mensagens": 0, "cliques": 0, "pedidos": 0,
              "comissao_liquida": 0.0, "gasto_atribuido": 0.0, "lucro": 0.0}
    for linha in linhas_por_grupo.values():
        for chave in ("participantes", "entradas", "saidas", "ficaram",
                      "mensagens", "cliques", "pedidos"):
            totais[chave] += linha[chave]
        totais["comissao_liquida"] += linha["comissao_liquida"]
    totais["comissao_liquida"] = round(totais["comissao_liquida"], 2)
    # No consolidado o gasto é o investimento INTEIRO do período, não a soma dos
    # rateios por grupo: assim o lucro desconta também o que foi gasto em
    # campanha que ainda não tem grupo — antes esse dinheiro entrava no
    # "Investimento" e sumia do "Lucro".
    totais["gasto_atribuido"] = investimento_com_imposto
    totais["lucro"] = round(totais["comissao_liquida"] - investimento_com_imposto, 2)
    totais["lucro_por_pessoa"] = (round(totais["lucro"] / totais["participantes"], 2)
                                  if totais["participantes"] else 0.0)
    por_campanha.sort(key=lambda l: l["lucro"], reverse=True)
    return {
        "periodo": {"inicio": d_ini.isoformat(), "fim": d_fim.isoformat()},
        "campanhas_ativas": total_ativas,
        "campanhas_omitidas": total_ativas - len(ativas),
        "totais": totais,
        "investimento": round(gasto_bruto, 2),
        "investimento_com_imposto": investimento_com_imposto,
        "leads": leads_total,
        "custo_por_entrada": (round(investimento_com_imposto / totais["entradas"], 2)
                              if totais["entradas"] else None),
        "por_campanha": por_campanha,
    }


@router.get("", response_model=list[CampanhaOut])
def listar(
    incluir_arquivadas: bool = False,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    campanhas, contagens = servico.listar(current_user.id, incluir_arquivadas)
    return [_out(c, contagens.get(c.id, 0)) for c in campanhas]


@router.post("", response_model=CampanhaOut, status_code=201)
def criar(
    payload: CampanhaCriar,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    try:
        campanha = _servico(db, current_user).criar(
            current_user.id, payload.nome, payload.descricao
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except LimiteDeCampanhas as e:
        raise HTTPException(status_code=403, detail=str(e))
    return _out(campanha, 0)


@router.get("/{campanha_id}", response_model=CampanhaDetalheOut)
def detalhe(
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    grupos = _grupos_out(servico, campanha)
    base = _out(campanha, len(grupos))
    return CampanhaDetalheOut(**base.model_dump(), grupos=grupos)


@router.patch("/{campanha_id}", response_model=CampanhaOut)
def atualizar(
    payload: CampanhaAtualizar,
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    # Com limite: desarquivar é um "criar de volta" e re-conta a vaga do plano.
    servico = _servico(db, current_user)
    try:
        campanha = servico.atualizar(campanha, payload.model_dump(exclude_unset=True))
    except LimiteDeCampanhas as e:
        raise HTTPException(status_code=403, detail=str(e))
    total = servico.total_de_grupos(campanha)
    return _out(campanha, total)


@router.put("/{campanha_id}/grupos", response_model=CampanhaDetalheOut)
def definir_grupos(
    payload: list[GrupoDaCampanhaItem],
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    try:
        servico.definir_grupos(
            campanha, [(i.grupo_id, i.posicao, i.aberto) for i in payload]
        )
    except GrupoInvalido:
        raise HTTPException(status_code=404, detail="Grupo não encontrado.")
    grupos = _grupos_out(servico, campanha)
    base = _out(campanha, len(grupos))
    return CampanhaDetalheOut(**base.model_dump(), grupos=grupos)


# --- link de entrada (F6) ----------------------------------------------------


@router.get("/{campanha_id}/link")
def obter_link(campanha=Depends(campanha_da_usuaria), db: Session = Depends(get_db)):
    """Cria na primeira visita — a afiliada não precisa 'gerar' nada."""
    from app.core.config import settings

    link = CampanhaLinkService(db).obter_ou_criar(campanha)
    base = (settings.FRONTEND_URL or "https://marketdash.com.br").rstrip("/")
    return {
        "id": link.id,
        "slug": link.slug,
        "url": f"{base}/g/{link.slug}",
        "url_teste": f"{base}/g/preview/{link.slug}",
        "titulo_previa": link.titulo_previa,
        "descricao_previa": link.descricao_previa,
        "banner_previa_url": link.banner_previa_url,
        "pixel_facebook_id": link.pixel_facebook_id,
        "pixel_eventos": link.pixel_eventos,
        "ativo": link.ativo,
    }


@router.patch("/{campanha_id}/link")
def atualizar_link(
    payload: dict,
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = CampanhaLinkService(db)
    link = servico.obter_ou_criar(campanha)
    servico.atualizar(link, payload or {})
    return obter_link(campanha=campanha, db=db)


@router.get("/{campanha_id}/atividade")
def atividade(
    limite: int = 50,
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    """Feed de entradas e saídas dos grupos da campanha (sem dado pessoal)."""
    from app.repositories.campanha_link_repository import CampanhaLinkRepository

    servico = CampanhaGruposService(db)
    pares = servico.grupos_da_campanha(campanha)
    nomes = {g.id: g.nome for _v, g in pares}
    eventos = CampanhaLinkRepository(db).atividade(list(nomes), min(int(limite or 50), 200))
    return {
        "eventos": [
            {
                "tipo": e.tipo,
                "origem": e.origem,
                "grupo_id": e.grupo_id,
                "grupo": nomes.get(e.grupo_id),
                "quando": e.criado_em.isoformat() if e.criado_em else None,
            }
            for e in eventos
        ]
    }


# --- Anúncios × Grupos e Resultados (F7) -------------------------------------


def _periodo(inicio: str | None, fim: str | None):
    """Período do relatório. Default: últimos 30 dias em BRT (o dia civil que
    a afiliada enxerga), nunca UTC."""
    from datetime import timedelta

    from app.services.janela_envio_service import BRT
    from datetime import datetime as _dt

    hoje = _dt.now(BRT).date()
    # Data inválida vira 422, não "últimos 30 dias": engolir a falha faz um bug
    # de data no frontend virar número silenciosamente errado numa tela que
    # decide investimento — o pior tipo de erro deste produto.
    try:
        d_fim = _dt.fromisoformat(fim).date() if fim else hoje
        d_ini = _dt.fromisoformat(inicio).date() if inicio else d_fim - timedelta(days=29)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail="Período inválido. Use datas no formato AAAA-MM-DD.")
    if d_ini > d_fim:
        d_ini, d_fim = d_fim, d_ini
    return d_ini, d_fim


@router.get("/{campanha_id}/anuncios", response_model=AnunciosDaCampanhaOut)
def listar_anuncios(
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """Campanhas de anúncio do Meta + quais estão vinculadas a ESTA campanha
    de grupos (o multi-select da tela)."""
    from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository

    repo = CampanhaAnuncioRepository(db)
    anuncios = repo.campanhas_de_anuncio(current_user.id)
    vinculadas = set(repo.campaign_ids(campanha.id))
    # Quem já pertence a OUTRA campanha de grupos vem marcado: a tela
    # desabilita a linha em vez de deixar clicar e tomar 409 no salvar.
    ocupados = repo.vinculos_de_outras_campanhas(campanha.id, [c.id for c in anuncios])
    nomes_de_campanha = {c.id: c.nome for c in _servico(db).listar(
        current_user.id, incluir_arquivadas=True)[0]}
    return {
        "anuncios": [
            {
                "id": c.id,
                "nome": c.name,
                "status": c.status,
                "sub_id": c.sub_id,
                "vinculada": c.id in vinculadas,
                # id junto do nome: sem o id a tela só consegue linkar para a
                # lista, e a afiliada tem que caçar qual campanha desvincular.
                "vinculada_em_outra": (
                    {"id": ocupados[c.id],
                     "nome": nomes_de_campanha.get(ocupados[c.id]) or ""}
                    if c.id in ocupados else None
                ),
            }
            for c in anuncios
        ]
    }


@router.put("/{campanha_id}/anuncios", response_model=AnunciosDaCampanhaOut)
def definir_anuncios(
    payload: list[int],
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository

    repo = CampanhaAnuncioRepository(db)
    # Ownership: só campanhas de anúncio DA usuária podem ser vinculadas.
    minhas = {c.id for c in repo.campanhas_de_anuncio(current_user.id)}
    alheias = [cid for cid in payload if cid not in minhas]
    if alheias:
        raise HTTPException(status_code=404, detail="Campanha de anúncio não encontrada.")

    # Um anúncio pertence a UMA campanha de grupos: vinculado a duas, o mesmo
    # gasto entraria inteiro nas duas e os dois lucros sairiam errados. O banco
    # tem UNIQUE, mas 409 explicado é melhor do que violação de constraint.
    ocupados = repo.vinculos_de_outras_campanhas(campanha.id, list(payload))
    if ocupados:
        # A mensagem tem que dizer PARA ONDE ir desvincular — nomear só o
        # anúncio deixa a afiliada sem o próximo passo.
        nomes_de_anuncio = {c.id: c.name for c in repo.campanhas_de_anuncio(current_user.id)}
        nomes_de_campanha = {c.id: c.nome for c in _servico(db).listar(
            current_user.id, incluir_arquivadas=True)[0]}
        conflitos = ", ".join(sorted(
            f'"{nomes_de_anuncio.get(cid, cid)}" (em "{nomes_de_campanha.get(gid, gid)}")'
            for cid, gid in ocupados.items()
        ))
        raise HTTPException(
            status_code=409,
            detail=(f"Estes anúncios já pertencem a outra campanha de grupos: "
                    f"{conflitos}. Desvincule lá antes de vincular aqui."),
        )
    repo.definir(campanha.id, list(dict.fromkeys(payload)))
    db.commit()
    return listar_anuncios(campanha=campanha, current_user=current_user, db=db)


@router.get("/{campanha_id}/resultados", response_model=ResultadosOut)
def resultados(
    inicio: str | None = None,
    fim: str | None = None,
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """Linha por grupo: participantes, entradas/saídas, mensagens, cliques,
    pedidos, comissão líquida, lucro e **lucro por pessoa**."""
    from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository
    from app.services.campanha_resultado_service import CampanhaResultadoService

    d_ini, d_fim = _periodo(inicio, fim)
    dados = CampanhaResultadoService(db).por_grupo(current_user.id, campanha, d_ini, d_fim)
    anuncios = CampanhaAnuncioRepository(db).metricas(current_user.id, campanha.id,
                                                      d_ini, d_fim)
    entradas = dados["totais"]["entradas"]
    ficaram = dados["totais"]["ficaram"]
    # A conta do imposto vem pronta do repository — repeti-la aqui criaria uma
    # segunda fórmula de dinheiro numa camada que não devia calcular nada.
    gasto_com_imposto = anuncios["gasto_com_imposto"]
    return {
        "periodo": {"inicio": d_ini.isoformat(), "fim": d_fim.isoformat()},
        "linhas": dados["linhas"],
        "totais": dados["totais"],
        "anuncios": {
            "campanhas_vinculadas": anuncios["campanhas"],
            "investimento": round(anuncios["gasto"], 2),
            "investimento_com_imposto": round(gasto_com_imposto, 2),
            # None = sem pixel/sem dado. A tela mostra "configure o pixel",
            # nunca 0 (que significaria "ninguém virou lead").
            "leads": anuncios["leads"],
            # CPL é None tanto sem pixel quanto com 0 lead — nos dois casos a
            # divisão não existe. Quem distingue os dois na tela é `leads`
            # acima (None = "configure o pixel"; 0 = "ninguém virou lead").
            "cpl": (round(gasto_com_imposto / anuncios["leads"], 2)
                    if anuncios["leads"] else None),
            "custo_por_entrada": (round(gasto_com_imposto / entradas, 2)
                                  if entradas else None),
            "custo_por_permanencia": (round(gasto_com_imposto / ficaram, 2)
                                      if ficaram else None),
        },
    }


def _seguro_para_planilha(valor: str) -> str:
    """
    Neutraliza fórmula em campo de CSV.

    O nome do grupo é escrito por QUALQUER admin do grupo — inclusive alguém
    que não é a afiliada. Um nome como `=HYPERLINK("http://…"&A1;"Clique")`
    vira fórmula ativa quando ela abre o arquivo no Excel/Sheets.
    """
    texto = "" if valor is None else str(valor)
    if texto[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + texto
    return texto


@router.get("/{campanha_id}/leads/export")
def exportar_leads(
    inicio: str | None = None,
    fim: str | None = None,
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """
    CSV dos EVENTOS DE ENTRADA (decisão de produto, 25/08).

    Exporta data, grupo, origem e se a pessoa continua no grupo — **nunca**
    número de telefone: com `getParticipants=false` nós sequer coletamos os
    números, e o identificador que guardamos é um hash irreversível. Quem
    esperava "lista de contatos" recebe o que existe, com a explicação no
    cabeçalho do arquivo.
    """
    import csv
    import io

    from fastapi.responses import StreamingResponse

    from app.models.campanha_link import EVENTO_ENTRADA, EVENTO_SAIDA, GrupoEvento
    from app.services.campanha_grupos_service import CampanhaGruposService

    pares = CampanhaGruposService(db).grupos_da_campanha(campanha)
    nomes = {g.id: (g.nome or f"Grupo {g.id}") for _v, g in pares}
    if not nomes:
        raise HTTPException(status_code=404, detail="A campanha não tem grupos.")

    from app.services.campanha_resultado_service import _intervalo_brt

    d_ini, d_fim = _periodo(inicio, fim)
    ini_utc, fim_utc = _intervalo_brt(d_ini, d_fim)

    entradas = (
        db.query(GrupoEvento.grupo_id, GrupoEvento.identificador_hash,
                 GrupoEvento.origem, GrupoEvento.criado_em)
        .filter(GrupoEvento.grupo_id.in_(list(nomes)),
                GrupoEvento.tipo == EVENTO_ENTRADA,
                GrupoEvento.criado_em >= ini_utc,
                GrupoEvento.criado_em < fim_utc)
        .order_by(GrupoEvento.criado_em)
        .all()
    )

    # As SAÍDAS não são limitadas pela JANELA — quem entrou no período e saiu
    # depois dele não continua no grupo —, mas são limitadas aos identificadores
    # que aparecem nestas entradas. Sem esse recorte, uma campanha madura traz
    # o histórico de saída inteiro de todos os grupos só para descartá-lo.
    hashes = {e.identificador_hash for e in entradas}
    saidas_por_chave: dict = {}
    if hashes:
        for gid, h, quando in (
            db.query(GrupoEvento.grupo_id, GrupoEvento.identificador_hash,
                     GrupoEvento.criado_em)
            .filter(GrupoEvento.grupo_id.in_(list(nomes)),
                    GrupoEvento.tipo == EVENTO_SAIDA,
                    GrupoEvento.identificador_hash.in_(list(hashes)))
            .all()
        ):
            saidas_por_chave.setdefault((gid, h), []).append(quando)

    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(["data_entrada", "grupo", "origem", "ainda_no_grupo"])
    for e in entradas:
        # "Ainda no grupo" é por ENTRADA, não por pessoa: quem entrou, saiu e
        # voltou tem duas linhas — a primeira é "nao", a segunda "sim".
        # Resolver pelo estado final marcaria as duas como "sim" e inflaria a
        # permanência justamente da coorte que a exportação existe para medir.
        posteriores = saidas_por_chave.get((e.grupo_id, e.identificador_hash), ())
        saiu = any(s > e.criado_em for s in posteriores if s and e.criado_em)
        escritor.writerow([
            e.criado_em.isoformat() if e.criado_em else "",
            _seguro_para_planilha(nomes.get(e.grupo_id, "")),
            _seguro_para_planilha(e.origem),
            "nao" if saiu else "sim",
        ])
    buffer.seek(0)
    # Nome de arquivo ASCII e fixo: header HTTP é codificado em latin-1 pelo
    # Starlette e um emoji no nome da campanha (rotineiro) derruba a request
    # com UnicodeEncodeError — 500 sem mensagem, sem saída para a usuária.
    nome_arquivo = f"entradas-campanha-{campanha.id}-{d_ini}-a-{d_fim}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
    )
