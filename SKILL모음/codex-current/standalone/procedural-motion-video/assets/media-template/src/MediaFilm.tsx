import React from 'react';
import {AbsoluteFill, Img, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {Audio, Video} from '@remotion/media';
import config from './film.json';

type Shot = {kind: 'image' | 'video'; src: string; duration: number; fit: 'cover' | 'contain'; focus: number[]; scale: number[]; pan: number[]; title: string; subtitle: string; trimBefore?: number; playbackRate?: number; volume?: number};
type Film = {title: string; eyebrow: string; subtitle: string; width: number; height: number; fps: number; overlap: number; music?: string; musicVolume?: number; shots: Shot[]};
export const film = config as Film;
if (!film.shots.length || !Number.isInteger(film.overlap) || film.overlap < 0 || !Number.isInteger(film.fps) || film.fps <= 0) throw new Error('Invalid film timing or empty shots');
for (const shot of film.shots) {
  if (!Number.isInteger(shot.duration) || shot.duration < 3 || shot.duration <= film.overlap * 2) throw new Error('Shot must be at least 3 frames and longer than twice the overlap');
  if (!['image','video'].includes(shot.kind) || !['cover','contain'].includes(shot.fit)) throw new Error('Invalid media kind or fit');
  if ((shot.playbackRate ?? 1) <= 0 || (shot.trimBefore ?? 0) < 0) throw new Error('Invalid trim or playback rate');
}
export const totalFrames = film.shots.reduce((sum,shot)=>sum+shot.duration,0)-film.overlap*(film.shots.length-1);
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

const ShotScene: React.FC<{shot: Shot; index: number}> = ({shot,index}) => {
  const f=useCurrentFrame();
  const progress=interpolate(f,[0,shot.duration-1],[0,1],clamp);
  const opacity=index===0||film.overlap===0?1:interpolate(f,[0,Math.max(1,film.overlap-1)],[0,1],clamp);
  const scale=shot.scale[0]+(shot.scale[1]-shot.scale[0])*progress;
  const x=shot.pan[0]+(shot.pan[1]-shot.pan[0])*progress;
  const position=`${shot.focus[0]}% ${shot.focus[1]}%`;
  const fade=interpolate(f,[0,18,shot.duration-22,shot.duration-1],[0,1,1,0],clamp);
  const source=staticFile(shot.src);
  const mediaStyle: React.CSSProperties={width:'100%',height:'100%',objectFit:shot.fit,objectPosition:position};
  const soundFade=interpolate(f,[0,Math.max(1,film.overlap),shot.duration-Math.max(2,film.overlap),shot.duration-1],[index===0?1:0,1,1,0],clamp);
  return <AbsoluteFill style={{opacity,background:'#151c1b',overflow:'hidden'}}>
    {shot.kind==='image' && shot.fit==='contain' && <AbsoluteFill style={{transform:'scale(1.13)',filter:'blur(30px) brightness(0.37) saturate(0.7)'}}><Img src={source} style={{width:'100%',height:'100%',objectFit:'cover',objectPosition:position}}/></AbsoluteFill>}
    <AbsoluteFill style={{transform:`translateX(${x}px) scale(${scale})`,padding:shot.fit==='contain'?'38px 0 74px':0}}>
      {shot.kind==='image'?<Img src={source} style={mediaStyle}/>:<Video src={source} objectFit={shot.fit} trimBefore={shot.trimBefore??0} playbackRate={shot.playbackRate??1} muted={(shot.volume??0)===0} volume={(shot.volume??0)*soundFade} style={{width:'100%',height:'100%',objectPosition:position}}/>}
    </AbsoluteFill>
    <AbsoluteFill style={{background:'linear-gradient(180deg,rgba(8,17,17,.3) 0%,transparent 26%,transparent 50%,rgba(9,18,17,.72) 100%)'}}/>
    <div style={{position:'absolute',left:90,bottom:118,color:'#faf6e9',opacity:fade}}>
      <div style={{fontFamily:'Arial',fontSize:17,letterSpacing:5,color:'#e7d3a9',marginBottom:18}}>MEMORY {String(index+1).padStart(2,'0')}</div>
      <div style={{fontSize:43,fontWeight:500,letterSpacing:-1}}>{shot.title}</div>
      <div style={{fontSize:23,color:'#dfdfd3',marginTop:15,letterSpacing:1}}>{shot.subtitle}</div>
    </div>
  </AbsoluteFill>;
};

export const MediaFilm: React.FC = () => {
  const f=useCurrentFrame();
  const end=interpolate(f,[totalFrames-30,totalFrames-1],[0,1],clamp);
  const intro=interpolate(f,[0,20,76,106],[0,1,1,0],clamp);
  let start=0;
  return <AbsoluteFill style={{background:'#0c1413',fontFamily:'Malgun Gothic, sans-serif'}}>
    {film.music && <Audio src={staticFile(film.music)} volume={film.musicVolume??1}/>}
    {film.shots.map((shot,i)=>{const from=start;start+=shot.duration-film.overlap;return <Sequence key={i} from={from} durationInFrames={shot.duration}><ShotScene shot={shot} index={i}/></Sequence>;})}
    <div style={{position:'absolute',left:90,right:90,top:48,display:'flex',justifyContent:'space-between',color:'#f0eedf',fontSize:16,letterSpacing:4,fontFamily:'Arial'}}><span>{film.eyebrow}</span><span>A VISUAL JOURNAL</span></div>
    <AbsoluteFill style={{pointerEvents:'none',opacity:intro,alignItems:'center',justifyContent:'center',color:'#fff9e9',background:'rgba(13,22,19,.28)',textShadow:'0 2px 30px #13241a99'}}>
      <div style={{fontFamily:'Arial',fontSize:21,letterSpacing:11,marginBottom:25}}>{film.eyebrow}</div>
      <div style={{fontSize:98,fontWeight:500,letterSpacing:-4}}>{film.title}</div>
      <div style={{width:65,height:2,background:'#eddaa8',margin:'32px 0'}}/>
      <div style={{fontSize:25,letterSpacing:3}}>{film.subtitle}</div>
    </AbsoluteFill>
    <div style={{position:'absolute',left:90,right:90,bottom:66,height:1,background:'#ffffff30'}}><div style={{width:`${100*f/(totalFrames-1)}%`,height:2,background:'#dfc492'}}/></div>
    <AbsoluteFill style={{background:'#0c1413',opacity:end}}/>
  </AbsoluteFill>;
};
