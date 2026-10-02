"""Write RGBA Aseprite files with normal-blend layers and compressed cels."""
import struct
import zlib


def write_aseprite(path, size, layers, frames, duration_ms):
    def chunk(kind, payload):
        return struct.pack('<IH', len(payload) + 6, kind) + payload

    def string(value):
        raw = value.encode('utf-8')
        return struct.pack('<H', len(raw)) + raw

    if not frames or len(frames) > 65535 or len(layers) > 65533:
        raise ValueError('Aseprite frame/layer count is out of range')
    header = bytearray(128)
    width, height = size
    struct.pack_into('<I5HIH', header, 0, 0, 0xA5E0, len(frames), width, height, 32, 1, duration_ms)
    header[34:36] = bytes([1, 1])
    with path.open('wb') as stream:
        stream.write(header)
        for index, frame in enumerate(frames):
            if len(frame) != len(layers):
                raise ValueError('Cel/layer count mismatch')
            chunks = []
            if index == 0:
                for layer in layers:
                    flags = 2 | int(layer.get('visible', True))
                    payload = struct.pack('<6HB3x', flags, 0, 0, 0, 0, 0, 255)
                    chunks.append(chunk(0x2004, payload + string(layer['name'])))
            for layer_index, cel in enumerate(frame):
                if cel is None:
                    continue
                image, (x, y) = cel
                payload = struct.pack('<HhhBHh5xHH', layer_index, x, y, 255, 2, 0, image.width, image.height)
                chunks.append(chunk(0x2005, payload + zlib.compress(image.convert('RGBA').tobytes(), 6)))
            body = b''.join(chunks)
            stream.write(struct.pack('<IHHH2xI', 16 + len(body), 0xF1FA, min(len(chunks), 65535), duration_ms, len(chunks)))
            stream.write(body)
        total = stream.tell()
        stream.seek(0)
        stream.write(struct.pack('<I', total))
