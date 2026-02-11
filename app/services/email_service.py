import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional
import logging
import base64

from jinja2 import Template
from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Serviço para envio de emails via SMTP."""
    
    def __init__(self):
        self.smtp_host = settings.SMTP_HOST
        self.smtp_port = settings.SMTP_PORT
        self.smtp_user = settings.SMTP_USER
        self.smtp_password = settings.SMTP_PASSWORD
        self.from_email = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
        self.from_name = settings.SMTP_FROM_NAME
        self.frontend_url = settings.FRONTEND_URL
        
    def _get_template_path(self, template_name: str) -> Path:
        """Retorna o caminho do template."""
        base_path = Path(__file__).parent.parent
        template_path = base_path / "templates" / "emails" / template_name
        return template_path
    
    def _load_logo_base64(self) -> Optional[str]:
        """Carrega a logo em base64 para embed no email."""
        try:
            base_path = Path(__file__).parent.parent
            logo_path = base_path / "assets" / "logo" / "logo.png"
            
            if logo_path.exists():
                with open(logo_path, "rb") as logo_file:
                    logo_data = logo_file.read()
                    logo_base64 = base64.b64encode(logo_data).decode("utf-8")
                    return f"data:image/png;base64,{logo_base64}"
            else:
                logger.warning(f"Logo não encontrada em: {logo_path}")
                return None
        except Exception as e:
            logger.error(f"Erro ao carregar logo: {e}")
            return None
    
    def _render_template(self, template_name: str, context: dict) -> str:
        """Renderiza um template HTML com o contexto fornecido."""
        template_path = self._get_template_path(template_name)
        
        if not template_path.exists():
            raise FileNotFoundError(f"Template não encontrado: {template_path}")
        
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()
        
        template = Template(template_content)
        return template.render(**context)
    
    def _send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """Envia email via SMTP."""
        if not self.smtp_user or not self.smtp_password:
            logger.error("Credenciais SMTP não configuradas")
            return False
        
        try:
            # Criar mensagem
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = f"{self.from_name} <{self.from_email}>"
            message["To"] = to
            
            # Adicionar conteúdo HTML
            html_part = MIMEText(html_content, "html", "utf-8")
            message.attach(html_part)
            
            # Adicionar conteúdo texto (opcional)
            if text_content:
                text_part = MIMEText(text_content, "plain", "utf-8")
                message.attach(text_part)
            
            # Conectar e enviar
            context = ssl.create_default_context()
            
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_email, [to], message.as_string())
            
            logger.info(f"Email enviado com sucesso para: {to}")
            return True
            
        except smtplib.SMTPException as e:
            logger.error(f"Erro SMTP ao enviar email para {to}: {e}")
            return False
        except Exception as e:
            logger.error(f"Erro inesperado ao enviar email para {to}: {e}")
            return False
    
    def send_set_password_email(self, user_email: str, user_name: str, token: str) -> bool:
        """Envia email com link para definir senha."""
        try:
            # Gerar URL do link
            set_password_url = f"{self.frontend_url}/auth/set-password?token={token}"
            
            # Carregar logo
            logo_base64 = self._load_logo_base64()
            
            # Contexto para o template
            context = {
                "user_name": user_name or "Usuário",
                "set_password_url": set_password_url,
                "logo_base64": logo_base64,
                "frontend_url": self.frontend_url,
            }
            
            # Renderizar template
            html_content = self._render_template("set_password.html", context)
            
            # Conteúdo texto alternativo
            text_content = f"""
Olá {user_name or 'Usuário'},

Bem-vindo ao MarketDash!

Para completar seu cadastro e acessar a plataforma, defina sua senha clicando no link abaixo:

{set_password_url}

Este link expira em 24 horas.

Se você não solicitou este email, pode ignorá-lo com segurança.

Atenciosamente,
Equipe MarketDash
"""
            
            # Enviar email
            subject = "Bem-vindo ao MarketDash - Defina sua senha"
            success = self._send_email(
                to=user_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content
            )
            
            return success
            
        except Exception as e:
            logger.error(f"Erro ao enviar email de definir senha para {user_email}: {e}")
            return False
    
    def send_reset_password_email(self, user_email: str, user_name: str, token: str) -> bool:
        """Envia email com link para resetar senha."""
        try:
            # Gerar URL do link (reutiliza a mesma página de set-password)
            reset_password_url = f"{self.frontend_url}/auth/set-password?token={token}"
            
            # Carregar logo
            logo_base64 = self._load_logo_base64()
            
            # Contexto para o template
            context = {
                "user_name": user_name or "Usuário",
                "reset_password_url": reset_password_url,
                "logo_base64": logo_base64,
                "frontend_url": self.frontend_url,
            }
            
            # Renderizar template
            html_content = self._render_template("reset_password.html", context)
            
            # Conteúdo texto alternativo
            text_content = f"""
