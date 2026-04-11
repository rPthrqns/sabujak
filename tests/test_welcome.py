from prompts.welcome import welcome_msg


def _agents():
    return [
        {'name': 'CEO'}, {'name': 'CTO'}, {'name': 'CMO'},
    ]


def test_korean_default():
    m = welcome_msg('TestCo', '온라인 마케팅', _agents(), 'ko')
    assert 'TestCo' in m['greeting']
    assert 'CTO' in m['greeting']
    assert 'CMO' in m['greeting']
    assert '온라인 마케팅' in m['greeting']
    assert m['ready'].startswith('✅')


def test_english():
    m = welcome_msg('Acme', 'B2B SaaS', _agents(), 'en')
    assert 'Acme' in m['greeting']
    assert 'B2B SaaS' in m['greeting']
    assert m['ready'].startswith('✅')


def test_unknown_lang_falls_back_to_ko():
    m = welcome_msg('TestCo', 'topic', _agents(), 'fr')
    assert '안녕하세요' in m['greeting']  # Korean fallback
