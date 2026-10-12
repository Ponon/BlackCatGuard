import unittest
from services.member_flow import join_actions, verification_success_actions, execute_actions


class MemberFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_join_switch_combinations(self):
        self.assertEqual(join_actions(False, False), ())
        self.assertEqual(join_actions(False, True), ('send_welcome',))
        self.assertEqual(join_actions(True, False), ('start_verification',))
        self.assertEqual(join_actions(True, True), ('start_verification',))

    async def test_success_deletes_before_welcome(self):
        calls = []
        async def delete(): calls.append('delete')
        async def welcome(): calls.append('welcome')
        await execute_actions(verification_success_actions(True), {'delete_verification': delete, 'send_welcome': welcome})
        self.assertEqual(calls, ['delete', 'welcome'])
        self.assertEqual(verification_success_actions(False), ('delete_verification',))

    async def test_delete_failure_does_not_send_welcome(self):
        calls = []
        async def delete(): raise RuntimeError('delete failed')
        async def welcome(): calls.append('welcome')
        with self.assertRaises(RuntimeError):
            await execute_actions(verification_success_actions(True), {'delete_verification': delete, 'send_welcome': welcome})
        self.assertEqual(calls, [])
