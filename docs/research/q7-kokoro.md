# Kokoro runtime and voice rights

## Decision

Stop Kokoro runtime inclusion and its release.
Status: `confirmed_blocked_absent`. The project is experimental and has no release.
Default voice allowlist: `[]`.

No voice is a default selection.
No Kokoro model, voice, dictionary, or runtime is in this release tree.
Image inventory: `NOT CHECKED`. There is no image.

The packaged Python requirements do not include the server interpreter.
The English phonemizer chain also contains GPL and LGPL components.
The recorded stop causes are:

```json
[
  "Kokoro 0.9.4 requires Python <3.13,>=3.10; misaki 0.9.4 requires <3.13,>=3.8. The server requires >=3.14.2.",
  "The actual English fallback loads phonemizer-fork and bundled eSpeak NG with GPL-3.0-or-later rights.",
  "English number normalization uses num2words with LGPL-2.1-or-later rights.",
  "No file-scoped license statement was found for the af_heart voice or config.json. Neither is approved."
]
```

## Actual first-use inspection

The candidate is `kokoro==0.9.4` with `misaki==0.9.4` in scratch Python `3.12`.
No candidate package is a repository dependency.
The client and server package dependencies stay unchanged.
All caches and downloaded files stayed in scratch storage.
`ASSIST_LIVE_PROBE_DIRECTORY` selects that storage for the decision tool.
The tool uses the standard library only.
It does not install or download runtime components.

The inspection used `KPipeline(lang_code="a", trf=False, device="cpu")`.
The candidate voice was `af_heart`.
The Hugging Face download calls used the pinned revision in the records below.
The upstream example uses that voice.
It is an inspection candidate, not an approved default.
The first-use download list was:

- `config.json` with vocabulary and model configuration.
- `kokoro-v1_0.pth` with the model weights.
- `en_core_web_sm-3.8.0-py3-none-any.whl` for English tagging.
- `voices/af_heart.pt`, with tensor shape `(510, 1, 256)`.

The inspection loaded the model and voice but made no voice output or audio file.
It tested English G2P and the eSpeak fallback on a synthetic out-of-dictionary word.
The fallback used the bundled `libespeak-ng.so`, which reported version `1.52.0`.
`misaki/espeak.py` sets the library and data paths from `espeakng_loader` explicitly.
The English path loads `us_gold.json`, `us_silver.json`, and `num2words`.
It also loads the spaCy tagger.

The package supplies dictionaries and eSpeak data.
They are not first-use network downloads.

The eSpeak data bundle contains 364 files.
Its tree digest is `bfc761d924f35641b0c124dfb9def58ae433113d6c3f118b859169fbec2335da`.
The digest includes sorted relative paths and individual SHA-256 values.
The format is compact, key-sorted JSON.
The complete file inventory stays in scratch storage.
Necessary English data digests are below.

The loader wheel has no license file.
Its MIT source license does not apply to its bundled GPL parts.

The inspection did not select other languages.
It did not download their optional G2P models and dictionaries.
No rights approval applies to those paths or to other voices.

## File and package records

Access date for all sources: 2026-10-24.
The manifest records the source statement that sets each license decision.
`NOASSERTION` is a blocked rights finding, not permission to use a file.

The weights have an Apache statement and a complete digest in the same model page.
The voice table supplies a name and short digest.
It supplies no voice-specific license statement.
The model-level license tag alone does not supply approval for the voice or config.
Package statements come from the versioned wheel and its attached license.
Source headers also supply license statements.

### `kokoro-82m`

- Status: `confirmed_blocked_absent`.
- License: `Apache-2.0`.
- Version or revision: `f3ff3571791e39611d31c381e3a41a3af07b4987`.
- Source: https://huggingface.co/hexgrad/Kokoro-82M/resolve/f3ff3571791e39611d31c381e3a41a3af07b4987/kokoro-v1_0.pth
- SHA-256: `496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4`.

Attribution or stop cause:

