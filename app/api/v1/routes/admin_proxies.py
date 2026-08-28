"""
Pool de proxies das sessões WAHA — SÓ admin.

A afiliada não vê nada disto: para ela, proxy é capacidade de conexão. Quando
o pool esgota e `WHATSAPP_PROXY_OBRIGATORIO` está ligado, a tela de Números
mostra "sem capacidade, fale com o suporte" — sem a palavra proxy.

Nenhuma resposta daqui carrega usuário/senha do proxy (ver
`schemas/whatsapp_proxies.py`).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_admin
from app.core.config import settings
from app.core.encryption import encrypt_value
from app.db.session import get_db
from app.models.user import User
from app.models.whatsapp_grupos import INSTANCIA_REMOVIDA, WhatsappInstancia
from app.models.whatsapp_proxies import PROXY_OK, TIPOS, WhatsappProxy
from app.repositories.whatsapp_proxy_repository import WhatsappProxyRepository
from app.schemas.whatsapp_proxies import (
    InstanciaProxyOut, PoolOut, ProxyAtualizar, ProxyCriar, ProxyOut,
    ProxyVerificarOut, RealocarProxyIn, RealocarProxyOut,
)
from app.services import proxy_pool_service
from app.services.waha_client import ErroWhatsapp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-proxies"])


def _proxy_out(p: WhatsappProxy, ocupacao: dict, usuarias: dict) -> ProxyOut:
    return ProxyOut(
        id=p.id, rotulo=p.rotulo, tipo=p.tipo, servidor=p.servidor,
        pais=p.pais, max_sessoes=p.max_sessoes,
        ocupacao=ocupacao.get(p.id, 0), ativo=p.ativo, status=p.status,
        falhas_seguidas=p.falhas_seguidas or 0,
        tem_credencial=bool(p.usuario_cifrado or p.senha_cifrada),
        ultimo_erro=p.ultimo_erro, ultimo_ip=p.ultimo_ip,
        ultimo_pais=p.ultimo_pais, verificado_em=p.verificado_em,
        usuarias=len(usuarias.get(p.id, set())), criado_em=p.criado_em,
    )


def _instancia_out(db: Session, i: WhatsappInstancia, proxies: dict) -> InstanciaProxyOut:
    from app.services.waha_client import mascarar

    proxy = proxies.get(i.proxy_id)
    dono = db.query(User).filter(User.id == i.user_id).first()
    return InstanciaProxyOut(
        id=i.id, user_id=i.user_id, user_email=getattr(dono, "email", None),
        nome_exibicao=i.nome_exibicao,
        numero_mascarado=mascarar(i.numero) if i.numero else None,
        status=i.status, proxy_id=i.proxy_id,
        proxy_rotulo=getattr(proxy, "rotulo", None),
        proxy_status=getattr(proxy, "status", None),
        proxy_fixado_em=i.proxy_fixado_em, proxy_trocas=i.proxy_trocas or 0,
        em_cooldown=proxy_pool_service.em_cooldown(i),
    )


def _obter(db: Session, proxy_id: int) -> WhatsappProxy:
    proxy = WhatsappProxyRepository(db).por_id(proxy_id)
    if proxy is None:
        raise HTTPException(status_code=404, detail="Proxy não encontrado.")
    return proxy


@router.get("/proxies", response_model=PoolOut)
def listar_pool(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Pool + as sessões, do ângulo do IP. As duas listas vêm juntas porque a
    pergunta do admin é sempre a mesma: 'quem está em qual IP?'."""
    repo = WhatsappProxyRepository(db)
    proxies = repo.listar()
    ocupacao = repo.contagem_de_sessoes()
    usuarias = repo.usuarias_por_proxy()
    por_id = {p.id: p for p in proxies}
    instancias = (
        db.query(WhatsappInstancia)
        .filter(WhatsappInstancia.status != INSTANCIA_REMOVIDA)
        .order_by(WhatsappInstancia.user_id, WhatsappInstancia.id)
        .all()
    )
    return PoolOut(
        proxies=[_proxy_out(p, ocupacao, usuarias) for p in proxies],
        instancias=[_instancia_out(db, i, por_id) for i in instancias],
        ligado=proxy_pool_service.habilitado(),
        obrigatorio=bool(settings.WHATSAPP_PROXY_OBRIGATORIO),
    )


