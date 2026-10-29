#!/usr/bin/env python3
"""Validate exported response samples with JSON Schema 2020-12."""
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
contract = json.loads((ROOT / 'docs/openapi-v1.json').read_text())
samples = json.loads((ROOT / 'integration/fixtures/samples.json').read_text())
for sample in samples:
    schema = contract['paths'][sample['path'].split('?')[0]][sample['method'].lower()]['responses'][str(sample['status'])]['content']['application/json']['schema']
    schema = {**schema, 'components': contract['components']}
    Draft202012Validator(schema).validate(sample['body'])
for name, expected in json.loads((ROOT / 'integration/fixtures/manifest.json').read_text()).items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
print(f'Validated {len(samples)} response samples and contract digests.')
