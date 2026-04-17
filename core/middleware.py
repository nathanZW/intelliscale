import logging
import time
import uuid


request_logger = logging.getLogger('intelliscale.requests')


def _get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


class RequestResponseLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/static/'):
            return self.get_response(request)

        request_id = uuid.uuid4().hex[:12]
        started_at = time.perf_counter()
        user = getattr(request, 'user', None)
        username = user.get_username() if getattr(user, 'is_authenticated', False) else 'anonymous'

        request_logger.info(
            'web_request request_id=%s method=%s path=%s query=%s user=%s ip=%s',
            request_id,
            request.method,
            request.path,
            request.META.get('QUERY_STRING', ''),
            username,
            _get_client_ip(request),
        )

        try:
            response = self.get_response(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            request_logger.exception(
                'web_exception request_id=%s method=%s path=%s user=%s duration_ms=%s',
                request_id,
                request.method,
                request.path,
                username,
                duration_ms,
            )
            raise

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        request_logger.info(
            'web_response request_id=%s method=%s path=%s status=%s duration_ms=%s',
            request_id,
            request.method,
            request.path,
            getattr(response, 'status_code', 'unknown'),
            duration_ms,
        )
        response['X-Request-ID'] = request_id
        return response

