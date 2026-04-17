import json
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MAX_LOG_VALUE_LENGTH = 1000
SENSITIVE_KEYS = {
    'api_key',
    'apikey',
    'authorization',
    'cookie',
    'csrfmiddlewaretoken',
    'password',
    'secret',
    'session',
    'sessionid',
    'token',
    'x-api-key',
}


def truncate_value(value, max_length=MAX_LOG_VALUE_LENGTH):
    text = str(value)
    if len(text) <= max_length:
        return text
    return f'{text[:max_length]}...<truncated>'


def _is_sensitive_key(key):
    key_text = str(key).lower()
    return key_text in SENSITIVE_KEYS or any(
        marker in key_text for marker in ('password', 'secret', 'token', 'auth')
    )


def sanitize_data(value):
    if isinstance(value, Mapping):
        cleaned = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                cleaned[key] = '<redacted>'
            else:
                cleaned[key] = sanitize_data(item)
        return cleaned

    if isinstance(value, (list, tuple, set)):
        return [sanitize_data(item) for item in value]

    if isinstance(value, bytes):
        try:
            return truncate_value(value.decode('utf-8'))
        except UnicodeDecodeError:
            return f'<{len(value)} bytes>'

    if value is None:
        return None

    return truncate_value(value)


def sanitize_headers(headers):
    if not headers:
        return {}
    return sanitize_data(dict(headers))


def sanitize_url(url):
    if not url:
        return ''

    split_url = urlsplit(url)
    sanitized_query = []
    for key, value in parse_qsl(split_url.query, keep_blank_values=True):
        sanitized_query.append((key, '<redacted>' if _is_sensitive_key(key) else value))

    return urlunsplit(
        (
            split_url.scheme,
            split_url.netloc,
            split_url.path,
            urlencode(sanitized_query, doseq=True),
            split_url.fragment,
        )
    )


def dumps_for_log(value):
    return truncate_value(json.dumps(sanitize_data(value), default=str, ensure_ascii=True))


def summarize_request_payload(kwargs):
    if 'json' in kwargs and kwargs['json'] is not None:
        return dumps_for_log(kwargs['json'])

    if 'data' in kwargs and kwargs['data'] is not None:
        return dumps_for_log(kwargs['data'])

    return ''


def summarize_response_body(response):
    if response is None:
        return ''

    if getattr(response, 'request', None) and getattr(response.request, 'stream', False):
        return '<streamed response>'

    content_type = (response.headers.get('Content-Type') or '').lower()

    try:
        if 'application/json' in content_type:
            return dumps_for_log(response.json())
    except (ValueError, TypeError):
        pass

    try:
        return truncate_value(response.text)
    except Exception:
        return '<unavailable>'


def summarize_sql(sql):
    compact_sql = ' '.join(str(sql).split())
    return truncate_value(compact_sql, max_length=600)


def summarize_params(params):
    return dumps_for_log(params)

