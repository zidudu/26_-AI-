import {AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame} from 'remotion';
import {Chrome, Sphere} from './Design';

export const Spark: React.FC = () => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 48% 54%, #29203f, #14101e 65%)', color: '#f5f1e8', overflow: 'hidden'}}>
    <Chrome dark label="01 — THE SPARK" number="01" />
    <Interactive.Div name="첫 장면 타이틀" style={{position: 'absolute', left: 84, top: 328, fontSize: 112, fontWeight: 700, letterSpacing: -8,
      opacity: interpolate(frame, [4, 22], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
      translate: interpolate(frame, [4, 26], ['0px 58px', '0px 0px'], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.bezier(.16, 1, .3, 1)})}}>작은 점에서,</Interactive.Div>
    <div style={{position: 'absolute', left: 89, top: 503, fontSize: 27, letterSpacing: 7, fontFamily: 'Arial, sans-serif', color: '#b9accb', opacity: interpolate(frame,[15,35],[0,1], {extrapolateLeft:'clamp',extrapolateRight:'clamp'})}}>EVERYTHING BEGINS WITH A DOT.</div>
    <svg width="1080" height="1920" style={{position: 'absolute'}}>
      {[270, 352, 440].map((r,i) => <circle key={r} cx="540" cy="1020" r={r + Math.sin(frame / 30 + i) * 9} stroke="#ceb2ff" strokeWidth="1" fill="none" opacity={0.11 + i*.025} />)}
      <line x1="540" x2="540" y1="600" y2="1440" stroke="#dfcaff" opacity=".10" />
      <line x1="100" x2="980" y1="1020" y2="1020" stroke="#dfcaff" opacity=".10" />
    </svg>
    <Interactive.Div name="작은 점에서 커지는 구체" style={{position: 'absolute', left: 280, top: 760,
      scale: interpolate(frame, [0, 10, 48, 108, 119], [.018, .018, 1, 1.04, 1.14], {extrapolateRight: 'clamp', easing: Easing.bezier(.2,.75,.25,1)})}}><Sphere size={520} /></Interactive.Div>
    <div style={{position: 'absolute', left: 505 + 353 * Math.cos(frame*.035), top: 985 + 353 * Math.sin(frame*.035), width: 70, height: 70, borderRadius: '50%', background: '#d6ff64', opacity: interpolate(frame,[32,48],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'})}} />
    <Interactive.Div name="첫 장면 설명" style={{position: 'absolute', top: 1518, left: 84, fontSize: 44, letterSpacing: -2, opacity: interpolate(frame,[35,55],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'})}}>상상은 여기서 시작됩니다.</Interactive.Div>
    <div style={{position: 'absolute', inset: 0, background: '#eae8e0', clipPath: `circle(${interpolate(frame,[112,120],[0,170],{extrapolateLeft:'clamp',extrapolateRight:'clamp'})}% at 85% 70%)`}} />
  </AbsoluteFill>;
};
