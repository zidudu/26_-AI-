"""DC Inside minor galleries, grounded in the aichatting public pages (2026-09-28).

Comments use the same read-only /board/comment/ POST as the official comment.js.
Opaque page tokens are consumed from that page only and never logged.
"""
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urljoin, parse_qs, urlencode
from lxml import html as lxml_html
from lxml import etree

from ..common import sha256_text
from ..parsers.article_page import _body_text
from ..parsers.page_state import PageState, OK, classify
from .base import ArticleData, ArticleParseError, CommentPage, ListParse, ListRowData, Request, SitePolicy

KST = timezone(timedelta(hours=9))

def nodes(root, name):
    return root.xpath(f'.//*[contains(concat(" ",normalize-space(@class)," ")," {name} ")]')

def text(root, name):
    found = nodes(root, name)
    return found[0].text_content().strip() if found else ''

def number(value):
    match = re.search(r'\d[\d,]*', str(value or ''))
    return int(match[0].replace(',', '')) if match else 0

def date_iso(value):
    value = value.replace('.', '-').strip() if isinstance(value, str) else ''
    try:
        return datetime.fromisoformat(value).replace(tzinfo=KST).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None

def root_of(result):
    try:
        return lxml_html.fromstring(result.html)
    except (ValueError, etree.ParserError):
        raise ArticleParseError('EMPTY_RESPONSE', '빈 응답입니다.') from None


def item_id(value):
    """댓글/글 번호만 허용합니다. 숫자가 섞인 문자열이나 0은 ID가 아닙니다."""
    raw = str(value)
    return raw if re.fullmatch(r'[1-9][0-9]{0,11}', raw) else None

