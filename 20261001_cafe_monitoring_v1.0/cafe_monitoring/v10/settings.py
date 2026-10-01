"""Validate the UI contract again at the server boundary."""
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlsplit
import re
import unicodedata
from .common import Problem, addresses, parse_time, stamp, now

CAFE_DEFAULTS = [
    ('GN7','그랜저 GN7 · bestcm','bestcm'), ('MX5','싼타페 MX5 · iroid','iroid'),
    ('ME','아이오닉 · cafeclip','cafeclip'), ('제클','제네시스 전차종 · newgenesisdh','newgenesisdh'),
    ('RG3','G80 · fam100','fam100'), ('JX1','GV80 · gruu','gruu'), ('GL3','K8 · ite','ite'),
    ('KA4','카니발 · story77','story77'), ('MQ4','쏘렌토 · englishenglish','englishenglish')]
WORDS = ['SCC','전방카메라','경고등','후방','우측','제동','쏠림','전방 카메라','운전자 보조','크루즈','좌측','긴급','자율 주행','레이더','전방','컨트롤','계기판','보조']


def normalized(value):
    return unicodedata.normalize('NFKC', value).strip().lower()


def keyword_id(value):
    from urllib.parse import quote
    return 'keyword:' + quote(normalized(value), safe="~()*!.'-")


def defaults():
    cafes = [dict(id='cafe:'+s,code=c,name=n,url='https://cafe.naver.com/'+s,
                  active=True,naverCafeId=None,createdAt=stamp()) for c,n,s in CAFE_DEFAULTS]
    keys = [dict(id=keyword_id(w),name=w,active=True,deleted=False,createdAt=stamp(),deletedAt=None) for w in WORDS]
    return dict(schemaVersion=3,catalog={'cafes':cafes,'keywords':keys},keywords=list(WORDS),
        keywordIds=[k['id'] for k in keys],selectedCafeIds=[c['id'] for c in cafes],selectedCafes=[c['code'] for c in cafes],
        summaryCafes=deepcopy(cafes),range='최근 24시간',periodStart='',periodEnd='',periodDedupeEnabled=True,
        parallel='3',overlap='1',scheduleEnabled=True,scheduleTime='01:25',weekdaysOnly=True,
        provider='codex',ppi='125',summarySlide=True,rawNote=True,sendMail=True,mailTo='',mailCc='',
        mailSubject='자동차 동호회 모니터링 결과',mailScope='full',outputPath='output_v10',
        stepCollect=True,stepAnalyze=True,stepPpt=True,sourceRun='',evidence=True,humanReview=True)


