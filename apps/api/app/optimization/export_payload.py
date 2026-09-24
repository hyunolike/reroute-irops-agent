"""Export the demo re-accommodation MILP as a NVIDIA cuOpt server request body.

    python -m app.optimization.export_payload > ../../nvidia/cuopt/ke123-milp.json
    curl -s -X POST http://localhost:5000/cuopt/request -H 'Content-Type: application/json' \
         -H 'CLIENT-VERSION: custom' -d @nvidia/cuopt/ke123-milp.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date

from app.config import Settings
from app.db.base import Database
from app.domain.models import OptimizationRequest
from app.optimization.config import OptimizationConfig
from app.optimization.cuopt import to_cuopt_payload
from app.optimization.formulation import build_formulation
from app.rag.compiler import compile_policy_rules
from app.rag.documents import load_policy_chunks
from app.rag.lexical import LexicalRetrieverProvider
from app.seed.loader import seed_database
from app.services.airline import AirlineService

SERVICE_DATE = date(2026, 10, 15)


async def build_request(settings: Settings) -> OptimizationRequest:
    db = Database("sqlite://")
    db.create_all()
    with db.session() as s:
        seed_database(s, settings.seed_path, SERVICE_DATE)
        svc = AirlineService(s)
        flight = svc.get_flight("KE123")
        pax = svc.affected_passengers("KE123")
        alts = svc.search_alternatives("ICN", "NRT", SERVICE_DATE, exclude_flight_no="KE123")
    retriever = LexicalRetrieverProvider(load_policy_chunks(settings.docs_path))
    hits = [h for ch in retriever.chunks for h in await retriever.search(ch.title, 1)]
    return OptimizationRequest(disrupted_flight=flight, passengers=pax, alternatives=alts, rules=compile_policy_rules(hits))


def main() -> None:
    settings = Settings(_env_file=None)
    cfg = OptimizationConfig.load(settings.optimization_config)
    form = build_formulation(asyncio.run(build_request(settings)), cfg.weights)
    json.dump(to_cuopt_payload(form.problem, cfg.solver), sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
