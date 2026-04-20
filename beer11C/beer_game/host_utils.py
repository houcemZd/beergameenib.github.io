import ipaddress
from urllib.parse import urlparse


def normalize_host(value: str) -> str:
    """
    Accept bare hosts or URLs and return a Django ALLOWED_HOSTS-compatible host.
    """
    value = value.strip()
    if not value:
        return ''

    if value == '*' or value.startswith('.'):
        return value

    if '://' not in value:
        host = value.split('/', 1)[0].strip()
        if host.startswith('['):
            closing_bracket = host.find(']')
            if closing_bracket > 0 and (
                closing_bracket == len(host) - 1 or host[closing_bracket + 1] == ':'
            ):
                return host[1:closing_bracket]
            return ''
        if host.count(':') > 1:
            try:
                ipaddress.IPv6Address(host)
                return host
            except ValueError:
                return ''
        if host.count(':') == 1:
            return host.split(':', 1)[0].strip()
        return host

    parsed = urlparse(value)
    return parsed.hostname or ''
