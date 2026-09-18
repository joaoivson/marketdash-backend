"""Upload de mídia para o Supabase Storage.

O WAHA **baixa a URL** — não recebe bytes nossos (ver `_enviar_midia` em
`waha_client.py`). Então tudo que vai para um grupo passa por aqui primeiro e
vira URL pública: imagem, vídeo, nota de voz e arquivo.
"""
import uuid
import logging
import re
from typing import Dict, Tuple

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query

from app.api.v1.dependencies import get_current_user, get_supabase_service_client
from app.core.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

MB = 1024 * 1024

#: tipo de bloco → (prefixos de mimetype aceitos, teto em MB, extensão padrão)
#:
#: Os tetos vêm do `settings` e são declarados ANTES de o botão existir na
#: tela: sem limite, o upload trava sem mensagem e ela não sabe se o arquivo é
#: grande demais ou se o sistema quebrou.
#:
#: `arquivo` aceita qualquer mimetype de propósito — é o bloco de "manda o que
#: for", e restringir aqui só empurraria a afiliada para renomear extensão.
TIPOS: Dict[str, Tuple[Tuple[str, ...], int, str]] = {
    "imagem": (("image/",), settings.UPLOAD_MB_IMAGEM, "png"),
    "video": (("video/",), settings.UPLOAD_MB_VIDEO, "mp4"),
    # Áudio gravado no navegador chega como `audio/webm` (Chrome) ou
    # `video/webm` (alguns navegadores rotulam assim mesmo sem vídeo) — os dois
    # viram nota de voz, porque quem converte para OGG/Opus é o WAHA.
    "audio": (("audio/", "video/webm"), settings.UPLOAD_MB_AUDIO, "ogg"),
    "arquivo": ((), settings.UPLOAD_MB_ARQUIVO, "bin"),
}

# Buckets na ordem de tentativa — o primeiro que aceitar vence. Herdado do
# upload de imagem original: os ambientes não têm o mesmo bucket criado.
BUCKETS = ("capturas", "images", "public", "avatars")


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9-]', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def _validar(tipo: str, file: UploadFile, tamanho: int) -> None:
    prefixos, teto_mb, _ext = TIPOS[tipo]
    mimetype = (file.content_type or "").lower()
    if prefixos and not any(mimetype.startswith(p) for p in prefixos):
        raise HTTPException(
            status_code=400,
            detail=f"Esse arquivo não é {'uma imagem' if tipo == 'imagem' else f'um {tipo}'}.",
        )
    if tamanho > teto_mb * MB:
        raise HTTPException(
            status_code=400,
            detail=f"Arquivo muito grande. O limite para {tipo} é {teto_mb} MB.",
        )
    if tamanho == 0:
        raise HTTPException(status_code=400, detail="O arquivo está vazio.")


async def _subir(tipo: str, file: UploadFile, user: User) -> Dict[str, str]:
    if tipo not in TIPOS:
        raise HTTPException(status_code=400, detail="Tipo de arquivo inválido.")

    # Tamanho pelo seek, antes de ler: `await file.read()` de um vídeo de
    # 200 MB carregaria tudo na memória da API só para depois recusar.
    file.file.seek(0, 2)
    tamanho = file.file.tell()
    file.file.seek(0)
    _validar(tipo, file, tamanho)

    nome = file.filename or tipo
    ext = nome.rsplit(".", 1)[-1] if "." in nome else TIPOS[tipo][2]
    base = slugify(nome.rsplit(".", 1)[0] if "." in nome else nome) or tipo
    caminho = f"captures/{user.id}/{base}_{uuid.uuid4().hex[:8]}.{ext}"

    # Service role para contornar a RLS do bucket.
    supabase = get_supabase_service_client()
    conteudo = await file.read()

    bucket_ok = None
    ultimo_erro = None
    for bucket in BUCKETS:
        try:
            supabase.storage.from_(bucket).upload(
                path=caminho,
                file=conteudo,
                file_options={
                    "content-type": file.content_type or "application/octet-stream",
                    "x-upsert": "true",
                },
            )
            bucket_ok = bucket
            break
        except Exception as e:
            logger.warning("Upload falhou no bucket %r, tentando o próximo", bucket)
            ultimo_erro = e

    if not bucket_ok:
        logger.error("Upload falhou em todos os buckets: %s", ultimo_erro)
        raise HTTPException(
            status_code=500,
            detail="Não foi possível enviar o arquivo. Tente novamente ou fale com o suporte.",
        )

    try:
        return {"url": supabase.storage.from_(bucket_ok).get_public_url(caminho)}
    except Exception as e:
        logger.error("URL pública falhou: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Arquivo enviado, mas não foi possível gerar o link. Tente de novo.",
        )


@router.post("/midia")
async def upload_midia(
    file: UploadFile = File(...),
    tipo: str = Query("imagem", description="imagem | video | audio | arquivo"),
    current_user: User = Depends(get_current_user),
):
    """Sobe um arquivo de bloco e devolve a URL pública que o WAHA vai baixar."""
    return await _subir(tipo, file, current_user)


@router.post("/image")
async def upload_image(
    file: UploadFile = File(...),
    # ⚠️ NÃO renomeie para `user_id`: o `fetchWithAuth` do frontend injeta
    # `?user_id=user_N` em toda request e sobrescreveria o valor em silêncio.
    # O parâmetro sobrevive só por compatibilidade com o cliente antigo.
    user_id: str = Query(None),
    current_user: User = Depends(get_current_user),
):
    """Rota histórica, mantida porque a Captura de Site depende dela."""
    return await _subir("imagem", file, current_user)
