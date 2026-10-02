#!/usr/bin/env python3
"""Small SQLite raw/version/observation reference store (stdlib only)."""
import argparse, hashlib, json, sqlite3, os
from datetime import datetime,timezone
from pathlib import Path
SCHEMA='''
CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,source TEXT NOT NULL,item TEXT NOT NULL,first_collected TEXT NOT NULL,last_checked TEXT NOT NULL,latest_version INTEGER,UNIQUE(source,item));
CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY,record_id INTEGER NOT NULL REFERENCES records(id),fingerprint TEXT NOT NULL,content TEXT NOT NULL,UNIQUE(record_id,fingerprint));
CREATE TABLE IF NOT EXISTS observations(run_id TEXT NOT NULL,record_id INTEGER NOT NULL REFERENCES records(id),version_id INTEGER NOT NULL REFERENCES versions(id),observed_at TEXT NOT NULL,PRIMARY KEY(run_id,record_id));
'''
def utc(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if d.tzinfo is None or d.utcoffset() is None: raise ValueError('Aware observed_at required')
 return d.astimezone(timezone.utc).isoformat(timespec='microseconds')
def prepared(row):
 for k in ('source','id','title','body_raw','observed_at'):
  if not isinstance(row.get(k),str): raise ValueError('String required: '+k)
 if not row['source'] or not row['id']: raise ValueError('Empty record identity')
 if 'published' not in row or (row['published'] is not None and not isinstance(row['published'],str)): raise ValueError('published string or null required')
 if not isinstance(row.get('media'),list): raise ValueError('media array required')
 content=json.dumps({k:row[k] for k in ('title','body_raw','published','media')},sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
 return row['source'],row['id'],utc(row['observed_at']),content,hashlib.sha256(content.encode()).hexdigest()
def ingest(db,rows,run_id):
 if not isinstance(rows,list) or not run_id: raise ValueError('records array and nonempty run ID required')
 inputs=[prepared(r) for r in rows]
 con=sqlite3.connect(db,timeout=30)
 try:
  con.execute('PRAGMA foreign_keys=ON'); con.executescript(SCHEMA)
  with con:
   con.execute('BEGIN IMMEDIATE')
   for source,item,at,content,fp in inputs:
    con.execute('INSERT OR IGNORE INTO records(source,item,first_collected,last_checked) VALUES(?,?,?,?)',(source,item,at,at))
    rid,last=con.execute('SELECT id,last_checked FROM records WHERE source=? AND item=?',(source,item)).fetchone()
    con.execute('INSERT OR IGNORE INTO versions(record_id,fingerprint,content) VALUES(?,?,?)',(rid,fp,content))
    vid=con.execute('SELECT id FROM versions WHERE record_id=? AND fingerprint=?',(rid,fp)).fetchone()[0]
    con.execute('UPDATE records SET first_collected=min(first_collected,?),last_checked=max(last_checked,?),latest_version=CASE WHEN ?>=last_checked THEN ? ELSE latest_version END WHERE id=?',(at,at,at,vid,rid))
    con.execute('INSERT INTO observations VALUES(?,?,?,?) ON CONFLICT(run_id,record_id) DO UPDATE SET version_id=excluded.version_id,observed_at=excluded.observed_at WHERE excluded.observed_at>=observations.observed_at',(run_id,rid,vid,at))
  return {t:con.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('records','versions','observations')}
 finally: con.close()
def backup(db,out):
 if not db.is_file(): raise ValueError('Source DB does not exist')
 with out.open('xb'): pass
 try:
  src=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)
  dst=sqlite3.connect(out)
  try:
   src.backup(dst)
   if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Backup integrity failed')
  finally: dst.close(); src.close()
 except BaseException:
  out.unlink(missing_ok=True); raise
 return {'backup':str(out),'integrity':'ok'}
def main():
 p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='cmd',required=True)
 i=sub.add_parser('ingest'); i.add_argument('--db',required=True,type=Path); i.add_argument('--input',required=True,type=Path); i.add_argument('--run-id',required=True)
 b=sub.add_parser('backup'); b.add_argument('--db',required=True,type=Path); b.add_argument('--output',required=True,type=Path)
 a=p.parse_args()
 try:
  result=ingest(a.db,json.loads(a.input.read_text(encoding='utf-8')),a.run_id) if a.cmd=='ingest' else backup(a.db,a.output)
  print(json.dumps(result,ensure_ascii=False))
 except (ValueError,TypeError,OSError,sqlite3.Error) as e:p.exit(2,str(e)+'\n')
if __name__=='__main__':main()
