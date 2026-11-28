"""``logpipe deploy render``: a topology becomes the hosted and client compose files."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from logpipe.deploy import load, render

EXAMPLE = Path(__file__).resolve().parent.parent / "deploy" / "topology.example.yaml"


def compose(path: Path) -> dict:
    return yaml.safe_load((path / "docker-compose.yml").read_text())


def env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in (path / ".env").read_text().splitlines())


def test_the_example_renders_a_hosted_dir_and_one_per_client(tmp_path):
    dirs = render(load(EXAMPLE), tmp_path)
    assert [d.name for d in dirs] == ["hosted", "client-acme"]
    hosted = compose(tmp_path / "hosted")["services"]
    assert set(hosted) == {"demo-server", "demo-clickhouse", "acme-server"}

    acme = hosted["acme-server"]
    assert acme["command"][-4:] == ["--store", "remote", "--queue", "memory"]
    assert "CLICKHOUSE_HOST" not in acme["environment"]  # no database for acme on the VPS
    assert acme["environment"]["LLM_BACKEND"] == "llm:anthropic"
    rule = acme["labels"]["traefik.http.routers.logpipe-acme-dataplane.rule"]
    assert "acme.logpipe.example.com" in rule and "/dataplane" in rule

    demo = hosted["demo-server"]
    assert demo["environment"]["LLM_BACKEND"] == "offline"
    assert demo["environment"]["LOGPIPE_GRAPH_ENABLED"] == "true"
    assert demo["environment"]["CLICKHOUSE_HOST"] == "demo-clickhouse"

    client = compose(tmp_path / "client-acme")["services"]
    assert set(client) == {"clickhouse", "dataplane"}
    plane = client["dataplane"]["environment"]
    assert plane["LOGPIPE_DATAPLANE_URL"] == "wss://acme.logpipe.example.com/dataplane"
    assert (tmp_path / "client-acme" / "README.md").exists()


def test_secrets_are_shared_where_needed_kept_apart_otherwise_and_stable(tmp_path):
    render(load(EXAMPLE), tmp_path)
    hosted, client = env(tmp_path / "hosted"), env(tmp_path / "client-acme")
    assert hosted["ACME_DATAPLANE_KEY"] == client["ACME_DATAPLANE_KEY"]
    assert "ACME_CH_PASSWORD" not in hosted  # the client's database passwords stay with them
    assert not any(k.startswith("DEMO_") for k in client)  # nor another tenant's anything
    render(load(EXAMPLE), tmp_path)
    assert env(tmp_path / "hosted") == hosted  # a re-render never rotates a key


@pytest.mark.parametrize(
    ("tenant", "error"),
    [
        ({"residency": "cloud"}, "residency"),
        ({"store": "mongodb"}, "supports"),
        ({"components": ["parse", "store"]}, "cannot be left out"),
        ({"components": ["parse", "store", "detect", "magic"]}, "unknown components"),
        ({"components": ["parse", "store", "detect", "enrich"]}, "needs an `llm:`"),
    ],
)
def test_bad_topologies_say_what_is_wrong(tmp_path, tenant, error):
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump({"domain": "x.test", "tenants": {"t1": tenant}}))
    with pytest.raises(ValueError, match=error):
        load(path)


def test_leaving_enrich_out_turns_the_llm_off(tmp_path):
    path = tmp_path / "t.yaml"
    spec = {"llm": "llm:anthropic", "components": ["parse", "store", "detect"]}
    path.write_text(yaml.safe_dump({"domain": "x.test", "tenants": {"t1": spec}}))
    assert load(path)["tenants"]["t1"]["llm"] == "offline"
