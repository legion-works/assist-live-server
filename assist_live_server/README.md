# assist-live-server app

The project is experimental and has no release. No app image is available for installation.
This directory contains the app definition for amd64 and aarch64.
The image name in `config.yaml` is a future publication destination, not an availability claim.

## Build

From the repository root, use the root Dockerfile's `app` target:

```sh
docker build --platform linux/amd64 --target app --tag assist-live-server-app:check .
```

CI uses this same target for the two platforms without a registry push.
The root `repository.yaml` identifies the repository. This directory name agrees with `slug` in `config.yaml`.
Source: [HA app repository layout](https://developers.home-assistant.io/docs/apps/repository/).

Current HA builds do not use `build.yaml`.
Source: [HA BuildKit migration](https://developers.home-assistant.io/blog/2026/04/02/builder-migration/).
The [HA app configuration document](https://developers.home-assistant.io/docs/apps/configuration/) gives the configuration keys.

## Options and access

See [server configuration](../docs/configuration.md) for each provider setting and `environment variable`.
`deepgram_api_key` and `elevenlabs_api_key` use password fields.
The app gives options through the environment, not through command arguments or log messages.

`local_only` defaults to `true`. Cloud selection must have an explicit change.

The app requests no HA API, Supervisor API, host network, or `privileged` option.
Only TCP port `8099` accepts connections. The HA integration owns its own HA access.

The image health check uses a credential from the private registry when one is available. It does not print it.
With an empty registry, it checks the documented `401 unauthorized` response from `/health`.
This response shows that the server accepts requests, not that a client has permission to use it.

The app uses `root` to read the Supervisor options file and write its private `/data` directory.
The standalone service uses UID `10001`. It does not read Supervisor options.

The [Supervisor options notice](https://developers.home-assistant.io/blog/2020/10/29/supervisor-update/#add-on-options) states:

```text
You can change your add-on to run as root (default).
```

The [current folder document](https://developers.home-assistant.io/docs/apps/configuration/#folders) identifies `/data` as private app data.
A local container test checks the write path with a root-owned `/data` `bind mount`.
No live Supervisor installation test ran.

## License

Project code uses Apache-2.0. Other components keep their own licenses.
See [third-party notices](../THIRD_PARTY_NOTICES.md) and [release checks](../docs/release.md).

A Legion Works project.
