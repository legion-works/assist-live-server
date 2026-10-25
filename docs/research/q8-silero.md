# Silero model rights

## Decision

Stop model inclusion and the VAD-dependent server runtime.
Status: `confirmed_blocked_absent`. File license: `NOASSERTION`.

The project is experimental and has no release.
The release tree and startup download plan do not include the model file.
Image inventory: `NOT CHECKED`. There is no image.

The general MIT statements conflict with the non-commercial badge text.
This investigation found no file-scoped clarification in the examined sources.
It does not select the more permissive statement or a replacement VAD.
A badge supplies no file-license approval.

## Selected file and package

- Tag: `v6.2.3`.
- Commit: `5cd7945676eb32225748052e2e6a0580e4686a08`.
- File: `src/silero_vad/data/silero_vad.onnx`.
- Source: https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx
- SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
- Size: `2327524` bytes.
- PyPI version: `6.2.3`.
- Wheel source: https://files.pythonhosted.org/packages/84/ef/9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/silero_vad-6.2.3-py3-none-any.whl
- Wheel SHA-256: `7b7f5436cfcb02fae583a05b512ea96467fd449fe54cb49a5e4f06c51a1e43b8`.
- Wheel member: `silero_vad/data/silero_vad.onnx`.
- Member SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

The release file and packaged model bytes are the same.
The model and wheel stayed in scratch storage during inspection.
No model bytes, package, or new dependency are in this repository.
The decision checker uses the standard library only.
`ASSIST_LIVE_PROBE_DIRECTORY` selects scratch storage for the decision record.

Package metadata source: https://pypi.org/pypi/silero-vad/6.2.3/json
`Requires-Python` is `>=3.8`.
This includes the server floor `3.14.2`.
The PyPI `license` and `license_expression` fields are `null`.
The classifier says `MIT License`. The wheel also includes the complete MIT text.
The attached license digest is `2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b`.

## Prototype contract and model metadata

Prototype byte identity: `unverified`.
The prototype names `silero_vad.onnx` and inputs `input`, `state`, and `sr`.
Its binary, download URL, revision, and digest are not available.
This decision is for the selected release file.
It does not claim prototype byte identity.

The installed ONNX Runtime `1.31.0`
loaded this file.
Its actual input names agree with the prototype contract:

```json
[
  {
    "name": "input",
    "type": "tensor(float)",
    "shape": [
      null,
      null
    ]
  },
  {
    "name": "state",
    "type": "tensor(float)",
    "shape": [
      2,
      null,
      128
    ]
  },
  {
    "name": "sr",
    "type": "tensor(int64)",
    "shape": []
  }
]
```

The model metadata has no license statement:

```json
{
  "producer_name": "spox",
  "domain": "",
  "description": "",
  "custom_metadata_map": {}
}
```

The package and selected model directory have no `model card`.
The investigation examined all textual package members for license statements.
The six license statements are in `METADATA` and its attached `LICENSE`.
The statements below include the complete license text and each badge part.

## License statements

Access date for all sources: `2026-10-24`.
The package source has a pinned version and wheel SHA-256.
Repository sources have the selected commit as their pin.
General declarations do not supply file-scoped clarification.
No source below resolves the contradictory model-rights statements.

This model uses only `license_sources` for rights statements.
Each entry has `kind`, `url`, `quote`, and `revision_or_version`.
The same entry has `file_binding` with `path` and `sha256`.
The checker reads license identifiers from `quote`.
The decision uses no other source list.

### `license_text` at `L1-L21`

- Statement from source: `MIT`.
- Type: `license_text`.
- Source: https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/LICENSE#L1-L21
- Source revision or version: `5cd7945676eb32225748052e2e6a0580e4686a08`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
MIT License

Copyright (c) 2020-present Silero Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### `readme_body` at `L137`

- Statement from source: `MIT`.
- Type: `readme_body`.
- Source: https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/README.md#L137
- Source revision or version: `5cd7945676eb32225748052e2e6a0580e4686a08`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
Published under permissive license (MIT) Silero VAD has zero strings attached - no telemetry, no keys, no registration, no built-in expiration, no keys or vendor lock.
```

### `badge_text` at `L1`

- Statement from source: `CC-BY-NC-4.0`.
- Type: `badge_text`.
- Source: https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/README.md#L1
- Source revision or version: `5cd7945676eb32225748052e2e6a0580e4686a08`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
License: CC BY-NC 4.0
```

