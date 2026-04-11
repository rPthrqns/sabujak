"""Unit tests for parsers/commands.py — pure regex parsers."""
from parsers.commands import (
    parse_task_add, parse_task_done, parse_task_start, parse_task_block,
    parse_cron_add, parse_cron_del, parse_approval,
    extract_mentions, has_system_command, has_mention,
)


# ─── TASK_ADD ───
def test_task_add_basic():
    r = parse_task_add("[TASK_ADD:홈페이지 제작:high]")
    assert r == [{'title': '홈페이지 제작', 'priority': 'high'}]


def test_task_add_multiple():
    r = parse_task_add("[TASK_ADD:A:high] some text [TASK_ADD:B:low]")
    assert len(r) == 2
    assert r[0]['title'] == 'A'
    assert r[1]['priority'] == 'low'


def test_task_add_none():
    assert parse_task_add("그냥 평범한 메시지") == []


# ─── TASK_DONE / START / BLOCK ───
def test_task_done():
    assert parse_task_done("[TASK_DONE:홈페이지 제작]") == ['홈페이지 제작']


def test_task_start():
    assert parse_task_start("[TASK_START:DB 설계]") == ['DB 설계']


def test_task_block():
    r = parse_task_block("[TASK_BLOCK:로그인:OAuth 키 미발급]")
    assert r == [{'title': '로그인', 'reason': 'OAuth 키 미발급'}]


# ─── CRON ───
def test_cron_add():
    r = parse_cron_add("[CRON_ADD:일일보고:60:오늘 진행 상황 정리]")
    assert r == [{'title': '일일보고', 'interval': 60, 'prompt': '오늘 진행 상황 정리'}]


def test_cron_add_interval_int():
    r = parse_cron_add("[CRON_ADD:weekly:10080:weekly summary]")
    assert r[0]['interval'] == 10080
    assert isinstance(r[0]['interval'], int)


def test_cron_del():
    assert parse_cron_del("[CRON_DEL:일일보고]") == ['일일보고']


# ─── APPROVAL ───
def test_approval_3parts():
    r = parse_approval("[APPROVAL:예산:서버 구매:AWS 월 50불]")
    assert r == [{'category': '예산', 'title': '서버 구매', 'detail': 'AWS 월 50불'}]


def test_approval_2parts_defaults_to_general():
    r = parse_approval("[APPROVAL:디자인 시안:컨셉 A vs B]")
    assert r == [{'category': 'general', 'title': '디자인 시안', 'detail': '컨셉 A vs B'}]


def test_approval_english_category():
    r = parse_approval("[APPROVAL:budget:Server purchase:AWS $50/mo]")
    assert r[0]['category'] == 'budget'


def test_approval_unknown_category_falls_to_general():
    r = parse_approval("[APPROVAL:weird_cat:title:detail]")
    assert r[0]['category'] == 'general'
    assert r[0]['title'] == 'weird_cat'


def test_approval_multiple():
    r = parse_approval("[APPROVAL:예산:A:1] [APPROVAL:인사:B:2]")
    assert len(r) == 2


# ─── Mentions ───
def test_extract_mentions():
    assert extract_mentions("@CEO 할일 @CTO 부탁") == ['CEO', 'CTO']


def test_extract_mentions_none():
    assert extract_mentions("그냥 텍스트") == []


# ─── Combined predicates ───
def test_has_system_command():
    assert has_system_command("[TASK_ADD:foo:high]") is True
    assert has_system_command("[APPROVAL:예산:foo:bar]") is True
    assert has_system_command("[CRON_ADD:foo:60:bar]") is True
    assert has_system_command("그냥 텍스트") is False


def test_has_mention():
    assert has_mention("@CEO hello") is True
    assert has_mention("hello") is False


# ─── Unified [TASK:verb:args] format ───
def test_unified_task_add():
    r = parse_task_add("[TASK:add:홈페이지 제작:high]")
    assert r == [{'title': '홈페이지 제작', 'priority': 'high'}]


def test_unified_task_done():
    assert parse_task_done("[TASK:done:홈페이지 제작]") == ['홈페이지 제작']


def test_unified_task_start():
    assert parse_task_start("[TASK:start:DB 설계]") == ['DB 설계']


def test_unified_task_block():
    r = parse_task_block("[TASK:block:로그인:OAuth 키 미발급]")
    assert r == [{'title': '로그인', 'reason': 'OAuth 키 미발급'}]


def test_unified_cron_add():
    r = parse_cron_add("[CRON:add:일일보고:60:오늘 진행 상황 정리]")
    assert r == [{'title': '일일보고', 'interval': 60, 'prompt': '오늘 진행 상황 정리'}]


def test_unified_cron_del():
    assert parse_cron_del("[CRON:del:일일보고]") == ['일일보고']


def test_unified_and_legacy_mixed():
    """Both formats can coexist in the same response."""
    text = "[TASK_ADD:Old:high] and [TASK:add:New:low]"
    r = parse_task_add(text)
    assert len(r) == 2
    assert r[0]['title'] == 'Old'
    assert r[1]['title'] == 'New'


def test_unified_has_command():
    assert has_system_command("[TASK:add:foo:high]") is True
    assert has_system_command("[CRON:del:foo]") is True


def test_unified_task_add_default_priority():
    """Priority is optional in unified format."""
    r = parse_task_add("[TASK:add:Quick task]")
    assert r == [{'title': 'Quick task', 'priority': 'normal'}]
