"""Render a data-driven pixel cutout rig to PNG, GIF and layered Aseprite."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from aseprite_io import write_aseprite


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def shift(x=0., y=0.):
    return np.array([[1., 0., x], [0., 1., y], [0., 0., 1.]])


def rotation(pivot, degrees):
    theta = math.radians(degrees)
    c, s = math.cos(theta), math.sin(theta)
    return shift(*pivot) @ np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]]) @ shift(-pivot[0], -pivot[1])


def transform(matrix, point):
    return (matrix @ np.array([*point, 1.]))[:2]


def curve(value, t):
    if isinstance(value, (int, float)):
        return float(value)
    if t <= value[0][0]:
        return value[0][1]
    for a, b in zip(value, value[1:]):
        if t <= b[0]:
            u = (t - a[0]) / (b[0] - a[0])
            return a[1] + (b[1] - a[1]) * u * u * (3 - 2 * u)
    return value[-1][1]


def validate_curve(value, loop):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError('Curve must be a finite number or at least two [time,value] keys')
    if any(not isinstance(k, list) or len(k) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in k) for k in value):
        raise ValueError('Invalid curve key')
    if value[0][0] != 0 or value[-1][0] != 1 or any(a[0] >= b[0] for a, b in zip(value, value[1:])):
        raise ValueError('Key times must strictly increase from 0 to 1')
    if loop and abs(value[0][1] - value[-1][1]) > 1e-8:
        raise ValueError('Loop curve endpoints differ')


def vector(value):
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        raise ValueError(f'Expected finite [x,y], got {value!r}')
    return value


def solve_arm(shoulder, elbow, wrist, moved_shoulder, target):
    a, e, k, q, goal = [np.asarray(p, dtype=float) for p in (shoulder, elbow, wrist, moved_shoulder, target)]
    l1, l2 = np.linalg.norm(e - a), np.linalg.norm(k - e)
    distance = np.linalg.norm(goal - q)
    if min(l1, l2, distance) <= 1e-9 or distance < abs(l1 - l2) - 1e-8 or distance > l1 + l2 + 1e-8:
        raise ValueError('IK target unreachable or zero-length chain; adjust pivots, target, or motion')
    direction = (goal - q) / distance
    along = (l1*l1 - l2*l2 + distance*distance) / (2*distance)
    offset = np.array([-direction[1], direction[0]]) * math.sqrt(max(0., l1*l1 - along*along))
    joint = min((q + along*direction + offset, q + along*direction - offset), key=lambda p: np.linalg.norm(p - e))

    def align(start, end, new_start, new_end):
        old, new = end - start, new_end - new_start
        angle = math.degrees(math.atan2(new[1], new[0]) - math.atan2(old[1], old[0]))
        return shift(*(new_start - start)) @ rotation(start, angle)

    upper, lower = align(a, e, q, joint), align(e, k, joint, goal)
    return upper, lower, [q, joint, goal], float(np.linalg.norm(transform(lower, k) - goal))


def pose(cfg, t):
    definitions = cfg['bones']
    matrices, visiting, guides = {}, set(), []

    def resolve(name):
        if name in matrices:
            return matrices[name]
        if name in visiting:
            raise ValueError(f'Bone hierarchy cycle at {name}')
        if name not in definitions:
            raise ValueError(f'Unknown bone {name}; define its parent or earlier IK chain')
        visiting.add(name)
        bone = definitions[name]
        parent = resolve(bone['parent']) if bone.get('parent') else np.eye(3)
        matrices[name] = parent @ shift(curve(bone.get('x', 0), t), curve(bone.get('y', 0), t)) @ rotation(bone['pivot'], curve(bone.get('rotation', 0), t))
        visiting.remove(name)
        return matrices[name]

    ik_error = 0.
    for chain in cfg.get('ik', []):
        parent = resolve(chain['parent']) if chain.get('parent') else np.eye(3)
        upper, lower, joints, error = solve_arm(chain['shoulder'], chain['elbow'], chain['wrist'], transform(parent, chain['shoulder']), chain['target'])
        matrices[chain['upper_bone']], matrices[chain['lower_bone']] = upper, lower
        guides.append(joints)
        ik_error = max(ik_error, error)
    for name, bone in definitions.items():
        m = resolve(name)
        if bone.get('tip'):
            guides.append([transform(m, bone['pivot']), transform(m, bone['tip'])])
    return matrices, guides, ik_error


def render_part(part, matrix, size):
    width, height = size
    src = part['array']
    px, py = part['position']
    sh, sw = src.shape[:2]
    corners = np.array([transform(matrix, p) for p in ((px, py), (px+sw, py), (px, py+sh), (px+sw, py+sh))])
    clipped = bool(np.any(corners.min(axis=0) < -1e-7) or np.any(corners.max(axis=0) > np.array(size) + 1e-7))
    lo = np.maximum(np.floor(corners.min(axis=0)).astype(int) - 1, [0, 0])
    hi = np.minimum(np.ceil(corners.max(axis=0)).astype(int) + 1, [width-1, height-1])
    if np.any(hi < lo):
        return None, clipped
    yy, xx = np.mgrid[lo[1]:hi[1]+1, lo[0]:hi[0]+1]
    inv = np.linalg.inv(matrix)
    sx = np.floor(inv[0,0]*(xx+.5) + inv[0,1]*(yy+.5) + inv[0,2]).astype(int) - px
    sy = np.floor(inv[1,0]*(xx+.5) + inv[1,1]*(yy+.5) + inv[1,2]).astype(int) - py
    valid = (sx >= 0) & (sy >= 0) & (sx < sw) & (sy < sh)
    array = np.zeros((*sx.shape, 4), dtype=np.uint8)
    array[valid] = src[sy[valid], sx[valid]]
    if not np.any(array[:,:,3]):
        return None, clipped
    return (Image.fromarray(array), tuple(int(v) for v in lo)), clipped


def composite(cels, size):
    image = Image.new('RGBA', size)
    for cel in cels:
        if cel is not None:
            image.alpha_composite(cel[0], cel[1])
    return image


def save_gif(path, frames, duration_ms, loop):
    # Build one palette from all frames; no frame-dependent color selection.
    sheet = Image.new('RGB', (frames[0].width * len(frames), frames[0].height))
    for index, frame in enumerate(frames):
        sheet.paste(frame, (index * frame.width, 0))
    palette = sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    options = {'loop': 0} if loop else {}
    quantized[0].save(path, save_all=True, append_images=quantized[1:], duration=duration_ms, optimize=False, disposal=1, **options)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    project, output = args.project.resolve(), args.output.resolve()
    if output.exists():
        raise ValueError(f'Output exists; choose a new folder: {output}')
    cfg, manifest = read_json(project/'motion.json'), read_json(project/'parts.json')
    size = tuple(manifest['canvas'])
    n, ms, loop = cfg['frames'], cfg['frame_ms'], cfg.get('loop', True)
    if len(size) != 2 or any(type(v) is not int or not 1 <= v <= 32767 for v in size):
        raise ValueError('Canvas width and height must be integers in 1..32767')
    if type(n) is not int or not 1 <= n <= 65535 or type(ms) is not int or not 10 <= ms <= 65530 or ms % 10:
        raise ValueError('frames must be 1..65535; frame_ms must be a multiple of 10 in 10..65530')
    if type(loop) is not bool or not isinstance(cfg.get('bones'), dict) or not cfg['bones']:
        raise ValueError('Expected boolean loop and nonempty bones mapping')
    background = cfg.get('background', [25, 28, 38])
    if len(background) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in background):
        raise ValueError('background must be [R,G,B] bytes')
    names = set(cfg['bones'])
    for name, bone in cfg['bones'].items():
        vector(bone['pivot'])
        if 'tip' in bone:
            vector(bone['tip'])
        for channel in ('rotation', 'x', 'y'):
            validate_curve(bone.get(channel, 0), loop)
    for chain in cfg.get('ik', []):
        for key in ('shoulder', 'elbow', 'wrist', 'target'):
            vector(chain[key])
        for key in ('upper_bone', 'lower_bone'):
            if chain[key] in names:
                raise ValueError(f'Duplicate IK bone name: {chain[key]}')
            names.add(chain[key])
    inputs = [{'file': f, 'sha256': hashlib.sha256((project/f).read_bytes()).hexdigest()} for f in ('parts.json', 'motion.json')]
    parts, ids = [], set()
    for original in sorted(manifest['parts'], key=lambda p: p.get('z', 0)):
        part = dict(original)
        if part['id'] in ids or part['bone'] not in names:
            raise ValueError(f'Duplicate part id or undefined bone: {part["id"]}')
        ids.add(part['id'])
        if any(type(v) is not int for v in vector(part['position'])):
            raise ValueError('Part position must contain integers')
        path = (project/part['file']).resolve()
        if not path.is_relative_to(project):
            raise ValueError('Copy part files into the project before rendering')
        inputs.append({'file': str(path.relative_to(project)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        with Image.open(path) as opened:
            image = opened.convert('RGBA')
        box = image.getchannel('A').getbbox()
        if box is None:
            raise ValueError(f'Empty alpha part: {part["id"]}')
        part['position'] = [part['position'][0]+box[0], part['position'][1]+box[1]]
        part['array'] = np.array(image.crop(box))
        parts.append(part)
    if not parts:
        raise ValueError('No parts')
    times = [i/n if loop else i/max(1, n-1) for i in range(n)]
    poses = [pose(cfg, t) for t in times]
    start, end = pose(cfg, 0)[0], pose(cfg, 1)[0]
    seam_error = max(float(np.abs(start[name] - end[name]).max()) for name in names)
    fixed_errors = {name: max(float(np.abs(p[0][name] - start[name]).max()) for p in poses) for name in cfg.get('fixed_bones', [])}
    if any(v > 1e-8 for v in fixed_errors.values()):
        raise ValueError(f'Fixed bone moved: {fixed_errors}')
    output.mkdir(parents=True)
    (output/'frames').mkdir()
    bind_cels = [render_part(p, np.eye(3), size)[0] for p in parts]
    bind = composite(bind_cels, size)
    bind.save(output/'bind.png')
    layers = [{'name': p.get('name', p['id'])} for p in parts]
    write_aseprite(output/'bind.aseprite', size, layers, [bind_cels], ms)
    layers += [{'name': 'BONE GUIDES - visual only', 'visible': False}, {'name': 'REFERENCE - bind', 'visible': False}]
    native, previews, guides, cropped, absent = [], [], [], set(), set()
    bg = Image.new('RGBA', size, tuple(background) + (255,))
    for index, (matrices, joints, _) in enumerate(poses):
        cels = []
        for part in parts:
            cel, clipped = render_part(part, matrices[part['bone']], size)
            cels.append(cel)
            if clipped:
                cropped.add(part['id'])
            if cel is None:
                absent.add(part['id'])
        image = composite(cels, size)
        image.save(output/'frames'/f'{index:05d}.png')
        flat = bg.copy(); flat.alpha_composite(image)
        previews.append(flat.convert('RGB'))
        guide = Image.new('RGBA', size)
        draw = ImageDraw.Draw(guide)
        for joint in joints:
            points = [tuple(int(v) for v in np.round(p)) for p in joint]
            draw.line(points, fill=(71, 217, 226, 255), width=1)
            for x, y in points:
                draw.rectangle((x-1, y-1, x+1, y+1), fill=(255, 222, 126, 255))
        flat.alpha_composite(guide); guides.append(flat.convert('RGB'))
        native.append(cels + [(guide, (0, 0)), (bind, (0, 0)) if index == 0 else None])
    write_aseprite(output/'animation.aseprite', size, layers, native, ms)
    save_gif(output/'preview.gif', previews, ms, loop)
    save_gif(output/'bones.gif', guides, ms, loop)
    selected = sorted(set([0, n//4, n//2, 3*n//4, n-1]))
    contact = Image.new('RGB', (size[0]*len(selected), size[1]+20), (235, 236, 240))
    draw = ImageDraw.Draw(contact)
    for column, frame in enumerate(selected):
        contact.paste(previews[frame], (column*size[0], 20))
        draw.text((column*size[0]+4, 4), f'{frame*ms/1000:.2f}s', fill='black')
    contact.save(output/'contact.png')
    for record in inputs:
        if hashlib.sha256((project/record['file']).read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Input changed during rendering')
    info = {'canvas': size, 'frames': n, 'frame_ms': ms, 'duration_ms': n*ms, 'loop': loop, 'background': background,
            'parts': len(parts), 'layers': len(layers), 'bone_count': len(names), 'project': os.path.relpath(project, output),
            'inputs': inputs, 'bounds_crossing_parts': sorted(cropped), 'fully_absent_parts': sorted(absent),
            'fixed_bone_max_matrix_error': fixed_errors, 'max_ik_target_error': max(p[2] for p in poses),
            'loop_endpoint_matrix_error': seam_error, 'visual_review': 'not performed by this script',
            'aseprite_app_test': 'not performed by this script'}
    save_json(output/'render_info.json', info)
    print(json.dumps({k: v for k, v in info.items() if k != 'inputs'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
