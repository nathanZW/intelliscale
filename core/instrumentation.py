import logging
import time
import uuid

import requests
from django.db.backends.utils import CursorWrapper

from .logging_utils import (
    sanitize_headers,
    sanitize_url,
    summarize_params,
    summarize_request_payload,
    summarize_response_body,
    summarize_sql,
)


api_logger = logging.getLogger('intelliscale.api')
db_logger = logging.getLogger('intelliscale.db')

_REQUESTS_PATCHED = False
_DB_PATCHED = False


def install_runtime_instrumentation():
    install_requests_logging()
    install_database_logging()


def install_requests_logging():
    global _REQUESTS_PATCHED

    if _REQUESTS_PATCHED:
        return

    original_request = requests.sessions.Session.request
    if getattr(original_request, '_intelliscale_wrapped', False):
        _REQUESTS_PATCHED = True
        return

    def instrumented_request(self, method, url, **kwargs):
        call_id = f'api-{uuid.uuid4().hex[:12]}'
        started_at = time.perf_counter()
        sanitized_url = sanitize_url(url)
        sanitized_headers = sanitize_headers(kwargs.get('headers'))
        payload_preview = summarize_request_payload(kwargs)

        api_logger.info(
            'api_request call_id=%s method=%s url=%s headers=%s payload=%s',
            call_id,
            str(method).upper(),
            sanitized_url,
            sanitized_headers,
            payload_preview,
        )

        try:
            response = original_request(self, method, url, **kwargs)
        except requests.RequestException as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            api_logger.exception(
                'api_error call_id=%s method=%s url=%s duration_ms=%s error=%s',
                call_id,
                str(method).upper(),
                sanitized_url,
                duration_ms,
                exc,
            )
            raise

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        api_logger.info(
            'api_response call_id=%s method=%s url=%s status=%s duration_ms=%s headers=%s body=%s',
            call_id,
            str(method).upper(),
            sanitized_url,
            response.status_code,
            duration_ms,
            sanitize_headers(response.headers),
            summarize_response_body(response),
        )
        return response

    instrumented_request._intelliscale_wrapped = True
    requests.sessions.Session.request = instrumented_request
    _REQUESTS_PATCHED = True


def install_database_logging():
    global _DB_PATCHED

    if _DB_PATCHED:
        return

    _wrap_cursor_method('execute')
    _wrap_cursor_method('executemany')
    _DB_PATCHED = True


def _wrap_cursor_method(method_name):
    original_method = getattr(CursorWrapper, method_name)
    if getattr(original_method, '_intelliscale_wrapped', False):
        return

    def instrumented(self, sql, params=None):
        started_at = time.perf_counter()

        try:
            return original_method(self, sql, params)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            is_lock_error = _is_lock_error(exc)
            log_message = (
                'db_lock alias=%s vendor=%s operation=%s duration_ms=%s sql=%s params=%s error=%s'
                if is_lock_error
                else 'db_error alias=%s vendor=%s operation=%s duration_ms=%s sql=%s params=%s error=%s'
            )
            log_method = db_logger.warning if is_lock_error else db_logger.exception
            log_method(
                log_message,
                getattr(self.db, 'alias', 'default'),
                getattr(self.db, 'vendor', 'unknown'),
                method_name,
                duration_ms,
                summarize_sql(sql),
                summarize_params(params),
                exc,
            )
            raise

    instrumented._intelliscale_wrapped = True
    setattr(CursorWrapper, method_name, instrumented)


def _is_lock_error(exc):
    return 'locked' in str(exc).lower()
