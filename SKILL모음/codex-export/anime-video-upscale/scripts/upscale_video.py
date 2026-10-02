"""Inspect, sample, or upscale a CFR SDR anime video using Real-ESRGAN ncnn."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def find_ffmpeg(explicit):
    if explicit:
        return str(Path(explicit).resolve(strict=True))
    if found := shutil.which('ffmpeg'):
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit('Provide --ffmpeg or install imageio-ffmpeg in the task venv.')


def run(command, log):
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run([str(x) for x in command], stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'Command failed; see {log}\n' + log.read_text(encoding='utf-8', errors='replace')[-1800:])
    return log.read_text(encoding='utf-8', errors='replace')


def inspect(ffmpeg, path, folder, label):
    # showinfo exposes integer PTS and rational rate without needing a separate ffprobe.
    text = run([ffmpeg, '-hide_banner', '-nostdin', '-nostats', '-xerror', '-err_detect', 'explode',
                '-i', path, '-map', '0:V:0', '-an', '-vf', 'showinfo', '-fps_mode', 'passthrough',
                '-f', 'null', '-'], folder / f'{label}_inspect.log')
    configs = re.findall(r'config in time_base:\s*(\d+/\d+), frame_rate:\s*(\d+/\d+)', text)
    if not configs or len(set(configs)) != 1:
        raise ValueError('Missing or changing time base / frame rate')
    tick, rate = (Fraction(v) for v in configs[0])
    rows = re.findall(r'\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:[^\r\n]+', text)
    frame_lines = [line for line in text.splitlines() if re.search(r'\bn:\s*\d+\s+pts:', line)]
    if not rows or rate <= 0 or tick <= 0:
        raise ValueError('Could not read frames or a positive frame rate')
    if [int(row[0]) for row in rows] != list(range(len(rows))):
        raise ValueError('Non-contiguous decoded frame indices')
    times = [int(pts) * tick for _, pts in rows]
    cfr = abs(times[0]) <= tick and all(abs(t - Fraction(i, 1) / rate) <= tick for i, t in enumerate(times))
    durations = [int(v) * tick for v in re.findall(r'\bduration:\s*(\d+)', '\n'.join(frame_lines))]
    if durations and any(d > 0 and abs(d - 1 / rate) > tick for d in durations):
        cfr = False
    sizes = set(re.findall(r'\bs:(\d+)x(\d+)', '\n'.join(frame_lines)))
    formats = set(re.findall(r'\bfmt:(\S+)', '\n'.join(frame_lines)))
    sars = set(re.findall(r'\bsar:(\S+)', '\n'.join(frame_lines)))
    colors = re.findall(r'color_range:(\S+) color_space:(\S+) color_primaries:(\S+) color_trc:(\S+)', text)
    header = text.split('Stream mapping:')[0]
    audio = re.findall(r'Stream #0:\d+[^\r\n]*Audio: ([^,\s]+)', header)
    if len(sizes) != 1:
        raise ValueError('Changing frame dimensions are unsupported')
    width, height = map(int, next(iter(sizes)))
    problems = []
    if not cfr:
        problems.append('variable frame timing or nonzero video start; preserve timestamps with a dedicated workflow')
    if not formats <= {'yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p', 'yuv444p', 'yuvj444p', 'nv12', 'rgb24', 'bgr24', 'gray'}:
        problems.append('unsupported pixel format (e.g. HDR, high bit depth, or alpha)')
    if not sars <= {'0/1', '1/1'}:
        problems.append('non-square pixels')
    if re.search(r'\bi:[TB]', '\n'.join(frame_lines)):
        problems.append('interlaced video')
    if any(c[3] in {'smpte2084', 'arib-std-b67'} or c[2] == 'bt2020' for c in colors):
        problems.append('HDR or wide gamut')
    if re.search(r'rotation of\s+(?!-?0\.0+\b)-?\d', header):
        problems.append('rotation metadata; normalize orientation separately')
    info = dict(width=width, height=height, frames=len(rows), fps=str(rate), duration_seconds=float(len(rows) / rate),
                audio_codecs=audio, pixel_formats=sorted(formats), colors=sorted(set(colors)), cfr=cfr, unsupported=problems)
    (folder / f'{label}_info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    return info


def snapshot(ffmpeg, video, index, output, folder, tag):
    run([ffmpeg, '-hide_banner', '-nostdin', '-n', '-i', video, '-map', '0:V:0', '-an',
         '-vf', f'select=eq(n\\,{index})', '-frames:v', '1', '-update', '1', output], folder / f'{tag}.log')
    if not output.is_file():
        raise RuntimeError(f'Missing snapshot: {output}')


def comparison(source, enhanced, output, crop=None):
    with Image.open(source) as im:
        original = im.convert('RGB')
    with Image.open(enhanced) as im:
        result = im.convert('RGB')
    scale = result.width / original.width
    if crop:
        x, y, w, h = map(int, crop.split(','))
    else:
        w, h = max(1, original.width // 3), max(1, original.height // 3)
        x, y = (original.width - w) // 2, (original.height - h) // 2
    if min(x, y) < 0 or min(w, h) <= 0 or x+w > original.width or y+h > original.height:
        raise ValueError('Crop must fit in source coordinates: x,y,width,height')
    box = tuple(round(v * scale) for v in (x, y, x+w, y+h))
    orig_large = original.resize(result.size, Image.Resampling.LANCZOS)
    width = 640
    full_h = round(width * result.height / result.width)
    crop_h = round(width * h / w)
    canvas = Image.new('RGB', (2*width, full_h + crop_h + 88), '#111827')
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 21)
    except OSError:
        font = ImageFont.load_default()
    for col, (label, im) in enumerate([('Original at same display size', orig_large), ('AI upscale', result)]):
        draw.text((col*width+12, 10), label, font=font, fill='white')
        canvas.paste(im.resize((width, full_h), Image.Resampling.LANCZOS), (col*width, 42))
        canvas.paste(im.crop(box).resize((width, crop_h), Image.Resampling.LANCZOS), (col*width, full_h+84))
    canvas.save(output, quality=95, subsampling=0)


def audio_hash(ffmpeg, path, folder, tag):
    text = run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-i', path, '-map', '0:a',
                '-c:a', 'copy', '-f', 'streamhash', '-hash', 'sha256', '-'], folder / f'{tag}_audio.log')
    values = re.findall(r'^\d+,a,SHA256=([a-fA-F0-9]+)', text, re.M)
    if not values:
        raise ValueError('Could not verify audio packet hashes')
    return values


def publish(source, target):
    # Exclusive creation protects a file that appeared after the initial checks too.
    with source.open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['inspect', 'sample', 'run'])
    parser.add_argument('input', type=Path)
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--engine-dir', type=Path)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--scale', type=int, choices=[2, 3, 4], default=3)
    parser.add_argument('--tile', type=int, default=256)
    parser.add_argument('--gpu', default='auto', help='NCNN device index or auto')
    parser.add_argument('--threads', default='2:2:2')
    parser.add_argument('--crf', type=int, default=16)
    parser.add_argument('--audio', choices=['copy', 'aac'], default='copy')
    parser.add_argument('--crop', help='Comparison detail region in source pixels: x,y,width,height')
    args = parser.parse_args()
    source = args.input.resolve(strict=True)
    if not source.is_file():
        parser.error('Input must be a video file')
    if args.tile < 32 or not 0 <= args.crf <= 51:
        parser.error('Tile must be >=32; CRF must be 0..51')
    if args.mode == 'run':
        if not args.output:
            parser.error('run requires --output')
        output = args.output.resolve()
        if output == source or output.suffix.lower() != '.mp4':
            parser.error('Use a separate .mp4 output')
        sidecar = output.with_name(output.stem + '_validation.json')
        preview = output.with_name(output.stem + '_comparison.jpg')
        if any(path.exists() for path in (output, sidecar, preview)):
            parser.error('Output or sidecar exists; choose a new output name')
        output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode != 'inspect':
        if not args.engine_dir:
            parser.error('sample/run requires --engine-dir')
        engine = (args.engine_dir / 'realesrgan-ncnn-vulkan.exe').resolve(strict=True)
        models = engine.parent / 'models'
        for ext in ('bin', 'param'):
            if not (models / f'realesr-animevideov3-x{args.scale}.{ext}').is_file():
                parser.error('Model files are missing')
    ffmpeg = find_ffmpeg(args.ffmpeg)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f'{args.mode}-', dir=args.work_dir.resolve()))
    started = time.time()

    def status(phase):
        data = dict(phase=phase, work_dir=str(folder), elapsed_seconds=round(time.time()-started, 1))
        (folder / 'status.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
        print(json.dumps(data), flush=True)

    try:
        source_hash = digest(source)
        status('Inspecting source timing, format, and decoded frames')
        info = inspect(ffmpeg, source, folder, 'source')
        picks = sorted(set([0, info['frames']//2, info['frames']-1]))
        for index in picks:
            snapshot(ffmpeg, source, index, folder/f'source_{index:08d}.png', folder, f'snapshot_{index}')
        print(json.dumps(info, indent=2), flush=True)
        if args.mode == 'inspect':
            status('Inspection complete; inspect the sample PNGs before choosing a model')
            return
        if info['unsupported']:
            raise ValueError('Unsupported by this helper: ' + '; '.join(info['unsupported']))
        middle = info['frames']//2
        src_mid = folder/f'source_{middle:08d}.png'

        def upscale(inp, out):
            command = [engine, '-i', inp, '-o', out, '-m', models, '-n', 'realesr-animevideov3',
                       '-s', args.scale, '-t', args.tile, '-j', args.threads, '-f', 'png']
            if args.gpu != 'auto':
                command += ['-g', args.gpu]
            run(command, folder/'engine.log')

        if args.mode == 'sample':
            status('Upscaling one representative frame')
            sample = folder/'sample_upscaled.png'
            upscale(src_mid, sample)
            with Image.open(sample) as im:
                if im.size != (info['width']*args.scale, info['height']*args.scale):
                    raise ValueError('Sample dimensions do not match requested scale')
            comparison(src_mid, sample, folder/'sample_comparison.jpg', args.crop)
            if digest(source) != source_hash:
                raise ValueError('Source changed during sample processing')
            status('Sample ready; visually inspect sample_comparison.jpg before run')
            return

        estimate = int(info['frames'] * info['width'] * info['height'] * 3 * (1+args.scale**2) * 1.1)
        if shutil.disk_usage(folder).free < estimate + 512*1024**2:
            raise ValueError(f'Insufficient scratch space. Conservative requirement: {estimate/1024**3:.1f} GiB plus 512 MiB')
        frames, enhanced = folder/'source_frames', folder/'upscaled_frames'
        frames.mkdir()
        enhanced.mkdir()
        status('Extracting lossless source frames')
        run([ffmpeg, '-hide_banner', '-nostdin', '-n', '-xerror', '-i', source, '-map', '0:V:0', '-an',
             '-fps_mode', 'passthrough', '-pix_fmt', 'rgb24', '-compression_level', '2', frames/'frame%08d.png'], folder/'extract.log')
        expected = [f'frame{i:08d}.png' for i in range(1, info['frames']+1)]
        if sorted(p.name for p in frames.glob('*.png')) != expected:
            raise ValueError('Extracted frame count or names differ from decoded source')
        status('AI upscaling; progress = completed PNGs in upscaled_frames / source frame count')
        upscale(frames, enhanced)
        if sorted(p.name for p in enhanced.glob('*.png')) != expected:
            raise ValueError('Upscaled frame count or names differ from source')
        for name in expected:
            with Image.open(enhanced/name) as im:
                if im.size != (info['width']*args.scale, info['height']*args.scale):
                    raise ValueError(f'Wrong dimensions: {name}')
        status('Encoding H264 at the original rational frame rate')
        encoded = folder/'encoded.mp4'
        command = [ffmpeg, '-hide_banner', '-nostdin', '-n', '-framerate', info['fps'], '-start_number', '1',
                   '-i', enhanced/'frame%08d.png', '-i', source, '-map', '0:v:0', '-map', '1:a?', '-map_metadata', '-1',
                   '-vf', 'scale=iw:ih:out_color_matrix=bt709:out_range=tv,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709', '-c:v', 'libx264', '-preset', 'medium',
                   '-crf', args.crf, '-pix_fmt', 'yuv420p', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                   '-colorspace', 'bt709', '-color_range', 'tv', '-c:a', args.audio]
        if args.audio == 'aac':
            command += ['-b:a', '192k']
        command += ['-movflags', '+faststart', encoded]
        run(command, folder/'encode.log')
        status('Verifying final video and audio')
        result = inspect(ffmpeg, encoded, folder, 'output')
        for field in ('frames', 'fps', 'duration_seconds'):
            if result[field] != info[field]:
                raise ValueError(f'Output {field} differs from source')
        if (result['width'], result['height']) != (info['width']*args.scale, info['height']*args.scale):
            raise ValueError('Output dimensions differ from requested scale')
        if not result['cfr'] or len(result['audio_codecs']) != len(info['audio_codecs']):
            raise ValueError('Timing or audio stream count mismatch')
        if result['colors'] != [('tv', 'bt709', 'bt709', 'bt709')]:
            raise ValueError('Encoded frame color tags differ from the intended SDR BT.709 output')
        audio_exact = None
        if info['audio_codecs']:
            run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-xerror', '-i', encoded,
                 '-map', '0:a', '-f', 'null', '-'], folder/'audio_decode.log')
            if args.audio == 'copy':
                audio_exact = audio_hash(ffmpeg, source, folder, 'source') == audio_hash(ffmpeg, encoded, folder, 'output')
                if not audio_exact:
                    raise ValueError('Copied audio packet hashes differ')
        final_mid = folder/'final_middle.png'
        snapshot(ffmpeg, encoded, middle, final_mid, folder, 'final_snapshot')
        comparison(src_mid, final_mid, folder/'comparison.jpg', args.crop)
        if digest(source) != source_hash:
            raise ValueError('Source file changed during processing')
        report = dict(source=str(source), output=str(output), source_sha256=source_hash, output_sha256=digest(encoded),
                      source_unchanged=True, input_info=info, output_info=result, audio_mode=args.audio,
                      audio_packet_hashes_match=audio_exact, full_decode_passed=True,
                      model=f'realesr-animevideov3-x{args.scale}', tile=args.tile, crf=args.crf,
                      visual_review='pending; inspect the final comparison and motion before claiming visual quality',
                      work_dir=str(folder), elapsed_seconds=round(time.time()-started, 1))
        (folder/'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        publish(encoded, output)
        publish(folder/'comparison.jpg', preview)
        publish(folder/'validation.json', sidecar)
        status('Processing and structural verification complete; visual review remains')
        print(json.dumps({'output': str(output), 'comparison': str(preview), 'validation': str(sidecar)}, indent=2), flush=True)
    except Exception as error:
        status('FAILED: '+str(error))
        raise


if __name__ == '__main__':
    main()
