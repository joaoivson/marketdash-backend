import re

_BOT_PATTERN = re.compile(
    r"bot|crawler|spider|slurp|facebookexternalhit|whatsapp|telegrambot|"
    r"linkedinbot|twitterbot|pinterest|discordbot|googlebot|google-structured-data|"
    r"google-image|bingbot|duckduckbot|applebot|yandex|baiduspider|semrush|ahrefs|"
    r"mj12bot|headless|puppeteer|playwright|lighthouse|pagespeed|prerender|"
    r"preview|embedly|vkshare|w3c_validator|curl|wget|python-requests|python-urllib|"
    r"httpclient|go-http-client",
    re.IGNORECASE,
)


def is_bot(user_agent: str | None) -> bool:
    if user_agent is None:
        return True
    ua = user_agent.strip()
    if len(ua) < 5:
        return True
    return bool(_BOT_PATTERN.search(ua))