```text
Not distributed. Apache-2.0 weights do not approve the complete runtime chain.
```
- License source: https://huggingface.co/hexgrad/Kokoro-82M/blob/f3ff3571791e39611d31c381e3a41a3af07b4987/README.md

License statement:

```text
This is an Apache-licensed model, and Kokoro has been deployed in numerous projects and commercial APIs.
```
Full license text: https://github.com/hexgrad/kokoro/blob/dfb907a02bba8152ca444717ca5d78747ccb4bec/LICENSE
License SHA-256: `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`.

### `kokoro-code`

- Status: `confirmed_blocked_absent`.
- License: `Apache-2.0`.
- Version or revision: `0.9.4`.
- Source: https://files.pythonhosted.org/packages/ea/cc/75f41633c75224ba820a4533163bc8b070b6bf25416014074c63284c2d4e/kokoro-0.9.4-py3-none-any.whl
- SHA-256: `a129dc6364a286bd6a92c396e9862459d3d3e45f2c15596ed5a94dcee5789efd`.

Attribution or stop cause:

```text
Not distributed. The packaged Python requirement excludes the server interpreter.
```
- License source: https://pypi.org/pypi/kokoro/0.9.4/json

License statement:

```text
Apache License Version 2.0, January 2004
```
Source `installed_file`: `kokoro-0.9.4.dist-info/licenses/LICENSE`.
Source `sha256`: `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`.

### `kokoro-config`

- Status: `confirmed_blocked_absent`.
- License: `NOASSERTION`.
- Version or revision: `f3ff3571791e39611d31c381e3a41a3af07b4987`.
- Source: https://huggingface.co/hexgrad/Kokoro-82M/resolve/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json
- SHA-256: `5abb01e2403b072bf03d04fde160443e209d7a0dad49a423be15196b9b43c17f`.

Attribution or stop cause:

```text
Not distributed. Model configuration and vocabulary have no explicit file-scoped license approval.
```

No file-scoped license statement has project approval.

### `kokoro-voice-af-heart`

- Status: `confirmed_blocked_absent`.
- License: `NOASSERTION`.
- Version or revision: `f3ff3571791e39611d31c381e3a41a3af07b4987`.
- Source: https://huggingface.co/hexgrad/Kokoro-82M/resolve/f3ff3571791e39611d31c381e3a41a3af07b4987/voices/af_heart.pt
- SHA-256: `0ab5709b8ffab19bfd849cd11d98f75b60af7733253ad0d67b12382a102cb4ff`.

Attribution or stop cause:

```text
Not selected. The voice table names af_heart and its short hash but supplies no voice-specific license statement.
```

No file-scoped license statement has project approval.

### `kokoro-misaki`

- Status: `confirmed_blocked_absent`.
- License: `Apache-2.0`.
- Version or revision: `0.9.4`.
- Source: https://files.pythonhosted.org/packages/82/ec/0ee4110ddb54278b8f21c40a140370ae8f687036c4edf578316602697c56/misaki-0.9.4-py3-none-any.whl
- SHA-256: `90e2eeb169786c014c429e5058d2ea6bcd02d651f2a24450ba6c9ffc0f8da15a`.

Attribution or stop cause:

```text
Not distributed. The Python floor and the actual transitive fallback stop runtime inclusion.
```
- License source: https://pypi.org/pypi/misaki/0.9.4/json

License statement:

```text
Apache License Version 2.0, January 2004
```
Source `installed_file`: `misaki-0.9.4.dist-info/licenses/LICENSE`.
Source `sha256`: `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`.

### `kokoro-us-gold`

- Status: `confirmed_blocked_absent`.
- License: `Apache-2.0`.
- Version or revision: `0.9.4`.
- Source: https://files.pythonhosted.org/packages/82/ec/0ee4110ddb54278b8f21c40a140370ae8f687036c4edf578316602697c56/misaki-0.9.4-py3-none-any.whl
- SHA-256: `dc414872a49a28ae6c141463d502fd945f3b2fde040484fdc47d00cc4612686f`.

