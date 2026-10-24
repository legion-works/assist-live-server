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


def check(condition: bool, label: str) -> None:
    if not condition:
        print(f"FAIL {label}")
        raise SystemExit(1)


def read_text(relative_path: str, label: str) -> str:
    path = ROOT / relative_path
    check(path.is_file(), label)
    return path.read_text(encoding="utf-8")


def distribution_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)", requirement)
    if match is None:
        print("FAIL client dependency name")
        raise SystemExit(1)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


for document in DOCUMENTS:
    check((ROOT / document).is_file(), f"root document {document}")

license_text = read_text("LICENSE", "Apache-2.0 license")
check(
    "Apache License" in license_text and "Version 2.0" in license_text,
    "Apache-2.0 license",
)

server = tomllib.loads(read_text("server/pyproject.toml", "server package metadata"))
client = tomllib.loads(read_text("client/pyproject.toml", "client package metadata"))
check(server["project"]["name"] == "assist-live-server", "server package name")
check(client["project"]["name"] == "assist-live-client", "client package name")
check(server["project"]["name"] != client["project"]["name"], "package separation")
client_dependencies = client["project"].get("dependencies", [])
client_distribution_names = [distribution_name(item) for item in client_dependencies]
check(
    not any(
        name in MODEL_RUNTIME_NAMES
        or any(name.startswith(prefix) for prefix in MODEL_RUNTIME_PREFIXES)
        for name in client_distribution_names
    ),
    "client model-runtime dependencies",
)

agents = read_text("AGENTS.md", "privacy rule").lower()
check("personal data" in agents and "secrets" in agents, "privacy rule")

descriptor = read_text("ha-app/repository.yaml", "app descriptor").lower()
check("homeassistant_api" not in descriptor, "app descriptor API boundary")
check(
    not re.search(r"(?i)(password|token|secret|api[_-]?key)\s*:", descriptor),
    "app descriptor credentials",
)

print("PASS server bootstrap")
