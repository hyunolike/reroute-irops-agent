"""Policy corpus loader: splits markdown policy documents into one chunk per Policy ID."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SECTION = re.compile(r"^## +(?P<id>[A-Z]{2,5}-\d{3})\s*[—-]\s*(?P<title>.+)$", re.M)
_PARAMS = re.compile(r"```policy-params\n(?P<body>.*?)```", re.S)


@dataclass(frozen=True)
class PolicyChunk:
    policy_id: str
    title: str
    source_document: str
    section: str
    text: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def search_text(self) -> str:
        return f"{self.policy_id} {self.title}. {self.text}"


def load_policy_chunks(docs_dir: Path) -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        doc_title = raw.splitlines()[0].lstrip("# ").strip() if raw else path.stem
        matches = list(_SECTION.finditer(raw))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            body = raw[m.end() : end]
            params: dict[str, Any] = {}
            pm = _PARAMS.search(body)
            if pm:
                params = yaml.safe_load(pm.group("body")) or {}
                body = _PARAMS.sub("", body)
            text = "\n".join(ln for ln in body.strip().splitlines() if not ln.startswith(("Policy ID:", "Category:"))).strip()
            text = re.sub(r"\s*\n\s*", " ", text).strip()
            chunks.append(
                PolicyChunk(
                    policy_id=m.group("id"),
                    title=m.group("title").strip(),
                    source_document=path.name,
                    section=f"{doc_title} > {m.group('id')}",
                    text=text,
                    params=params,
                )
            )
    return chunks