Attribution or stop cause:

```text
Not distributed. Dictionary rights use the attached package license, not an independent upstream-data approval.
```
- License source: https://pypi.org/pypi/misaki/0.9.4/json

License statement:

```text
Apache License Version 2.0, January 2004
```
Source `scope`: `Packaged dictionary under the wheel's attached license; no separate dictionary notice is present.`.
Archive member: `misaki/data/us_gold.json`.

### `kokoro-us-silver`

- Status: `confirmed_blocked_absent`.
- License: `Apache-2.0`.
- Version or revision: `0.9.4`.
- Source: https://files.pythonhosted.org/packages/82/ec/0ee4110ddb54278b8f21c40a140370ae8f687036c4edf578316602697c56/misaki-0.9.4-py3-none-any.whl
- SHA-256: `de8f67be911bb6c659187b4a65fd966b6a30e56350e0f790d763210b053ac475`.

Attribution or stop cause:

```text
Not distributed. Dictionary rights use the attached package license, not an independent upstream-data approval.
```
- License source: https://pypi.org/pypi/misaki/0.9.4/json

License statement:

```text
Apache License Version 2.0, January 2004
```
Source `scope`: `Packaged dictionary under the wheel's attached license; no separate dictionary notice is present.`.
Archive member: `misaki/data/us_silver.json`.

### `kokoro-phonemizer-fork`

- Status: `confirmed_blocked_absent`.
- License: `GPL-3.0-or-later`.
- Version or revision: `3.3.2`.
- Source: https://files.pythonhosted.org/packages/64/f1/0dcce21b0ae16a82df4b6583f8f3ad8e55b35f7e98b6bf536a4dd225fa08/phonemizer_fork-3.3.2-py3-none-any.whl
- SHA-256: `97305c76f4183b3825dae8f4c032265fe78c9946ce58c47d4b62161349264b74`.

Attribution or stop cause:

```text
Not distributed. GPL runtime components do not satisfy the Apache image policy.
```
- License source: https://pypi.org/pypi/phonemizer-fork/3.3.2/json

License statement:

```text
either version 3 of the License, or (at your option) any later version.
```
Source `installed_file`: `phonemizer/__init__.py`.
Source `license_file`: `phonemizer_fork-3.3.2.dist-info/licenses/LICENSE`.
Source `sha256`: `d62f065830aa3739cc031156b9690805c7b2e811b4a178c8b4acd8725d561c94`.

### `kokoro-espeak-loader`

- Status: `confirmed_blocked_absent`.
- License: `MIT`.
- Version or revision: `0.2.4`.
- Source: https://files.pythonhosted.org/packages/de/1e/25ec5ab07528c0fbb215a61800a38eca05c8a99445515a02d7fa5debcb32/espeakng_loader-0.2.4-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
- SHA-256: `08721baf27d13d461f6be6eed9a65277e70d68234ff484fd8b9897b222cdcb6d`.

Attribution or stop cause:

```text
Not distributed. The wheel contains no license file and bundles a GPL library and data. MIT for the loader does not cover those parts.
```
- License source: https://github.com/thewh1teagle/espeakng-loader/blob/0ddc87adf77e5850d7eeb542ac8a87d421b64daa/LICENSE

