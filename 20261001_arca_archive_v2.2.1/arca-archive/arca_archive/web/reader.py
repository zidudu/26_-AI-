"""Allowlisted archive reader. Only saved local media may load automatically."""
from html import escape
from urllib.parse import urljoin, urlsplit, parse_qsl
from lxml import html as lxml_html

TAGS = set('p div span br hr h1 h2 h3 h4 h5 h6 strong b em i u s del blockquote pre code ul ol li table thead tbody tr th td a figure figcaption'.split())
REMOVE = set('script style iframe object embed form input button textarea select link meta base svg math source'.split())

def display_kind(media):
    content_type = (media.get('content_type') or '').lower()
    path = (media.get('file_path') or '').lower()
    return 'video' if content_type.startswith('video/') or path.endswith(('.mp4', '.webm', '.mov', '.m4v')) else 'image'

def _key(url):
    parts = urlsplit(url or '')
    params = tuple(sorted((k, v) for k, v in parse_qsl(parts.query) if k not in ('expires', 'key', 'signature', 'type')))
    return (parts.hostname or '').replace('ac-o.arca.live', 'ac.arca.live'), parts.path, params

def render_body(html, article_url, media):
    lookup = {}
    for m in media:
        if m.get('state') != 'downloaded' or not m.get('local_url'):
            continue
        for field in ('served_url', 'original_url'):
            if m.get(field):
                lookup[_key(m[field])] = m
    if not html:
        return ''
    root = lxml_html.fragment_fromstring(html, create_parent='div')
    def render(node):
        if not isinstance(node.tag, str):
            return ''
        name = node.tag.lower()
        if name in REMOVE:
            return ''
        if name in ('img', 'video'):
            urls = [node.get(k) for k in ('src', 'data-src', 'data-originalurl')] + node.xpath('./source/@src')
            item = next((lookup[_key(urljoin(article_url, u))] for u in urls if u and _key(urljoin(article_url, u)) in lookup), None)
            if not item:
                return ' [미디어 미수집] '
            src = escape(item['local_url'], quote=True)
            if display_kind(item) == 'video':
                return f'<video class="reader-media" src="{src}" controls preload="metadata"></video>'
            return f'<img class="reader-media" src="{src}" alt="보관된 미디어" loading="lazy">'
        content = escape(node.text or '') + ''.join(render(child) + escape(child.tail or '') for child in node)
        if name not in TAGS:
            return content
        attrs = ''
        if name == 'a' and node.get('href'):
            href = urljoin(article_url, node.get('href'))
            if urlsplit(href).scheme in ('http', 'https'):
                attrs = f' href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
        if name in ('br', 'hr'):
            return f'<{name}>'
        return f'<{name}{attrs}>{content}</{name}>'
    return render(root)
