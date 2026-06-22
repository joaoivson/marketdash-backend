import logging
from logging.config import dictConfig


def configure_logging():
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": "INFO",
            },
        }
    )

    logging.getLogger("uvicorn").setLevel(logging.INFO)

    # Segurança: o httpx loga a URL COMPLETA de cada request em nível INFO — e isso
    # inclui o access_token do Facebook na query string (vazamento de credencial nos
    # logs, crítico com o app em App Review da Meta). Sobe esses loggers pra WARNING:
    # some o log de request (com o token); os logs da própria app (INFO) continuam.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
