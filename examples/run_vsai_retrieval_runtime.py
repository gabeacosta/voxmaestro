"""Exercise the bounded VSAI retrieval route through VoxMaestro and L1.

This runner requires the checked-in retrieval worker and remote L1 worker to
already be serving. It injects only the preselected ``service_question`` intent,
so its evidence does not claim L0 classification or physical voice coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

from serve_gateway import http_tool_executor
from voxmaestro import VoxMaestroRuntime
from voxmaestro.conductor import SchemaLoader
from voxmaestro.fleet import fleet_from_config
from voxmaestro.integrations.web_session import WebSessionAdapter

ROOT = Path(__file__).parent.parent
DEFAULT_CONFIG = ROOT / "examples" / "microscroll_landing_small_fleet.yaml"
DEFAULT_CORPUS = ROOT / "docs" / "wt" / "vsai_retrieval_v0.json"
DEFAULT_OUTPUT = ROOT / "evidence" / "small-model-fleet" / "retrieval_runtime_slice.json"

OUT_OF_DOMAIN = (
    ("en", "Does VoiceScheduleAI sell pizza?"),
    ("en", "What is the weather in Phoenix?"),
    ("en", "Can you repair my car engine?"),
    ("en", "Who won the baseball game?"),
    ("es", "¿VoiceScheduleAI vende pizza?"),
    ("es", "¿Qué tiempo hace en Phoenix?"),
    ("es", "¿Puedes reparar el motor de mi coche?"),
    ("es", "¿Quién ganó el partido de béisbol?"),
)


def _request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={} if payload is None else {"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            status_code = response.status
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        status_code = error.code
        body = json.loads(error.read())
    return {
        "status_code": status_code,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "body": body,
    }


async def _collect(adapter: WebSessionAdapter, message: dict[str, Any]) -> list[dict]:
    return [event async for event in adapter.iter_events(message)]


async def _scenario(
    config: dict[str, Any],
    *,
    session_id: str,
    locale: str,
    text: str,
) -> dict[str, Any]:
    async def classify(caller_text, context):
        return "service_question"

    _, generator = fleet_from_config(config)
    if generator is None:
        raise RuntimeError("configured L1 generator is unavailable")
    l1_calls: list[str] = []

    async def tracked_generator(caller_text, context, generation_config):
        l1_calls.append(caller_text)
        return await generator(caller_text, context, generation_config)

    runtime = VoxMaestroRuntime(
        config,
        intent_classifier=classify,
        tool_executor=http_tool_executor,
    )
    adapter = WebSessionAdapter(runtime, generation_adapter=tracked_generator)
    await _collect(
        adapter,
        {"type": "start", "sessionId": session_id, "locale": locale},
    )
    started = time.perf_counter()
    events = await _collect(
        adapter,
        {"type": "message", "sessionId": session_id, "text": text},
    )
    context = adapter.context_for(session_id)
    return {
        "session": session_id,
        "locale": locale,
        "input": text,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "l1_calls": len(l1_calls),
        "events": events,
        "state": context.current_state,
        "intent_history": list(context.intent_history),
        "retrieval": context.tool_results.get("retrieve_business_context"),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config = SchemaLoader.load(args.config)
    corpus = json.loads(args.corpus.read_text())
    retrieval_url = config["tools"]["retrieve_business_context"]["endpoint"]
    health_url = retrieval_url.removesuffix("/v1/retrieve") + "/health"
    health = _request(health_url)

    query_results = []
    for query in corpus["queries"]:
        result = _request(
            retrieval_url,
            {"query": query["text"], "language": query["language"]},
        )
        matches = result["body"].get("matches", [])
        accepted_id = matches[0]["id"] if matches else None
        query_results.append(
            {
                "query_id": query["id"],
                "language": query["language"],
                "expected_id": query["expected_id"],
                "accepted_id": accepted_id,
                "correct": accepted_id == query["expected_id"],
                "status_code": result["status_code"],
                "elapsed_ms": result["elapsed_ms"],
            }
        )

    out_of_domain = []
    for language, text in OUT_OF_DOMAIN:
        result = _request(retrieval_url, {"query": text, "language": language})
        out_of_domain.append(
            {
                "language": language,
                "text": text,
                "status_code": result["status_code"],
                "status": result["body"].get("status"),
                "accepted": bool(result["body"].get("matches")),
                "elapsed_ms": result["elapsed_ms"],
            }
        )

    scenarios = [
        await _scenario(
            deepcopy(config),
            session_id="retrieval-en",
            locale="en-US",
            text="Does VoiceScheduleAI work with Outlook?",
        ),
        await _scenario(
            deepcopy(config),
            session_id="retrieval-es",
            locale="es-MX",
            text="¿VoiceScheduleAI gestiona pacientes dentales nuevos?",
        ),
        await _scenario(
            deepcopy(config),
            session_id="retrieval-no-match",
            locale="en-US",
            text="Does VoiceScheduleAI sell pizza?",
        ),
    ]
    dead_config = deepcopy(config)
    dead_config["tools"]["retrieve_business_context"]["endpoint"] = (
        "http://127.0.0.1:1/v1/retrieve"
    )
    dead_endpoint = await _scenario(
        dead_config,
        session_id="retrieval-dead",
        locale="en-US",
        text="Does VoiceScheduleAI work with Outlook?",
    )

    accepted = [result for result in query_results if result["accepted_id"]]
    correct_accepted = [result for result in accepted if result["correct"]]
    final_events = [scenario["events"][-1] for scenario in scenarios]
    grounded_ids = [
        (
            scenario["retrieval"]["matches"][0]["id"]
            if scenario.get("retrieval")
            and scenario["retrieval"].get("matches")
            else None
        )
        for scenario in scenarios[:2]
    ]
    final_phases = [
        event.get("metadata", {}).get("phase") for event in final_events
    ]
    final_texts = [str(event.get("text") or "").lower() for event in final_events]
    forbidden_effect_terms = (
        "booked",
        "booking confirmed",
        "appointment confirmed",
        "cita confirmada",
        "reservada",
    )
    checks = {
        "health": health["status_code"] == 200 and health["body"].get("status") == "ok",
        "accepted_precision_is_one": bool(accepted)
        and len(correct_accepted) == len(accepted),
        "bilingual_grounded_l1": (
            grounded_ids == ["en_calendars", "es_new_patient"]
            and scenarios[0]["l1_calls"] == scenarios[1]["l1_calls"] == 1
            and final_phases[:2] == ["final", "final"]
            and "outlook" in final_texts[0]
            and "pacientes" in final_texts[1]
            and "nuevos" in final_texts[1]
            and final_texts[0].startswith("yes")
            and final_texts[1].startswith("sí")
            and " not " not in f" {final_texts[0]} "
            and " no " not in f" {final_texts[1]} "
            and not any(
                term in text
                for text in final_texts[:2]
                for term in forbidden_effect_terms
            )
        ),
        "out_of_domain_abstains": all(not result["accepted"] for result in out_of_domain),
        "no_match_bypasses_l1": (
            scenarios[2]["l1_calls"] == 0
            and final_phases[2] == "tool_failure"
            and final_events[2].get("text")
            == "I cannot verify that information right now."
        ),
        "dead_endpoint_bypasses_l1": (
            dead_endpoint["l1_calls"] == 0
            and dead_endpoint["events"][-1].get("metadata", {}).get("phase")
            == "tool_failure"
            and dead_endpoint["events"][-1].get("text")
            == "I cannot verify that information right now."
        ),
    }
    return {
        "program": "VM-VSAI-RETRIEVAL-ROUTE-001",
        "observed_date": time.strftime("%Y-%m-%d"),
        "config": str(args.config),
        "corpus": str(args.corpus),
        "intent_mode": "injected preselected service_question; L0 not tested",
        "security_boundaries": {
            "embedding_endpoint": (
                "numeric loopback HTTP only; URL credentials/query/fragment forbidden"
            ),
            "transport": (
                "direct HTTPConnection; redirects and environment proxies are not used"
            ),
            "credentials": (
                "credential-like corpus and live query text rejected before inference"
            ),
            "model_identity": (
                "admitted Ollama digest checked before and after every embedding request"
            ),
        },
        "health": health,
        "precision_gate": {
            "top_k": health["body"].get("top_k"),
            "min_score": health["body"].get("min_score"),
            "min_margin": health["body"].get("min_margin"),
            "queries": len(query_results),
            "accepted": len(accepted),
            "abstained": len(query_results) - len(accepted),
            "correct_accepted": len(correct_accepted),
            "incorrect_accepted": len(accepted) - len(correct_accepted),
            "accepted_precision": (
                round(len(correct_accepted) / len(accepted), 6) if accepted else None
            ),
            "coverage": round(len(accepted) / len(query_results), 6),
            "results": query_results,
        },
        "out_of_domain": out_of_domain,
        "runtime_scenarios": scenarios,
        "dead_endpoint": dead_endpoint,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "scope": (
            "Synthetic-corpus text route through the persistent Nomic worker and "
            "real remote L1 worker; no production tenant corpus, L0, effects, or "
            "physical voice claim."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
