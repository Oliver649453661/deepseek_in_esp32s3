"""Build a bounded, checksummed JPEG-frame pack from the 30 licensed WebM clips.

Use the same 12 fps cadence as the upstream GIF previews, over each ENTIRE clip.
Frames are decoded from the original 640x360 WebM for sharper enlarged artwork.
Requires Pillow and imageio-ffmpeg. Does not execute upstream plugin code.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import struct
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
import zlib

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageStat, ImageChops

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "pc2005"
OUT = ROOT / "generated"
WIDTH, HEIGHT, FPS = 128, 148, 12
SCREEN_Y = 12
# All source clips share a 640x360 stage. Do not fit separate prop bounding boxes.
# The standing character is ~132 px tall; source feet y=330 land at screen y=158.
CAMERA_SCALE = 0.48
SOURCE_ANCHOR = (320.0, 330.0)
TARGET_ANCHOR = (64.0, 146.0)
PACK_LIMIT = 0xDE0000
MAGIC = b"PETMJPG8"


def download(action):
    entry = action["webm"]
    path = SOURCE / (action["id"] + ".webm")
    if path.exists():
        data = path.read_bytes()
        if hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == entry["sha"]:
            return path
    error = None
    for attempt in range(4):
        try:
            url = "https://raw.githubusercontent.com/PC2005-cloud/dsh-pet/main/" + quote(entry["path"])
            with urlopen(Request(url, headers={"User-Agent": "ESP32-personal-pet-import"}), timeout=45) as response:
                data = response.read()
            digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if digest != entry["sha"]:
                raise ValueError("Upstream asset changed: " + entry["path"])
            path.write_bytes(data)
            return path
        except Exception as exc:
            error = exc
            time.sleep(attempt + 1)
    raise RuntimeError(f"Could not fetch {action['id']}: {error}")


def decode_clip(path):
    reader = imageio_ffmpeg.read_frames(
        str(path), pix_fmt="rgba", bits_per_pixel=32,
        input_params=["-c:v", "libvpx-vp9"],
        output_params=["-vf", f"fps={FPS}", "-threads", "1"],
    )
    meta = next(reader)
    frames, boxes = [], []
    for data in reader:
        frame = Image.frombytes("RGBA", meta["size"], data)
        box = frame.getchannel("A").point(lambda a: 255 if a >= 8 else 0).getbbox()
        if box:
            boxes.append(box)
        frames.append(frame)
    if not frames or not boxes:
        raise ValueError(f"Empty/transparent clip: {path.name}")
    if all(frame.getchannel("A").getextrema() == (255, 255) for frame in frames):
        raise ValueError(f"Alpha was lost while decoding {path.name}")
    duration = round(meta["duration"] * 1000)
    if abs(len(frames) / FPS * 1000 - duration) > 100:
        raise ValueError(f"Truncated clip: {path.name}, {len(frames)} frames, {duration} ms")
    # Keep bounds only for diagnostics. Props must never change the character scale/position.
    union = [min(b[0] for b in boxes), min(b[1] for b in boxes),
             max(b[2] for b in boxes), max(b[3] for b in boxes)]
    if tuple(meta["size"]) != (640, 360):
        raise ValueError("Source stage must be 640x360 for the shared camera")
    left = SOURCE_ANCHOR[0] - TARGET_ANCHOR[0] / CAMERA_SCALE
    top = SOURCE_ANCHOR[1] - TARGET_ANCHOR[1] / CAMERA_SCALE
    camera = (1 / CAMERA_SCALE, 0, left, 0, 1 / CAMERA_SCALE, top)
    output = []
    for frame in frames:
        scaled = frame.transform((WIDTH, HEIGHT), Image.Transform.AFFINE, camera, Image.Resampling.BICUBIC)
        black = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255))
        black.alpha_composite(scaled)
        output.append(black.convert("RGB"))
    delays = [round((i + 1) * duration / len(frames)) - round(i * duration / len(frames))
              for i in range(len(frames))]
    return output, delays, dict(source_size=meta["size"], source_fps=meta["fps"],
                              source_content_bounds=union, camera_scale=CAMERA_SCALE,
                              source_anchor=SOURCE_ANCHOR, target_anchor=TARGET_ANCHOR,
                              source_window=[left, top, left + WIDTH / CAMERA_SCALE, top + HEIGHT / CAMERA_SCALE],
                              duration_ms=duration, frames=len(frames))


def encode_frame(frame, quality):
    stream = io.BytesIO()
    frame.save(stream, "JPEG", quality=quality, subsampling=2, optimize=True, progressive=False)
    return stream.getvalue()


def jpeg_prefix_size(jpeg):
    """The identical SOI/JFIF/quantization/dimension headers need storing only once."""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("Not a JPEG")
    position = 2
    while position + 4 <= len(jpeg):
        if jpeg[position] != 0xFF:
            break
        marker = jpeg[position + 1]
        if marker == 0xC4:  # Huffman tables depend on each image; keep these per frame.
            return position
        length = int.from_bytes(jpeg[position + 2:position + 4], "big")
        if length < 2:
            break
        position += 2 + length
    raise ValueError("JPEG has no supported shared prefix")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality", type=int, default=65)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "actions.json").read_text(encoding="utf-8"))
    actions = manifest["actions"]
    if len(actions) != 30 or actions[0]["id"] != "lion":
        raise ValueError("Expected exactly 30 actions, with lion dance first")
    SOURCE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(download, actions))
    print(f"Verified {len(paths)} source WebM files", flush=True)
    if args.probe:
        for index in (0, 1, 7):
            frames, delays, meta = decode_clip(paths[index])
            sizes = {q: sum(len(encode_frame(f, q)) for f in frames) for q in (75, 80, 85, 90)}
            frames[len(frames)//2].save(OUT / (actions[index]["id"] + "-probe.png"))
            print(json.dumps(dict(id=actions[index]["id"], **meta, encoded_sizes=sizes)), flush=True)
        return

    pack = bytearray(64)
    frame_records, action_records, report, thumbnails = [], [], [], []
    max_jpeg = 0
    jpeg_prefix = None
    for index, (action, path) in enumerate(zip(actions, paths)):
        frames, delays, meta = decode_clip(path)
        first = len(frame_records)
        begin = len(pack)
        errors = []
        for n, (frame, delay) in enumerate(zip(frames, delays)):
            jpeg = encode_frame(frame, args.quality)
            prefix_size = jpeg_prefix_size(jpeg)
            prefix = jpeg[:prefix_size]
            if jpeg_prefix is None:
                jpeg_prefix = prefix
                pack.extend(prefix)
            if prefix != jpeg_prefix:
                raise ValueError("JPEG prefix changed; frames must use one format/quality/dimension")
            stored_frame = jpeg[prefix_size:]
            if len(jpeg) > 65535:
                raise ValueError("JPEG exceeds 16-bit frame length")
            with Image.open(io.BytesIO(jpeg)) as decoded:
                decoded.load()
                if decoded.size != (WIDTH, HEIGHT) or decoded.info.get("progressive"):
                    raise ValueError("Decoder-incompatible JPEG")
                errors.append(sum(ImageStat.Stat(ImageChops.difference(frame, decoded.convert("RGB"))).mean) / 3)
            frame_records.append((len(pack), len(stored_frame), delay, zlib.crc32(jpeg)))
            pack.extend(stored_frame)
            max_jpeg = max(max_jpeg, len(jpeg))
            if n == len(frames) // 2:
                with Image.open(io.BytesIO(jpeg)) as decoded:
                    thumbnails.append((action, decoded.convert("RGB")))
        action_records.append((action, first, len(frames), sum(delays)))
        report.append(dict(index=index, id=action["id"], name=action["name"], **meta,
                           packed_bytes=len(pack)-begin, mean_color_error=sum(errors)/len(errors),
                           source_sha=action["webm"]["sha"]))
        print(f"{index+1:02d}/30 {action['id']}: {len(frames)} frames / {sum(delays)} ms / {len(pack)-begin} bytes", flush=True)
    if len(pack) > PACK_LIMIT:
        raise ValueError(f"Pack {len(pack)} exceeds {PACK_LIMIT}; lower JPEG quality without truncating clips")
    crc = zlib.crc32(pack[64:])
    prefix_crc = zlib.crc32(jpeg_prefix)
    pack[:64] = struct.pack("<8s10I16s", MAGIC, 8, WIDTH, HEIGHT, len(actions),
                            len(frame_records), len(pack), crc, args.quality,
                            len(jpeg_prefix), prefix_crc, bytes(16))
    # Independent full readback validation of offsets, durations and payload checksums.
    for offset, length, delay, checksum in frame_records:
        assert 64 + len(jpeg_prefix) <= offset and offset + length <= len(pack) and delay > 0
        reconstructed = jpeg_prefix + pack[offset:offset+length]
        assert zlib.crc32(reconstructed) == checksum
        with Image.open(io.BytesIO(reconstructed)) as check:
            check.load()
            assert check.size == (WIDTH, HEIGHT)
    header = ["// Generated by tools/build_pet_assets.py. Do not edit.", "#pragma once", "#include <stdint.h>",
              "struct PetFrame { uint32_t offset; uint16_t length; uint16_t delayMs; uint32_t crc32; };",
              "struct PetAction { const char* id; const char* name; uint16_t firstFrame; uint16_t frameCount; uint32_t durationMs; };",
              f"constexpr uint16_t PET_ACTION_COUNT = {len(actions)};",
              f"constexpr uint16_t PET_FRAME_COUNT = {len(frame_records)};",
              f"constexpr uint32_t PET_PACK_BYTES = {len(pack)};",
              f"constexpr uint32_t PET_PACK_CRC32 = 0x{crc:08X};",
              f"constexpr uint16_t PET_MAX_JPEG_BYTES = {max_jpeg};",
              f"constexpr uint16_t PET_JPEG_PREFIX_BYTES = {len(jpeg_prefix)};",
              f"constexpr uint32_t PET_JPEG_PREFIX_CRC32 = 0x{prefix_crc:08X}u;",
              f"constexpr uint16_t PET_FRAME_WIDTH = {WIDTH}, PET_FRAME_HEIGHT = {HEIGHT};",
              "const PetFrame petFrames[] = {"]
    header += [f"  {{{o}u, {n}, {d}, 0x{c:08X}u}}," for o, n, d, c in frame_records]
    header += ["};", "const PetAction petActions[] = {"]
    header += [f'  {{"{a["id"]}", "{a["name"]}", {first}, {count}, {duration}u}},' for a, first, count, duration in action_records]
    header += ["};", ""]
    (ROOT / "pet_assets.h").write_text("\n".join(header), encoding="utf-8")
    (OUT / "pet-assets.bin").write_bytes(pack)
    summary = dict(version=8, revision="7.1-fixed-camera", repository=manifest["repository"], source_tree_sha=manifest["source_tree_sha"],
                   actions=report, action_count=len(actions), frame_count=len(frame_records),
                   fps=FPS, width=WIDTH, height=HEIGHT, screen_y=SCREEN_Y, quality=args.quality,
                   camera_scale=CAMERA_SCALE, source_anchor=SOURCE_ANCHOR, target_anchor=TARGET_ANCHOR,
                   pack_bytes=len(pack), pack_limit=PACK_LIMIT, pack_crc32=f"{crc:08x}",
                   pack_sha256=hashlib.sha256(pack).hexdigest(), max_jpeg_bytes=max_jpeg,
                   jpeg_prefix_bytes=len(jpeg_prefix), shared_prefix_saved_bytes=(len(frame_records)-1)*len(jpeg_prefix),
                   validation="all JPEGs decoded; all CRCs, offsets and complete durations verified")
    (OUT / "asset-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 13) if font_path.exists() else ImageFont.load_default()
    sheet = Image.new("RGB", (5*160, 6*210), "#202028")
    draw = ImageDraw.Draw(sheet)
    for index, (action, frame) in enumerate(thumbnails):
        x, y = index % 5 * 160, index // 5 * 210
        screen = Image.new("RGB", (128,160))
        screen.paste(frame,(0,SCREEN_Y))
        ImageDraw.Draw(screen).text((4,3), "33.4", fill="#b8b8c0")
        sheet.paste(screen,(x+16,y+4))
        label = f"{index+1:02d} {action['name']}"
        rows, row = [], ""
        for character in label:
            if font.getlength(row + character) > 150:
                rows.append(row)
                row = ""
            row += character
        rows.append(row)
        for line, row in enumerate(rows):
            draw.text((x+5,y+166+line*17), row, font=font, fill="#eeeeee")
    sheet.save(OUT / "action-contact-sheet.png")
    print(f"PASS: {len(actions)} actions, {len(frame_records)} frames, pack={len(pack)}/{PACK_LIMIT}, max JPEG={max_jpeg}", flush=True)


if __name__ == "__main__":
    main()
