from langgraph.graph import StateGraph, START, END

try:
    from langgraph.checkpoint.memory import InMemorySaver as _Saver
except ImportError:
    from langgraph.checkpoint.memory import MemorySaver as _Saver

from graph.agents.worker import make_worker
from graph.state import UltronState, WORKER_NAMES
from graph.supervisor import make_supervisor


def route_next(state: UltronState) -> str:
    return state["next"]


def build_graph(bus, ollama=None, pool=None, *, route_fn=None, run_worker=None, checkpointer=None):
    g = StateGraph(UltronState)
    g.add_node("supervisor", make_supervisor(bus, route_fn))
    for name in WORKER_NAMES:
        g.add_node(name, make_worker(name, bus, ollama, run_worker, pool))
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route_next,
                            {**{n: n for n in WORKER_NAMES}, "FINISH": END})
    for name in WORKER_NAMES:
        g.add_edge(name, "supervisor")   # workers always return to the supervisor
    # Optional AsyncPostgresSaver comes from graph/checkpointer.py; in-memory when none is given.
    return g.compile(checkpointer=checkpointer or _Saver())
