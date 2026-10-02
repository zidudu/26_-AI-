"""사이트 어댑터 등록소. 채널의 `site` 값으로 어댑터를 고릅니다."""
from __future__ import annotations

from .arca import ArcaSite
from .base import Site
from .naver_cafe import NaverCafeSite
from .dcinside import DCInsideSite

_SITES: dict[str, Site] = {ArcaSite.key: ArcaSite(), NaverCafeSite.key: NaverCafeSite(), DCInsideSite.key: DCInsideSite()}
DEFAULT_SITE = ArcaSite.key


def get_site(key: str | None) -> Site:
    if (key or DEFAULT_SITE) not in _SITES:
        raise ValueError('지원하지 않는 사이트입니다.')
    return _SITES[key or DEFAULT_SITE]


def all_sites() -> list[Site]:
    return list(_SITES.values())
