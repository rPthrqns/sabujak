"""Task categorization (mirrors frontend PLAN_CAT logic in index.html).

Pure function: title → category key.
Loads keyword config from heuristics.json (with hardcoded fallback).
"""
import json
from pathlib import Path

# Hardcoded fallback (used if heuristics.json missing/broken).
# Order matters: more specific categories first; 'plan' is the fallback.
_DEFAULT_KEYWORDS = {
    'design': ['디자인', 'ui', 'ux', '시안', '로고', '이미지', 'design',
               'logo', 'wireframe', 'mockup', '프로토'],
    'market': ['마케팅', '홍보', 'sns', '광고', 'seo', '콘텐츠', '브랜딩',
               'marketing', 'branding', 'content', 'ads', '고객', '캠페인'],
    'dev': ['코딩', '개발', 'api', '서버', '프론트', '백엔드', 'db', '배포',
            'frontend', 'backend', 'deploy', 'server', 'database', 'coding',
            '버그', '테스트', '구현'],
    'ops': ['운영', '인사', '재무', '예산', '비용', '법률', '채용', 'hr',
            'finance', 'budget', 'legal', 'policy', '급여'],
    'plan': ['기획', '전략', '분석', '리서치', '조사', '계획', 'planning',
             'strategy', 'research', 'analysis', '보고', '정리'],
}


def _load_keywords():
    try:
        path = Path(__file__).parent / 'heuristics.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        kws = data.get('category_keywords')
        if kws and isinstance(kws, dict):
            return kws
    except (OSError, json.JSONDecodeError):
        pass
    return _DEFAULT_KEYWORDS


CATEGORY_KEYWORDS = _load_keywords()


def detect_category(title: str) -> str:
    """Return one of: dev, plan, market, design, ops. Default: plan."""
    if not title:
        return 'plan'
    low = title.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in low for k in kws):
            return cat
    return 'plan'
