"""Welcome message templates for new companies.

Pure function — no DB / IO. Returns dict with greeting/waiting/ready/log keys.
"""


def welcome_msg(name: str, topic: str, agents: list, lang: str = 'ko') -> dict:
    """Build the localized welcome message bundle for a new company."""
    team = ', '.join(a['name'] for a in agents[1:])
    msgs = {
        'ko': {
            'greeting': (
                f"안녕하세요 마스터! 👋\n\n"
                f"저는 '{name}'의 CEO입니다.\n\n"
                f"주제: {topic}\n팀원: {team}\n\n"
                f"@멘션으로 팀원들에게 지시하실 수 있습니다. 무엇부터 시작할까요?"
            ),
            'waiting': '⏳ 에이전트를 준비하고 있습니다. 잠시만 기다려주세요...',
            'ready': '✅ 모든 에이전트 준비 완료! 대화를 시작하세요.',
            'log': f"🏢 '{name}' 프로젝트 시작. 주제: {topic}",
        },
        'en': {
            'greeting': (
                f"Hello Master! 👋\n\n"
                f"I'm the CEO of '{name}'.\n\n"
                f"Topic: {topic}\nTeam: {team}\n\n"
                f"Use @mention to instruct team members. What should we start with?"
            ),
            'waiting': '⏳ Preparing agents, please wait...',
            'ready': '✅ All agents are ready! You can start the conversation.',
            'log': f"🏢 '{name}' project started. Topic: {topic}",
        },
        'ja': {
            'greeting': (
                f"こんにちはマスター！👋\n\n"
                f"私は '{name}' のCEOです。\n\n"
                f"テーマ: {topic}\nチーム: {team}\n\n"
                f"@メンションでチームメンバーに指示できます。何から始めましょうか？"
            ),
            'waiting': '⏳ エージェントを準備しています。しばらくお待ちください...',
            'ready': '✅ 全エージェントの準備が完了しました！会話を開始できます。',
            'log': f"🏢 '{name}' プロジェクト開始。テーマ: {topic}",
        },
        'zh': {
            'greeting': (
                f"你好管理员！👋\n\n"
                f"我是 '{name}' 的CEO。\n\n"
                f"主题: {topic}\n团队: {team}\n\n"
                f"使用@提及来指示团队成员。我们从什么开始？"
            ),
            'waiting': '⏳ 正在准备代理，请稍等...',
            'ready': '✅ 所有代理已准备就绪！您可以开始对话了。',
            'log': f"🏢 '{name}' 项目启动。主题: {topic}",
        },
    }
    return msgs.get(lang, msgs['ko'])
