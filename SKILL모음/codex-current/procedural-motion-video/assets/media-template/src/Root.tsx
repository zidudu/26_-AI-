import {Composition} from 'remotion';
import {MediaFilm, film, totalFrames} from './MediaFilm';
export const RemotionRoot: React.FC = () => <Composition id="MediaFilm" component={MediaFilm} width={film.width} height={film.height} fps={film.fps} durationInFrames={totalFrames}/>;
