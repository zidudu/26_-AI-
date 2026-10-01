"""Editable PowerPoint dashboard bars and text, using the existing A4 slide size.

The default renderer uses shapes and text only. Native charts remain an
explicit compatibility option; only that path accesses ChartData workbooks.
"""
import json
from v754.core import V7Error, wrap_text
from v754.powerpoint import SLIDE_W, SLIDE_H, text_bounds_fit
from v9.dashboard_data import axis, article_key

NAVY='153C64'; INK='0D2445'; GRAY='65788E'; LINE='D4DEEA'
STATE={'incomplete':'수집 미완료','not_selected':'수집 대상 아님','empty':'해당 기간 수집 게시글 없음',
       'analysis_review':'게시글 분석 확인 필요','not_generated':'저장된 카페 요약 없음',
       'failed':'카페 요약 생성 실패','unavailable':'요약 확인 필요'}

def rgb(value):
    return int(value[0:2],16) | int(value[2:4],16)<<8 | int(value[4:6],16)<<16

def coords(x,y,w,h):return (x*SLIDE_W/1600,y*SLIDE_H/900,w*SLIDE_W/1600,h*SLIDE_H/900)

class Canvas:
    def __init__(self,slide,cfg,kind,audit=None,guard=None,slide_name=None):
        from v9.dashboard_com import Guard
        self.com=guard or Guard()
        self.slide,self.font=self.com.wrap(slide,'slide'),cfg['font_name']
        self.slide_name=slide_name if slide_name is not None else str(self.slide.Name)
        self.renderer=cfg.get('v96_dashboard',{}).get('chart_renderer','shapes')
        if self.renderer not in ('shapes','native'):raise V7Error('DASHBOARD_CONFIG_ERROR','그래프 방식이 올바르지 않습니다.')
        self.manifest={'name':self.slide_name,'type':kind,'texts':{},'charts':[], 'links':[], 'colors':{}}
        self.count=0;self.audit=audit if audit is not None else []
    def name(self):
        self.count+=1;return 'V96_'+str(self.count)
    def rect(self,x,y,w,h,fill='FFFFFF',line=None,name=None):
        s=self.slide.Shapes.AddShape(1,*coords(x,y,w,h));s.Name=name or self.name()
        s.Fill.Solid();s.Fill.ForeColor.RGB=rgb(fill)
        s.Line.Visible=-1 if line else 0
        if line:s.Line.ForeColor.RGB=rgb(line);s.Line.Weight=0.5
        self.manifest['colors'][s.Name]=rgb(fill)
        return s
    def text(self,text,x,y,w,h,size=20,bold=False,color=INK,minimum=None,word_wrap=True):
        s=self.slide.Shapes.AddTextbox(1,*coords(x,y,w,h));s.Name=self.name()
        tf=s.TextFrame;tf.AutoSize=0;tf.WordWrap=-1 if word_wrap else 0
        tf.MarginLeft=tf.MarginRight=tf.MarginTop=tf.MarginBottom=0
        tf.VerticalAnchor=3
        tr=tf.TextRange;tr.Text=str(text).replace('\n','\r')
        tr.Font.Name=tr.Font.NameFarEast=self.font;tr.Font.Bold=-1 if bold else 0
        tr.Font.Color.RGB=rgb(color);tr.ParagraphFormat.SpaceBefore=0;tr.ParagraphFormat.SpaceAfter=0
        size=size*SLIDE_W/1600;floor=(minimum or size*1600/SLIDE_W)*SLIDE_W/1600
        tr.Font.Size=size
        while not text_bounds_fit(s):
            size-=0.25
            if size < floor-0.01:
                error=V7Error('DASHBOARD_TEXT_OVERFLOW','요약 양식의 글자 배치를 확인하세요: '+str(text)[:60])
                error.text_layout={'text':str(text),'shape':s.Name,'box_pt':[float(s.Left),float(s.Top),float(s.Width),float(s.Height)],
                    'bound_width_pt':float(tr.BoundWidth),'bound_height_pt':float(tr.BoundHeight),
                    'font_pt':float(tr.Font.Size),'word_wrap':int(tf.WordWrap)}
                raise error
            tr.Font.Size=size
        self.manifest['texts'][s.Name]=tr.Text
        return s
    def panel(self,title,x,y,w,h):
        self.rect(x,y,w,h,line=LINE);self.rect(x,y,w,54,NAVY)
        self.text(title,x+22,y+7,w-44,40,26,True,'FFFFFF',22)
    def legend(self,names,colors,x,y,w,cols,title):
        self.text(title,x,y,w,20,14,True,GRAY)
        step=w/cols
        for i,(name,color) in enumerate(zip(names,colors)):
            left=x+i%cols*step;top=y+26+i//cols*22
            self.rect(left,top+5,10,10,color)
            self.text(name,left+17,top,step-23,20,14,color=GRAY,minimum=12)
    def chart(self,names,values,colors,x,y,w,h):
        if self.renderer=='shapes':
            from v9.dashboard_bars import draw
            shape,item=draw(self,names,values,colors,x,y,w,h)
            self.manifest['charts'].append(item)
            return shape
        limits=axis(values)
        shape=self.slide.Shapes.AddChart2(-1,57,*coords(x,y,w,h),False)
        shape.Name=self.name();chart=shape.Chart
        from v9.dashboard_chart import populate_chart, verify_chart
        item={'name':shape.Name,'categories':names,'values':values,'colors':colors,'axis':limits}
        populate_chart(shape,item,self.font,self.slide_name,self.audit)
        verify_chart(shape.Chart,item)
        self.manifest['charts'].append({'name':shape.Name,'categories':names,'values':values,
                                        'colors':colors,'axis':limits})
        return shape

