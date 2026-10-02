"""Optional CLIP image/text retrieval plus reciprocal-rank fusion.

No model download happens in keyword search. Large libraries need a dedicated
ANN index; version 1.0 uses an inspectable SQLite vector store and cosine scan.
"""
import importlib.util
import threading
import numpy as np
from PIL import Image, ImageOps
from .store import now

IMAGE_MODEL='sentence-transformers/clip-ViT-B-32'
TEXT_MODEL='sentence-transformers/clip-ViT-B-32-multilingual-v1'
INDEX_MODEL=IMAGE_MODEL+'|'+TEXT_MODEL+'|normalized-v1'
class SemanticUnavailable(ValueError): pass

class Search:
    def __init__(self,store): self.store=store; self.image_model=None; self.text_model=None; self.lock=threading.RLock()
    def status(self):
        with self.store.db() as c: indexed=c.execute('SELECT COUNT(*) FROM vectors WHERE model=?',(INDEX_MODEL,)).fetchone()[0]
        return {'installed':importlib.util.find_spec('sentence_transformers') is not None,'indexed':indexed,'loaded':self.text_model is not None,'image_model':IMAGE_MODEL,'text_model':TEXT_MODEL}
    def load(self,download=False):
        with self.lock:
            if self.image_model is not None and self.text_model is not None: return
            if not self.status()['installed']: raise SemanticUnavailable('의미 검색 모듈이 없습니다. 04_install_semantic.bat을 실행하세요.')
            try:
                from sentence_transformers import SentenceTransformer
                kw={'cache_folder':str(self.store.root/'models'),'device':'cpu','local_files_only':not download}
                self.image_model=SentenceTransformer(IMAGE_MODEL,**kw); self.text_model=SentenceTransformer(TEXT_MODEL,**kw)
            except Exception as e:
                self.image_model=None; self.text_model=None
                raise SemanticUnavailable('CLIP 모델 로드 실패. 설정에서 색인을 만드세요. 원인: '+type(e).__name__) from e
    def index_one(self,pid):
        p=self.store.get(pid)
        if not p: raise ValueError('색인할 이미지가 삭제되었거나 없습니다.')
        with self.lock:
            self.load(download=True)
            with Image.open(self.store.images/p['filename']) as im:
                image=ImageOps.exif_transpose(im).convert('RGBA'); bg=Image.new('RGBA',image.size,'white'); bg.alpha_composite(image)
                vector=np.asarray(self.image_model.encode([bg.convert('RGB')],normalize_embeddings=True,show_progress_bar=False)[0],dtype='<f4')
        with self.store.db() as c:
            if not c.execute('SELECT 1 FROM pins WHERE id=?',(pid,)).fetchone(): raise ValueError('이미지가 삭제되었습니다.')
            c.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?)',(pid,INDEX_MODEL,vector.tobytes(),vector.size,now()))
    def vector_ranks(self,query,candidates):
        if not self.status()['indexed']: raise SemanticUnavailable('의미 검색 색인이 없습니다. 설정에서 색인을 만드세요.')
        with self.lock:
            self.load(); q=np.asarray(self.text_model.encode([query],normalize_embeddings=True,show_progress_bar=False)[0],dtype='<f4')
        pins={p['id']:p for p in candidates}; ranked=[]
        with self.store.db() as c:
            for r in c.execute('SELECT * FROM vectors WHERE model=?',(INDEX_MODEL,)):
                if r['pin_id'] in pins and r['dimensions']==q.size:
                    ranked.append((pins[r['pin_id']],float(np.dot(q,np.frombuffer(r['embedding'],dtype='<f4')))))
        return sorted(ranked,key=lambda x:x[1],reverse=True)
    def search(self,query='',mode='keyword',limit=60,offset=0,**filters):
        candidates=self.store.candidates(**filters); warnings=[]; effective=mode
        if not query.strip(): ranked=[(p,0.) for p in candidates]; effective='latest'
        elif mode=='keyword': ranked=self.store.keyword_ranks(query,candidates)
        elif mode=='semantic': ranked=self.vector_ranks(query,candidates)
        elif mode=='hybrid':
            lexical=self.store.keyword_ranks(query,candidates)
            try:
                semantic=self.vector_ranks(query,candidates); scores={}; byid={p['id']:p for p in candidates}
                for source in (lexical,semantic):
                    for i,(p,_) in enumerate(source[:300]): scores[p['id']]=scores.get(p['id'],0)+1/(61+i)
                ranked=[(byid[k],v) for k,v in sorted(scores.items(),key=lambda p:p[1],reverse=True)]
            except SemanticUnavailable as e:
                ranked=lexical; effective='keyword'; warnings=[str(e)+' 이번 결과는 키워드 검색만 사용했습니다.']
        else: raise ValueError('검색 방식이 올바르지 않습니다.')
        return {'items':[{**p,'score':round(v,6)} for p,v in ranked[offset:offset+limit]],'total':len(ranked),'query':query,
                'requested_mode':mode,'effective_mode':effective,'warnings':warnings,'offset':offset,'limit':limit}
    def similar(self,pid,limit=12):
        source=self.store.get(pid)
        if not source: raise KeyError(pid)
        h=int(source['dhash'],16)
        ranked=sorted([(p,(h^int(p['dhash'],16)).bit_count()) for p in self.store.candidates() if p['id']!=pid],key=lambda x:x[1])[:limit]
        return {'items':[{**p,'hash_distance':d} for p,d in ranked],'method':'dhash','note':'밝기 패턴 비교입니다. 의미 검색이나 동일 이미지 판정은 아닙니다.'}
