"""成员入群与验证成功后的执行顺序约定。"""


def join_actions(verification_enabled: bool, welcome_enabled: bool) -> tuple[str, ...]:
    if verification_enabled:
        return ('start_verification',)
    return ('send_welcome',) if welcome_enabled else ()


def verification_success_actions(welcome_enabled: bool) -> tuple[str, ...]:
    # 无论欢迎语开关是否开启，都应先清理已完成的验证消息。
    return ('delete_verification', 'send_welcome') if welcome_enabled else ('delete_verification',)


async def execute_actions(actions, handlers):
    """逐步等待成功；删除验证消息失败时不越过该步骤发送欢迎语。"""
    for action in actions:
        await handlers[action]()
