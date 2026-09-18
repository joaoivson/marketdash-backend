from typing import List, Optional
from datetime import datetime, timezone, timedelta
import logging
import uuid
from app.models.custom_link import CustomLink
from app.schemas.custom_link import CustomLinkCreate, CustomLinkUpdate, SlugCheckResponse
from app.schemas.custom_link_insight import LinkInsightResponse, LinkInsightSeriesPoint
from app.repositories.custom_link_repository import CustomLinkRepository
from app.repositories.custom_link_event_repository import CustomLinkEventRepository
from app.utils.bot_detection import is_bot
from app.utils.tracking_dedup import should_count
from app.services import click_buffer
from app.core.config import settings

logger = logging.getLogger(__name__)


class CustomLinkService:
    def __init__(self, repository: CustomLinkRepository):
        self.repository = repository

    def get_user_links(self, user_id: int) -> List[CustomLink]:
        return self.repository.get_by_user(user_id)

    def get_link(self, link_id: int) -> Optional[CustomLink]:
        return self.repository.get(link_id)

    def get_link_by_slug(self, slug: str) -> Optional[CustomLink]:
        return self.repository.get_by_slug(slug)

    def check_slug(self, slug: str) -> SlugCheckResponse:
        existing = self.repository.get_by_slug(slug)
        if not existing:
            return SlugCheckResponse(available=True, suggested_slug=slug)

        random_suffix = uuid.uuid4().hex[:4]
        suggested = f"{slug}-{random_suffix}"
        return SlugCheckResponse(available=False, suggested_slug=suggested)

    MAX_LINKS_PER_USER = 30

    def _max_links(self, user_id: int) -> int:
        from app.core.plans import plan_limit, normalize_plan
        from app.repositories.subscription_repository import SubscriptionRepository

        sub = SubscriptionRepository(self.repository.db).get_by_user_id(user_id)
        return plan_limit(normalize_plan(sub.plan if sub else None), "links")

    def create_link(self, user_id: int, link_in: CustomLinkCreate) -> CustomLink:
        from app.core.plans import is_unlimited

        existing_links = self.repository.get_by_user(user_id)
        max_links = self._max_links(user_id)
        if not is_unlimited(max_links):
            if max_links <= 0:
                raise ValueError("PLANO_INSUFICIENTE: Links rastreáveis disponíveis no plano Pro")
            if len(existing_links) >= max_links:
                raise ValueError(f"Limite de {max_links} links atingido")

        if link_in.slug:
            existing = self.repository.get_by_slug(link_in.slug)
            if existing:
                random_suffix = uuid.uuid4().hex[:4]
                link_in.slug = f"{link_in.slug}-{random_suffix}"
        else:
            link_in.slug = uuid.uuid4().hex[:8]

        return self.repository.create(user_id, link_in)

    def update_link(self, link_id: int, user_id: int, link_in: CustomLinkUpdate) -> Optional[CustomLink]:
        link = self.repository.get(link_id)
        if not link or link.user_id != user_id:
            return None

        if link_in.slug and link_in.slug != link.slug:
            existing = self.repository.get_by_slug(link_in.slug)
            if existing:
                random_suffix = uuid.uuid4().hex[:4]
                link_in.slug = f"{link_in.slug}-{random_suffix}"

        slug_antigo = link.slug
        atualizado = self.repository.update(link, link_in)
        self._invalidar_cache(slug_antigo, getattr(atualizado, "slug", None))
        return atualizado

    def delete_link(self, link_id: int, user_id: int) -> bool:
        link = self.repository.get(link_id)
        if not link or link.user_id != user_id:
            return False

        slug = link.slug
        self.repository.delete(link_id)
        self._invalidar_cache(slug)
        return True

    @staticmethod
    def _invalidar_cache(*slugs: Optional[str]) -> None:
        """Remove a decisão de redirect em cache (ver _resolver_slug)."""
        from app.core.cache import get_client

        client = get_client()
        if client is None:
            return
        chaves = [f"link:redirect:{s}" for s in slugs if s]
        if not chaves:
            return
        try:
            client.delete(*chaves)
        except Exception as exc:
            logger.warning("Falha ao invalidar cache de redirect %s: %s", chaves, exc)

    def get_insight(
        self, link_id: int, user_id: int, granularity: str
    ) -> Optional[LinkInsightResponse]:
        """Insight de cliques de um link do usuário (ownership check).

        total_clicks vem do click_count (total verdadeiro); a série e os demais
        números vêm dos eventos (forward-only). day=últimos 14 dias, month=últimos
        6 meses. Retorna None se o link não existir ou não pertencer ao usuário.
        """
        link = self.repository.get(link_id)
        if not link or link.user_id != user_id:
            return None

        events = CustomLinkEventRepository(self.repository.db)
        now = datetime.now(timezone.utc)

        if granularity == "month":
            # Início do mês corrente menos 5 meses => janela dos últimos 6 meses
            # (5 anteriores + o corrente = 6 buckets). Cálculo por mês exato, não por
            # dias (31*5 atravessava a fronteira e gerava 7 buckets em vários meses).
            first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            m = first_of_month.month - 5
            y = first_of_month.year
            while m <= 0:
                m += 12
                y -= 1
            since = first_of_month.replace(year=y, month=m)
            label_fmt = "%Y-%m"
        else:
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            since = today - timedelta(days=13)  # hoje + 13 dias atrás = 14 dias
            label_fmt = "%Y-%m-%d"

        rows = events.series(link_id, granularity, since)
        series = [
            LinkInsightSeriesPoint(label=bucket.strftime(label_fmt), value=value)
            for bucket, value in rows
        ]

        last_click = events.last_click(link_id)
        first_click = events.first_click(link_id)

        # avg_per_day a partir dos eventos registrados: total de eventos / dias de cobertura.
        if first_click is not None:
            span_days = max(1, (now - first_click).days + 1)
            total_events = events.count_since(link_id, first_click)
            avg_per_day = round(total_events / span_days, 2)
        else:
            avg_per_day = 0.0

        return LinkInsightResponse(
            total_clicks=link.click_count or 0,
            last_click_at=last_click.isoformat() if last_click else None,
            avg_per_day=avg_per_day,
            granularity=granularity,
            series=series,
            series_started_at=first_click.isoformat() if first_click else None,
        )

    def _resolver_slug(self, slug: str) -> tuple[dict, Optional[CustomLink]]:
        """Decide o destino de um slug: {"url", "link_id", "user_id"} ou
        {"error", "status_code"}. A decisão fica em cache no Redis por
        CLIQUES_CACHE_LINK_S segundos, então o caminho quente do redirect não
        toca o Postgres (incidente de 18/09/2026). Efeito colateral aceito:
        desativar/expirar um link demora até esse tempo para valer.

        Devolve também o objeto CustomLink quando ele veio do banco, para o
        fallback de contagem síncrona não precisar de uma segunda consulta."""
        from app.core.cache import cache_get, cache_set

        chave = f"link:redirect:{slug}"
        cached = cache_get(chave)
        if isinstance(cached, dict) and ("url" in cached or "error" in cached):
            return cached, None

        link = self.repository.get_by_slug(slug)
        if not link:
            decisao = {"error": "Link não encontrado", "status_code": 404}
            cache_set(chave, decisao, settings.CLIQUES_CACHE_LINK_S)
            return decisao, None

        # Defesa extra: URL com espaço/quebra de linha nas pontas manda o comprador
        # para a página de erro da Shopee. O schema já limpa na criação; aqui cobre
        # registros antigos gravados antes do fix.
        target_url = (link.original_url or "").strip()

        if not link.is_active:
            decisao = {"error": "Este link está desativado", "status_code": 403}
        elif link.expires_at and link.expires_at < datetime.now(timezone.utc):
            decisao = {"error": "Este link expirou", "status_code": 410}
        else:
            decisao = {"url": target_url, "link_id": link.id, "user_id": link.user_id}
            # Cancelamento: links continuam 30 dias após assinatura_status=cancelada
            try:
                from app.repositories.subscription_repository import SubscriptionRepository

                sub = SubscriptionRepository(self.repository.db).get_by_user_id(link.user_id)
                if sub and (sub.assinatura_status or "").lower() == "cancelada" and not sub.is_active:
                    cutoff = sub.updated_at or sub.assinatura_vence_em or sub.expires_at
                    if cutoff:
                        if cutoff.tzinfo is None:
                            cutoff = cutoff.replace(tzinfo=timezone.utc)
                        if datetime.now(timezone.utc) > cutoff + timedelta(days=30):
                            decisao = {
                                "error": "Este link não está mais disponível",
                                "status_code": 410,
                            }
            except Exception:
                pass

        cache_set(chave, decisao, settings.CLIQUES_CACHE_LINK_S)
        return decisao, link

    def handle_redirect(
        self,
        slug: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
        purpose: str = "",
    ) -> dict:
        """
        Handle redirect for a given slug.
        Returns dict with 'url' on success, or 'error' and 'status_code' on failure.
        Clicks are ignored for bots, browser prefetch, and duplicate hits within 60s.

        O clique vai para o buffer no Redis (app/services/click_buffer.py) e é
        gravado em lote pelo worker. Só sem Redis cai no incremento atômico
        direto no banco (fix de 17/09/2026).
        """
        decisao, link = self._resolver_slug(slug.strip())
        if "error" in decisao:
            return {"error": decisao["error"], "status_code": decisao["status_code"]}

        target_url = decisao["url"]

        if "prefetch" in (purpose or "").lower():
            return {"url": target_url}

        if is_bot(user_agent):
            return {"url": target_url}

        link_id = decisao["link_id"]
        if not should_count("clk", link_id, ip, user_agent, 60):
            return {"url": target_url}

        if click_buffer.registrar(link_id, decisao["user_id"]):
            return {"url": target_url}

        # Fallback: Redis indisponível. Conta direto no banco, sem nunca deixar
        # a contagem impedir o redirecionamento.
        try:
            if link is None:
                link = self.repository.get_by_slug(slug.strip())
            if link is not None:
                self.repository.increment_click_count(link)
        except Exception as exc:
            try:
                self.repository.db.rollback()
            except Exception:
                pass
            logger.warning("Clique não contado no link %s: %s", link_id, exc)
        return {"url": target_url}
