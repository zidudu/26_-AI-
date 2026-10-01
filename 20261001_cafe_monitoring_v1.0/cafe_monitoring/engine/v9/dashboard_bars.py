"""Editable bar plots using only PowerPoint rectangles and text boxes.

No AddChart2, Shape.Chart, ChartData, embedded Excel or workbook lifecycle.
Every drawn label, bar, tick and position has a deterministic verification plan.
"""
from math import isclose, isfinite, floor
from v754.core import V7Error
from v9.dashboard_data import axis


def plan(names, values, colors, x, y, w, h, tick_profile=None):
    from v9.dashboard_ppt import coords, rgb, INK, GRAY, LINE
    limits=axis(values)
    if not names or len(names)!=len(values) or len(names)!=len(colors):
        raise V7Error('DASHBOARD_CHART_INPUT','막대그래프 항목·건수·색상 수가 다릅니다.')
    maximum=limits['maximum'];parts=[]
    def rect(role,left,top,width,height,color):
        parts.append({'role':role,'kind':'rect','box':coords(left,top,width,height),'fill':rgb(color),'rotation':0})
        return len(parts)-1
    def text(role,value,left,top,width,height,size,align=1,rotation=0):
        parts.append({'role':role,'kind':'text','text':str(value),'box':coords(left,top,width,height),
                      'size':size,'color':GRAY if role in ('category','tick') else INK,
                      'align':align,'rotation':rotation})
        return len(parts)-1
    # Labels use the original fixed-order category names. Reserve only what fits
    # the longest label; no automatic chart title can occupy the plot area.
    longest=max(sum(1 if ord(c)>127 else .55 for c in str(name)) for name in names)
    label_w=min(w*.46,max(100,longest*16+12))
    value_w=max(34,len(str(maximum))*10+12)
    plot_x=x+label_w+8;plot_y=y+8
    plot_w=w-label_w-value_w-20;plot_h=h-50
    row_h=plot_h/len(names);bar_h=row_h*.60
    if plot_w<=0 or row_h<10:raise V7Error('DASHBOARD_CHART_LAYOUT','막대그래프 공간이 부족합니다.')
    ticks=[]
    spacing=plot_w/maximum
    for tick in range(maximum+1):
        left=plot_x+tick*spacing
        # Reference width 2.0 becomes about 1.05 pt on the A4 slide.
        # Keep grid positions and the existing rectangle renderer unchanged.
        line=rect('grid',left,plot_y,2.0,plot_h,LINE)
        if tick_profile is not None:
            from v754.powerpoint import SLIDE_W, SLIDE_H
            font=tick_profile['font_pt']*1600/SLIDE_W
            tw=tick_profile['box_width_pt']*1600/SLIDE_W
            th=tick_profile['box_height_pt']*900/SLIDE_H
            label=text('tick',tick,left-tw/2,plot_y+plot_h+7,tw,th,font,align=2)
            parts[label]['word_wrap']=False
        else:
            # Provisional geometry only. draw() replaces every tick with the
            # measured profile before adding any permanent text or shapes.
            label=text('tick',tick,left-15,plot_y+plot_h+7,30,22,14,align=2)
        ticks.append({'value':tick,'line':line,'label':label})
    rows=[]
    for i,(name,value,color) in enumerate(zip(names,values,colors)):
        center_y=plot_y+(i+.5)*row_h
        category=text('category',name,x,center_y-row_h/2,label_w,row_h,16,align=3)
        bar=None
        if value is not None and value>0:
            bar=rect('bar',plot_x,center_y-bar_h/2,plot_w*value/maximum,bar_h,color)
        end=plot_x+plot_w*(value or 0)/maximum
        number=text('value','—' if value is None else value,end+6,center_y-row_h/2,value_w,row_h,16)
        rows.append({'category':category,'bar':bar,'label':number,'value':value})
    return {'axis':limits,'parts':parts,'rows':rows,'ticks':ticks,
            'plot_box':coords(plot_x,plot_y,plot_w,plot_h),'box':coords(x,y,w,h)}


