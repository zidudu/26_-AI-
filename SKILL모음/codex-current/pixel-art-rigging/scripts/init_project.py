"""Copy supplied PNG parts and tools into a new portable project."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from PIL import Image


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--motion', type=Path)
    args = parser.parse_args()
    src, dest = args.manifest.resolve(), args.destination.resolve()
    if dest.exists():
        raise ValueError(f'Destination already exists; choose a new folder: {dest}')
    manifest = json.loads(src.read_text(encoding='utf-8-sig'))
    motion = json.loads(args.motion.read_text(encoding='utf-8-sig')) if args.motion else None
    if not manifest.get('parts') or len(manifest.get('canvas', [])) != 2:
        raise ValueError('Manifest needs canvas [width,height] and nonempty parts')
    prepared, sources, bones, ids = [], [], {}, set()
    for index, original in enumerate(manifest['parts']):
        part = dict(original)
        name = str(part['id'])
        if name in ids:
            raise ValueError(f'Duplicate part id: {name}')
        ids.add(name)
        relative = part.get('file') or f"parts/{int(part['index']):02d}_{name}.png"
        image_path = (src.parent / relative).resolve()
        if image_path.suffix.lower() != '.png':
            raise ValueError(f'Expected PNG: {image_path}')
        with Image.open(image_path) as image:
            image.load()
            width, height = image.size
        position = part.get('position', [0, 0])
        if len(position) != 2 or any(type(v) is not int for v in position):
            raise ValueError(f'Part {name}: position must contain two integers')
        slug = re.sub(r'[^\w-]', '_', name)[:80] or 'part'
        target = f'parts/{index:03d}_{slug}.png'
        bone = part.get('bone') or (motion or {}).get('part_to_bone', {}).get(part.get('rig')) or part.get('rig') or name
        part.update(file=target, position=position, bone=bone, z=part.get('z', index))
        prepared.append((part, image_path))
        sources.append({'original': str(image_path), 'copy': target, 'sha256': hashlib.sha256(image_path.read_bytes()).hexdigest()})
        if bone != 'root' and bone not in bones:
            x, y = position
            bones[bone] = {'parent': 'root', 'pivot': [x + width / 2, y + height / 2],
                           'tip': [x + width / 2, y + height], 'rotation': [[0, 0], [1, 0]]}
    dest.mkdir(parents=True)
    (dest / 'parts').mkdir()
    for part, path in prepared:
        shutil.copyfile(path, dest / part['file'])
    write_json(dest / 'parts.json', {'canvas': manifest['canvas'], 'parts': [p for p, _ in prepared]})
    if motion is None:
        motion = {'frames': 60, 'frame_ms': 80, 'loop': True, 'background': [25, 28, 38],
                  'fixed_bones': ['root'], 'bones': {'root': {'parent': None, 'pivot': [0, 0], 'tip': [0, 12]}, **bones}}
    write_json(dest / 'motion.json', motion)
    write_json(dest / 'sources.json', sources)
    for filename in ('render_rig.py', 'verify_rig.py', 'aseprite_io.py', 'requirements.txt'):
        shutil.copyfile(Path(__file__).with_name(filename), dest / filename)
    print(json.dumps({'project': str(dest), 'parts': len(prepared), 'motion': 'supplied' if args.motion else 'static scaffold: set pivots and curves'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