def validate(data, *, for_run=False):
    if not isinstance(data, dict):
        raise Problem('설정은 JSON 객체여야 합니다.')
    cfg = deepcopy(data)
    allowed = set(defaults())
    if set(cfg) - allowed:
        raise Problem('지원하지 않는 설정 항목이 있습니다.')
    base = defaults(); base.update(cfg); cfg = base
    for key in ('periodDedupeEnabled','scheduleEnabled','weekdaysOnly','summarySlide','rawNote','sendMail','stepCollect','stepAnalyze','stepPpt'):
        if type(cfg[key]) is not bool:
            raise Problem(key + ' 값은 참/거짓이어야 합니다.')
    cfg.update(evidence=True,humanReview=True,schemaVersion=3)
    for key, opts in {'range':['최근 24시간','최근 48시간','이전 실행 이후','기간 직접 지정'],
                      'parallel':['1','2','3'],'overlap':['0','1','2'],'provider':['codex','openai'],
                      'ppi':['96','120','125','150'],'mailScope':['full','summary10']}.items():
        if str(cfg[key]) not in opts:
            raise Problem(key + ' 설정을 확인하세요.')
        cfg[key] = str(cfg[key])
    for key in ('periodStart','periodEnd','scheduleTime','mailTo','mailCc','mailSubject','outputPath','sourceRun'):
        if not isinstance(cfg[key],str) or len(cfg[key])>4000 or '\x00' in cfg[key]:
            raise Problem(key+' 형식을 확인하세요.')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',cfg['scheduleTime']):
        raise Problem('예약 시간을 HH:MM 형식으로 지정하세요.')
    catalog = cfg.get('catalog')
    if not isinstance(catalog,dict) or not all(isinstance(catalog.get(k),list) for k in ('cafes','keywords')):
        raise Problem('카페/키워드 목록을 확인하세요.')
    cafe_ids, codes, urls, normalized_keys, key_ids = set(),set(),set(),set(),set()
    for c in catalog['cafes']:
        if not isinstance(c,dict) or any(not isinstance(c.get(k),str) or not c[k].strip() for k in ('id','code','name','url')):
            raise Problem('카페의 ID·코드·이름·URL이 필요합니다.')
        if len(c['id'])>150 or len(c['name'])>200 or len(c['code'])>40 or type(c.get('active')) is not bool:
            raise Problem('카페 입력값을 확인하세요.')
        u=urlsplit(c['url']); slug=u.path.strip('/')
        if u.scheme!='https' or u.hostname not in ('cafe.naver.com','m.cafe.naver.com') or u.username or u.password or u.port or u.query or u.fragment or not re.fullmatch('[a-zA-Z0-9_-]{1,80}',slug) or slug.lower() in ('ca-fe','articles','article','cafes','mycafe','login'):
            raise Problem('네이버 카페 메인 주소를 입력하세요.')
        c['url']='https://cafe.naver.com/'+slug
        if c['id'] in cafe_ids or c['code'] in codes or c['url'] in urls:
            raise Problem('카페 ID·코드·URL은 중복될 수 없습니다.')
        cafe_ids.add(c['id']); codes.add(c['code']); urls.add(c['url'])
        if c.get('naverCafeId') is not None and not re.fullmatch('[1-9][0-9]{0,11}',str(c['naverCafeId'])):
            raise Problem('네이버 카페 번호를 확인하세요.')
    for k in catalog['keywords']:
        if not isinstance(k,dict) or not isinstance(k.get('name'),str) or not 1<=len(k['name'].strip())<=100 or k.get('id')!=keyword_id(k['name']):
            raise Problem('키워드 ID와 이름을 확인하세요.')
        norm=normalized(k['name'])
        if norm in normalized_keys or k['id'] in key_ids or type(k.get('active')) is not bool or type(k.get('deleted')) is not bool:
            raise Problem('키워드 중복/상태를 확인하세요.')
        normalized_keys.add(norm);key_ids.add(k['id'])
    if sum(c['active'] for c in catalog['cafes'])>100 or sum(not k['deleted'] for k in catalog['keywords'])>100 or len(catalog['cafes'])>10000 or len(catalog['keywords'])>10000:
        raise Problem('사용 중인 카페와 키워드는 각각 최대 100개입니다.')
    selected=cfg.get('selectedCafeIds')
    if not isinstance(selected,list) or not all(isinstance(i,str) for i in selected) or len(set(selected))!=len(selected):
        raise Problem('선택 카페 형식을 확인하세요.')
    active={c['id']:c for c in catalog['cafes'] if c['active']}
    if set(selected)-set(active):raise Problem('선택된 카페 중 수집이 중단된 카페가 있습니다.')
    selected_cafes=[active[i] for i in selected]
    active_keys=[k for k in catalog['keywords'] if k['active'] and not k['deleted']]
    cfg.update(keywords=[k['name'] for k in active_keys],keywordIds=[k['id'] for k in active_keys],
        selectedCafes=[c['code'] for c in selected_cafes],summaryCafes=deepcopy(selected_cafes))
    if cfg['range']=='기간 직접 지정' and cfg['stepCollect']:
        lo,hi=parse_time(cfg['periodStart']),parse_time(cfg['periodEnd'])
        if any(d.second or d.microsecond for d in (lo,hi)):
            raise Problem('수집 시작·종료 시각은 분 단위로 지정하세요. 초는 00이어야 합니다.')
        if lo>=hi or hi>now()+timedelta(minutes=1):raise Problem('시작 < 종료 ≤ 현재 시각으로 지정하세요.')
    if not cfg['outputPath'].strip():raise Problem('결과 폴더가 필요합니다.')
    if cfg['sendMail']:
        if not addresses(cfg['mailTo']):raise Problem('받는 사람(To)을 입력하거나 Outlook 발송을 끄세요.')
        addresses(cfg['mailCc'])
        if not cfg['stepPpt']:raise Problem('메일 발송에는 PPT 생성이 필요합니다.')
        if '\n' in cfg['mailSubject'] or '\r' in cfg['mailSubject']:raise Problem('메일 제목은 한 줄로 입력하세요.')
    if for_run:
        if not any(cfg[k] for k in ('stepCollect','stepAnalyze','stepPpt')):raise Problem('실행 작업을 선택하세요.')
        if cfg['stepCollect'] and (not selected or not active_keys):raise Problem('수집할 카페와 키워드가 필요합니다.')
        if cfg['stepCollect'] and cfg['stepPpt'] and not cfg['stepAnalyze']:raise Problem('새 수집 결과의 PPT에는 AI 분석을 함께 선택하세요.')
        if not cfg['stepCollect'] and not cfg['sourceRun']:raise Problem('재사용할 실행을 선택하세요.')
    return cfg


def next_schedule(cfg, at=None):
    if not cfg or not cfg.get('scheduleEnabled'):return None
    at=at or now();h,m=map(int,cfg['scheduleTime'].split(':'))
    candidate=at.replace(hour=h,minute=m,second=0,microsecond=0)
    if candidate<=at:candidate+=timedelta(days=1)
    while cfg.get('weekdaysOnly') and candidate.weekday()>=5:candidate+=timedelta(days=1)
    return stamp(candidate)
