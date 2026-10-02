"""Create a new ORBIT or image/video editing project without overwriting files."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

IMAGE_EXT={'.png','.jpg','.jpeg','.webp'}
VIDEO_EXT={'.mp4','.mov','.mkv','.webm','.m4v'}

def probe_input(path, ffprobe):
    if path.suffix.lower() in IMAGE_EXT:
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            if getattr(image,'n_frames',1)>1:
                raise ValueError(f'Animated image requires an explicit conversion decision: {path}')
            image.load()
            oriented=ImageOps.exif_transpose(image)
            return {'kind':'image','width':oriented.width,'height':oriented.height}
    if path.suffix.lower() not in VIDEO_EXT:
        raise ValueError(f'Unsupported input extension: {path}')
    if not ffprobe:
        raise ValueError('--ffprobe is required for video input')
    run=subprocess.run([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(path)],check=True,capture_output=True,text=True,encoding='utf-8')
    data=json.loads(run.stdout)
    video=next((s for s in data['streams'] if s['codec_type']=='video'),None)
    if video is None: raise ValueError(f'No video stream: {path}')
    duration=float(video.get('duration',data.get('format',{}).get('duration',0)))
    if not math.isfinite(duration) or duration<=0: raise ValueError(f'Unknown video duration: {path}')
    return {'kind':'video','width':video['width'],'height':video['height'],'seconds':duration}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',type=Path)
    parser.add_argument('--template',choices=['orbit','media'],default='orbit')
    parser.add_argument('--media',type=Path,nargs='+')
    parser.add_argument('--ffprobe')
    parser.add_argument('--title',default='새로운 이야기')
    parser.add_argument('--seconds-per-shot',type=float,default=4.5)
    parser.add_argument('--overlap-seconds',type=float,default=.4)
    args=parser.parse_args()
    skill=Path(__file__).resolve().parents[1]
    destination=args.destination.expanduser().resolve()
    if destination.exists(): parser.error(f'Destination already exists; nothing overwritten: {destination}')
    if skill==destination or skill in destination.parents: parser.error('Choose a workspace outside the installed skill')
    if args.template=='orbit':
        if args.media: parser.error('--media requires --template media')
        shutil.copytree(skill/'assets/orbit-template',destination)
        print(destination)
        return
    if not args.media: parser.error('--template media requires --media paths')
    if not math.isfinite(args.seconds_per_shot) or args.seconds_per_shot<=0: parser.error('Shot length must be positive and finite')
    if not math.isfinite(args.overlap_seconds) or args.overlap_seconds<0: parser.error('Overlap must be nonnegative and finite')
    fps=30; overlap=round(args.overlap_seconds*fps)
    records=[]
    try:
        for source in args.media:
            source=source.expanduser().resolve(strict=True)
            info=probe_input(source,args.ffprobe)
            duration=round(args.seconds_per_shot*fps)
            if info['kind']=='video': duration=min(duration,math.floor(info['seconds']*fps))
            if duration<3 or duration<=overlap*2:
                raise ValueError(f'Shot too short for overlap; lower --overlap-seconds: {source}')
            records.append((source,info,duration))
    except (OSError,ValueError,KeyError,subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    shutil.copytree(skill/'assets/media-template',destination)
    common=skill/'assets/orbit-template'
    for filename in ['package.json','package-lock.json','remotion.config.ts','eslint.config.mjs','.gitignore']:
        shutil.copy2(common/filename,destination/filename)
    (destination/'public/media').mkdir(parents=True,exist_ok=True)
    shots=[];manifest=[]
    for index,(source,info,duration) in enumerate(records):
        relative=f'media/shot-{index+1:02}{source.suffix.lower()}'
        shutil.copy2(source,destination/'public'/relative)
        shots.append({'kind':info['kind'],'src':relative,'duration':duration,'fit':'contain' if info['width']/info['height']<1.25 else 'cover','focus':[50,50],'scale':[1.025,1.075] if info['kind']=='image' else [1,1],'pan':[0,0],'title':f'장면 {index+1:02}','subtitle':'','trimBefore':0,'playbackRate':1,'volume':0})
        manifest.append({'source':str(source),'copied_to':relative,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),**info})
    film={'title':args.title,'eyebrow':'VISUAL JOURNAL','subtitle':'이미지와 영상으로 기록하는 순간','width':1920,'height':1080,'fps':fps,'overlap':overlap,'music':'music.wav','musicVolume':.9,'shots':shots}
    (destination/'src/film.json').write_text(json.dumps(film,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (destination/'source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    package=json.loads((destination/'package.json').read_text(encoding='utf-8'))
    package['scripts'].update({'typecheck':'tsc --noEmit','render':'remotion render src/index.ts MediaFilm out/film.mp4 --codec=h264','still':'remotion still src/index.ts MediaFilm out/scene.png --frame=45'})
    (destination/'package.json').write_text(json.dumps(package,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'project':str(destination),'shots':len(shots),'frames':sum(s['duration'] for s in shots)-overlap*(len(shots)-1)},ensure_ascii=False))

if __name__=='__main__': main()