def measure_ticks(canvas,layout,entry):
    """Use a wide no-wrap Office probe; never estimate a digit's width.

    The probe is deleted before final drawing and never enters the manifest.
    One font is selected for the whole axis, then all integer labels are kept.
    """
    from v754.powerpoint import SLIDE_W, SLIDE_H
    maximum=layout['axis']['maximum'];spacing=layout['plot_box'][2]/maximum
    width=min(30*SLIDE_W/1600,spacing*.92);height=22*SLIDE_H/900
    guard=min(.4,width*.05)
    requested=14*SLIDE_W/1600
    probe=canvas.slide.Shapes.AddTextbox(1,0,0,600,60)
    probe.Name='V964_TICK_MEASURE'
    primary=None
    attempts=entry.setdefault('tick_measurements',[])
    try:
        tf=probe.TextFrame;tf.AutoSize=0;tf.WordWrap=0
        tf.MarginLeft=tf.MarginRight=tf.MarginTop=tf.MarginBottom=0;tf.VerticalAnchor=3
        tr=tf.TextRange;tr.Font.Name=tr.Font.NameFarEast=canvas.font;tr.Font.Bold=0
        tr.ParagraphFormat.SpaceBefore=tr.ParagraphFormat.SpaceAfter=0
        tr.ParagraphFormat.Alignment=2
        for _ in range(12):
            tr.Font.Size=requested
            actual=float(tr.Font.Size);measurements=[]
            for n in range(maximum+1):
                tr.Text=str(n)
                bw,bh=float(tr.BoundWidth),float(tr.BoundHeight)
                if not all(isfinite(v) and v>0 for v in (bw,bh,actual)):
                    raise V7Error('DASHBOARD_TICK_MEASURE','눈금 글자 실측값이 올바르지 않습니다.')
                measurements.append({'text':str(n),'width_pt':bw,'height_pt':bh})
            widest=max(measurements,key=lambda m:m['width_pt'])
            tallest=max(m['height_pt'] for m in measurements)
            attempts.append({'requested_font_pt':requested,'actual_font_pt':actual,
                'widest':widest,'max_height_pt':tallest,'box_width_pt':width,'box_height_pt':height})
            if widest['width_pt']+guard<=width and tallest+guard<=height:
                return {'method':'office_no_wrap_v964','font_pt':actual,'box_width_pt':width,
                        'box_height_pt':height,'guard_pt':guard,'labels':measurements}
            ratio=min((width-guard)/widest['width_pt'],(height-guard)/tallest)
            next_size=max(1.0,floor(min(requested-.25,actual*ratio*.94)*4)/4)
            if next_size>=requested or requested<=1.0:break
            requested=next_size
        raise V7Error('DASHBOARD_TICK_SPACE','1건 눈금의 글자가 표시 공간에 맞지 않습니다. tick_measurements를 확인하세요.')
    except BaseException as exc:
        primary=exc;raise
    finally:
        try:probe.Delete()
        except BaseException as exc:
            from v9.dashboard_chart import error_record
            entry['probe_cleanup_error']=error_record(exc)
            if primary is None:raise


def draw(canvas,names,values,colors,x,y,w,h):
    from v9.dashboard_chart import error_record
    layout=plan(names,values,colors,x,y,w,h)
    name=canvas.name()
    item={'name':name,'renderer':'shapes','categories':list(names),'values':list(values),
          'colors':list(colors),'axis':layout['axis'],'layout':layout,
          'reference_box':[x,y,w,h]}
    entry={'slide':canvas.slide_name,'chart':name,'renderer':'shapes',
           'stage':'draw_shapes','rows':len(names),'verified':False,'excel_used':False}
    canvas.audit.append(entry)
    com_start=len(canvas.com.audit)
    try:
        entry['stage']='measure_ticks'
        profile=measure_ticks(canvas,layout,entry)
        layout=plan(names,values,colors,x,y,w,h,tick_profile=profile)
        item['layout']=layout;item['tick_profile']=profile
        entry['stage']='draw_shapes'
        root=canvas.rect(x,y,w,h,name=name)
        for index,part in enumerate(layout['parts']):
            entry['current_part']={'role':part['role'],'text':part.get('text'),'box_pt':part['box']}
            from v754.powerpoint import SLIDE_W, SLIDE_H
            l,t,pw,ph=part['box'];sx=SLIDE_W/1600;sy=SLIDE_H/900
            box=(l/sx,t/sy,pw/sx,ph/sy)
            if part['kind']=='rect':
                from v9.dashboard_ppt import LINE
                color=colors[next(i for i,r in enumerate(layout['rows']) if r['bar']==index)] if part['role']=='bar' else LINE
                shape=canvas.rect(*box,fill=color)
            else:
                shape=canvas.text(part['text'],*box,size=part['size'],minimum=part['size'],color=part['color'],
                                  word_wrap=part.get('word_wrap',True))
                shape.TextFrame.TextRange.ParagraphFormat.Alignment=part['align']
            # All graph parts are horizontal fresh shapes. Do not issue a
            # redundant property write; geometry() still checks rotation.
            if part['rotation']!=0:
                raise V7Error('DASHBOARD_BAR_GEOMETRY','도형 그래프 계획에 지원하지 않는 회전이 있습니다.')
            part['shape']=shape.Name
        entry['stage']='verify_shapes'
        verify(canvas.slide,item)
        entry.update(stage='verified',verified=True)
        entry['com_events']=canvas.com.audit[com_start:]
        entry.pop('current_part',None)
        print(f'[요약 그래프 검증] {entry["slide"]} / {name} / '
              f'{len(names)}개 항목·막대·건수 일치 / Excel 연결 없음',flush=True)
        return root,item
    except BaseException as exc:
        entry['error']=error_record(exc)
        entry['com_events']=canvas.com.audit[com_start:]
        if hasattr(exc,'text_layout'):entry['text_layout']=exc.text_layout
        print(f'[요약 그래프 실패] {entry["slide"]} / {name} / {entry["stage"]} / {exc}',flush=True)
        raise


