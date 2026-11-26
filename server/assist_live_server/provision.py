import argparse
import secrets
from pathlib import Path

from .auth import Registry


def provision(path: Path, satellite: str) -> None:
    registry = Registry.load(path) if path.exists() else Registry(path)
    registry.bind(satellite, satellite, secrets.token_bytes(32), save=False)
    registry.add_token(secrets.token_urlsafe(32), [satellite], save=False)
    registry.save()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--satellite", required=True)
    args = parser.parse_args()
    provision(args.store, args.satellite)


if __name__ == "__main__":
    main()
