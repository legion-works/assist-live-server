# Release artifacts

The project is experimental and has no release.
Local builds and CI checks do not publish an artifact.

## Build and check

Run these commands from the repository root. Set `RUNROOT` to a temporary output directory.

```sh
uv run pytest tests/test_release.py tests/test_main.py -q
uv build --package assist-live-client
mkdir -p "$RUNROOT"

docker build --platform linux/amd64 --tag assist-live-server:check .
docker save --output "$RUNROOT/server-image.tar" assist-live-server:check

uv run python tools/check_release.py --wheel dist --image "$RUNROOT/server-image.tar" --assets assets/manifest.json --inventory "$RUNROOT/amd64.json"
task check
```

The base image has a pinned multi-platform digest. The service `runtime stage` runs as UID `10001`.
The `runtime stage` copies no compiler, `uv`, or build environment from the `build stage`.
It has no build argument for credentials.

The pinned base image keeps its own installer and headers. The server does not use these files.
The build does not delete base-image paths or change installed runtime wheel files.

Compose uses a named data volume, a read-only root filesystem, and no more `Linux capabilities`.
Do not put credentials in a Dockerfile, build argument, image layer, or Compose file.
Supply cloud keys at runtime through the documented `environment variables` or HA password options.

The client wheel uses only `aiohttp>=3.14.4,<4` as a dependency.
The package check installs it in a new environment without server model dependencies.

## Asset and license records

`assets/manifest.json` has two source plans:

- `download_plan` lists startup models, tokenizers, and voices that are not bundled.
- `build_plan` identifies runtime, phonemizer, and voice-data wheels from `uv.lock`.

The startup command downloads only `download_plan`. It checks each SHA-256 before an atomic rename.
It also checks files in the cache. A mismatch stops startup.

The selected voice stays Lessac. No other bundled package file selects a different voice.

The image keeps each runtime wheel without changes. Some wheels contain ONNX files and NumPy arrays that the selected providers do not use.
The manifest records these files as bundled, with `runtime_used: false` where applicable.
`fbanks.npz` has `runtime_used: true`. The NumPy preprocessor uses its `filter-bank constants`.
Its usage source is in the manifest.

To regenerate package-data records from the locked environment, run:

```sh
uv sync --locked
uv run python tools/check_release.py --catalog-installed --assets assets/manifest.json
```

Review the new records and attribution notices before a build.
The build records installed wheel versions, URLs, and hashes in `/app/wheel-provenance.json`.

The image check compares these with the manifest and the installed metadata.
The image check reads Docker-save and OCI archives without filesystem extraction.

It examines each layer, with files that a subsequent layer deletes.
It lists model-like files by extension, size, SHA-256, and license.
Missing records, changed bytes, missing quotations, missing attribution, and incorrect Apache claims stop the check.
License terms alone do not stop technical selection.

The OCI `license label` applies Apache-2.0 only to project code.
It points to `/app/THIRD_PARTY_NOTICES.md` for other components.
Keep package license files, Debian notices, and the necessary `corresponding source` with redistributed binaries.

## Platform checks

CI uses SHA-pinned QEMU and Buildx `actions` with the `docker-container` driver.
The emulator and BuildKit images also have pinned digests.
CI builds different `linux/amd64` and `linux/arm64` OCI archives in runner temporary storage.
It checks each platform and compares their license inventories. It does not push an image.

The HA name `aarch64` identifies the OCI platform `linux/arm64`.
Local native checks include amd64 only. A CI definition does not prove a completed aarch64 build.

## Publication approval

Repository publication does not give permission to publish packages or images.
Get new operator approval before PyPI publication, a registry login, an image push, a tag, or a release.
The manual artifact workflow first makes sure that `vars.ASSIST_LIVE_RELEASES_APPROVED` equals `true`.
Its default permission is `contents: read`. It has no upload or publish step.

Publish the approved client version before an integration release consumes that same version.
The manual artifact workflow checks the version in `client/pyproject.toml` against PyPI.
An unavailable or yanked wheel stops that check. This check does not publish anything.

```sh
uv run python tools/check_release.py --pypi-version "$CLIENT_VERSION" --require-published
```

After new approval, publication commands include `uv publish`, `docker login`, `docker push`, and `docker buildx build --push`.
They are not part of the local gate or the CI image check.
