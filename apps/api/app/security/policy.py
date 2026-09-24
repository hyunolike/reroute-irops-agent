"""OpenShell policy mirror.

Loads the *same* OpenShell sandbox policy YAML and evaluates network / filesystem requests with the
documented semantics (deny by default; endpoint host+port match; `access` presets or L7 `rules`;
`deny_rules` take precedence; filesystem allow-lists). It is a demo/dev stand-in and a pre-check - when
the agent runs inside a real OpenShell sandbox, OpenShell's proxy and Landlock are the enforcement point.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

_ACCESS = {
    "read-only": {"GET", "HEAD", "OPTIONS"},
    "read-write": {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH"},
    "full": {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"},
}
_TOP_LEVEL = {"version", "filesystem_policy", "landlock", "process", "network_policies", "network_middlewares"}


class PolicyValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    policy: str  # matching network policy key, or "-" / "filesystem_policy"
    reason: str

    @property
    def result(self) -> str:
        return "ALLOW" if self.allowed else "DENY"


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def _host_match(pattern: str, host: str) -> bool:
    pattern, host = pattern.lower(), host.lower()
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and host.count(".") >= pattern.count(".")
    return pattern == host


@dataclass
class OpenShellPolicy:
    raw: dict[str, Any]
    source: str = ""
    binary: str = "/app/.venv/bin/python3"
    home: str = field(default_factory=lambda: os.path.expanduser("~"))

    # ------------------------------------------------------------------ loading / validation
    @classmethod
    def load(cls, path: Path, **kw: Any) -> OpenShellPolicy:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        policy = cls(raw=raw, source=str(path), **kw)
        policy.validate()
        return policy

    def validate(self) -> None:
        unknown = set(self.raw) - _TOP_LEVEL
        if unknown:
            raise PolicyValidationError(f"unknown top-level keys: {sorted(unknown)}")
        if self.raw.get("version") != 1:
            raise PolicyValidationError("version: 1 is required")
        fs = self.raw.get("filesystem_policy", {})
        if "/" in fs.get("read_write", []):
            raise PolicyValidationError("'/' as read_write is rejected")
        proc = self.raw.get("process", {})
        for k in ("run_as_user", "run_as_group"):
            if str(proc.get(k, "sandbox")) in {"0", "root"}:
                raise PolicyValidationError(f"process.{k} may not be root")
        for key, pol in (self.raw.get("network_policies") or {}).items():
            if not isinstance(pol.get("endpoints"), list) or not pol["endpoints"]:
                raise PolicyValidationError(f"{key}: endpoints must be a non-empty list")
            if not isinstance(pol.get("binaries"), list) or not pol["binaries"]:
                raise PolicyValidationError(f"{key}: binaries must be a non-empty list")
            for ep in pol["endpoints"]:
                if "access" in ep and "rules" in ep:
                    raise PolicyValidationError(f"{key}: access and rules are mutually exclusive")
                if ep.get("protocol") in {"rest", "websocket", "graphql"} and not (ep.get("access") or ep.get("rules")):
                    raise PolicyValidationError(f"{key}: protocol {ep['protocol']} requires access or rules")
                if "rules" in ep and not ep["rules"]:
                    raise PolicyValidationError(f"{key}: empty rules list is rejected")

    # ------------------------------------------------------------------ network
    def check_network(self, method: str, host: str, port: int, path: str, binary: str | None = None) -> Decision:
        method = method.upper()
        binary = binary or self.binary
        for key, pol in (self.raw.get("network_policies") or {}).items():
            if not any(fnmatch.fnmatch(binary, b["path"]) for b in pol["binaries"]):
                continue
            for ep in pol["endpoints"]:
                ports = ep.get("ports") or [ep.get("port")]
                if not _host_match(ep["host"], host) or port not in ports:
                    continue
                for d in ep.get("deny_rules", []) or []:
                    if d.get("method", method).upper() == method and _glob_to_regex(d.get("path", "/**")).match(path):
                        return Decision(False, key, f"deny_rule {d.get('method')} {d.get('path')}")
                if "access" in ep:
                    if method in _ACCESS.get(ep["access"], set()):
                        return Decision(True, key, f"access preset {ep['access']}")
                    return Decision(False, key, f"{method} not in access preset {ep['access']}")
                for r in ep.get("rules", []):
                    allow = r.get("allow", {})
                    if allow.get("method", method).upper() == method and _glob_to_regex(allow.get("path", "/**")).match(path):
                        return Decision(True, key, f"rule allow {allow.get('method')} {allow.get('path')}")
                return Decision(False, key, f"no L7 rule allows {method} {path}")
        return Decision(False, "-", f"no network policy for {host}:{port} (deny by default)")

    # ------------------------------------------------------------------ filesystem
    def check_file(self, path: str, write: bool = False) -> Decision:
        p = PurePosixPath(os.path.normpath(path.replace("~", self.home, 1) if path.startswith("~") else path))
        fs = self.raw.get("filesystem_policy", {})
        rw = [PurePosixPath(x) for x in fs.get("read_write", [])]
        ro = [PurePosixPath(x) for x in fs.get("read_only", [])]
        allowed = rw if write else rw + ro

        def under(base: PurePosixPath) -> bool:
            return p == base or base in p.parents

        for base in allowed:
            if under(base):
                return Decision(True, "filesystem_policy", f"{'read_write' if base in rw else 'read_only'} {base}")
        return Decision(False, "filesystem_policy", f"{p} is outside the filesystem allow-list")

    def summary(self) -> dict[str, Any]:
        nets = []
        for key, pol in (self.raw.get("network_policies") or {}).items():
            for ep in pol["endpoints"]:
                nets.append(
                    {
                        "policy": key,
                        "host": ep["host"],
                        "port": ep.get("port") or ep.get("ports"),
                        "rules": [f"{r['allow'].get('method', '*')} {r['allow'].get('path', '/**')}" for r in ep.get("rules", [])]
                        or [f"access: {ep.get('access')}"],
                        "deny_rules": [f"{d.get('method', '*')} {d.get('path')}" for d in ep.get("deny_rules", []) or []],
                        "enforcement": ep.get("enforcement", "audit"),
                    }
                )
        return {
            "source": self.source,
            "filesystem": self.raw.get("filesystem_policy", {}),
            "process": self.raw.get("process", {}),
            "landlock": self.raw.get("landlock", {}),
            "network": nets,
        }
