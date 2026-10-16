"""Unit tests for conversation-memory settings (no AWS/LLM needed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from langchain_core.messages import AIMessage, HumanMessage

from knowledge_agent.agents import supervisor as S


def _conv(n_turns):
    msgs = []
    for i in range(n_turns):
        msgs.append(HumanMessage(content=f"q{i}", id=f"h{i}"))
        msgs.append(AIMessage(content=f"a{i}", id=f"a{i}"))
    return msgs


def test_trim_keeps_last_n_turns():
    kept = S.trim_conversation(_conv(5), 2)
    assert [m.content for m in kept] == ["q3", "a3", "q4", "a4"]


def test_trim_zero_means_unlimited():
    msgs = _conv(4)
    assert S.trim_conversation(msgs, 0) == msgs
    assert S.trim_conversation(msgs, -1) == msgs


def test_trim_short_history_untouched():
    msgs = _conv(2)
    assert S.trim_conversation(msgs, 10) == msgs


def test_trim_keeps_trailing_unanswered_human():
    msgs = _conv(3) + [HumanMessage(content="q3", id="h3")]
    kept = S.trim_conversation(msgs, 2)
    assert [m.content for m in kept] == ["q2", "a2", "q3"]


def _reset_graph(monkeypatch):
    """Snapshot + clear graph globals so each build test starts fresh."""
    old_graph, old_cp = S._graph, S._checkpointer
    S._graph, S._checkpointer = None, None
    monkeypatch.setattr(S, "_graph", None, raising=False)
    monkeypatch.setattr(S, "_checkpointer", None, raising=False)
    return old_graph, old_cp


def test_build_without_memory_has_no_checkpointer(monkeypatch):
    old = _reset_graph(monkeypatch)
    try:
        monkeypatch.setenv("MEMORY_ENABLED", "false")
        g = S.build_supervisor_graph()
        assert S._checkpointer is None
        assert g is not None
    finally:
        S._graph, S._checkpointer = old


def test_build_with_memory_has_checkpointer(monkeypatch):
    old = _reset_graph(monkeypatch)
    try:
        monkeypatch.setenv("MEMORY_ENABLED", "true")
        S.build_supervisor_graph()
        assert S._checkpointer is not None
    finally:
        S._graph, S._checkpointer = old


def test_memory_settings_parsed_from_env(monkeypatch):
    from knowledge_agent.config import load_settings

    monkeypatch.setenv("MEMORY_ENABLED", "false")
    monkeypatch.setenv("MEMORY_MAX_TURNS", "3")
    monkeypatch.setenv("AGENTCORE_MEMORY_ID", "mem-123")
    s = load_settings()
    assert s.memory_enabled is False
    assert s.memory_max_turns == 3
    assert s.agentcore_memory_id == "mem-123"


def test_prune_never_raises_on_broken_graph():
    class Broken:
        def get_state(self, cfg):
            raise RuntimeError("nope")

    S._prune_checkpoint(Broken(), {}, 2)  # must not raise


def test_prune_removes_stale_messages():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import Annotated

    from langgraph.graph.message import add_messages
    from typing import TypedDict

    class St(TypedDict):
        messages: Annotated[list, add_messages]

    g = StateGraph(St)
    g.add_edge(START, END)
    cp = MemorySaver()
    graph = g.compile(checkpointer=cp)
    cfg = {"configurable": {"thread_id": "t-prune"}}
    graph.invoke({"messages": _conv(5)}, config=cfg)
    S._prune_checkpoint(graph, cfg, 2)
    stored = graph.get_state(cfg).values["messages"]
    assert [m.content for m in stored] == ["q3", "a3", "q4", "a4"]
