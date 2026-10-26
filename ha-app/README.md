# assist-live-server app

The project is experimental and has no release. No app image is available for installation.
This directory contains the app definition for amd64 and aarch64.
The image name in `config.yaml` is a future publication destination, not an availability claim.

## Build

From the repository root, build the service first:

```sh
docker build --platform linux/amd64 --tag assist-live-server:check .
docker build --platform linux/amd64 --tag assist-live-server-app:check ha-app
```

`ha-app/Dockerfile` derives from the checked service image through `BUILD_FROM`.
For a release build, set `BUILD_FROM` to the approved service digest.
CI uses the root Dockerfile's `app` target for the two platforms without a registry push.
The two paths use the same option mapper and server command.

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

The server health endpoint must have a provisioned integration credential.
The image health check reads that credential from the private registry file. It does not print it.
An unprovisioned image is not healthy.

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
