"""
Módulo de Grupos — F2: Campanhas (conjuntos de grupos). Tudo MAX-only.

Rota fina: validação Pydantic + service. Ownership como dependency — a
próxima rota da fase herda o guard em vez de lembrar de copiá-lo.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_plan
from app.core.plans import normalize_plan, plan_limit
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.campanhas_grupos import (
    AnunciosDaCampanhaOut, CampanhaAtualizar, CampanhaCriar, CampanhaDetalheOut,
    CampanhaOut, GrupoDaCampanhaItem, GrupoDaCampanhaOut, NumerosDaCampanhaOut,
    ResultadosOut, ResumoConsolidadoOut, SubIdsDaCampanhaOut,
    VinculosDeAnuncioOut, VisaoGeralOut,
)
from app.services.campanha_grupos_service import (
    CampanhaGruposService, GrupoForaDosNumeros, GrupoInvalido, LimiteDeCampanhas,
    NumeroEmUso, NumeroInvalido, SubIdEmUso,
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
        modo_imagem=campanha.modo_imagem,
        limite_participantes=campanha.limite_participantes,
        total_grupos=total_grupos, criado_em=campanha.criado_em,
    )


def _grupos_out(servico: CampanhaGruposService, campanha) -> list[GrupoDaCampanhaOut]:
    # Uma consulta para todos os grupos: `instancias_por_grupo` já devolve o
    # mapa inteiro, e pedir por grupo aqui seria N+1 numa tela de listagem.
    das_instancias = servico.repo_grupos.instancias_por_grupo(campanha.user_id)
    saida = []
    for v, g in servico.grupos_da_campanha(campanha):
        # Teto e "cheio" saem daqui prontos, e não do JavaScript: a regra é a
        # MESMA que decide a rotação (campanha_link_repository), e uma segunda
        # cópia no frontend é como a tela passou a dizer "Aberto" para um grupo
        # que o roteador já tinha parado de escolher.
        teto = min(g.capacidade,
                   campanha.limite_participantes or g.capacidade)
        cheio = (v.cheio_override if v.cheio_override is not None
                 else (g.participantes or 0) >= teto)
        saida.append(GrupoDaCampanhaOut(
            grupo_id=g.id, posicao=v.posicao, aberto=v.aberto,
            cheio=cheio, cheio_override=v.cheio_override, teto=teto,
            nome=g.nome, participantes=g.participantes, capacidade=g.capacidade,
            permite_envio=g.permite_envio, ativo=g.ativo, sub_id=g.sub_id,
            instancia_ids=das_instancias.get(g.id, []),
        ))
    return saida


def _detalhe_out(servico: CampanhaGruposService, campanha) -> CampanhaDetalheOut:
    grupos = _grupos_out(servico, campanha)
    base = _out(campanha, len(grupos))
    return CampanhaDetalheOut(
        **base.model_dump(), grupos=grupos,
        instancia_ids=servico.repo_numeros.instancia_ids(campanha.id),
    )


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
                                  if totais["participantes"] else None)
    # Mesma fórmula do resto do produto: comissão LÍQUIDA sobre gasto COM
    # imposto. Sem investimento, `None` — 0.00x afirmaria que cada real gasto
    # voltou zero, que é outra coisa.
    totais["roas"] = (round(totais["comissao_liquida"] / investimento_com_imposto, 2)
                      if investimento_com_imposto else None)
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
        # Só o nome: `descricao` saiu de CampanhaCriar (§1.1) e o Pydantic v2
        # não guarda campo que não declarou — ler `payload.descricao` aqui
        # levantava AttributeError, que nenhum `except` abaixo pega (500).
        campanha = _servico(db, current_user).criar(current_user.id, payload.nome)
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
    return _detalhe_out(_servico(db), campanha)


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


@router.post("/{campanha_id}/duplicar", response_model=CampanhaOut, status_code=201)
def duplicar(
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """
    Cópia da campanha SEM os grupos — é como a próxima campanha nasce já com
    prévia, estratégia, limite e números configurados.

    Vínculo de anúncio e Sub ID NÃO são copiados: os dois são invariantes de
    dinheiro (UNIQUE global em `campanha_anuncios.campaign_id`), e duplicá-los
    contaria o mesmo gasto/comissão em duas campanhas.
    """
    servico = _servico(db, current_user)
    try:
        nova = servico.duplicar(campanha)
    except LimiteDeCampanhas as e:
        raise HTTPException(status_code=403, detail=str(e))
    # 0 grupos: a cópia nasce vazia de propósito.
    return _out(nova, 0)


@router.delete("/{campanha_id}", status_code=204)
def excluir(
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    """
    Encerra a campanha e desarma o que continuaria rodando sem ela.

    É soft-delete: a linha fica para `/g/{slug}` poder responder "campanha
    encerrada" com 200 enquanto o anúncio ainda veicula, e para preservar a
    atribuição de gasto e os cliques do link, que são histórico financeiro.
    Os grupos continuam nos Números e a comissão já atribuída ao Sub ID
    permanece — nada disso pende da campanha.
    """
    _servico(db).excluir(campanha)
    return Response(status_code=204)


@router.get("/{campanha_id}/sub-ids", response_model=SubIdsDaCampanhaOut)
def listar_sub_ids(
    inicio: str | None = None,
    fim: str | None = None,
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """
    Sub IDs que a usuária pode vincular a esta campanha, com pedidos e comissão
    do período — é a mesma informação do "Vincular ao Sub ID" de Anúncios, para
    ela escolher sabendo quanto cada um trouxe.

    Quem NÃO pode ser vinculado vem na lista mesmo assim, com `bloqueado_por`
    dizendo o motivo: esconder a opção faz a afiliada procurar o Sub ID que ela
    sabe que existe e concluir que a tela está quebrada.
    """
    from app.services.campanha_resultado_service import CampanhaResultadoService

    d_ini, d_fim = _periodo(inicio, fim)
    servico = _servico(db)
    vinculados = set(servico.sub_ids(campanha))
    bloqueados = servico._sub_ids_bloqueados(campanha)

    resultado = CampanhaResultadoService(db)
    metricas = resultado.sub_ids_disponiveis(current_user.id, d_ini, d_fim)

    # União: o que teve movimento no período + o que já está vinculado (mesmo
    # sem venda). Sem a segunda parte, desmarcar sem querer um Sub ID sem
    # pedidos no período o faria sumir da lista e ela não conseguiria voltar.
    todos = sorted(set(metricas) | vinculados)
    return {
        "sub_ids": [
            {
                "sub_id": s,
                "pedidos": metricas.get(s, {}).get("pedidos", 0),
                "comissao_liquida": metricas.get(s, {}).get("comissao_liquida", 0.0),
                "vinculado": s in vinculados,
                "bloqueado_por": (None if s in vinculados else bloqueados.get(s)),
            }
            for s in todos
        ]
    }


@router.put("/{campanha_id}/sub-ids", response_model=SubIdsDaCampanhaOut)
def definir_sub_ids(
    payload: list[str],
    inicio: str | None = None,
    fim: str | None = None,
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """Substitui o conjunto inteiro — mesmo contrato do PUT de anúncios."""
    servico = _servico(db)
    try:
        servico.definir_sub_ids(campanha, payload)
    except SubIdEmUso as e:
        # 409 explicado, no mesmo formato do conflito de anúncios e números:
        # dizer só "não pode" deixa a afiliada sem o próximo passo.
        travas = "; ".join(f"{sub_id} ({motivo})"
                           for sub_id, motivo in sorted(e.motivos.items()))
        raise HTTPException(
            status_code=409,
            detail=f"Estes Sub IDs já entram por outro caminho: {travas}.",
        )
    return listar_sub_ids(inicio=inicio, fim=fim, campanha=campanha,
                          current_user=current_user, db=db)


@router.put("/{campanha_id}/grupos", response_model=CampanhaDetalheOut)
def definir_grupos(
    payload: list[GrupoDaCampanhaItem],
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    try:
        servico.definir_grupos(
            campanha,
            [(i.grupo_id, i.posicao, i.aberto, i.cheio_override) for i in payload],
        )
    except GrupoInvalido:
        raise HTTPException(status_code=404, detail="Grupo não encontrado.")
    except GrupoForaDosNumeros as e:
        # 422 e não 404: os grupos existem — o que não bate é o escopo. A
        # mensagem nomeia os grupos e diz para onde ir resolver.
        raise HTTPException(
            status_code=422,
            detail=(f"Estes grupos não pertencem a nenhum número da campanha: "
                    f"{', '.join(e.nomes)}. Ajuste a aba Números ou remova-os."),
        )
    return _detalhe_out(servico, campanha)


# --- Números da campanha (spec §2) -------------------------------------------


def _mascarar(numero: str | None) -> str | None:
    """Últimos 4 dígitos bastam para a afiliada reconhecer o chip."""
    limpo = "".join(c for c in (numero or "") if c.isdigit())
    return f"•••• {limpo[-4:]}" if len(limpo) >= 4 else (numero or None)


@router.get("/{campanha_id}/numeros", response_model=NumerosDaCampanhaOut)
def listar_numeros(campanha=Depends(campanha_da_usuaria), db: Session = Depends(get_db)):
    """Números da conta + quais esta campanha usa + grupos que dependem de cada um."""
    instancias, selecionados, contagem = _servico(db).numeros_da_campanha(campanha)
    return {
        "numeros": [
            {
                "id": i.id,
                "nome_exibicao": i.nome_exibicao,
                "numero": _mascarar(i.numero),
                "status": i.status,
                "selecionado": i.id in selecionados,
                "grupos_na_campanha": contagem.get(i.id, 0),
            }
            for i in instancias
        ]
    }


@router.put("/{campanha_id}/numeros", response_model=NumerosDaCampanhaOut)
def definir_numeros(
    payload: list[int],
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    try:
        servico.definir_numeros(campanha, payload)
    except NumeroInvalido:
        raise HTTPException(status_code=404, detail="Número não encontrado.")
    except NumeroEmUso as e:
        # 409 explicado, no mesmo formato do conflito de anúncios: dizer só
        # "não pode" deixa a afiliada sem o próximo passo. Aqui o passo é
        # remover os grupos listados na aba Grupos.
        travas = "; ".join(
            f'{numero} (grupos: {", ".join(grupos)})'
            for numero, grupos in sorted(e.grupos_por_numero.items())
        )
        raise HTTPException(
            status_code=409,
            detail=(f"Estes números ainda têm grupos nesta campanha: {travas}. "
                    f"Remova os grupos na aba Grupos antes de desmarcar."),
        )
    return listar_numeros(campanha=campanha, db=db)


# --- Visão geral (spec §1.3) -------------------------------------------------


@router.get("/{campanha_id}/visao-geral", response_model=VisaoGeralOut)
def visao_geral(
    dias: int = 7,
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    """KPIs operacionais + entradas × saídas por dia. Sem métrica financeira."""
    from app.services.campanha_visao_geral_service import (
        DIAS_VALIDOS, CampanhaVisaoGeralService,
    )

    if dias not in DIAS_VALIDOS:
        raise HTTPException(
            status_code=422,
            detail=f"Período inválido. Use um de: {', '.join(map(str, DIAS_VALIDOS))}.",
        )
    return CampanhaVisaoGeralService(db).resumo(campanha, dias)


# --- link de entrada (F6) ----------------------------------------------------


@router.get("/{campanha_id}/link")
def obter_link(campanha=Depends(campanha_da_usuaria), db: Session = Depends(get_db)):
    """Cria na primeira visita — a afiliada não precisa 'gerar' nada."""
    from app.core.config import settings

    link = CampanhaLinkService(db).obter_ou_criar(campanha)
    # `settings.frontend_url` e não a env crua: sem valor explícito ele
    # deriva do ENVIRONMENT. O default fixo em produção fazia homologação
    # gerar link para `marketdash.com.br/g/{slug}`, onde a rota não existe.
    base = settings.frontend_url
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
    from datetime import date as _date, timedelta

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


def _contas_selecionadas(db: Session, user_id: int) -> list[str] | None:
    """
    Contas de anúncio marcadas em Configurações › Facebook Ads (spec §4.6).

    `None` quando não há integração — aí não filtra, para não esvaziar a lista
    de quem sincronizou antes de a seleção existir.
    """
    from app.repositories.facebook_integration_repository import (
        FacebookIntegrationRepository,
    )

    integracao = FacebookIntegrationRepository(db).get_by_user_id(user_id)
    return integracao.account_ids_list() if integracao else None


@router.get("/{campanha_id}/anuncios", response_model=AnunciosDaCampanhaOut)
def listar_anuncios(
    inicio: str | None = None,
    fim: str | None = None,
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """Campanhas de anúncio do Meta + quais estão vinculadas a ESTA campanha
    de grupos (o multi-select da tela), com gasto no período e veiculação real."""
    from datetime import date as _date, timedelta

    from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository
    from app.repositories.campaign_repository import CampaignRepository
    from app.services.campaign_service import (
        RECENT_ACTIVITY_WINDOW_DAYS, _is_active, _still_delivering,
    )

    repo = CampanhaAnuncioRepository(db)
    d_ini, d_fim = _periodo(inicio, fim)
    vinculadas = set(repo.campaign_ids(campanha.id))

    anuncios = repo.campanhas_de_anuncio(
        current_user.id, ad_account_ids=_contas_selecionadas(db, current_user.id)
    )
    # Anúncio JÁ VINCULADO nunca some da lista, mesmo que a conta dele tenha
    # sido desmarcada depois: senão a afiliada perde a única forma de
    # desvincular, e o gasto segue entrando no lucro sem ela poder ver por quê.
    faltando = vinculadas - {c.id for c in anuncios}
    if faltando:
        anuncios = anuncios + [
            c for c in repo.campanhas_de_anuncio(current_user.id) if c.id in faltando
        ]

    gastos = repo.gasto_por_campaign(current_user.id, [c.id for c in anuncios],
                                     d_ini, d_fim)
    # Veiculação real (spec §4.2): campanha com orçamento vitalício esgotado
    # fica ACTIVE para sempre na Meta. Mesma regra do card "campanhas ativas".
    recentes = CampaignRepository(db).campaign_ids_with_recent_activity(
        current_user.id,
        since=_date.today() - timedelta(days=RECENT_ACTIVITY_WINDOW_DAYS),
    )
    veiculando = {
        c.id: bool(_is_active(c) and not c.ad_review_issue
                   and _still_delivering(c, recentes))
        for c in anuncios
    }
    # Ordenado por gasto desc (spec §4.3): alfabético jogava "alvejantepo1805"
    # acima de campanha com R$800 gastos, e é a que gasta que ela quer vincular.
    anuncios = sorted(anuncios, key=lambda c: gastos.get(c.id, 0.0), reverse=True)
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
                "gasto": round(gastos.get(c.id, 0.0), 2),
                "veiculando": veiculando.get(c.id, False),
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
    inicio: str | None = None,
    fim: str | None = None,
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
    # Repassa o período: sem ele a resposta vinha com o gasto dos últimos 30
    # dias e reordenada, enquanto o chip da tela seguia marcando "7 dias" —
    # número de outra janela numa tela que decide investimento.
    return listar_anuncios(inicio=inicio, fim=fim, campanha=campanha,
                           current_user=current_user, db=db)


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
    grupos: str | None = None,
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """
    CSV de quem está NOS GRUPOS AGORA — não dos eventos de entrada.

    Colunas: telefone, grupo, data_entrada.

    **Mudou de conceito em 04/09.** Antes exportava as entradas dos últimos 30
    dias, e por isso um grupo com 946 pessoas acumuladas em meses exportava 8
    linhas — inútil para quem quer falar com os leads. A fonte agora é
    `grupo_participantes` (migration 080), que o sync mantém com quem está no
    grupo neste momento. Sem filtro de período: a lista é um retrato, não uma
    janela.

    `data_entrada` sai preenchida quando existe evento de entrada da pessoa
    (`grupo_eventos`, casado pelo mesmo HMAC) e, na falta dele, quando o sync
    viu a pessoa aparecer DEPOIS de já estar acompanhando o grupo. Quem já
    estava no grupo antes do primeiro sync sai sem data — não temos como
    inventá-la.

    `telefone` fica vazio para quem entrou com o número oculto: o WhatsApp
    entrega um LID, que é id opaco e não disca.

    `grupos` (opcional) = ids separados por vírgula; ausente exporta todos os
    grupos da campanha.
    """
    import csv
    import io

    from fastapi.responses import StreamingResponse

    from app.models.campanha_link import EVENTO_ENTRADA, GrupoEvento
    from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository
    from app.services.campanha_grupos_service import CampanhaGruposService

    pares = CampanhaGruposService(db).grupos_da_campanha(campanha)
    nomes = {g.id: (g.nome or f"Grupo {g.id}") for _v, g in pares}
    if not nomes:
        raise HTTPException(status_code=404, detail="A campanha não tem grupos.")

    # Seleção de grupos (spec §3.6). Validada contra os grupos da campanha: id
    # de fora vira 422, não um CSV silenciosamente vazio.
    if grupos is not None:
        try:
            pedidos = {int(p) for p in grupos.split(",") if p.strip()}
        except ValueError:
            raise HTTPException(status_code=422,
                                detail="Lista de grupos inválida.")
        estranhos = pedidos - set(nomes)
        if estranhos:
            raise HTTPException(
                status_code=422,
                detail="Há grupos que não pertencem a esta campanha na seleção.",
            )
        if not pedidos:
            raise HTTPException(status_code=422, detail="Selecione ao menos um grupo.")
        nomes = {gid: nome for gid, nome in nomes.items() if gid in pedidos}

    participantes = WhatsappGrupoRepository(db).participantes_de(list(nomes))

    # Data de entrada pelo evento, quando existe. Uma query para todos os
    # grupos: pedir por pessoa seria N+1 num grupo de 946.
    entrada_por_chave: dict = {}
    hashes = {p.identificador_hash for p in participantes if p.identificador_hash}
    if hashes:
        for gid, h, quando in (
            db.query(GrupoEvento.grupo_id, GrupoEvento.identificador_hash,
                     GrupoEvento.criado_em)
            .filter(GrupoEvento.grupo_id.in_(list(nomes)),
                    GrupoEvento.tipo == EVENTO_ENTRADA,
                    GrupoEvento.identificador_hash.in_(list(hashes)))
            .all()
        ):
            # A ENTRADA MAIS RECENTE: quem saiu e voltou está no grupo por
            # causa da última, não da primeira.
            atual = entrada_por_chave.get((gid, h))
            if atual is None or (quando and quando > atual):
                entrada_por_chave[(gid, h)] = quando

    # Fallback honesto: o sync só sabe a data de quem apareceu DEPOIS de já
    # estarmos acompanhando o grupo. O primeiro sync trouxe todo mundo de uma
    # vez, e para essa gente `visto_em` é a data do sync, não da entrada.
    primeiro_sync: dict = {}
    for p in participantes:
        atual = primeiro_sync.get(p.grupo_id)
        if atual is None or (p.visto_em and p.visto_em < atual):
            primeiro_sync[p.grupo_id] = p.visto_em

    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(["telefone", "grupo", "data_entrada"])
    for p in participantes:
        quando = entrada_por_chave.get((p.grupo_id, p.identificador_hash))
        if quando is None and p.visto_em and primeiro_sync.get(p.grupo_id):
            if p.visto_em > primeiro_sync[p.grupo_id]:
                quando = p.visto_em
        escritor.writerow([
            _seguro_para_planilha(p.telefone or ""),
            _seguro_para_planilha(nomes.get(p.grupo_id, "")),
            quando.isoformat() if quando else "",
        ])
    buffer.seek(0)
    # Nome de arquivo ASCII e fixo: header HTTP é codificado em latin-1 pelo
    # Starlette e um emoji no nome da campanha (rotineiro) derruba a request
    # com UnicodeEncodeError — 500 sem mensagem, sem saída para a usuária.
    nome_arquivo = f"participantes-campanha-{campanha.id}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
    )
