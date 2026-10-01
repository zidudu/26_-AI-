"""Asynchronous port. Generated at build time; original business rules retained."""
from urllib.parse import urlsplit
from v754.collect.page_guard import STATUS_JS, check_states
from v9.collection_control import check_stop

async def read_states(page):
    check_stop()
    states = []
    for frame in list(page.frames):
        host = urlsplit(frame.url).hostname or ''
        if host not in ('cafe.naver.com', 'nid.naver.com'):
            continue
        try:
            state = await frame.evaluate(STATUS_JS)
        except Exception as exc:
            if any((s in str(exc) for s in ('Execution context was destroyed', 'Frame was detached', 'Cannot find context'))):
                continue
            raise
        if not isinstance(state, dict):
            continue
        states.append((frame, state))
    return states

async def assert_session(page):
    check_states(page, await read_states(page))

async def assert_article_access(page):
    states = await read_states(page)
    check_states(page, states, article=True)
    return states
