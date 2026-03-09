import uuid
import logging
import re
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query
from app.api.v1.dependencies import get_current_user, get_supabase_client
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

def slugify(text: str) -> str:
    # A simple utility to sanitize filenames
    text = text.lower()
    text = re.sub(r'[^a-z0-9-]', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')

@router.post("/image")
async def upload_image(
    file: UploadFile = File(...),
    user_id: str = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Faz o upload de uma imagem para o bucket do Supabase.
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="O arquivo deve ser uma imagem.")

    # Validate file size - 5MB Limit
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    
    if file_size > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem muito grande. Limite de 5MB.")

    supabase = get_supabase_client()
    
    file_ext = file.filename.split(".")[-1] if "." in file.filename else "png"
    safe_filename = slugify(file.filename.rsplit(".", 1)[0]) if "." in file.filename else slugify(file.filename)
    # Format: captures/user_id/safe_name_uuid.ext
    # Ensure it's unique
    file_name = f"captures/{current_user.id}/{safe_filename}_{uuid.uuid4().hex[:8]}.{file_ext}"
    
    # Tentaremos buckets comuns do Supabase
    buckets_to_try = ["capturas", "images", "public", "avatars"]
    
    file_bytes = await file.read()
    
    success_bucket = None
    last_err = None
    
    for bucket in buckets_to_try:
        try:
            res = supabase.storage.from_(bucket).upload(
                path=file_name,
                file=file_bytes,
                file_options={"content-type": file.content_type, "x-upsert": "true"}
            )
            success_bucket = bucket
            break # Upload succeeded
        except Exception as e:
            logger.warning(f"Falha ao usar bucket '{bucket}'. Tentando próximo...")
            last_err = e
            
    if not success_bucket:
        logger.error(f"Erro ao fazer upload da imagem em qualquer bucket: {last_err}")
        raise HTTPException(status_code=500, detail="Falha ao fazer upload da imagem. Verifique se o bucket de storage existe no Supabase (tente criar bucket 'capturas' ou 'images').")

    try:
        # Retornar URL pública
        public_url = supabase.storage.from_(success_bucket).get_public_url(file_name)
        
        return {"url": public_url}
        
    except Exception as e:
        logger.error(f"Erro ao gerar URL pública da imagem: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao gerar URL da imagem.")