def period(row):
    def display(t):return str(t or '')[:16].replace('T',' ').replace('-','.')
    return display(row.get('start'))+' ~ '+display(row.get('end'))

def title(c,text,period_text):
    c.text(text,32,22,1510,62,40,True,minimum=30)
    c.text('수집 기간  '+period_text,35,88,1500,32,21,color=GRAY,minimum=17)

def kwchart(c,stats,counts,x,y,w,h):
    c.chart(stats['keywords'],counts,stats['keyword_colors'],x,y,w,h)

def short_title(text):
    lines=wrap_text(text,32)
    return '\n'.join(lines[:2])+('…' if len(lines)>2 else '')

def append_dashboard(deck,cfg,report,articles,targets):
    from v9.dashboard_com import Guard
    guard=Guard(report.setdefault('dashboard_com_audit',[]),'build')
    deck=guard.wrap(deck)
    stats=report['dashboard_stats'];summaries=report.get('cafe_summaries',{})
    canvases=[];bykey={article_key(a):a for a in articles}
    def new(kind,name):
        slide=deck.Slides.Add(deck.Slides.Count+1,12);slide.Name=name
        c=Canvas(slide,cfg,kind,report.setdefault('dashboard_chart_audit',[]),guard=guard,slide_name=name);canvases.append(c);return c
    active=[r for r in stats['cafes'] if r['selected']]
    intervals={(r['start'],r['end']) for r in active}
    when=period(active[0]) if len(intervals)==1 else '카페별 수집 기간은 다음 슬라이드 참고'
    c=new('overall','V96_OVERALL')
    c.text('자동차 동호회 모니터링 | 전체 수집 현황',32,22,1070,62,40,True,minimum=30)
    c.text('수집 기간  '+when,35,88,1070,32,21,color=GRAY,minimum=17)
    c.rect(1124,18,444,105,line=LINE)
    c.text('수집 대상 카페',1144,29,194,27,19,color=GRAY,minimum=17)
    c.text(str(stats['selected_cafes'])+'개',1144,65,194,38,34,True,'1E64AA')
    c.text('총 집계된 게시글 수',1350,29,200,27,19,color=GRAY,minimum=17)
    c.text(str(stats['total_posts'])+'건',1350,65,200,38,34,True,'1E64AA')
    c.panel('카페별 수집 게시글 수',32,139,750,747)
    c.panel('전체 키워드별 수집 게시글 수',802,139,766,747)
    labels=[r['name']+(' ('+STATE[r['state']]+')' if r['state']!='complete' else '') for r in stats['cafes']]
    c.chart(labels,[r['count'] for r in stats['cafes']],[r['color'] for r in stats['cafes']],46,208,718,547)
    kwchart(c,stats,stats['keyword_counts'],817,208,730,547)
    c.legend([r['name'] for r in stats['cafes']],[r['color'] for r in stats['cafes']],52,768,710,3,'카페 범례')
    c.legend(stats['keywords'],stats['keyword_colors'],822,768,720,6,'키워드 범례')
    c.text('수집 완료 카페 기준 집계 · 0건과 수집 미완료(—) 구분',52,864,710,19,14,color=GRAY,minimum=12)
    c.text('한 게시글은 여러 키워드에 중복 집계될 수 있음',820,864,730,19,14,color=GRAY,minimum=12)
    for row in (stats['cafes'] if cfg.get('v96_dashboard',{}).get('include_cafe_summaries',True) else []):
        c=new('cafe','V96_CAFE_'+row['slug'])
        title(c,row['name']+' | 모니터링 요약',period(row) if row['selected'] else '이번 실행 수집 대상 아님')
        c.text('수집 게시글 '+(str(row['count'])+'건' if row['count'] is not None else '—'),1180,93,370,30,22,True,'1E64AA')
        c.panel('키워드별 수집 게시글 수',32,145,716,741)
        kwchart(c,stats,row['keyword_counts'],45,211,684,534)
        c.legend(stats['keywords'],stats['keyword_colors'],52,768,678,6,'키워드 범례')
        c.text('한 게시글은 여러 키워드에 중복 집계될 수 있음',48,864,688,19,14,color=GRAY,minimum=12)
        c.panel('이번 수집 내용 요약',768,145,800,226)
        c.panel('대표 게시글',768,391,800,442)
        summary=summaries.get(row['slug'],{'state':'not_generated','bullets':[],'representatives':[]})
        state=summary['state']
        if state!='ready':
            c.text(STATE.get(state,'요약 확인 필요'),792,216,750,96,25,color=GRAY,minimum=22)
            c.text(STATE.get(state,'선정된 대표 게시글 없음'),792,465,750,90,25,color=GRAY,minimum=22)
        else:
            bullets=summary['bullets']
            if not bullets:c.text('요약할 주요 사례 없음',792,216,750,80,24,color=GRAY)
            for i,b in enumerate(bullets):c.text('- '+b['text'],792,207+i*49,750,47,22,minimum=19)
            reps=summary['representatives']
            if not reps:c.text('선정된 대표 게시글 없음',792,465,750,90,24,color=GRAY)
            for i,r in enumerate(reps):
                article=bykey[r['article_key']];y=459+i*122
                c.rect(783,y,770,111,line=LINE)
                c.text(short_title(article['title']),800,y+5,610,45,22,True,minimum=18)
                c.text(r['summary'],800,y+51,614,50,18,color=GRAY,minimum=16)
                button=c.rect(1431,y+33,104,38,line='3F79B1')
                label=c.text('상세 보기',1437,y+34,94,36,18,True,'1E64AA',16)
                for shape in (button,label):
                    c.manifest['links'].append({'shape':shape.Name,'article_key':r['article_key'],
                                                'target_slide_id':targets[r['article_key']]})
        c.manifest['cafe_slug']=row['slug']
    # Stable catalog order regardless of counts or collection outcome.
    for i,c in enumerate(canvases,1):c.slide.MoveTo(i)
    index={deck.Slides.Item(i).SlideID:(i,deck.Slides.Item(i).Name) for i in range(1,deck.Slides.Count+1)}
    from v9.ppt_notes import notes_range
    for i,c in enumerate(canvases,1):
        c.text(f'{i:02}',1518,887,45,12,12,color=GRAY)
        for link in c.manifest['links']:
            sid=link['target_slide_id'];position,name=index[sid]
            action=c.slide.Shapes.Item(link['shape']).ActionSettings(1)
            action.Action=7;action.Hyperlink.Address='';action.Hyperlink.SubAddress=f'{sid},{position},{name}'
        notes={'type':c.manifest['type'],'stats_fingerprint':stats['fingerprint'],
               'count_basis':stats['count_basis'],'keyword_basis':stats['keyword_basis']}
        if c.manifest.get('cafe_slug'):
            slug=c.manifest['cafe_slug'];notes['cafe']=next(r for r in stats['cafes'] if r['slug']==slug)
            notes['summary']=summaries.get(slug)
        expected=json.dumps(notes,ensure_ascii=False,indent=2)
        notes_range(c.slide).Text=expected
        c.manifest['notes']=expected
    report['dashboard_manifest']=[c.manifest for c in canvases]
    if cfg.get('v96_dashboard',{}).get('chart_renderer','shapes')=='native':
        # All ChartData sessions, slide moves, text, notes and links finish first.
        # Later charts can restore automatic decorations on earlier charts.
        from v9.dashboard_chart import FINALIZE_ATTEMPTS, finalize_decorations, audit_entry, pause
        for attempt in range(1,FINALIZE_ATTEMPTS+1):
            for c in canvases:
                for item in c.manifest['charts']:
                    entry=audit_entry(report,c.slide_name,item['name'])
                    finalize_decorations(c.slide.Shapes.Item(item['name']),item,entry,attempt)
            pause()
            try:
                verify_dashboard(deck,report,'finalize')
                # A second read after pumping catches a queued late layout update.
                pause()
                verify_dashboard(deck,report,'finalize_stable')
                break
            except V7Error as exc:
                if exc.code!='DASHBOARD_CHART_TITLE_MISMATCH' or attempt==FINALIZE_ATTEMPTS:raise
        print('[요약 차트 마무리] 전체 11개 / 자동 제목·중복 범례 없음',flush=True)
    else:
        verify_dashboard(deck,report,'finalize_shapes')
        print('[요약 그래프 마무리] 전체 11개 / PowerPoint 도형·텍스트 / Excel 연결 없음',flush=True)
    report['dashboard_insert_checked']=len(canvases)
    print(f'[요약 슬라이드] 전체 1장 + 카페 {len(stats["cafes"])}장 / 모든 건수 눈금 1',flush=True)
    return len(canvases)

