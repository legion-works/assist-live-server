"""Check server package metadata and bootstrap boundaries."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    "README.md",
    "ARCHITECTURE.md",
    "STRUCTURE.md",
    "CHANGELOG.md",
    "AGENTS.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
)
MODEL_RUNTIME_NAMES = {
    "torch",
    "transformers",
    "sentence-transformers",
    "sherpa-onnx",
    "onnx-asr",
    "modelscope",
    "keras",
    "huggingface-hub",
    "tflite-runtime",
    "openvino",
    "faster-whisper",
    "openai-whisper",
    "mlx",
    "spacy",
    "sentencepiece",
    "ctranslate2",
}
MODEL_RUNTIME_PREFIXES = ("onnxruntime", "tensorflow")


def read_text(
    root: Path, relative_path: str, label: str, failures: list[str]
) -> str | None:
    path = root / relative_path
    if not path.is_file():
        failures.append(label)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        failures.append(label)
        return None


def parse_toml(
    text: str | None, label: str, failures: list[str]
) -> dict[str, object] | None:
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        failures.append(label)
        return None


def project_metadata(
    metadata: dict[str, object] | None, label: str, failures: list[str]
) -> dict[str, object]:
    project = metadata.get("project") if metadata is not None else None
    if not isinstance(project, dict):
        failures.append(label)
        return {}
    return project


def distribution_name(requirement: str) -> str | None:
    match = re.match(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)", requirement)
    if match is None:
        return None
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def check_bootstrap(root: Path = ROOT) -> list[str]:
    """Return all failed package and boundary checks for the repository root."""
    root = Path(root)
    failures: list[str] = []

    for document in DOCUMENTS:
        if not (root / document).is_file():
            failures.append(f"root document {document}")

    license_text = read_text(root, "LICENSE", "Apache-2.0 license", failures)
    if license_text is not None and not all(
        part in license_text
        for part in ("Apache License", "Version 2.0", "END OF TERMS AND CONDITIONS")
    ):
        failures.append("Apache-2.0 license")

    server_text = read_text(
        root, "server/pyproject.toml", "server package metadata", failures
    )
    client_text = read_text(
        root, "client/pyproject.toml", "client package metadata", failures
    )
    server_metadata = parse_toml(server_text, "server package metadata", failures)
    client_metadata = parse_toml(client_text, "client package metadata", failures)
    server_project = project_metadata(
        server_metadata, "server project metadata", failures
    )
    client_project = project_metadata(
        client_metadata, "client project metadata", failures
    )

    server_name = server_project.get("name")
    client_name = client_project.get("name")
    if server_name != "assist-live-server":
        failures.append("server package name")
    if client_name != "assist-live-client":
        failures.append("client package name")
    if server_name == client_name:
        failures.append("package separation")

    client_dependencies = client_project.get("dependencies", [])
    client_names: list[str] = []
    if not isinstance(client_dependencies, list) or not all(
        isinstance(item, str) for item in client_dependencies
    ):
        failures.append("client dependency names")
    else:
        for item in client_dependencies:
            name = distribution_name(item)
            if name is None:
                failures.append("client dependency name")
            else:
                client_names.append(name)
    if any(
        name in MODEL_RUNTIME_NAMES
        or any(name.startswith(prefix) for prefix in MODEL_RUNTIME_PREFIXES)
        for name in client_names
    ):
        failures.append("client model-runtime dependencies")

    agents = read_text(root, "AGENTS.md", "privacy rule", failures)
    if agents is not None:
        agents = agents.lower()
        if "personal data" not in agents or "secrets" not in agents:
            failures.append("privacy rule")

    descriptor = read_text(root, "ha-app/repository.yaml", "app descriptor", failures)
    if descriptor is not None:
        descriptor = descriptor.lower()
        if "homeassistant_api" in descriptor:
            failures.append("app descriptor API boundary")
        if re.search(r"(password|token|secret|api[_-]?key)\s*:", descriptor):
            failures.append("app descriptor credentials")

    root_text = read_text(root, "pyproject.toml", "Python requirement", failures)
    root_metadata = parse_toml(root_text, "Python requirement", failures)
    root_project = project_metadata(root_metadata, "Python requirement", failures)
    if root_project.get("requires-python") != ">=3.14.2":
        failures.append("Python requirement")

    return failures


def main() -> int:
    """Print all failed checks or the bootstrap success line."""
    failures = check_bootstrap()
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        return 1
    print("PASS server bootstrap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
