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
            self.assertEqual(classify_message('广告', keyword, ai)['message'], '垃圾消息：关键词[广告]命中。')
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
        await save_ai(self.request, 'true', 'http://localhost:8000/v1/chat/completions', 'model', 'secret', 'false', 'token')
        await save_ai(self.request, 'true', 'http://localhost:8000/v1', 'model', '', 'false', 'token')
        self.assertEqual((await AISettings.get(id=1)).api_key, 'secret')
        self.assertTrue((await KeywordSettings.get(id=1)).enabled)
        html = (await antispam_page(self.request)).body.decode()
        self.assertNotIn('value="secret"', html)
        self.assertIn('id="spam-test-form"', html)
        await save_ai(self.request, 'false', '', '', '', 'true', 'token')
        self.assertEqual((await AISettings.get(id=1)).api_key, '')

    async def test_group_requirement_and_auth(self):
        await GroupConfig.all().delete()
        with self.assertRaises(HTTPException) as error:
            await save_keywords(self.request, 'true', '广告', 'token')
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            await save_ai(self.request, 'true', 'https://example.test/v1', 'test', '', 'false', 'token')
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
            await save_ai(self.request, 'true', 'file:///tmp/test', 'model', '', 'false', 'token')

    async def test_disabled_rules_without_group(self):
        await GroupConfig.all().delete()
        await save_keywords(self.request, 'false', '广告', 'token')
        result = await test_message(self.request, '这是广告', 'token')
        self.assertTrue(result['is_spam'])
        self.assertFalse((await KeywordSettings.get(id=1)).enabled)
        await save_ai(self.request, 'false', 'http://localhost:8000/v1', 'model', '', 'false', 'token')
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
        result = await save_ai(self.request, 'true', 'http://localhost/v1', ' model ', '', 'false', 'token')
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