def verify_dashboard(deck,report,stage):
    """Read-only check used before save, after save and after reopening."""
    from v9.dashboard_com import Guard
    deck=Guard(report.setdefault('dashboard_com_audit',[]),stage).wrap(deck)
    for i,manifest in enumerate(report['dashboard_manifest'],1):
        verify(deck.Slides.Item(i),manifest,report=report,stage=stage)


def verify(slide,manifest,*,report=None,stage='verify'):
    from v9.ppt_notes import verify_notes
    if slide.Name!=manifest['name']:raise V7Error('DASHBOARD_ORDER_MISMATCH','요약 슬라이드 순서가 달라졌습니다.')
    for name,expected in manifest['texts'].items():
        shape=slide.Shapes.Item(name)
        if shape.TextFrame.TextRange.Text!=expected or not text_bounds_fit(shape):
            raise V7Error('DASHBOARD_TEXT_MISMATCH','요약 슬라이드 문구 또는 배치가 다릅니다.')
    for name,color in manifest['colors'].items():
        if slide.Shapes.Item(name).Fill.ForeColor.RGB!=color:
            raise V7Error('DASHBOARD_COLOR_MISMATCH','요약 슬라이드 범례 색상이 다릅니다.')
    for item in manifest['charts']:
        from v9.dashboard_chart import verify_chart, audit_entry, decorations, error_record
        check={'stage':stage,'renderer':item.get('renderer','native')}
        if report is not None:
            audit_entry(report,manifest['name'],item['name']).setdefault('checks',[]).append(check)
        try:
            if item.get('renderer')=='shapes':
                from v9.dashboard_bars import verify as verify_bars
                verify_bars(slide,item)
                check['excel_used']=False
            else:
                chart=slide.Shapes.Item(item['name']).Chart
                check.update(decorations(chart))
                verify_chart(chart,item)
            check['passed']=True
        except BaseException as exc:
            check.update(passed=False,error=error_record(exc))
            print(f'[요약 차트 검증 실패] {stage} / {manifest["name"]} / {item["name"]} / '
                  f'제목={check.get("has_title")} / 범례={check.get("has_legend")} / {exc}',flush=True)
            raise
    for link in manifest['links']:
        target=slide.Shapes.Item(link['shape']).ActionSettings(1).Hyperlink
        if target.Address or str(target.SubAddress).split(',')[0]!=str(link['target_slide_id']):
            raise V7Error('DASHBOARD_LINK_MISMATCH','대표 게시글 상세 이동 링크가 다릅니다.')
    verify_notes(slide,manifest['notes'])
