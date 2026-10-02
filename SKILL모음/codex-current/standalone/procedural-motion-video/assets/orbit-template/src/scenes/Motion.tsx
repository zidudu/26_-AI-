import {AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame} from 'remotion';
import {Chrome, OrbitalArt} from './Design';

export const Motion: React.FC = () => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{background: '#eae8e0', color: '#211a2e', overflow: 'hidden'}}>
    <Chrome label="02 — FIND YOUR RHYTHM" number="02" />
    <Interactive.Div name="움직임 타이틀" style={{position: 'absolute', left: 84, top: 306, fontSize: 112, lineHeight: 1.25, fontWeight: 700, letterSpacing: -8,
      opacity: interpolate(frame,[0,12],[0,1],{extrapolateRight:'clamp'}),
      translate: interpolate(frame,[0,23],['0px 80px','0px 0px'],{extrapolateRight:'clamp',easing:Easing.bezier(.16,1,.3,1)})}}>움직임을<br/><span style={{color: '#7240c4'}}>더하면.</span></Interactive.Div>
    <OrbitalArt progress={interpolate(frame,[0,30],[.78,1],{extrapolateRight:'clamp',easing:Easing.bezier(.16,1,.3,1)})} />
    <Interactive.Div name="두 번째 장면 설명" style={{position: 'absolute', left: 84, top: 1518, fontSize: 44, letterSpacing: -2, opacity: interpolate(frame,[23,40],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'})}}>새로운 리듬, 새로운 시선.</Interactive.Div>
    <div style={{position: 'absolute', inset: 0, background: '#bca0ee', clipPath: `circle(${interpolate(frame,[112,120],[0,170],{extrapolateLeft:'clamp',extrapolateRight:'clamp'})}% at 15% 70%)`}} />
  </AbsoluteFill>;
};