License statement:

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
```
Source `scope`: `Loader source only. The installed __init__.py exactly matches this commit; its SHA-256 is 3900f0b174feebccb8991fd6f3888f037c7bfa23d9a3bdec480e894f75b03f3f.`.

### `kokoro-espeak-ng`

- Status: `confirmed_blocked_absent`.
- License: `GPL-3.0-or-later`.
- Version or revision: `1.52.0`.
- Source: https://github.com/espeak-ng/espeak-ng/tree/4870adfa25b1a32b4361592f1be8a40337c58d6c
- SHA-256: `b15ce0803b26254e8a0d088c6b9281a2637b2b02613147cc320cfdefaf2c3ccf`.

Attribution or stop cause:

```text
Not distributed. Actual loaded version is 1.52.0. The loader source references this eSpeak source commit.
```
- License source: https://github.com/espeak-ng/espeak-ng/blob/4870adfa25b1a32b4361592f1be8a40337c58d6c/README.md

License statement:

```text
eSpeak NG Text-to-Speech is released under the GPL version 3 or later license.
```
Full license text: https://github.com/espeak-ng/espeak-ng/blob/4870adfa25b1a32b4361592f1be8a40337c58d6c/COPYING
Archive member: `espeakng_loader/libespeak-ng.so`.

### `kokoro-espeak-data`

- Status: `confirmed_blocked_absent`.
- License: `GPL-3.0-or-later AND Unicode-DFS-2016`.
- Version or revision: `1.52.0`.
- Source: https://github.com/espeak-ng/espeak-ng/tree/4870adfa25b1a32b4361592f1be8a40337c58d6c/espeak-ng-data
- SHA-256: `None`.

Attribution or stop cause:

```text
Not distributed. The active bundled data includes GPL and Unicode notices, not the loader's MIT license.
```
- License source: https://github.com/espeak-ng/espeak-ng/blob/4870adfa25b1a32b4361592f1be8a40337c58d6c/README.md

License statement:

```text
eSpeak NG Text-to-Speech is released under the GPL version 3 or later license.
```
- License source: https://github.com/espeak-ng/espeak-ng/blob/4870adfa25b1a32b4361592f1be8a40337c58d6c/COPYING.UCD

License statement:

```text
Permission is hereby granted, free of charge, to any person obtaining a copy of the Unicode data files and any associated documentation
```

Individual file digests:

```json
{
  "en_dict": "1053f74a34af38abb6fbd64b7838dacbc655a687c48ce1bb663d364a127b05de",
  "phondata": "a0b643b155cb6b12628d9e7865b57d9fca0d35844614f2594a5e009c80c80bb4",
  "phonindex": "384e5fa6f714ba5356c58008249b78699c31c1ad044b243068d263fb806b7d73",
  "phontab": "1b40690667e1e9aa1ba5e5234773c799e7e72ea751426e5150423d53c3f24fa2",
  "intonations": "031a105bf6bb2ebcd73a2c579ccfb42469b27266db0078af65ae298fd3588b21"
}
```

### `kokoro-num2words`

- Status: `confirmed_blocked_absent`.
- License: `LGPL-2.1-or-later`.
- Version or revision: `0.5.14`.
- Source: https://files.pythonhosted.org/packages/d6/5b/545e9267a1cc080c8a1be2746113a063e34bcdd0f5173fd665a5c13cb234/num2words-0.5.14-py3-none-any.whl
- SHA-256: `1c8e5b00142fc2966fd8d685001e36c4a9911e070d1b120e1beb721fa1edb33d`.

Attribution or stop cause:

```text
Not distributed. LGPL components do not satisfy the Apache image policy.
```
- License source: https://pypi.org/pypi/num2words/0.5.14/json

License statement:

```text
version 2.1 of the License, or (at your option) any later version.
```
Source `installed_file`: `num2words/__init__.py`.
Source `license_file`: `num2words-0.5.14.dist-info/COPYING`.
Source `sha256`: `1457a03d928aeb06af3ea75ef065c517e915d7e41283d550dd4fa18ab20cda12`.

### `kokoro-spacy-english-model`

- Status: `confirmed_blocked_absent`.
- License: `MIT`.
- Version or revision: `3.8.0`.
- Source: https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
- SHA-256: `1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85`.

Attribution or stop cause:

```text
Not distributed. The model has MIT and source-data notices. It is part of the blocked English chain.
```
- License source: https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0

License statement:

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
```
Source `installed_file`: `en_core_web_sm/en_core_web_sm-3.8.0/LICENSE`.
Source `sha256`: `3933c176979b68bc6d0bcc902c7d6c130f1d127f476f17ba5cdba8d99cfd0012`.

