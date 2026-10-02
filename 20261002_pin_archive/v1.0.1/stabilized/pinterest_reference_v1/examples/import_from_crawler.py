"""Import existing crawler output without changing the crawler.

Local files: python examples/import_from_crawler.py manifest.json
URL jobs:    python examples/import_from_crawler.py manifest.json --urls --authorized

Input: an array or {"items": [...]} object. Local 'file'/'image' paths are relative
to the manifest directory and cannot escape it. Metadata and source links survive.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mcp_bridge import base_url
from app.runtime import discover_server
import httpx
FIELDS={'image_url','title','description','source_url','pin_url','tags','crawl_keyword','category','author','collection_id','license_note','source'}

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('manifest',type=Path)
    ap.add_argument('--server'); ap.add_argument('--data-dir',type=Path); ap.add_argument('--urls',action='store_true'); ap.add_argument('--authorized',action='store_true')
    args=ap.parse_args()
    try:
        origin=base_url(args.server) if args.server else discover_server(args.data_dir); file=args.manifest.resolve()
        doc=json.loads(file.read_text(encoding='utf-8-sig')); rows=doc.get('items') if isinstance(doc,dict) else doc
        if not isinstance(rows,list) or not rows or any(not isinstance(r,dict) for r in rows): raise ValueError('Expected a nonempty array or {"items": [...]} object.')
        with httpx.Client(base_url=origin,timeout=120,trust_env=False) as c:
            r=c.get('/api/bootstrap');r.raise_for_status();c.headers['X-PinArchive-CSRF']=r.json()['csrf']
            if args.urls:
                if not args.authorized: raise ValueError('Confirm collection permission with --authorized.')
                for i in range(0,len(rows),100):
                    batch=[{k:v for k,v in row.items() if k in FIELDS} for row in rows[i:i+100]]
                    r=c.post('/api/import/urls',json={'items':batch,'permission_confirmed':True}); r.raise_for_status();print('Queued job:',r.json()['id'])
                print('Queuing is not completion. Review counts and failures in the Jobs page.');return
            total=dupes=errors=0
            for row in rows:
                relative=row.get('file') or row.get('image')
                if not isinstance(relative,str): raise ValueError('Each local item requires a relative file or image path.')
                image=(file.parent/relative).resolve()
                if not image.is_relative_to(file.parent): raise ValueError('Image path escapes the manifest folder: '+relative)
                if not image.is_file() or image.stat().st_size>20*1024*1024: raise ValueError('Missing or oversized image: '+relative)
                metadata={k:v for k,v in row.items() if k in FIELDS}; metadata['source']='import';metadata.setdefault('title',image.stem)
                # Exported archive metadata may contain several collected keywords.
                if not metadata.get('crawl_keyword') and row.get('crawl_keywords'): metadata['crawl_keyword']=', '.join(row['crawl_keywords'])[:2000]
                with image.open('rb') as f:
                    r=c.post('/api/import',files=[('files',(image.name,f,'application/octet-stream'))],data={'metadata':json.dumps([metadata],ensure_ascii=False)})
                r.raise_for_status();result=r.json();total+=result['added'];dupes+=result['duplicates'];errors+=len(result['errors'])
                for err in result['errors']: print('ERROR:',err['file'],err['error'],file=sys.stderr)
            print(f'Imported {total}; duplicates {dupes}; errors {errors}')
            if errors: raise SystemExit(1)
    except (ValueError,RuntimeError,OSError,httpx.HTTPError) as e: print('Import failed:',e,file=sys.stderr);raise SystemExit(1)
if __name__=='__main__': main()
