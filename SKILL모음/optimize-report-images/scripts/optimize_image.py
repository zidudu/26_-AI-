#!/usr/bin/env python3
"""Create a separate placement-sized PNG; requires Pillow."""
import argparse,hashlib,json,math,os
from pathlib import Path
from PIL import Image,ImageOps

def optimize(src,out,widths,ppi):
 if not widths or not math.isfinite(ppi) or ppi<=0 or any(not math.isfinite(w) or w<=0 for w in widths):raise ValueError('Positive finite widths/PPI required')
 if src.resolve()==out.resolve():raise ValueError('Output must differ from input')
 if out.suffix.lower()!='.png':raise ValueError('Output extension must be .png')
 raw=src.read_bytes(); digest=hashlib.sha256(raw).hexdigest()
 import io
 with Image.open(io.BytesIO(raw)) as im:
  if getattr(im,'n_frames',1)!=1:raise ValueError('Single-frame images only')
  native=list(im.size); normalized=ImageOps.exif_transpose(im)
  alpha=normalized.mode in ('RGBA','LA') or 'transparency' in normalized.info
  normalized=normalized.convert('RGBA' if alpha else 'RGB'); oriented=list(normalized.size)
  width=min(normalized.width,math.ceil(max(widths)/72*ppi));height=max(1,round(normalized.height*width/normalized.width))
  result=normalized.resize((width,height),Image.Resampling.LANCZOS) if (width,height)!=normalized.size else normalized
  data=io.BytesIO();result.save(data,format='PNG',optimize=True)
 payload=data.getvalue()
 # Exclusive creation protects existing assets; remove only a file created here if the write fails.
 with out.open('xb') as f:
  try:f.write(payload);f.flush();os.fsync(f.fileno())
  except BaseException:
   f.close();out.unlink(missing_ok=True);raise
 return {'input':str(src),'output':str(out),'source_sha256':digest,'output_sha256':hashlib.sha256(payload).hexdigest(),'source_size':len(raw),'output_size':len(payload),'native_dimensions':native,'oriented_dimensions':oriented,'output_dimensions':[width,height],'widths_pt':widths,'ppi':ppi}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--width-pt',required=True,type=float,action='append');p.add_argument('--ppi',required=True,type=float);a=p.parse_args()
 try:print(json.dumps(optimize(a.input,a.output,a.width_pt,a.ppi),indent=2))
 except (ValueError,OSError,OverflowError) as e:p.exit(2,str(e)+'\n')
if __name__=='__main__':main()