Olá {user_name or 'Usuário'},

Recebemos uma solicitação para redefinir a senha da sua conta no MarketDash.

Para redefinir sua senha, clique no link abaixo:

{reset_password_url}

Este link expira em 24 horas.

Se você não solicitou a redefinição de senha, pode ignorar este email com segurança. Sua senha permanecerá a mesma.

Atenciosamente,
Equipe MarketDash
"""
            
            # Enviar email
            subject = "Redefinir Senha - MarketDash"
            success = self._send_email(
                to=user_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content
            )
            
            return success
            
        except Exception as e:
            logger.error(f"Erro ao enviar email de reset de senha para {user_email}: {e}")
            return False
    
    def send_welcome_back_email(self, user_email: str, user_name: str) -> bool:
        """Envia email de reativação/bem-vindo de volta para usuário existente."""
        try:
            # Gerar URL de login
            login_url = f"{self.frontend_url}/login"
            
            # Carregar logo
            logo_base64 = self._load_logo_base64()
            
            # Contexto para o template
            context = {
                "user_name": user_name or "Usuário",
                "login_url": login_url,
                "logo_base64": logo_base64,
                "frontend_url": self.frontend_url,
            }
            
            # Renderizar template
            html_content = self._render_template("welcome_back.html", context)
            
            # Conteúdo texto alternativo
            text_content = f"""
Olá {user_name or 'Usuário'},

Ótimas notícias! Sua assinatura do MarketDash foi reativada com sucesso!

Você já pode acessar a plataforma usando suas credenciais:

{login_url}

Bem-vindo de volta! Estamos felizes em tê-lo novamente conosco.

Atenciosamente,
Equipe MarketDash
"""
            
            # Enviar email
            subject = "Bem-vindo de volta ao MarketDash - Assinatura Reativada"
            success = self._send_email(
                to=user_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content
            )
            
            return success
            
        except Exception as e:
            logger.error(f"Erro ao enviar email de reativação para {user_email}: {e}")
            return False

    def send_feedback_email(
        self,
        data: dict,
        user_name: Optional[str] = None,
        user_email: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> bool:
        """
        Envia o conteúdo do feedback para o email de relacionamento (ex.: relacionamento@marketdash.com.br).
        data: objeto com os campos coletados no formulário (ex.: message, rating, category). Será exibido como chave: valor.
        """
        from app.core.config import settings
        to_email = getattr(settings, "FEEDBACK_EMAIL", None) or "relacionamento@marketdash.com.br"
        data_lines = []
        for k, v in (data or {}).items():
            val = v if v is None or isinstance(v, (str, int, float, bool)) else str(v)
            data_lines.append(f"{k}: {val}")
        data_text = "\n".join(data_lines) if data_lines else "(nenhum campo)"
        lines = [
            "Novo feedback recebido pelo MarketDash",
            "",
            "--- Dados do feedback ---",
            data_text,
            "",
            "--- Dados do usuário ---",
            f"Nome: {user_name or '(não informado)'}",
            f"Email: {user_email or '(não informado)'}",
            f"ID do usuário: {user_id if user_id is not None else '(não informado)'}",
        ]
        text_body = "\n".join(lines)
        data_html = "".join(
            f"<p><strong>{_escape_html(str(k))}:</strong> {_escape_html(str(v))}</p>"
            for k, v in (data or {}).items()
        ) or "<p>(nenhum campo)</p>"
        html_body = (
            "<h2>Novo feedback recebido pelo MarketDash</h2>"
            "<h3>Dados do feedback</h3>"
            f"{data_html}"
            "<h3>Dados do usuário</h3>"
            f"<p><strong>Nome:</strong> {name}<br><strong>Email:</strong> {email}<br><strong>ID:</strong> {uid}</p>"
        ).format(
            name=_escape_html(user_name or "(não informado)"),
            email=_escape_html(user_email or "(não informado)"),
            uid=user_id if user_id is not None else "(não informado)",
        )
        subject = "[MarketDash] Novo feedback"
        success = self._send_email(
            to=to_email,
            subject=subject,
            html_content=html_body,
            text_content=text_body,
        )
        return success


def _escape_html(s: str) -> str:
    """Escapa caracteres HTML para evitar injection no corpo do email."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
