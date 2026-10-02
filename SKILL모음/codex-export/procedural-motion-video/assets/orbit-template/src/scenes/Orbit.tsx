import {AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame} from 'remotion';
import {Chrome, OrbitalArt} from './Design';

export const Orbit: React.FC = () => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 70% 68%, #ceb6f1, #bca0ee 68%)', color: '#20162d', overflow: 'hidden'}}>
    <Chrome label="03 — MAKE YOUR MOVE" number="03" />
    <Interactive.Div name="ORBIT 최종 타이틀" style={{position: 'absolute', left: 74, top: 286, fontFamily: 'Arial, sans-serif', fontWeight: 900, fontSize: 254, lineHeight: 1, letterSpacing: -18,
      opacity: interpolate(frame,[0,14],[0,1],{extrapolateRight:'clamp'}),
      translate: interpolate(frame,[0,25],['0px 65px','0px 0px'],{extrapolateRight:'clamp',easing:Easing.bezier(.16,1,.3,1)})}}>ORBIT<span style={{color: '#e2ff89', fontSize: 106, verticalAlign: 'top', lineHeight: 1.6}}>✳</span></Interactive.Div>
    <Interactive.Div name="마지막 한글 메시지" style={{position: 'absolute', left: 84, top: 568, fontSize: 76, letterSpacing: -5, fontWeight: 700,
      opacity: interpolate(frame,[10,28],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'})}}>하나의 장면이 되다.</Interactive.Div>
    <OrbitalArt light progress={interpolate(frame,[0,32],[.83,1],{extrapolateRight:'clamp',easing:Easing.bezier(.16,1,.3,1)})} />
    <Interactive.Div name="마지막 문구" style={{position: 'absolute', left: 84, top: 1518, fontSize: 44, letterSpacing: -2,
      opacity: interpolate(frame,[24,43],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'})}}>작은 시작, 새로운 움직임.</Interactive.Div>
    <div style={{position:'absolute',left:84,top:1620,height:5,width:interpolate(frame,[30,88],[0,912],{extrapolateLeft:'clamp',extrapolateRight:'clamp'}),background:'#20162d'}} />
  </AbsoluteFill>;
};
