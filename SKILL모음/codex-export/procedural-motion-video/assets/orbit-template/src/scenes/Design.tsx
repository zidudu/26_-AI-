import {AbsoluteFill, useCurrentFrame} from 'remotion';

export const Chrome: React.FC<{dark?: boolean; label: string; number: string}> = ({dark = false, label, number}) => (
  <AbsoluteFill style={{pointerEvents: 'none', color: dark ? '#f5f1e8' : '#201c2d'}}>
    <div style={{position: 'absolute', left: 84, top: 116, fontFamily: 'Arial, sans-serif', fontWeight: 800, fontSize: 34, letterSpacing: 7}}>O R B I T <span style={{color: dark ? '#d6ff64' : '#6840c7'}}>✳</span></div>
    <div style={{position: 'absolute', right: 84, top: 126, fontSize: 21, letterSpacing: 4, fontFamily: 'Arial, sans-serif'}}>MOTION STUDY</div>
    <div style={{position: 'absolute', left: 84, right: 84, top: 186, height: 1, background: dark ? '#ffffff30' : '#201c2d30'}} />
    <div style={{position: 'absolute', left: 84, bottom: 140, fontFamily: 'Arial, sans-serif', letterSpacing: 3, fontSize: 25}}>{label}</div>
    <div style={{position: 'absolute', right: 84, bottom: 140, fontFamily: 'Arial, sans-serif', fontSize: 27}}>{number} / 03</div>
  </AbsoluteFill>
);

export const Sphere: React.FC<{size?: number; light?: boolean}> = ({size = 490, light = false}) => (
  <div style={{width: size, height: size, borderRadius: '50%', background: light
    ? 'radial-gradient(circle at 30% 24%, #fffef5 0%, #eeeddf 28%, #bcb6d6 60%, #58516d 85%, #292036 100%)'
    : 'radial-gradient(circle at 29% 22%, #fcddff 0%, #c48cff 15%, #8a4aed 37%, #562499 64%, #271047 85%, #160d26 100%)',
  boxShadow: light ? 'inset -18px -24px 34px #30264750, inset 9px 8px 28px #ffffff70' : 'inset 10px 8px 28px #ffffff38, inset -18px -20px 36px #10031c80', position: 'relative'}}>
    <div style={{position: 'absolute', top: '12%', left: '15%', width: '38%', height: '15%', borderRadius: '50%', rotate: '-38deg', background: '#ffffff45', filter: 'blur(23px)'}} />
  </div>
);

export const OrbitalArt: React.FC<{light?: boolean; progress?: number}> = ({light = false, progress = 1}) => {
  const frame = useCurrentFrame();
  const rotation = frame * 0.75;
  return <div style={{position: 'absolute', left: 0, top: 690, width: 1080, height: 810, scale: progress}}>
    <div style={{position: 'absolute', left: 275, top: 685, width: 530, height: 70, background: light ? '#201c2d24' : '#00000070', borderRadius: '50%', filter: 'blur(32px)'}} />
    <svg width="1080" height="810" viewBox="0 0 1080 810" style={{position: 'absolute'}}>
      {[0,1,2,3,4].map(i => <ellipse key={i} cx="540" cy="390" rx={400 - i * 27} ry={190 + i * 24} fill="none" stroke={light ? '#35274b' : '#d6ff64'} strokeWidth={i === 0 ? 3 : 1.4} opacity={i === 0 ? 0.85 : 0.23} transform={`rotate(${-28 + i * 25 + rotation} 540 390)`} />)}
    </svg>
    <div style={{position: 'absolute', left: 295, top: 145, rotate: `${-frame * .15}deg`}}><Sphere light={light} /></div>
    <svg width="1080" height="810" viewBox="0 0 1080 810" style={{position: 'absolute'}}>
      <ellipse cx="540" cy="390" rx="400" ry="190" fill="none" stroke={light ? '#292037' : '#d6ff64'} strokeWidth="3" strokeDasharray="980 2000" transform={`rotate(${-28 + rotation} 540 390)`} />
      {[0, 1, 2].map(i => {
        const a = frame * .027 + i * Math.PI * .66;
        return <circle key={i} cx={540 + Math.cos(a) * 405} cy={390 + Math.sin(a) * 295} r={i === 0 ? 43 : 13} fill={i === 0 ? '#d6ff64' : light ? '#6c38c9' : '#efddff'} />;
      })}
    </svg>
  </div>;
};
