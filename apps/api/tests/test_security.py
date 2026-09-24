"""OpenShell policy configuration and enforcement (mirror) + OpenShell log ingestion."""

import pytest
import yaml

from app.security.openshell_logs import parse_openshell_line
from app.security.policy import OpenShellPolicy, PolicyValidationError
from tests.conftest import ROOT

POLICY = ROOT / "nvidia" / "openshell" / "policies" / "reroute-agent.yaml"


@pytest.fixture
def policy() -> OpenShellPolicy:
    return OpenShellPolicy.load(POLICY, home="/sandbox")


def test_policy_file_uses_documented_schema(policy):
    raw = yaml.safe_load(POLICY.read_text())
    assert raw["version"] == 1
    assert set(raw) <= {"version", "filesystem_policy", "landlock", "process", "network_policies"}
    assert raw["process"]["run_as_user"] == "sandbox"
    for pol in raw["network_policies"].values():
        assert pol["binaries"] and pol["endpoints"]
        for ep in pol["endpoints"]:
            assert ep["enforcement"] == "enforce" and ep["protocol"] == "rest" and ep["rules"]


@pytest.mark.parametrize(
    "method,host,port,path,expected",
    [
        ("GET", "airline-service", 8000, "/api/flights/KE123", True),
        ("GET", "airline-service", 8000, "/api/flights/KE123/passengers", True),
        ("POST", "reroute-api", 8000, "/api/optimization/rebooking", True),
        ("GET", "reroute-api", 8000, "/api/policies/search", True),
        ("POST", "integrate.api.nvidia.com", 443, "/v1/chat/completions", True),
        ("GET", "unknown-external-api.com", 443, "/passengers", False),
        ("DELETE", "airline-service", 8000, "/api/flights/KE123", False),
        ("POST", "reroute-api", 8000, "/api/rebooking/plans/p1/approve", False),
        ("POST", "integrate.api.nvidia.com", 443, "/v1/embeddings", False),
        ("GET", "169.254.169.254", 80, "/latest/meta-data/", False),
    ],
)
def test_network_decisions(policy, method, host, port, path, expected):
    assert policy.check_network(method, host, port, path).allowed is expected


def test_self_approval_hits_deny_rule(policy):
    d = policy.check_network("POST", "reroute-api", 8000, "/api/rebooking/plans/abc/approve")
    assert not d.allowed and d.policy == "reroute_services" and "deny_rule" in d.reason


def test_unlisted_binary_is_denied(policy):
    assert not policy.check_network("GET", "airline-service", 8000, "/api/flights/KE123", binary="/usr/bin/curl").allowed


@pytest.mark.parametrize(
    "path,write,expected",
    [
        ("~/.ssh/id_rsa", False, False),
        ("/root/.ssh/id_rsa", False, False),
        ("/app/documents/connection-policy.md", False, True),
        ("/app/documents/connection-policy.md", True, False),
        ("/tmp/scratch.json", True, True),
        ("/etc/../root/.aws/credentials", False, False),
    ],
)
def test_filesystem_decisions(policy, path, write, expected):
    assert policy.check_file(path, write=write).allowed is expected


def test_invalid_policies_rejected(tmp_path):
    bad = {
        "version": 1,
        "network_policies": {
            "x": {
                "endpoints": [
                    {"host": "a", "port": 1, "protocol": "rest", "access": "full", "rules": [{"allow": {"method": "GET"}}]}
                ],
                "binaries": [{"path": "/x"}],
            }
        },
    }
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(PolicyValidationError, match="mutually exclusive"):
        OpenShellPolicy.load(p)
    p.write_text(yaml.safe_dump({"version": 1, "process": {"run_as_user": "root"}}))
    with pytest.raises(PolicyValidationError, match="root"):
        OpenShellPolicy.load(p)
    p.write_text(yaml.safe_dump({"version": 1, "unknown": True}))
    with pytest.raises(PolicyValidationError):
        OpenShellPolicy.load(p)


async def test_probe_api_and_audit(client):
    policy = (await client.get("/api/security/policy")).json()
    results = {}
    for probe in policy["probes"]:
        body = {k: v for k, v in probe.items() if k in {"kind", "method", "url", "path"}}
        r = (await client.post("/api/security/probes", json=body)).json()
        results[probe["id"]] = r["result"]
        assert r["result"] == probe["expect"], probe
    audit = (await client.get("/api/audit")).json()["entries"]
    assert {"timestamp", "agent", "tool", "target", "action", "policy", "result"} <= set(audit[0])
    assert sum(e["result"] == "DENY" for e in audit) >= 3


def test_openshell_log_parser():
    denied = parse_openshell_line(
        "[1775014132.690] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(64) -> httpbin.org:443 [policy:- engine:opa]"
    )
    assert denied["result"] == "DENY" and denied["target"] == "httpbin.org:443" and denied["binary"] == "/usr/bin/curl"
    allowed = parse_openshell_line(
        "[1775014132.190] [sandbox] [OCSF ] [ocsf] HTTP:GET [INFO] ALLOWED GET http://api.github.com/zen [policy:github_api]"
    )
    assert allowed["result"] == "ALLOW" and allowed["policy"] == "github_api" and allowed["action"] == "HTTP:GET"
    assert parse_openshell_line("random text") is None


async def test_openshell_log_ingestion(client):
    lines = [
        "[1775014132.690] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(64) -> unknown-external-api.com:443 [policy:- engine:opa]",
        "not a log line",
    ]
    r = (await client.post("/api/audit/openshell", json={"lines": lines})).json()
    assert r == {"imported": 1, "skipped": 1}
    entries = (await client.get("/api/audit", params={"result": "DENY"})).json()["entries"]
    assert entries[0]["enforced_by"] == "openshell"
