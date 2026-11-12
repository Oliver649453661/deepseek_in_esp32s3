"""Create a local source/offline-assets bundle. Never contacts GitHub or flashes a board.

Only an explicit file list is exported. secrets.h and compiled firmware are excluded;
the sole allowed .bin is the checked animation-data pack. Uses the standard library.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "deepseek_in_esp32s3"
VERSION = "v7.1"
BASE_FILES = (
    "README.md", ".gitignore", ".gitattributes", "ASSET_LICENSE.txt",
    "actions.json", "partitions.csv", "pet_assets.h", "requirements-assets.txt",
    "secrets.example.h", "build.ps1", "verify_device.ps1",
    "tools/build_pet_assets.py", "tools/package_project.py",
    "generated/pet-assets.bin", "generated/asset-report.json", "generated/action-contact-sheet.png",
    "docs/images/action-contact-sheet.png", "docs/validation/device-validation-v7_1.json",
    "legacy/rgb565/README.md", "legacy/rgb565/convert.ps1", "legacy/rgb565/convert_frames.ps1",
    "legacy/rgb565/whale_frames.h", "legacy/rgb565/whale_girl.h",
)
LEGACY_ASSETS = ("whale-girl.png", "idle.gif", "happy.gif", "sleepy.gif", "think.gif", "alert.gif")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def validate_assets(files, catalog):
    pack = files["generated/pet-assets.bin"]
    report = json.loads(files["generated/asset-report.json"])
    magic, version, width, height, action_count, frame_count, length, crc, quality, prefix_size, prefix_crc, _ = struct.unpack("<8s10I16s", pack[:64])
    if (magic, version, width, height, action_count, frame_count) != (b"PETMJPG8", 8, 128, 148, 30, 3600):
        raise ValueError("Unexpected animation-pack format/dimensions/counts")
    if length != len(pack) or len(pack) > 0xDE0000 or zlib.crc32(pack[64:]) != crc:
        raise ValueError("Animation-pack length or CRC mismatch")
    if sha256(pack) != report["pack_sha256"]:
        raise ValueError("Animation pack does not match asset-report.json")
    prefix = pack[64:64 + prefix_size]
    if zlib.crc32(prefix) != prefix_crc:
        raise ValueError("Shared JPEG prefix CRC mismatch")
    header = files["pet_assets.h"].decode("utf-8")
    records = re.findall(r"\{(\d+)u,\s*(\d+),\s*(\d+),\s*0x([0-9A-F]+)u\}", header)
    if len(records) != frame_count:
        raise ValueError("Frame-index count does not match the asset pack")
    for offset, stored_length, delay, expected_crc in records:
        offset, stored_length, delay = int(offset), int(stored_length), int(delay)
        if offset < 64 + prefix_size or offset + stored_length > len(pack) or delay <= 0:
            raise ValueError("Invalid frame boundary/duration")
        jpeg = prefix + pack[offset:offset + stored_length]
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9") or zlib.crc32(jpeg) != int(expected_crc, 16):
            raise ValueError("Frame reconstruction/CRC failed")
    for action in catalog["actions"]:
        data = files[f"assets/pc2005/{action['id']}.webm"]
        digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if digest != action["webm"]["sha"]:
            raise ValueError("Source asset hash mismatch: " + action["id"])
    proof = json.loads(files["docs/validation/device-validation-v7_1.json"].decode("utf-8-sig"))
    if proof["result"] != "PASS" or proof["asset_pack_sha256"] != report["pack_sha256"]:
        raise ValueError("Device-validation record belongs to a different asset pack")
    return dict(actions=action_count, frames=frame_count, original_webm_files=len(catalog["actions"]),
                pack_sha256=report["pack_sha256"], jpeg_quality=quality,
                frame_checksums="PASS", source_checksums="PASS", device_record="PASS")


def scan_credentials(files, reference):
    known = []
    if reference and reference.exists():
        # Credentials are compared only in memory; never printed or included in a manifest.
        content = reference.read_text(encoding="utf-8-sig")
        for match in re.finditer(r'^\s*#define\s+(\w+)\s+("(?:[^"\\]|\\.)*")', content, re.M):
            name, quoted = match.groups()
            try:
                value = json.loads(quoted)
            except json.JSONDecodeError:
                value = quoted[1:-1]
            if value and not value.startswith("YOUR_"):
                # Short SSIDs can collide with binary data; compare their quoted form.
                known.append((name, (value if len(value) >= 6 else quoted).encode("utf-8")))
    patterns = (
        re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    for name, data in files.items():
        parts = PurePosixPath(name).parts
        if ".." in parts or name.startswith("/") or ".build" in parts or PurePosixPath(name).name == "secrets.h":
            raise ValueError("Forbidden package path: " + name)
        if PurePosixPath(name).suffix in (".elf", ".map", ".o", ".pyc"):
            raise ValueError("Compiled/debug file must not be packaged: " + name)
        if name.endswith(".bin") and name != "generated/pet-assets.bin":
            raise ValueError("Firmware binary must not be packaged: " + name)
        for symbol, needle in known:
            if needle in data:
                raise ValueError(f"Local credential ({symbol}) found in package file: {name}")
        if any(pattern.search(data) for pattern in patterns):
            raise ValueError("Credential-like content found in package file: " + name)
    return dict(result="PASS", known_credential_symbols_checked=sorted(n for n, _ in known),
                private_configuration_included=False, compiled_firmware_included=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT.parent / f"{PROJECT}-{VERSION}.zip")
    parser.add_argument("--export-dir", type=Path, help="Also create a clean local project folder; destination must not exist")
    parser.add_argument("--secrets-reference", type=Path, default=ROOT / "secrets.h", help="Optional local file for in-memory leak checks; never copied")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output.with_suffix(output.suffix + ".sha256").exists():
        raise ValueError("Archive/checksum already exists; choose a new output name")
    export = args.export_dir.resolve() if args.export_dir else None
    if export and export.exists():
        raise ValueError("Export destination already exists; choose a new folder")

    catalog = json.loads((ROOT / "actions.json").read_text(encoding="utf-8"))
    paths = list(BASE_FILES) + ["legacy/rgb565/assets/" + name for name in LEGACY_ASSETS]
    for action in catalog["actions"]:
        if not re.fullmatch(r"[a-z0-9_]+", action["id"]):
            raise ValueError("Unsafe action ID")
        paths.append(f"assets/pc2005/{action['id']}.webm")
    sketches = list(ROOT.glob("*.ino"))
    if len(sketches) != 1:
        raise ValueError("Expected exactly one main Arduino sketch")
    files = {name: (ROOT / name).read_bytes() for name in paths}
    files[f"{PROJECT}.ino"] = sketches[0].read_bytes()
    readme = files["README.md"].decode("utf-8").replace(sketches[0].name, f"{PROJECT}.ino")
    files["README.md"] = readme.encode("utf-8")
    assets = validate_assets(files, catalog)
    security = scan_credentials(files, args.secrets_reference)
    manifest = dict(project=PROJECT, version=VERSION, scope="local source and offline assets; no GitHub operations",
                    file_count=len(files), uncompressed_bytes=sum(map(len, files.values())),
                    validation=assets, credential_scan=security,
                    files=[dict(path=name, bytes=len(data), sha256=sha256(data)) for name, data in sorted(files.items())])
    files["PACKAGE_MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    if export:
        export.mkdir(parents=True)
        for name, data in files.items():
            destination = (export / name).resolve()
            if not destination.is_relative_to(export):
                raise ValueError("Export path escaped destination")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(f"{PROJECT}/{name}", data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(files):
            raise ValueError("ZIP integrity verification failed")
        for name, original in files.items():
            if sha256(archive.read(f"{PROJECT}/{name}")) != sha256(original):
                raise ValueError("ZIP readback mismatch: " + name)
    checksum = sha256(output.read_bytes())
    output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(json.dumps(dict(result="PASS", archive=str(output), export_dir=str(export) if export else None,
                          file_count=len(files), archive_bytes=output.stat().st_size, sha256=checksum,
                          assets=assets, credential_scan=security), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
