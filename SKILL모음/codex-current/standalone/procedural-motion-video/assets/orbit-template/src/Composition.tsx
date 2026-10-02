import {AbsoluteFill, staticFile} from 'remotion';
import {Audio} from '@remotion/media';
import {TransitionSeries} from '@remotion/transitions';
import {Spark} from './scenes/Spark';
import {Motion} from './scenes/Motion';
import {Orbit} from './scenes/Orbit';

export const OrbitFilm: React.FC = () => (
  <AbsoluteFill style={{fontFamily: 'Malgun Gothic, sans-serif'}}>
    <Audio src={staticFile('orbit-soundtrack.wav')} />
    <TransitionSeries>
      <TransitionSeries.Sequence durationInFrames={120} name="01 작은 점에서"><Spark /></TransitionSeries.Sequence>
      <TransitionSeries.Sequence durationInFrames={120} name="02 움직임을 더하면"><Motion /></TransitionSeries.Sequence>
      <TransitionSeries.Sequence durationInFrames={120} name="03 하나의 장면"><Orbit /></TransitionSeries.Sequence>
    </TransitionSeries>
  </AbsoluteFill>
);