### `badge_image` at `L1`

- Statement from source: `MIT`.
- Type: `badge_image`.
- Source: https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/README.md#L1
- Source revision or version: `5cd7945676eb32225748052e2e6a0580e4686a08`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
https://img.shields.io/badge/License-MIT-lightgrey.svg?style=for-the-badge
```

### `badge_link` at `L1`

- Statement from source: `navigation only`.
- Type: `badge_link`.
- Source: https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/README.md#L1
- Source revision or version: `5cd7945676eb32225748052e2e6a0580e4686a08`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
https://github.com/snakers4/silero-vad/blob/master/LICENSE
```

### `package_metadata` at `L15`

- Statement from source: `MIT`.
- Type: `package_metadata`.
- Source: https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/pyproject.toml#L15
- Source revision or version: `5cd7945676eb32225748052e2e6a0580e4686a08`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
License :: OSI Approved :: MIT License
```

### `license_text` at `silero_vad-6.2.3.dist-info/licenses/LICENSE:L1-L21`

- Statement from source: `MIT`.
- Type: `license_text`.
- Source: https://files.pythonhosted.org/packages/84/ef/9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/silero_vad-6.2.3-py3-none-any.whl#silero_vad-6.2.3.dist-info/licenses/LICENSE:L1-L21
- Source revision or version: `6.2.3`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
MIT License

Copyright (c) 2020-present Silero Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### `package_metadata` at `silero_vad-6.2.3.dist-info/METADATA:L8`

- Statement from source: `MIT`.
- Type: `package_metadata`.
- Source: https://files.pythonhosted.org/packages/84/ef/9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/silero_vad-6.2.3-py3-none-any.whl#silero_vad-6.2.3.dist-info/METADATA:L8
- Source revision or version: `6.2.3`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
Classifier: License :: OSI Approved :: MIT License
```

### `license_pointer` at `silero_vad-6.2.3.dist-info/METADATA:L12`

- Statement from source: `navigation only`.
- Type: `license_pointer`.
- Source: https://files.pythonhosted.org/packages/84/ef/9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/silero_vad-6.2.3-py3-none-any.whl#silero_vad-6.2.3.dist-info/METADATA:L12
- Source revision or version: `6.2.3`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
License-File: LICENSE
```

### `badge_text` at `silero_vad-6.2.3.dist-info/METADATA:L51`

- Statement from source: `CC-BY-NC-4.0`.
- Type: `badge_text`.
- Source: https://files.pythonhosted.org/packages/84/ef/9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/silero_vad-6.2.3-py3-none-any.whl#silero_vad-6.2.3.dist-info/METADATA:L51
- Source revision or version: `6.2.3`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
License: CC BY-NC 4.0
```

### `badge_image` at `silero_vad-6.2.3.dist-info/METADATA:L51`

- Statement from source: `MIT`.
- Type: `badge_image`.
- Source: https://files.pythonhosted.org/packages/84/ef/9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/silero_vad-6.2.3-py3-none-any.whl#silero_vad-6.2.3.dist-info/METADATA:L51
- Source revision or version: `6.2.3`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
https://img.shields.io/badge/License-MIT-lightgrey.svg?style=for-the-badge
```

### `badge_link` at `silero_vad-6.2.3.dist-info/METADATA:L51`

- Statement from source: `navigation only`.
- Type: `badge_link`.
- Source: https://files.pythonhosted.org/packages/84/ef/9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/silero_vad-6.2.3-py3-none-any.whl#silero_vad-6.2.3.dist-info/METADATA:L51
- Source revision or version: `6.2.3`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
https://github.com/snakers4/silero-vad/blob/master/LICENSE
```

### `readme_body` at `silero_vad-6.2.3.dist-info/METADATA:L187`

- Statement from source: `MIT`.
- Type: `readme_body`.
- Source: https://files.pythonhosted.org/packages/84/ef/9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/silero_vad-6.2.3-py3-none-any.whl#silero_vad-6.2.3.dist-info/METADATA:L187
- Source revision or version: `6.2.3`.
- Bound file: `src/silero_vad/data/silero_vad.onnx`.
- File SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

```text
Published under permissive license (MIT) Silero VAD has zero strings attached - no telemetry, no keys, no registration, no built-in expiration, no keys or vendor lock.
```

## Read-only clarification search

