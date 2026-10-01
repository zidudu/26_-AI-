#!/usr/bin/env python3
"""Compare ZIP paths and SHA-256 against an explicit clean source tree; no extraction."""
import argparse,hashlib,json,stat,zipfile
from pathlib import Path,PurePosixPath

def digest(stream):
 h=hashlib.sha256();size=0
 while True:
  b=stream.read(1024*1024)
  if not b:break
  size+=len(b);h.update(b)
 return {'size':size,'sha256':h.hexdigest()}
def verify(archive,source,max_bytes=2_000_000_000,max_entries=20000):
 if not source.is_dir():raise ValueError('Source must be an existing directory')
 expected={}
 for p in source.rglob('*'):
  if p.is_symlink():raise ValueError('Symlink in source: '+str(p))
  if p.is_file():
   with p.open('rb') as f:expected[p.relative_to(source).as_posix()]=digest(f)
 actual={};names=set();total=0
 with zipfile.ZipFile(archive) as z:
  entries=z.infolist()
  if len(entries)>max_entries:raise ValueError('Too many ZIP entries')
  for info in entries:
   name=info.filename;parts=PurePosixPath(name).parts
   if not name or '\\' in name or name.startswith('/') or '..' in parts or any(':' in x for x in parts) or '\x00' in name:raise ValueError('Unsafe ZIP path: '+repr(name))
   normalized=PurePosixPath(name).as_posix().rstrip('/')
   if normalized in ('','.'):raise ValueError('Empty ZIP path')
   if normalized in names:raise ValueError('Duplicate ZIP path: '+normalized)
   names.add(normalized)
   mode=info.external_attr>>16
   if stat.S_ISLNK(mode):raise ValueError('ZIP symlink: '+name)
   if stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR):raise ValueError('Nonregular ZIP entry: '+name)
   if info.flag_bits&1:raise ValueError('Encrypted ZIP entry')
   total+=info.file_size
   if total>max_bytes:raise ValueError('Uncompressed size limit exceeded')
   if info.is_dir():continue
   with z.open(info) as f:actual[normalized]=digest(f)
   if actual[normalized]['size']!=info.file_size:raise ValueError('ZIP size mismatch')
 missing=sorted(expected.keys()-actual.keys());extra=sorted(actual.keys()-expected.keys())
 changed=sorted(k for k in expected.keys()&actual.keys() if expected[k]!=actual[k])
 with archive.open('rb') as f:archive_hash=digest(f)
 return {'identical':not(missing or extra or changed),'zip':archive_hash,'file_count':len(actual),'missing':missing,'extra':extra,'changed':changed,'files':actual}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--zip',required=True,type=Path);p.add_argument('--source',required=True,type=Path);p.add_argument('--max-bytes',type=int,default=2_000_000_000);p.add_argument('--max-entries',type=int,default=20000);a=p.parse_args()
 try:r=verify(a.zip,a.source,a.max_bytes,a.max_entries)
 except (OSError,ValueError,zipfile.BadZipFile,RuntimeError) as e:p.exit(2,str(e)+'\n')
 print(json.dumps(r,ensure_ascii=False,indent=2))
 if not r['identical']:raise SystemExit(1)
if __name__=='__main__':main()
