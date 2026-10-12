"""不同 Telegram 方法的返回类型均经过校验。"""
import io
import json
import unittest
from unittest.mock import patch
from services.telegram import call_api, TelegramError


class TelegramResultTests(unittest.TestCase):
    def response(self, method, result):
        payload = io.BytesIO(json.dumps({'ok': True, 'result': result}).encode())
        with patch('services.telegram.open_request', return_value=payload):
            return call_api('fake-token', method)

    def test_update_array_and_boolean_operations(self):
        self.assertEqual(self.response('getUpdates', [{'update_id': 1}]), [{'update_id': 1}])
        for method in ['restrictChatMember', 'banChatMember', 'deleteMessage', 'answerCallbackQuery']:
            self.assertIs(self.response(method, True), True)
        self.assertEqual(self.response('sendMessage', {'message_id': 1}), {'message_id': 1})

    def test_invalid_return_types_are_rejected(self):
        for method, result in [('getUpdates', {}), ('sendMessage', True), ('deleteMessage', {})]:
            with self.subTest(method=method), self.assertRaises(TelegramError):
                self.response(method, result)

    def test_timeout_is_uncertain_and_connection_refused_is_safe_to_retry(self):
        from urllib.error import URLError
        for network_error, retryable, uncertain in [
            (TimeoutError(), False, True), (URLError(ConnectionRefusedError()), True, False),
        ]:
            with self.subTest(error=type(network_error)), patch('services.telegram.open_request', side_effect=network_error):
                with self.assertRaises(TelegramError) as caught:
                    call_api('fake-token', 'sendMessage')
                self.assertEqual(caught.exception.retryable, retryable)
                self.assertEqual(caught.exception.uncertain, uncertain)
                self.assertNotIn('fake-token', str(caught.exception))

    def test_rate_limit_preserves_retry_after(self):
        from urllib.error import HTTPError
        body = io.BytesIO(json.dumps({'parameters': {'retry_after': 7}}).encode())
        error = HTTPError('https://example.invalid', 429, 'limited', {}, body)
        with patch('services.telegram.open_request', side_effect=error), self.assertRaises(TelegramError) as caught:
            call_api('fake-token', 'sendMessage')
        self.assertTrue(caught.exception.retryable)
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(caught.exception.retry_after, 7)

    def test_idempotent_operations_retry_network_failure_but_send_does_not(self):
        from urllib.error import HTTPError
        for method in ['deleteMessage', 'deleteMessages', 'restrictChatMember', 'banChatMember', 'unbanChatMember']:
            for error in [TimeoutError(), HTTPError('https://example.invalid', 502, 'upstream', {}, io.BytesIO())]:
                with self.subTest(method=method, error=type(error)), patch('services.telegram.open_request', side_effect=error):
                    with self.assertRaises(TelegramError) as caught:
                        call_api('fake-token', method)
                    self.assertTrue(caught.exception.retryable)
                    self.assertFalse(caught.exception.uncertain)

    def test_latency_measures_one_telegram_request_through_configured_proxy(self):
        from services.telegram import inspect_group
        replies = [{'id': 1, 'first_name': '小猫'}, {'type': 'supergroup', 'title': '测试'}, {'status': 'creator'}]
        with patch('services.telegram.call_api', side_effect=replies) as api, patch('services.telegram.time.perf_counter', side_effect=[10.0, 10.456]):
            result = inspect_group('fake-token', '-1001', 'http://localhost:7897')
        self.assertTrue(result['ok'])
        self.assertEqual(result['latency_ms'], 456)
        self.assertEqual(api.call_count, 3)
        self.assertEqual(api.call_args_list[0].kwargs['proxy_url'], 'http://localhost:7897')
