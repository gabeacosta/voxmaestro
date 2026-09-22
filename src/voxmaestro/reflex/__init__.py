"""Bounded, local-first System-1 reflex surface for VoxMaestro."""

from .admission import evaluate_rows, hash_model_path, load_corpus
from .backends import LocalSchemaBackend, ReflexBackend
from .gate import ReflexGate
from .shapes import BackendDecision, GateDecision, Language, ReflexIntent

__all__ = [
    "BackendDecision",
    "evaluate_rows",
    "hash_model_path",
    "load_corpus",
    "GateDecision",
    "Language",
    "LocalSchemaBackend",
    "ReflexBackend",
    "ReflexGate",
    "ReflexIntent",
]
