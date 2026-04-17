import shutil
import tempfile
from pathlib import Path

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.instrumentation import _is_lock_error
from core.logging_utils import sanitize_data, sanitize_url
from core.middleware import RequestResponseLoggingMiddleware
from users.models import CustomUser
from .views.settings import _parse_log_line


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


class LogViewerHelperTests(SimpleTestCase):
    def test_parse_log_line_extracts_timestamp_and_level(self):
        entry = _parse_log_line(
            'database',
            'Database',
            'WARNING 2026-04-17 11:00:00,123 [intelliscale.db] db_lock alias=default error=database is locked',
        )

        self.assertEqual(entry['source'], 'database')
        self.assertEqual(entry['source_label'], 'Database')
        self.assertEqual(entry['timestamp'], '2026-04-17 11:00:00,123')
        self.assertEqual(entry['level'], 'WARNING')


class LogViewerTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.override = override_settings(LOG_DIR=Path(self.temp_dir))
        self.override.enable()
        self.admin_user = CustomUser.objects.create_user(
            username='admin-user',
            password='password123',
            role='admin',
        )

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_admin_can_filter_logs(self):
        log_dir = Path(self.temp_dir)
        (log_dir / 'api.log').write_text(
            'INFO 2026-04-17 11:00:00,123 [intelliscale.api] api_request call_id=1 path=/api/one\n'
            'ERROR 2026-04-17 11:01:00,123 [intelliscale.api] api_error call_id=2 path=/api/two\n',
            encoding='utf-8',
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse('scale:log_viewer'),
            {'log': 'api', 'level': 'error', 'q': 'call_id=2', 'lines': 100},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'api_error')
        self.assertNotContains(response, 'api_request')
        self.assertContains(response, 'API Traffic')

    def test_missing_logs_render_cleanly(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('scale:log_viewer'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not available yet')
