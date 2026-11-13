from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_compose_publishes_nothing_beyond_loopback():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    assert "ports" not in services["qdrant"]  # vector store is internal to the compose network
    for spec in services["api"]["ports"]:
        assert str(spec).startswith("127.0.0.1:"), spec