@router.post("/proxies", response_model=ProxyOut, status_code=201)
def criar_proxy(payload: ProxyCriar, _: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    if payload.tipo not in TIPOS:
        raise HTTPException(status_code=422,
                            detail=f"Tipo inválido. Use um de: {', '.join(TIPOS)}.")
    proxy = WhatsappProxy(
        rotulo=payload.rotulo.strip(),
        tipo=payload.tipo,
        host=payload.host.strip(),
        porta=payload.porta,
        pais=payload.pais.upper(),
        max_sessoes=payload.max_sessoes or settings.WHATSAPP_PROXY_MAX_SESSOES,
        # Credencial cifrada ANTES de tocar o banco (Fernet, mesma chave dos
        # tokens da Shopee). Em claro ela apareceria em backup e em `pg_dump`.
        usuario_cifrado=encrypt_value(payload.usuario) if payload.usuario else None,
        senha_cifrada=encrypt_value(payload.senha) if payload.senha else None,
    )
    repo = WhatsappProxyRepository(db)
    repo.criar(proxy)
    logger.info("Proxy %s cadastrado (%s, %s)", proxy.id, proxy.rotulo, proxy.tipo)
    return _proxy_out(proxy, repo.contagem_de_sessoes(), repo.usuarias_por_proxy())


@router.patch("/proxies/{proxy_id}", response_model=ProxyOut)
def atualizar_proxy(proxy_id: int, payload: ProxyAtualizar,
                    _: User = Depends(require_admin), db: Session = Depends(get_db)):
    proxy = _obter(db, proxy_id)
    if payload.tipo is not None and payload.tipo not in TIPOS:
        raise HTTPException(status_code=422,
                            detail=f"Tipo inválido. Use um de: {', '.join(TIPOS)}.")
    for campo in ("rotulo", "tipo", "host", "porta", "max_sessoes", "ativo"):
        valor = getattr(payload, campo)
        if valor is not None:
            setattr(proxy, campo, valor.strip() if isinstance(valor, str) else valor)
    if payload.pais is not None:
        proxy.pais = payload.pais.upper()
    # String vazia = "este proxy deixou de exigir autenticação"; ausente =
    # "não mexa na credencial". Sem essa distinção, editar o rótulo apagaria a
    # senha do proxy — e a sessão só falharia na próxima mensagem.
    if payload.usuario is not None:
        proxy.usuario_cifrado = encrypt_value(payload.usuario) if payload.usuario else None
    if payload.senha is not None:
        proxy.senha_cifrada = encrypt_value(payload.senha) if payload.senha else None
    if payload.reativar_status:
        proxy.status = PROXY_OK
        proxy.falhas_seguidas = 0
        proxy.ultimo_erro = None
    repo = WhatsappProxyRepository(db)
    repo.salvar(proxy)
    return _proxy_out(proxy, repo.contagem_de_sessoes(), repo.usuarias_por_proxy())


@router.delete("/proxies/{proxy_id}", status_code=status.HTTP_204_NO_CONTENT)
def desativar_proxy(proxy_id: int, _: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """Soft-delete: `ativo=false`. Apagar a linha zeraria o `proxy_id` das
    sessões (ON DELETE SET NULL) e perderíamos o histórico de qual IP atendeu
    qual chip — que é o dado da investigação quando um número cai.

    As sessões que já estão nele CONTINUAM nele: tirá-las daqui seria trocar
    o IP de vários números de uma vez, exatamente o que não pode acontecer.
    """
    proxy = _obter(db, proxy_id)
    WhatsappProxyRepository(db).desativar(proxy)
    ocupadas = WhatsappProxyRepository(db).contagem_de_sessoes().get(proxy_id, 0)
    if ocupadas:
        logger.warning("Proxy %s desativado com %s sessão(ões) ainda nele — "
                       "elas seguem no IP até realocação explícita",
                       proxy_id, ocupadas)
    return None


@router.post("/proxies/{proxy_id}/verificar", response_model=ProxyVerificarOut)
def verificar_proxy(proxy_id: int, _: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """Bate no eco de IP através deste proxy, agora, e atualiza o status.

    Síncrono de propósito: é um clique com resposta esperada na tela, e o
    timeout já é curto (`WHATSAPP_PROXY_HEALTH_TIMEOUT_S`)."""
    from app.tasks.proxy_tasks import verificar_proxy as sondar

    proxy = _obter(db, proxy_id)
    ok, ip, pais, detalhe = sondar(db, proxy)
    if ok:
        proxy_pool_service.registrar_sucesso(db, proxy, ip=ip, pais=pais)
    else:
        proxy_pool_service.registrar_falha(db, proxy, detalhe)
    return ProxyVerificarOut(ok=ok, ip=ip, pais=pais, detalhe=detalhe,
                             status=proxy.status)


@router.post("/instancias/{instancia_id}/realocar-proxy",
             response_model=RealocarProxyOut)
def realocar_proxy(instancia_id: int, payload: RealocarProxyIn,
                   _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Troca o IP de UMA sessão. Operação rara, registrada e confirmada na tela.

    Não existe "realocar em massa" de propósito: trocar o IP de vários números
    ao mesmo tempo é o padrão que se quer evitar. Migração de chips já pareados
    é um por dia por usuária (plano §6.4).
    """
    from app.services.whatsapp_instancia_service import (
        EnvioEmAndamento, aplicar_proxy_na_sessao,
    )

    instancia = (
        db.query(WhatsappInstancia)
        .filter(WhatsappInstancia.id == instancia_id,
                WhatsappInstancia.status != INSTANCIA_REMOVIDA)
        .first()
    )
    if instancia is None:
        raise HTTPException(status_code=404, detail="Número não encontrado.")

    try:
        novo = proxy_pool_service.realocar(
            db, instancia, motivo=payload.motivo.strip(),
            ignorar_cooldown=payload.ignorar_cooldown,
        )
    except proxy_pool_service.TrocaEmCooldown as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ErroWhatsapp as e:
        # `realocar` já soltou a vaga antiga com um flush: sem o rollback, a
        # sessão poderia sair daqui sem proxy nenhum — pior do que o IP ruim.
        db.rollback()
        raise HTTPException(status_code=409, detail=proxy_pool_service.MENSAGEM_SEM_PROXY
                            if e.motivo == "sem_proxy" else "Falha ao realocar.")
    if novo is None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Não há outro IP livre com vaga para este número.",
        )
    db.commit()

    aplicado, aviso = False, None
    if payload.aplicar_na_sessao:
        try:
            aplicar_proxy_na_sessao(db, instancia)
            aplicado = True
        except EnvioEmAndamento as e:
            aviso = str(e)
        except ErroWhatsapp as e:
            logger.warning("Proxy novo não aplicado na sessão %s: %s",
                           instancia.nome_instancia, e.motivo)
            aviso = ("O IP novo ficou registrado, mas a sessão não pôde ser "
                     "reiniciada agora. Ela continua no IP antigo até reiniciar.")
    else:
        aviso = ("IP novo registrado. A sessão só passa a usá-lo quando for "
                 "reiniciada — marque 'aplicar agora' se quiser fazer isso já.")
    return RealocarProxyOut(proxy_id=novo.id, proxy_rotulo=novo.rotulo,
                            aplicado_na_sessao=aplicado, aviso=aviso)
