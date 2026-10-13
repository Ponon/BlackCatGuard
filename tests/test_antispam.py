"""广告拦截使用模拟 AI 响应，不向外部发送消息或消耗额度。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from fastapi import HTTPException
from starlette.requests import Request
from tortoise import Tortoise

from dao.models import Administrator, GroupConfig, KeywordSettings, AISettings
from main import app
from routers.antispam import save_keywords, save_ai, antispam_page, test_message
from services.antispam import classify_message, classify_ai, AIError, match_keyword


class DecisionTests(unittest.TestCase):
    def test_keyword_unicode_and_substring(self):
        self.assertEqual(match_keyword('GET FREE Coins', ['free']), 'free')
        self.assertEqual(match_keyword('限时优惠广告', ['广告']), '广告')
        self.assertIsNone(match_keyword('正常聊天', ['广告']))

    def test_or_logic_and_short_circuit(self):
        keyword = SimpleNamespace(enabled=True, keywords=['广告'])
        ai = SimpleNamespace(enabled=True, base_url='http://localhost/v1', model='test', api_key='')
        with patch('services.antispam.classify_ai') as classify:
            self.assertTrue(classify_message('广告', keyword, ai)['is_spam'])
            self.assertEqual(classify_message('广告', keyword, ai)['message'], '关键词：命中[广告]关键词，垃圾信息')
            classify.assert_not_called()
        for spam in (True, False):
            with patch('services.antispam.classify_ai', return_value={'is_spam': spam, 'reason': 'test'}):
                self.assertIs(classify_message('聊天', keyword, ai)['is_spam'], spam)
        with patch('services.antispam.classify_ai', side_effect=AIError('连接失败')):
            self.assertIsNone(classify_message('聊天', keyword, ai)['is_spam'])
        self.assertIsNone(classify_message('聊天', None, None)['is_spam'])

    def test_ai_request_proxy_and_strict_boolean(self):
        for value, valid in [(True, True), (False, True), ('false', False)]:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({'choices': [{'message': {'content': json.dumps({'is_spam': value, 'reason': '测试'})}}]}).encode()
            with patch('services.antispam.open_request', return_value=response) as opener:
                if valid:
                    self.assertIs(classify_ai('消息', 'https://example.test/v1', 'model', 'secret', 'http://localhost:7897')['is_spam'], value)
                else:
                    with self.assertRaises(AIError):
                        classify_ai('消息', 'https://example.test/v1', 'model', 'secret', 'http://localhost:7897')
                self.assertEqual(opener.call_args.kwargs['proxy_url'], 'http://localhost:7897')
                request = opener.call_args.args[0]
                self.assertEqual(request.full_url, 'https://example.test/v1/chat/completions')
                self.assertEqual(request.headers['Authorization'], 'Bearer secret')


    def test_http_errors_explain_status_without_exposing_response_secrets(self):
        from urllib.error import HTTPError
        from io import BytesIO
        for code in (400, 401, 403, 404, 422, 429, 500):
            error = HTTPError('https://example.test/v1/chat/completions', code, 'private response', {}, BytesIO(b'secret-key'))
            with patch('services.antispam.open_request', side_effect=error):
                with self.assertRaises(AIError) as caught:
                    classify_ai('消息', 'https://example.test/v1', 'minimax', 'secret-key')
            self.assertIn(f'HTTP {code}', str(caught.exception))
            self.assertNotIn('secret-key', str(caught.exception))


    def test_reasoning_prefix_is_not_treated_as_classification(self):
        from services.antispam import parse_ai_content
        final = '{"is_spam": false, "reason": "正常聊天"}'
        for text in (
            '<think>推理中有 {"is_spam": true, "reason": "草稿"}</think>\n' + final,
            '<think>分析</think>\n```json\n' + final + '\n```',
            '<think>第一段</think><think>第二段</think>' + final,
        ):
            self.assertEqual(parse_ai_content(text), {'is_spam': False, 'reason': '正常聊天'})
        for text in ('<think>' + final, '<think>只有思考</think>', '</think>' + final,
                     '<think>分析</think>{"is_spam": "false", "reason": "错误类型"}'):
            with self.assertRaises(ValueError):
                parse_ai_content(text)

    def test_reasoning_response_works_through_real_parser_without_network(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'choices': [{'message': {
            'content': '<think>分析测试文本</think>\n{"is_spam":true,"reason":"广告推广"}'}}]}).encode()
        with patch('services.antispam.open_request', return_value=response):
            self.assertEqual(classify_ai('测试', 'https://example.test/v1', 'MiniMax-M3', 'fake'),
                             {'is_spam': True, 'reason': '广告推广'})


    def test_classification_summary_identifies_source_and_ai_reason(self):
        keyword = SimpleNamespace(enabled=True, keywords=['广告'])
        ai = SimpleNamespace(enabled=True, base_url='https://example.test/v1', model='test', api_key='fake')
        reason = '典型的兜售大力丸广告，属于兜售来路不明的药品/保健品的商业推广'
        with patch('services.antispam.classify_ai', return_value={'is_spam': True, 'reason': reason}):
            self.assertEqual(classify_message('卖大力丸', keyword, ai)['message'], 'AI：' + reason + '，垃圾信息')
        with patch('services.antispam.classify_ai', return_value={'is_spam': False, 'reason': '正常聊天'}):
            self.assertEqual(classify_message('早上好', keyword, ai)['message'], '综合判定：正常消息')
        self.assertEqual(classify_message('早上好', keyword, None)['message'], '综合判定：正常消息')


class ConfigTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='test')
        await GroupConfig.create(id=1, bot_token='test', chat_id='-1001', revision='test')
        self.request = Request({'type': 'http', 'method': 'POST', 'path': '/antispam', 'headers': [], 'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router, 'scheme': 'http', 'server': ('localhost', 80), 'query_string': b''})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_actions_saved_validated_and_rendered(self):
        from routers.antispam import save_actions
        from dao.models import ModerationSettings
        await save_actions(self.request, 'all', 'true', 'false', 'token')
        saved = await ModerationSettings.get(id=1)
        self.assertEqual(saved.message_action, 'all')
        self.assertTrue(saved.restrict_user)
        html = (await antispam_page(self.request)).body.decode()
        self.assertIn('id="moderation-actions-form"', html)
        for args in [('invalid', 'true', 'false', 'token'), ('all', 'bad', 'false', 'token'), ('all', 'true', 'false', 'wrong')]:
            with self.assertRaises(HTTPException):
                await save_actions(self.request, *args)

    async def test_independent_save_and_key_preservation(self):
        result = await save_keywords(self.request, 'true', '广告\nFREE\nfree\n', 'token')
        self.assertEqual(result['keywords'], '广告\nFREE')
        await save_ai(self.request, 'true', 'http://localhost:8000/v1/chat/completions', 'model', 'secret', 'token')
        await save_ai(self.request, 'true', 'http://localhost:8000/v1', 'model', 'secret', 'token')
        self.assertEqual((await AISettings.get(id=1)).api_key, 'secret')
        self.assertTrue((await KeywordSettings.get(id=1)).enabled)
        html = (await antispam_page(self.request)).body.decode()
        self.assertIn('type="password" value="secret"', html)
        self.assertIn('id="toggle-ai-key"', html)
        self.assertIn('id="spam-test-form"', html)
        result = await save_ai(self.request, 'true', 'http://localhost:8000/v1', 'model', '', 'token')
        self.assertEqual((await AISettings.get(id=1)).api_key, '')
        self.assertFalse((await AISettings.get(id=1)).enabled)
        self.assertFalse(result['enabled'])
        self.assertNotIn('clear_api_key', html)

    async def test_group_requirement_and_auth(self):
        await GroupConfig.all().delete()
        with self.assertRaises(HTTPException) as error:
            await save_keywords(self.request, 'true', '广告', 'token')
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            await save_ai(self.request, 'true', 'https://example.test/v1', 'test', 'secret', 'token')
        self.request.session.clear()
        with self.assertRaises(HTTPException) as error:
            await save_keywords(self.request, 'false', '', 'token')
        self.assertEqual(error.exception.status_code, 401)

    async def test_invalid_config(self):
        with self.assertRaises(HTTPException):
            await save_keywords(self.request, 'true', '', 'token')
        with self.assertRaises(HTTPException):
            await save_keywords(self.request, 'true', '广告', 'wrong')
        with self.assertRaises(HTTPException):
            await save_ai(self.request, 'true', 'file:///tmp/test', 'model', '', 'token')

    async def test_disabled_rules_without_group(self):
        await GroupConfig.all().delete()
        await save_keywords(self.request, 'false', '广告', 'token')
        result = await test_message(self.request, '这是广告', 'token')
        self.assertTrue(result['is_spam'])
        self.assertFalse((await KeywordSettings.get(id=1)).enabled)
        await save_ai(self.request, 'false', 'http://localhost:8000/v1', 'model', '', 'token')
        with patch('services.antispam.classify_ai', return_value={'is_spam': True, 'reason': '广告'}):
            result = await test_message(self.request, '推广消息', 'token')
        self.assertTrue(result['is_spam'])
        self.assertFalse((await AISettings.get(id=1)).enabled)

    async def test_without_any_saved_rule(self):
        with self.assertRaises(HTTPException) as error:
            await test_message(self.request, '消息', 'token')
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn('请先保存关键词或完整的 AI 配置', error.exception.detail)

    async def test_saved_state_and_incomplete_group(self):
        response = await antispam_page(self.request)
        self.assertFalse(response.context['keyword_saved'])
        self.assertFalse(response.context['ai_saved'])
        result = await save_keywords(self.request, 'true', '广告', 'token')
        self.assertTrue(result['enabled'])
        result = await save_ai(self.request, 'true', 'http://localhost/v1', ' model ', '', 'token')
        self.assertEqual(result['model'], 'model')
        response = await antispam_page(self.request)
        self.assertTrue(response.context['keyword_saved'])
        self.assertTrue(response.context['ai_saved'])
        await GroupConfig.filter(id=1).update(chat_id='')
        response = await antispam_page(self.request)
        self.assertFalse(response.context['group_configured'])
        self.assertFalse(response.context['keyword_enabled'])
        self.assertFalse(response.context['ai_enabled'])
        with self.assertRaises(HTTPException) as error:
            await save_keywords(self.request, 'true', '广告', 'token')
        self.assertEqual(error.exception.status_code, 409)
