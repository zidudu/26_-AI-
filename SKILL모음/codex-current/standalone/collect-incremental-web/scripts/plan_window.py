#!/usr/bin/env python3
"""Plan half-open UTC collection windows; never changes cursors."""
import argparse, json, math
from datetime import datetime, timedelta, timezone
from pathlib import Path

def instant(s):
    d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if d.tzinfo is None or d.utcoffset() is None:
        raise ValueError('Timestamp must include a timezone')
    return d.astimezone(timezone.utc)

def plan(mode, end, hours=24, start=None, cursors=None, overlap=24):
    end = instant(end).replace(second=0, microsecond=0)
    if not math.isfinite(hours) or hours <= 0 or not math.isfinite(overlap) or overlap < 0:
        raise ValueError('hours must be positive; overlap must be nonnegative')
    if mode == 'recent': windows = {'all': end-timedelta(hours=hours)}
    elif mode == 'custom': windows = {'all': instant(start or '')}
    elif mode == 'cursor':
        if not isinstance(cursors, dict) or not cursors or any(not isinstance(k,str) or not k or not v for k,v in cursors.items()):
            raise ValueError('Every selected source needs a cursor; choose an explicit initial window')
        raw = {k: instant(v) for k,v in cursors.items()}
        if any(v > end for v in raw.values()): raise ValueError('Cursor is later than planned end')
        windows = {k:v-timedelta(hours=overlap) for k,v in raw.items()}
    else: raise ValueError('Unknown mode')
    if any(s >= end for s in windows.values()): raise ValueError('Empty or reversed interval')
    return {'mode':mode,'end_exclusive':True,'advance_normal_cursors':mode!='custom',
            'windows':{k:{'start':s.isoformat(),'end':end.isoformat()} for k,s in windows.items()}}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['recent','custom','cursor'],required=True)
    p.add_argument('--end',required=True,help='Aware ISO timestamp; floored to minute')
    p.add_argument('--start'); p.add_argument('--hours',type=float,default=24)
    p.add_argument('--overlap-hours',type=float,default=24)
    p.add_argument('--cursors',type=Path,help='JSON source-key to aware timestamp map')
    a=p.parse_args()
    try:
        cursors=json.loads(a.cursors.read_text()) if a.cursors else None
        print(json.dumps(plan(a.mode,a.end,a.hours,a.start,cursors,a.overlap_hours),ensure_ascii=False,indent=2))
    except (ValueError,TypeError,OSError,OverflowError) as e: p.exit(2,str(e)+'\n')
if __name__=='__main__': main()
