from datetime import timedelta, datetime, timezone

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.security import verify_password, get_password_hash, create_access_token
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.cakto_service import check_active_subscription, CaktoError
from app.services.email_service import EmailService


class AuthService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

    def register(self, user_data) -> User:
        existing = self.user_repo.get_by_email(user_data.email)
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email já cadastrado")

        hashed = get_password_hash(user_data.password)
        new_user = User(
            name=user_data.name,
            cpf_cnpj=user_data.cpf_cnpj,
            email=user_data.email,
            hashed_password=hashed,
            is_active=True,
        )
        return self.user_repo.create(new_user)

    def login(self, email: str, password: str):
        import logging
        from supabase import create_client, Client
        logger = logging.getLogger(__name__)

        # 1. Tentar login direto no Supabase Auth (caso já migrado)
        try:
            supabase_anon: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
            auth_response = supabase_anon.auth.sign_in_with_password({"email": email, "password": password})
            
            if auth_response and auth_response.session:
                logger.info(f"Login Supabase bem-sucedido para {email}")
                # Buscar usuário local para retornar junto
                user = self.user_repo.get_by_email(email)
                return {
                    "access_token": auth_response.session.access_token,
                    "token_type": "bearer",
                    "user": user
                }
        except Exception as e:
            logger.info(f"Login Supabase falhou para {email} (usuário pode não estar migrado): {e}")

        # 2. Fallback: Validar contra banco local (Legacy)
        user = self.user_repo.get_by_email(email)
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email ou senha incorretos",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuário inativo")

        # 3. Validar assinatura (Cakto)
        if settings.CAKTO_ENFORCE_SUBSCRIPTION:
            try:
                has_access, reason = check_active_subscription(user.email)
                if not has_access:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=reason or "Assinatura ativa não encontrada",
                    )
            except CaktoError as e:
                if settings.ENVIRONMENT not in ("production"):
                    logger.warning(f"Cakto validation failed for {user.email} in {settings.ENVIRONMENT}: {str(e)}")
                else:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Falha ao validar assinatura. Tente novamente.",
                    )

        # 4. Migração Automática: Criar usuário no Supabase
        # IMPORTANTE: Requer SUPABASE_SERVICE_KEY para ignorar confirmação de email
        try:
            logger.info(f"Iniciando Lazy Migration para {email}")
            supabase_admin: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
            
            # Criar usuário via Admin API (isso define a senha e confirma o email automaticamente)
            admin_auth_response = supabase_admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True
            })
            
            if admin_auth_response:
                logger.info(f"Usuário {email} migrado para Supabase com sucesso.")
                
                # Agora logar como o usuário para obter um token de sessão normal
                supabase_anon: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
                auth_data = supabase_anon.auth.sign_in_with_password({"email": email, "password": password})
                
                return {
                    "access_token": auth_data.session.access_token,
                    "token_type": "bearer",
                    "user": user
                }
        except Exception as e:
            logger.error(f"Erro crítico na Lazy Migration para {email}: {e}")
            # Se a migração falhar (ex: usuário já existe mas a senha é diferente),
            # retornamos erro de autenticação normal para o usuário.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Erro na migração de conta. Por favor, use 'Esqueci minha senha'.",
            )

    def register_from_cakto(self, email: str, name: str = None, cpf_cnpj: str = None) -> User:
        """Cria usuário a partir dos dados do webhook do Cakto. Gera token para definir senha e envia email."""
        existing = self.user_repo.get_by_email(email)
        if existing:
            return existing  # Usuário já existe, retornar existente
        
        # Gerar token único para definir senha (32 caracteres)
        import secrets
        import string
        token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        
        # Definir expiração (24 horas)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        
        # Criar hash temporário (será substituído quando usuário definir senha)
        # Usar token como senha temporária para garantir que não possa fazer login sem definir senha
        temp_hash = get_password_hash(token)
        
        new_user = User(
            name=name,
            cpf_cnpj=cpf_cnpj,
            email=email,
            hashed_password=temp_hash,
            is_active=True,
            password_set_token=token,
            password_set_token_expires_at=expires_at,
        )
        user = self.user_repo.create(new_user)
        
        # Enviar email com link para definir senha
        import logging
        logger = logging.getLogger(__name__)
        try:
            email_service = EmailService()
            email_sent = email_service.send_set_password_email(
                user_email=email,
                user_name=name or "Usuário",
                token=token
            )
            if email_sent:
                logger.info(f"Email de definir senha enviado para: {email}")
            else:
                logger.error(f"Falha ao enviar email de definir senha para: {email}")
        except Exception as e:
            # Log do erro mas não falhar a criação do usuário
            logger.error(f"Erro ao enviar email de definir senha para {email}: {e}")
        
        return user
    
    def set_password(self, token: str, password: str) -> User:
        """Define senha do usuário usando token recebido por email."""
        import logging
        logger = logging.getLogger(__name__)
        
        # Buscar usuário pelo token
        user = self.user_repo.get_by_password_set_token(token)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token inválido ou expirado"
            )
        
        # Verificar se token não expirou
        if user.password_set_token_expires_at and user.password_set_token_expires_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token inválido ou expirado"
            )
        
        # Verificar força da senha
        if len(password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A senha deve ter no mínimo 8 caracteres"
            )
        
        # Atualizar senha
        hashed_password = get_password_hash(password)
        user.hashed_password = hashed_password
        
        # Remover token (usado apenas uma vez)
        user.password_set_token = None
        user.password_set_token_expires_at = None
        
        # Salvar alterações
        self.user_repo.update(user)
        
        logger.info(f"Senha definida com sucesso para usuário: {user.email}")
        
        return user
    
    def forgot_password(self, email: str) -> bool:
        """Solicita reset de senha. Gera token e envia email."""
        import logging
        import secrets
        import string
        logger = logging.getLogger(__name__)
        
        # Buscar usuário pelo email
        user = self.user_repo.get_by_email(email)
        
        # Por segurança, sempre retornar sucesso mesmo se email não existir
        # Isso previne enumeração de emails
        if not user:
            logger.warning(f"Tentativa de reset de senha para email não cadastrado: {email}")
            return True  # Retornar True para não revelar se email existe
        
        if not user.is_active:
            logger.warning(f"Tentativa de reset de senha para usuário inativo: {email}")
            return True  # Retornar True mesmo para usuário inativo
        
        # Gerar token único para reset de senha (32 caracteres)
        token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        
        # Definir expiração (24 horas)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        
        # Atualizar token no usuário
        user.password_set_token = token
        user.password_set_token_expires_at = expires_at
        self.user_repo.update(user)
        
        # Enviar email com link para resetar senha
        try:
            email_service = EmailService()
            email_sent = email_service.send_reset_password_email(
                user_email=email,
                user_name=user.name or "Usuário",
                token=token
            )
            if email_sent:
                logger.info(f"Email de reset de senha enviado para: {email}")
            else:
                logger.error(f"Falha ao enviar email de reset de senha para: {email}")
            return email_sent
        except Exception as e:
            logger.error(f"Erro ao enviar email de reset de senha para {email}: {e}")
            return False