def verify(slide,item):
    """Recalculate expected geometry from source values, then inspect Office."""
    from v754.powerpoint import text_bounds_fit
    from v9.dashboard_ppt import rgb
    profile=item.get('tick_profile')
    expected=plan(item['categories'],item['values'],item['colors'],*item['reference_box'],tick_profile=profile)
    saved=item['layout']
    if (item['axis']!=expected['axis'] or len(saved['parts'])!=len(expected['parts'])
            or saved['rows']!=expected['rows'] or saved['ticks']!=expected['ticks']):
        raise V7Error('DASHBOARD_CHART_MISMATCH','도형 그래프 계획의 항목·건수·눈금이 다릅니다.')
    def geometry(shape,box,rotation):
        actual=(shape.Left,shape.Top,shape.Width,shape.Height)
        if any(not isclose(float(a),float(b),abs_tol=.03,rel_tol=0) for a,b in zip(actual,box)):
            raise V7Error('DASHBOARD_BAR_GEOMETRY','도형 그래프의 막대 길이 또는 배치가 집계값과 다릅니다.')
        if not isclose(float(shape.Rotation)%360,rotation%360,abs_tol=.03,rel_tol=0):
            raise V7Error('DASHBOARD_BAR_GEOMETRY','도형 그래프의 글자 방향이 다릅니다.')
    root=slide.Shapes.Item(item['name'])
    geometry(root,expected['box'],0)
    if profile is not None:
        from v754.powerpoint import SLIDE_W, SLIDE_H
        spacing=expected['plot_box'][2]/expected['axis']['maximum']
        if (profile['method']!='office_no_wrap_v964' or not 1<=profile['font_pt']<=14*SLIDE_W/1600+.03
                or not isclose(profile['box_width_pt'],min(30*SLIDE_W/1600,spacing*.92),abs_tol=.03)
                or not isclose(profile['box_height_pt'],22*SLIDE_H/900,abs_tol=.03)
                or [m['text'] for m in profile['labels']]!=[str(i) for i in range(expected['axis']['maximum']+1)]):
            raise V7Error('DASHBOARD_TICK_MEASURE','눈금 실측 자료와 그래프 범위가 다릅니다.')
    for original,part in zip(saved['parts'],expected['parts']):
        shape=slide.Shapes.Item(original['shape'])
        geometry(shape,part['box'],part['rotation'])
        if part['kind']=='rect':
            if shape.Type!=1 or shape.Fill.ForeColor.RGB!=part['fill']:
                raise V7Error('DASHBOARD_CHART_MISMATCH','도형 그래프의 막대·눈금 또는 색상이 다릅니다.')
        else:
            tr=shape.TextFrame.TextRange
            if (tr.Text!=part['text'] or tr.ParagraphFormat.Alignment!=part['align']
                    or tr.Font.Color.RGB!=rgb(part['color']) or not text_bounds_fit(shape)):
                raise V7Error('DASHBOARD_CHART_MISMATCH','도형 그래프의 항목·건수·눈금 글자가 다르거나 넘칩니다.')
            if part['role']=='tick' and profile is not None:
                if int(shape.TextFrame.WordWrap)!=0 or not isclose(float(tr.Font.Size),profile['font_pt'],abs_tol=.03):
                    raise V7Error('DASHBOARD_TICK_MEASURE','눈금 글꼴 크기 또는 줄바꿈 설정이 다릅니다.')