The repository issue search used `license`, `CC BY-NC`, `badge`, and `commercial`.
Result counts were `{"CC BY-NC": 1, "badge": 0, "commercial": 5, "license": 5}`.
The positive `license` result set checked that the search could find known issues.
The investigation read the nine issues and their comments:
`[37, 46, 71, 110, 254, 335, 454, 551, 555]`.

It examined titles and bodies for all
`263` discussions.
The three term results were discussion numbers
`[111, 246, 336]`.
The `license` term in discussion `111` is part of a Python startup message.
Discussions `246` and `336` are about custom training and tuning.
The investigation examined their replies for an `OWNER` or `MEMBER` license statement.

No examined owner or member statement gives a license for this file and revision.
The owner replies below are about paid services, not a model-weight license grant.

- Source: https://github.com/snakers4/silero-vad/discussions/246#discussioncomment-3910625
- Date: `2022-10-19T00:26:45Z`.
- Author association: `OWNER`.

```text
Hi, no we do not provide model training options. If someone requires a custom model we do it commercially as a service.
```

Scope record: `Custom training service, not a license grant for the selected weights.`.

- Source: https://github.com/snakers4/silero-vad/issues/555#issuecomment-2413520879
- Date: `2024-10-15T10:35:18Z`.
- Author association: `OWNER`.

```text
Probably using ONNX runtime is the easiest option.

> converting the ONNX model to a Core ML model

Typically, such things are what we do commercially for companies for a fee.
```

Scope record: `ONNX deployment and Core ML conversion service, not a license grant for the selected file.`.

## Badge history

The investigation examined `108` root README commits.
It followed the renamed README to its initial path and first commit.
The first commit includes the alt text, before the selected model was available.
The historical image and `link target` referred to a different model project.
A subsequent diff changed the image to MIT but kept the non-commercial alt text.

- Source: https://github.com/snakers4/silero-vad/commit/56860ee673881e211ff2bda6f560cf9bc496575e
- Date: `2020-11-23T10:28:37Z`.
- Location: `Added silero-vad/README.md:1`.

```text
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-GNU%20AGPL%203.0-lightgrey.svg?style=for-the-badge)](https://github.com/snakers4/silero-models/blob/master/LICENSE)
```

- Source: https://github.com/snakers4/silero-vad/commit/dbf3adf06046fee7caa4fef096974c1891b3440d
- Date: `2020-11-23T10:42:52Z`.
- Location: `README.md:1 diff, image changed from AGPL to MIT; CC BY-NC alt text retained`.

```text
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-MIT-lightgrey.svg?style=for-the-badge)](https://github.com/snakers4/silero-models/blob/master/LICENSE)
```

This history suggests inherited text.
It supplies no license statement for the selected model.
Historical AGPL image text is not a license finding for this release.
The current conflict stays unresolved in the file-license policy.

## Upstream clarification draft

This investigation sent no upstream message and opened no issue.
Do not send this proposed question without operator approval:

What license applies to `src/silero_vad/data/silero_vad.onnx` at `v6.2.3`,
commit `5cd7945676eb32225748052e2e6a0580e4686a08`, SHA-256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`?

The PyPI `silero-vad==6.2.3` wheel contains the same model bytes.
Its SHA-256 is `7b7f5436cfcb02fae583a05b512ea96467fd449fe54cb49a5e4f06c51a1e43b8`.
The `LICENSE`, README body, and package classifier state `MIT`.
The README badge alt text states `CC BY-NC 4.0` in the repository and package.

Does MIT apply to these model weights for commercial use and redistribution,
with no non-commercial restriction?
Please give a file-scoped license statement for this revision or digest.
Please also make the badge text agree with the model license.

## Release checks and reproduction

The checker rejects conflicting or unknown terms and a badge-only approval.
It checks the source locations, pinned URLs, source statements, and file digest.
The asset gate also checks blocked paths and runtime source.
It checks the download plan.

It examines the image inventory when a record is available.
There is no image to examine here.
The Piper, Smart Turn, and Kokoro records stay unchanged.

```sh
uv run pytest tests/test_silero_license.py -q
uv run python tools/probe_silero_license.py --record-decision \
  --allow-blocked-absent --check assets/manifest.json \
  --report docs/research/q8-silero.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

The VAD-dependent runtime stays blocked until file rights have a satisfactory result.
This investigation selects no alternative model.