class DCInsideSite:
    key = 'dcinside'
    label = '디시인사이드 (마이너 갤러리)'
    login_url = 'https://sign.dcinside.com/login'
    input_hint = 'https://gall.dcinside.com/mgallery/board/lists/?id=aichatting'
    policy = SitePolicy(min_page_delay=2, page_delay_jitter=1, max_articles_per_run=50)

    def normalize_channel_input(self, value):
        parts = urlsplit(value.strip())
        if parts.hostname != 'gall.dcinside.com' or parts.scheme != 'https' or not parts.path.startswith('/mgallery/board/'):
            raise ValueError('디시 마이너 갤러리의 HTTPS 목록 주소를 입력하세요.')
        gallery = parse_qs(parts.query).get('id', [''])[0]
        if not re.fullmatch(r'[a-zA-Z0-9_]{1,50}', gallery):
            raise ValueError('갤러리 ID가 올바르지 않습니다.')
        return {'slug': 'dc-'+gallery, 'site_channel_id': gallery, 'name': None, 'category': None}

    def channel_url(self, channel):
        return 'https://gall.dcinside.com/mgallery/board/lists/?' + urlencode({'id': channel['site_channel_id']})

    def internal_article_id(self, channel, remote_id):
        if item_id(remote_id) is None:
            raise ValueError('Invalid DC item ID')
        return int(channel['id']) * 10**12 + int(remote_id)

    internal_comment_id = internal_article_id

    def list_request(self, channel, page):
        return Request(self.channel_url(channel) + '&page=' + str(page))

    def article_request(self, channel, article):
        return Request('https://gall.dcinside.com/mgallery/board/view/?' + urlencode({'id':channel['site_channel_id'], 'no':article['remote_id']}))

    def classify(self, result, kind):
        if kind == 'form' and result.status == 200:
            return OK
        state = classify(result.status, result.final_url, result.html, result.headers)
        if state.kind != 'ok':
            return state
        doc = root_of(result)
        if not nodes(doc, 'write_div') and not nodes(doc, 'gall_list'):
            visible = doc.text_content()
            if any(t in visible for t in ('삭제되었거나 존재하지 않는', '삭제된 게시물', '게시물이 없습니다')):
                return PageState('deleted','ARTICLE_DELETED','삭제되었거나 존재하지 않는 글입니다.',False,False)
            if any(t in visible for t in ('접근이 제한', '접근할 수 없', '차단되었습니다')):
                return PageState('restricted','DC_RESTRICTED','사이트에서 접근을 제한했습니다.',False,False)
        return OK

    def parse_list(self, result, channel):
        root = root_of(result)
        if not nodes(root,'gall_list'):
            raise ArticleParseError('LIST_STRUCTURE','디시 글 목록 표를 찾지 못했습니다.')
        rows = []
        for tr in nodes(root, 'ub-content'):
            remote = tr.get('data-no', '')
            if item_id(remote) is None:
                continue
            titles = nodes(tr, 'gall_tit')
            links = titles[0].xpath('./a[contains(@href,"/view/")]') if titles else []
            if not links:
                continue
            writer = nodes(tr,'gall_writer')
            dates = nodes(tr,'gall_date')
            rows.append(ListRowData(remote_id=remote,
                url=self.article_request(channel,{'remote_id':remote}).url, title=links[0].text_content().strip(),
                category=text(tr,'gall_subject') or None, author=writer[0].get('data-nick') if writer else None,
                created_at=date_iso(dates[0].get('title')) if dates else None,
                view_count=number(text(tr,'gall_count')), like_count=number(text(tr,'gall_recommend')),
                comment_count=number(text(tr,'reply_num')), is_notice='공지' in text(tr,'gall_num') or tr.get('data-type')=='icon_notice'))
        page_number = number(parse_qs(urlsplit(result.url).query).get('page',['1'])[0]) or 1
        page_links = root.xpath('//*[contains(@class,"bottom_paging_box")]//a/@href')
        has_next = any(number(parse_qs(urlsplit(urljoin(result.url,u)).query).get('page',['0'])[0]) > page_number for u in page_links)
        title = root.xpath('string(//title)')
        name = title.split(' - ')[0].strip() or None
        return ListParse(rows,has_next,channel_name=name)

    def media_hosts(self, configured):
        return ['dcinside.com','dcinside.co.kr']

    def session_cookie_names(self):
        return ()

    def parse_article(self, result, article, channel, media_hosts, parse_comments):
        root = root_of(result)
        bodies, titles = nodes(root,'write_div'), nodes(root,'title_subject')
        identity = root.xpath('//input[@id="no"]/@value')
        if identity and identity[0] != str(article['remote_id']):
            raise ArticleParseError('WRONG_ARTICLE','요청한 글 번호와 응답이 다릅니다.')
        if not bodies or not titles:
            raise ArticleParseError('ARTICLE_STRUCTURE','디시 제목 또는 본문을 찾지 못했습니다.')
        body = bodies[0]
        media, seen, warnings = [], set(), []
        for node in body.xpath('.//img|.//video|.//iframe'):
            urls = node.xpath('./source/@src') if node.tag == 'video' else []
            src = node.get('data-src') or node.get('src') or (urls[0] if urls else '')
            if not src:
                continue
            src = urljoin(result.url, src)
            parts = urlsplit(src)
            host = parts.hostname or ''
            allowed = parts.scheme in ('http','https') and any(host == h or host.endswith('.'+h) for h in media_hosts)
            kind = 'video' if node.tag == 'video' else 'image'
            if node.tag == 'iframe' or not allowed:
                kind = 'external'
                warnings.append('DC_EMBEDDED_MEDIA_UNSUPPORTED')
            if parts.path.lower().endswith('.gif'):
                kind = 'gif'
            file_no = node.get('data-fileno')
            source_key = 'dcinside:file:'+file_no if file_no else 'dcinside:url:'+sha256_text(src)
            if source_key in seen:
                continue
            seen.add(source_key)
            media.append({'seq':len(media)+1,'kind':kind,'source_key':source_key,'served_url':src,'original_url':None,
                          'poster_url':node.get('poster'),'origin':'body'})
        head = nodes(root,'gallview_head')[0] if nodes(root,'gallview_head') else root
        writers, dates = nodes(head,'gall_writer'), nodes(head,'gall_date')
        body_html = lxml_html.tostring(body,encoding='unicode')
        body_text = _body_text(body)
        count_text = root.xpath('string(//input[@id="comment_cnt"]/@value)').strip()
        return ArticleData(title=titles[0].text_content().strip(), body_html=body_html,body_text=body_text,
                           body_hash=sha256_text(body_html),category=text(head,'title_headtext').strip('[]'),
                           author=writers[0].get('data-nick') if writers else None,
                           created_at=date_iso(dates[0].get('title')) if dates else None,
                           view_count=number(text(head,'gall_count')),like_count=number(text(head,'gall_reply_num')),
                           comment_count=int(count_text) if re.fullmatch(r'[0-9]{1,12}', count_text) else None,
                           media=media,warnings=list(set(warnings)))

    def comments_request(self, result, article, channel, page):
        root = root_of(result)
        token = root.xpath('string(//input[@id="e_s_n_o"]/@value)')
        if not token:
            raise ArticleParseError('COMMENT_TOKEN','댓글 조회에 필요한 페이지 값이 없습니다.')
        gallery, remote = channel['site_channel_id'], str(article['remote_id'])
        return Request('https://gall.dcinside.com/board/comment/',kind='form',
                       headers={'Referer':result.url,'Origin':'https://gall.dcinside.com','X-Requested-With':'XMLHttpRequest',
                                'Accept':'application/json, text/javascript, */*; q=0.01'},
                       data={'id':gallery,'no':remote,'cmt_id':gallery,'cmt_no':remote,'e_s_n_o':token,'comment_page':str(page),
                             'sort':'D','prevCnt':'','focus_cno':'','focus_pno':'','_GALLTYPE_':'M','board_type':'',
                             'secret_article_key':'','clean':'','nptest':''})

    def parse_comments(self, result):
        try:
            data = json.loads(result.html)
        except ValueError:
            raise ArticleParseError('COMMENTS_FORMAT','댓글 응답이 JSON 형식이 아닙니다.') from None
        if not isinstance(data,dict) or 'comments' not in data or 'total_cnt' not in data:
            raise ArticleParseError('COMMENTS_FORMAT','댓글 목록 구조가 다릅니다.')
        total = str(data['total_cnt'])
        comments = data['comments']
        if not re.fullmatch(r'[0-9]{1,12}', total) or (comments is not None and not isinstance(comments, list)):
            raise ArticleParseError('COMMENTS_FORMAT','댓글 목록 또는 개수 형식이 다릅니다.')
        rows, invalid = [], 0
        for c in comments or []:
            if not isinstance(c, dict):
                invalid += 1
                continue
            # 실제 응답에는 댓글 뒤에 no=0인 부가 행이 들어옵니다. total_cnt에는 포함되지 않습니다.
            if str(c.get('no')) == '0':
                continue
            remote = item_id(c.get('no'))
            parent = item_id(c.get('c_no')) if number(c.get('depth')) else None
            if remote is None or (number(c.get('depth')) and parent is None):
                invalid += 1
                continue
            memo = c.get('memo') or ''
            if not isinstance(memo, str):
                invalid += 1
                continue
            try:
                body = lxml_html.fragment_fromstring(memo or ' ',create_parent='div')
            except (ValueError, etree.ParserError):
                invalid += 1
                continue
            rows.append({'remote_id':remote, 'parent_remote_id':parent,
                         'author':c.get('name') if isinstance(c.get('name'), str) else None, 'created_at':date_iso(c.get('reg_date')),
                         'body_html':memo,'body_text':body.text_content(),
                         'is_deleted':int(c.get('del_yn')=='Y' or str(c.get('is_delete'))=='1')})
        return CommentPage(rows, int(total), invalid)
