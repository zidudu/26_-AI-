import {Composition} from 'remotion';
import {OrbitFilm} from './Composition';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition id="OrbitFilm" component={OrbitFilm} durationInFrames={360} fps={30} width={1080} height={1920} />
    </>
  );
};
