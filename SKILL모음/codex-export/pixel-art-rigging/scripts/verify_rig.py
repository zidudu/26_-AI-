"""Independently decode generated Aseprite/GIF files and compare all PNG frames."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib
import numpy as np
from PIL import Image


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decode_ase(path):
    data = path.read_bytes()
    require(len(data) >= 128, 'Truncated Aseprite header')
    length, magic, count, w, h, depth = struct.unpack_from('<I5H', data)
    require(length == len(data) and magic == 0xA5E0 and depth == 32, 'Invalid Aseprite size, signature, or depth')
    layers, images, durations = [], [], []
    cursor = 128
    for frame in range(count):
        require(cursor + 16 <= len(data), 'Missing frame header')
        frame_bytes, frame_magic, old_count, duration = struct.unpack_from('<IHHH', data, cursor)
        new_count = struct.unpack_from('<I', data, cursor + 12)[0]
        end = cursor + frame_bytes
        require(frame_magic == 0xF1FA and frame_bytes >= 16 and end <= len(data), 'Invalid frame structure')
        cursor += 16
        cels = {}
        for _ in range(new_count or old_count):
            require(cursor + 6 <= end, 'Missing chunk header')
            size, kind = struct.unpack_from('<IH', data, cursor)
            require(size >= 6 and cursor + size <= end, 'Invalid chunk length')
            payload = data[cursor + 6:cursor + size]
            cursor += size
            if kind == 0x2004:
                require(frame == 0 and len(payload) >= 18, 'Unexpected layer definition')
                flags, layer_type, child_level = struct.unpack_from('<3H', payload)
                blend = struct.unpack_from('<H', payload, 10)[0]
                opacity = payload[12]
                text_length = struct.unpack_from('<H', payload, 16)[0]
                require(layer_type == 0 and child_level == 0 and blend == 0 and opacity == 255, 'Only flat normal RGBA layers are supported')
                require(len(payload) == 18 + text_length, 'Invalid layer name length')
                layers.append({'name': payload[18:].decode('utf-8'), 'visible': bool(flags & 1)})
            elif kind == 0x2005:
                require(len(payload) >= 20, 'Truncated cel')
                layer, x, y, opacity, cel_type, zindex = struct.unpack_from('<HhhBHh', payload)
                require(layer < len(layers) and layer not in cels, 'Invalid or duplicate cel layer')
                require(cel_type == 2 and opacity == 255 and zindex == 0, 'Expected unshifted compressed RGBA cel')
                cw, ch = struct.unpack_from('<HH', payload, 16)
                raw = zlib.decompress(payload[20:])
                require(cw > 0 and ch > 0 and len(raw) == cw*ch*4, 'Invalid RGBA cel size')
                cels[layer] = (Image.frombytes('RGBA', (cw, ch), raw), (x, y))
            else:
                raise ValueError(f'Unsupported chunk in generated file: {kind:#x}')
        require(cursor == end and duration > 0, 'Frame byte count or duration mismatch')
        image = Image.new('RGBA', (w, h))
        for index, layer in enumerate(layers):
            if layer['visible'] and index in cels:
                image.alpha_composite(*cels[index])
        images.append(image)
        durations.append(duration)
    require(cursor == len(data), 'Trailing bytes after frames')
    return images, durations, layers


def check_gif(path, expected, ms, loop, compare=True):
    elapsed, decoded, error = 0, 0, 0.
    with Image.open(path) as gif:
        require(gif.size == expected[0].size, f'GIF dimensions differ: {path.name}')
        require((gif.info.get('loop') == 0) if loop else ('loop' not in gif.info), 'GIF loop setting differs')
        for index in range(gif.n_frames):
            gif.seek(index)
            rgb = np.asarray(gif.convert('RGB'), dtype=np.int16)
            duration = gif.info.get('duration', 0)
            require(duration > 0 and duration % ms == 0, 'GIF duration is not an expected frame multiple')
            for expected_index in range(elapsed//ms, (elapsed+duration)//ms):
                require(expected_index < len(expected), 'GIF is too long')
                if compare:
                    error += float(np.abs(rgb - np.asarray(expected[expected_index], dtype=np.int16)).mean())
            elapsed += duration
            decoded += 1
    require(elapsed == len(expected)*ms, 'GIF total duration differs')
    return {'stored_frames': decoded, 'duration_ms': elapsed, 'gif_rgb_mae': error/len(expected) if compare else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    info = json.loads((output/'render_info.json').read_text(encoding='utf-8'))
    n, ms, size = info['frames'], info['frame_ms'], tuple(info['canvas'])
    paths = sorted((output/'frames').glob('*.png'))
    require([p.name for p in paths] == [f'{i:05d}.png' for i in range(n)], 'PNG frame set differs')
    frames = []
    for path in paths:
        with Image.open(path) as image:
            require(image.mode == 'RGBA' and image.size == size, 'Expected original-size RGBA frames')
            frames.append(image.copy())
    ase_frames, durations, layers = decode_ase(output/'animation.aseprite')
    require(len(ase_frames) == n and durations == [ms]*n and len(layers) == info['layers'], 'Aseprite counts or duration differ')
    for index, (actual, expected) in enumerate(zip(ase_frames, frames)):
        require(np.array_equal(np.asarray(actual), np.asarray(expected)), f'Aseprite pixels differ at frame {index}')
    bind_frames, bind_ms, bind_layers = decode_ase(output/'bind.aseprite')
    with Image.open(output/'bind.png') as bind:
        require(len(bind_frames) == 1 and bind_ms == [ms] and len(bind_layers) == info['parts'], 'Bind structure differs')
        require(np.array_equal(np.asarray(bind_frames[0]), np.asarray(bind.convert('RGBA'))), 'Bind pixels differ')
    previews = []
    for frame in frames:
        flat = Image.new('RGBA', size, tuple(info['background'])+(255,))
        flat.alpha_composite(frame)
        previews.append(flat.convert('RGB'))
    gif = check_gif(output/'preview.gif', previews, ms, info['loop'])
    bone_gif = check_gif(output/'bones.gif', previews, ms, info['loop'], compare=False)
    project = (output/info['project']).resolve()
    for record in info['inputs']:
        require(hashlib.sha256((project/record['file']).read_bytes()).hexdigest() == record['sha256'], f'Input changed since render: {record["file"]}')
    origins = {'originals_verified': 0, 'originals_unavailable': 0, 'originals_changed': [], 'copies_edited_since_import': []}
    if (project/'sources.json').is_file():
        for source in json.loads((project/'sources.json').read_text(encoding='utf-8')):
            original = Path(source['original'])
            if not original.is_file():
                origins['originals_unavailable'] += 1
            elif hashlib.sha256(original.read_bytes()).hexdigest() == source['sha256']:
                origins['originals_verified'] += 1
            else:
                origins['originals_changed'].append(source['original'])
            if hashlib.sha256((project/source['copy']).read_bytes()).hexdigest() != source['sha256']:
                origins['copies_edited_since_import'].append(source['copy'])
    unique = len({hashlib.sha256(f.tobytes()).hexdigest() for f in frames})
    report = {'passed': True, 'png_frames': n, 'distinct_rgba_frames': unique, 'aseprite_frames': len(ase_frames),
              'aseprite_pixel_mismatches': 0, 'layers': len(layers), 'preview': gif, 'bones': bone_gif,
              'inputs_verified': len(info['inputs']), 'origins': origins,
              'warnings': ['Animation is static; configure motion before delivering an animation'] if unique == 1 else [],
              'visual_review': 'not performed by this verifier', 'aseprite_app_test': 'not performed by this verifier'}
    (output/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
