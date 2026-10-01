#!/usr/bin/env python3
"""Validate exact source binding, not the semantic truth of claims."""
import argparse,hashlib,json
from pathlib import Path

def validate(sources,analysis):
 errors=[]; index={}
 if not isinstance(sources,list):return ['sources must be an array']
 for s in sources:
  if not isinstance(s,dict) or not isinstance(s.get('id'),str) or not s['id'] or not isinstance(s.get('body_raw'),str):
   errors.append('Invalid source');continue
  if s['id'] in index:errors.append('Duplicate source ID: '+s['id'])
  index[s['id']]=s['body_raw']
 if not isinstance(analysis,dict) or not isinstance(analysis.get('claims'),list):return errors+['claims array required']
 seen=set()
 for n,c in enumerate(analysis['claims']):
  label=f'claim[{n}]'
  if not isinstance(c,dict): errors.append(label+': object required');continue
  cid=c.get('id')
  if not isinstance(cid,str) or not cid or cid in seen:errors.append(label+': invalid/duplicate id')
  else:seen.add(cid)
  if not isinstance(c.get('text'),str) or not c['text'].strip():errors.append(label+': text required')
  ev=c.get('evidence')
  if not isinstance(ev,list) or not ev: errors.append(label+': evidence required');continue
  for e in ev:
   if not isinstance(e,dict) or not isinstance(e.get('source_id'),str) or e['source_id'] not in index:errors.append(label+': unknown source');continue
   raw=index[e['source_id']]; start=e.get('start'); end=e.get('end')
   if e.get('source_sha256')!=hashlib.sha256(raw.encode()).hexdigest():errors.append(label+': source hash mismatch')
   if type(start) is not int or type(end) is not int or not 0<=start<end<=len(raw):errors.append(label+': invalid offsets')
   elif not isinstance(e.get('quote'),str) or raw[start:end]!=e['quote']:errors.append(label+': quote mismatch')
 return errors

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sources',required=True,type=Path);p.add_argument('--analysis',required=True,type=Path);a=p.parse_args()
 try: errors=validate(json.loads(a.sources.read_text(encoding='utf-8')),json.loads(a.analysis.read_text(encoding='utf-8')))
 except (OSError,ValueError) as e:p.exit(2,str(e)+'\n')
 print(json.dumps({'valid':not errors,'semantic_review':'not_performed','errors':errors},ensure_ascii=False,indent=2))
 if errors:raise SystemExit(1)
if __name__=='__main__':main()
