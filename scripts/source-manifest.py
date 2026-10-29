#!/usr/bin/env python3
"""Content identity for an uncommitted provider candidate; excludes generated output."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = {}
for path in sorted(root.rglob('*')):
    relative = path.relative_to(root)
    if (not path.is_file() or any(p in {'.git', 'dist', 'target', '__pycache__'} for p in relative.parts)
            or relative.parts[:2] == ('docs', 'validation')):
        continue
    data = path.read_bytes()
    if path.suffix in {'.rs', '.py', '.sh', '.service', '.timer', '.yml', '.yaml', '.json', '.toml', '.md'}:
        data = data.replace(b'\r\n', b'\n')
    files[relative.as_posix()] = hashlib.sha256(data).hexdigest()
raw = (json.dumps(files, sort_keys=True, indent=2) + '\n').encode()
output = root / 'dist'
output.mkdir(exist_ok=True)
(output / 'source-manifest.json').write_bytes(raw)
print(hashlib.sha256(raw).hexdigest())
