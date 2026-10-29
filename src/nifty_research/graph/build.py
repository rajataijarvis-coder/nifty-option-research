"""Graph wiring — this is the agent.

Two cycles, both bounded, both deliberate:

* **inner cycle** ``validate -> relax -> validate``: when nothing clears the guard rails,
  loosen them one documented step at a time (never silently) and ask again, up to
  ``max_relaxations``.
* **outer cycle** ``next_cycle -> form_hypotheses``: after each published iteration the
  agent re-plans and tests a *different* parameter set, until the iteration budget runs out.

Both cycles are capped, so a run always terminates and always produces a report —
including the honest report that nothing was worth selling.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..config import RunConfig
from . import nodes
from .state import ResearchState


def build_graph():
    graph = StateGraph(ResearchState)

    graph.add_node("load_market_state", nodes.load_market_state)
    graph.add_node("assess_regime", nodes.assess_regime)
    graph.add_node("book_context", nodes.book_context)
    graph.add_node("form_hypotheses", nodes.form_hypotheses)
    graph.add_node("generate_candidates", nodes.generate_candidates)
    graph.add_node("score_candidates", nodes.score_candidates)
    graph.add_node("validate", nodes.validate)
    graph.add_node("relax", nodes.relax)
    graph.add_node("rank_and_publish", nodes.rank_and_publish)
    graph.add_node("next_cycle", nodes.next_cycle)
    graph.add_node("finalize", nodes.finalize)
    graph.add_node("halt", nodes.halt)

    graph.add_edge(START, "load_market_state")
    graph.add_conditional_edges(
        "load_market_state",
        nodes.route_after_load,
        {"assess_regime": "assess_regime", "halt": "halt"},
    )
    graph.add_edge("assess_regime", "book_context")
    graph.add_edge("book_context", "form_hypotheses")
    graph.add_edge("form_hypotheses", "generate_candidates")
    graph.add_edge("generate_candidates", "score_candidates")
    graph.add_edge("score_candidates", "validate")
    graph.add_conditional_edges(
        "validate",
        nodes.route_after_validate,
        {"relax": "relax", "rank_and_publish": "rank_and_publish"},
    )
    graph.add_edge("relax", "validate")
    graph.add_edge("rank_and_publish", "next_cycle")
    graph.add_conditional_edges(
        "next_cycle",
        nodes.route_after_cycle,
        {"form_hypotheses": "form_hypotheses", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    graph.add_edge("halt", END)

    return graph.compile()


def recursion_limit(config: RunConfig) -> int:
    """Enough headroom for every bounded cycle, with a hard ceiling."""
    per_iteration = 6 + 2 * (config.max_relaxations + 1)
    return min(500, 40 + per_iteration * (config.max_iterations + 1))


def run_once(config: RunConfig, graph=None) -> ResearchState:
    """Run the agent end to end once and return the final state."""
    compiled = graph or build_graph()
    initial: ResearchState = {"config": config, "warnings": [], "errors": [], "iteration": 0}
    return compiled.invoke(initial, config={"recursion_limit": recursion_limit(config)})
