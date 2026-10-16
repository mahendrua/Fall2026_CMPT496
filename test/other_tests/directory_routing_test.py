"""!
@file directory_routing_test.py
@brief Tests for the judge/rewrite loop in agent/directory_agent.py (T-138).
@details A rejected summary is still rewritten up to twice, but the judge no
longer runs after the last rewrite, since nothing read that verdict.
"""

from agent.directory_agent import MAX_REFINEMENTS, after_judgement, after_refinement


def walk_loop(judge_accepts):
    """Follow the loop from the first judgement, the way the graph does."""
    state = {"summary_acceptable": False, "refinement_attempts": 0}
    node, visited = "judgement", []

    while node != "business_rules_extractor":
        visited.append(node)

        if node == "judgement":
            state["summary_acceptable"] = judge_accepts(len(visited))
            node = after_judgement(state)
        else:
            state["refinement_attempts"] += 1
            node = after_refinement(state)

    return visited


def test_accepted_summary_is_not_rewritten():
    assert walk_loop(lambda _: True) == ["judgement"]


def test_rejected_summary_still_gets_two_rewrites_but_no_wasted_judge():
    # Before T-138: judgement, refinement, judgement, refinement, judgement.
    assert MAX_REFINEMENTS == 2
    assert walk_loop(lambda _: False) == ["judgement", "refinement", "judgement", "refinement"]


def test_rewrite_accepted_by_the_judge_stops_the_loop():
    # Rejected once, then the first rewrite is accepted.
    assert walk_loop(lambda step: step > 1) == ["judgement", "refinement", "judgement"]
