from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.instrumentation import _is_lock_error
from core.logging_utils import sanitize_data, sanitize_url
from core.middleware import RequestResponseLoggingMiddleware


class LoggingUtilityTests(SimpleTestCase):
    def test_sanitize_url_redacts_sensitive_query_values(self):
        url = 'https://example.com/api?token=abc123&page=2&api_key=secret'

        sanitized = sanitize_url(url)

        self.assertIn('token=%3Credacted%3E', sanitized)
        self.assertIn('api_key=%3Credacted%3E', sanitized)
        self.assertIn('page=2', sanitized)

    def test_sanitize_data_redacts_nested_sensitive_keys(self):
        payload = {
            'username': 'operator',
            'password': 'super-secret',
            'profile': {
                'token': 'abc123',
                'warehouse': 'A1',
            },
        }

        sanitized = sanitize_data(payload)

        self.assertEqual(sanitized['password'], '<redacted>')
        self.assertEqual(sanitized['profile']['token'], '<redacted>')
        self.assertEqual(sanitized['profile']['warehouse'], 'A1')

    def test_lock_error_detector_matches_sqlite_lock_messages(self):
        self.assertTrue(_is_lock_error(RuntimeError('database is locked')))
        self.assertFalse(_is_lock_error(RuntimeError('syntax error near SELECT')))


class RequestLoggingMiddlewareTests(SimpleTestCase):
    def test_middleware_adds_request_id_header(self):
        request = RequestFactory().get('/scale/')

        middleware = RequestResponseLoggingMiddleware(lambda req: HttpResponse('ok'))
        response = middleware(request)

        self.assertIn('X-Request-ID', response)
        self.assertEqual(response.status_code, 200)

