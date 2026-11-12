"""Build the TensorRT engines of one detector site before DeepStream starts.

DeepStream 7.1 (TensorRT 10) cannot load engines built on JetPack 5, and when nvinfer
builds an engine itself it saves it under its own name next to the ONNX file, which is
the read-only /home/jetson/models mount, so the engine would be rebuilt on every start.
This script builds each engine named by ``model-engine-file`` once with trtexec into the
writable /cache/engines volume. It uses only the standard library.

Usage: python3 prepare_engines.py /app/config/sites/<site>.json
"""

from __future__ import annotations

import configparser
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CACHE_DIR = Path(os.environ.get("MODA_ENGINE_CACHE_DIR", "/cache/engines"))
STAMP_FILE = CACHE_DIR / ".tensorrt-version"
# The pipeline accepts at most four sources (traffic_core.deepstream.MAX_SOURCES).
MAX_VEHICLE_BATCH = 4
INFER_MODELS = ("vehicle_detection", "plate_detection", "plate_recognition")


# --- Minimal protobuf reader: first graph input of an ONNX model -------------------

def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _fields(data: bytes):
    """Yield (field_number, wire_type, value) for one protobuf message."""
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire == 1:
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = _varint(data, pos)
            value, pos = data[pos:pos + length], pos + length
        elif wire == 5:
            value, pos = data[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire}")
        yield field, wire, value


def onnx_input(path: Path) -> tuple[str, list[int | str]]:
    """Return the name and dims (int, or str for symbolic) of the first graph input."""
    data = path.read_bytes()
    graph = next(v for f, w, v in _fields(data) if f == 7 and w == 2)  # ModelProto.graph
    initializers = set()
    inputs = []
    for field, wire, value in _fields(graph):
        if field == 5 and wire == 2:  # GraphProto.initializer -> TensorProto.name (8)
            initializers.update(
                v.decode() for f, w, v in _fields(value) if f == 8 and w == 2
            )
        elif field == 11 and wire == 2:  # GraphProto.input -> ValueInfoProto
            inputs.append(value)
    for value_info in inputs:
        name, dims = "", []
        for field, wire, value in _fields(value_info):
            if field == 1 and wire == 2:
                name = value.decode()
            elif field == 2 and wire == 2:  # TypeProto.tensor_type
                for f1, w1, tensor in _fields(value):
                    if f1 != 1 or w1 != 2:
                        continue
                    for f2, w2, shape in _fields(tensor):
                        if f2 != 2 or w2 != 2:  # Tensor.shape
                            continue
                        for f3, w3, dim in _fields(shape):
                            if f3 != 1 or w3 != 2:
                                continue
                            entry: int | str = "?"
                            for f4, w4, dv in _fields(dim):
                                if f4 == 1 and w4 == 0:
                                    entry = dv
                                elif f4 == 2 and w4 == 2:
                                    entry = dv.decode()
                            dims.append(entry)
        if name and name not in initializers:
            return name, dims
    raise ValueError(f"No graph input found in {path}")


# --- Engine build --------------------------------------------------------------------

def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base.parent / path


def read_infer_config(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(strict=False, inline_comment_prefixes=None)
    parser.read_string(path.read_text(encoding="utf-8").replace("\r", ""))
    return dict(parser["property"])


def tensorrt_version() -> str:
    for pattern in ("/usr/lib/aarch64-linux-gnu/libnvinfer.so.*", "/usr/lib/x86_64-linux-gnu/libnvinfer.so.*"):
        for candidate in sorted(glob.glob(pattern)):
            if candidate.count(".") >= 4:  # libnvinfer.so.X.Y.Z
                return Path(candidate).name
    return "unknown"


def trtexec_binary() -> str:
    for candidate in ("/usr/src/tensorrt/bin/trtexec", shutil.which("trtexec") or ""):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise SystemExit("trtexec not found in the DeepStream image")


def drop_stale_engines() -> None:
    """Engines are tied to the TensorRT version: drop them when it changes (JetPack 5 -> 6)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    current = tensorrt_version()
    previous = STAMP_FILE.read_text().strip() if STAMP_FILE.exists() else ""
    if previous != current:
        for engine in CACHE_DIR.glob("*.engine"):
            print(f"Removing engine built for another TensorRT version: {engine}", flush=True)
            engine.unlink()
        STAMP_FILE.write_text(current + "\n")


def build_engine(onnx: Path, engine: Path, opt_batch: int, max_batch: int, fp16: bool) -> None:
    if engine.is_file() and engine.stat().st_size > 0:
        print(f"Using cached engine {engine}", flush=True)
        return
    if not engine.is_relative_to(CACHE_DIR):
        raise SystemExit(f"model-engine-file must be inside {CACHE_DIR}: {engine}")
    name, dims = onnx_input(onnx)
    if len(dims) != 4:
        raise SystemExit(f"Expected an NCHW input in {onnx}, got {name} {dims}")
    # Several detector containers share /cache/engines and the plate engines: write to a
    # per-process temporary file and rename atomically.
    temporary = f"{engine}.{os.getpid()}.tmp"
    command = [trtexec_binary(), f"--onnx={onnx}", f"--saveEngine={temporary}"]
    if fp16:
        command.append("--fp16")
    if isinstance(dims[0], int):
        # Static batch: the engine supports exactly the batch stored in the model.
        if dims[0] < max_batch:
            print(f"Warning: {onnx.name} has a static batch of {dims[0]}", flush=True)
    else:
        chw = "x".join(str(d) for d in dims[1:])
        command += [
            f"--minShapes={name}:1x{chw}",
            f"--optShapes={name}:{opt_batch}x{chw}",
            f"--maxShapes={name}:{max_batch}x{chw}",
        ]
    print(f"Building TensorRT engine {engine} (first run only, may take several minutes)", flush=True)
    log = CACHE_DIR / f"{engine.stem}.trtexec.log"
    with log.open("w") as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        print(log.read_text()[-4000:], flush=True)
        raise SystemExit(f"trtexec failed for {onnx}")
    os.replace(temporary, engine)


def main(site_path: str) -> None:
    site = json.loads(Path(site_path).read_text(encoding="utf-8"))
    drop_stale_engines()
    for model in INFER_MODELS:
        config_path = Path(site["models"][model])
        config = read_infer_config(config_path)
        onnx = _resolve(config_path, config["onnx-file"])
        engine = _resolve(config_path, config["model-engine-file"])
        batch = int(config.get("batch-size", "1"))
        max_batch = max(batch, MAX_VEHICLE_BATCH) if model == "vehicle_detection" else batch
        build_engine(onnx, engine, batch, max_batch, config.get("network-mode", "0") == "2")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1].endswith(".onnx"):
        print(onnx_input(Path(sys.argv[1])))  # debug helper: show the input of one model
    elif len(sys.argv) == 2:
        main(sys.argv[1])
    else:
        raise SystemExit(__doc__)