## Other package declarations

These versions are from the scratch environment, not the repository environment.
The attached package licenses supply these statements.
These declarations do not supply approval for all bundled third-party parts.
They also do not supply approval for all their dependencies.

The complete Kokoro chain stays blocked. No package below is a new release selection.
NumPy has the same license expression as the recorded probe dependency in the notices.

### `torch==2.6.0+cpu`

- Package license: `BSD-3-Clause`.
- Python requirement: `>=3.9.0`.
- Source: https://download.pytorch.org/whl/cpu/torch/
- License file for inspection: `torch-2.6.0+cpu.dist-info/LICENSE`.
- License-file SHA-256: `6ecc267746f8c2b9c1faa1facc857c6f0d26db9bd00e6f43017da06f06120313`.

```text
Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
```

### `transformers==4.51.3`

- Package license: `Apache-2.0`.
- Python requirement: `>=3.9.0`.
- Source: https://pypi.org/pypi/transformers/4.51.3/json
- License file for inspection: `transformers-4.51.3.dist-info/LICENSE`.
- License-file SHA-256: `77fd4710def9ec3c0f6225800e0235f15a425abd4a8b03559127fcd782612049`.

```text
Apache License Version 2.0, January 2004
```

### `spacy==3.8.16`

- Package license: `MIT`.
- Python requirement: `<3.15,>=3.9`.
- Source: https://pypi.org/pypi/spacy/3.8.16/json
- License file for inspection: `spacy-3.8.16.dist-info/licenses/LICENSE`.
- License-file SHA-256: `3f4378e67708fecacd3e27651c8bc0f977cf20789e19da0d81a2840e7cf573a2`.

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
```

### `spacy-curated-transformers==0.3.1`

- Package license: `MIT`.
- Python requirement: `<3.14,>=3.9`.
- Source: https://pypi.org/pypi/spacy-curated-transformers/0.3.1/json
- License file for inspection: `spacy_curated_transformers-0.3.1.dist-info/licenses/LICENSE`.
- License-file SHA-256: `e4f2431f403ea0d20d108bbe1f0615a4ca3b6b765e2dbafae93908d3986d153a`.

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
```

### `curated-transformers==0.1.1`

- Package license: `MIT`.
- Python requirement: `>=3.6`.
- Source: https://pypi.org/pypi/curated-transformers/0.1.1/json
- License file for inspection: `curated_transformers-0.1.1.dist-info/LICENSE`.
- License-file SHA-256: `e4f2431f403ea0d20d108bbe1f0615a4ca3b6b765e2dbafae93908d3986d153a`.

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
```

### `curated-tokenizers==0.0.10`

- Package license: `MIT`.
- Python requirement: `>=3.9`.
- Source: https://pypi.org/pypi/curated-tokenizers/0.0.10/json
- License file for inspection: `curated_tokenizers-0.0.10.dist-info/licenses/LICENSE`.
- License-file SHA-256: `49d8fef96cd92719cb5242ef4c4f954edc008a46fcf7caabecae14dbcc444b50`.

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
```

## Release boundary and reproduction

The manifest has an explicit empty `kokoro_default_voices` list.
The tool refuses an unknown status or a blocked file in the release tree.
It also refuses an unapproved default voice.
The asset gate checks all nested components, the image inventory, and the download plan.

No Kokoro component is in that plan. The Smart Turn record stays unchanged.
An accepted blocked-absent decision is not satisfactory synthesis or a release approval.

```sh
uv run pytest tests/test_kokoro_licenses.py -q
uv run python tools/probe_kokoro_licenses.py --record-decision \
  --allow-blocked-absent --check assets/manifest.json \
  --report docs/research/q7-kokoro.md
uv run python tools/check_assets.py --manifest assets/manifest.json --release
```

A design decision is necessary before runtime work can continue.
This inspection selects no replacement model or runtime.
