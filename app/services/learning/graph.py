from dataclasses import dataclass, field


@dataclass
class GraphNode:
    id: str
    subject_id: str
    title: str
    slug: str
    order_index: int
    estimated_minutes: int = 45
    waec_weight: float = 0
    jamb_weight: float = 0
    prerequisite_topic_id: str | None = None


@dataclass
class GraphEdge:
    id: str
    source: str
    target: str
    kind: str = "PREREQUISITE"
    strength: float = 1.0
    rationale: str | None = None


@dataclass
class KnowledgeGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    incoming: dict[str, list[GraphEdge]] = field(default_factory=dict)
    outgoing: dict[str, list[GraphEdge]] = field(default_factory=dict)


def build_graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> KnowledgeGraph:
    graph = KnowledgeGraph(nodes={node.id: node for node in nodes})
    for edge in edges:
        graph.incoming.setdefault(edge.target, []).append(edge)
        graph.outgoing.setdefault(edge.source, []).append(edge)
    return graph


def incoming_edges(graph: KnowledgeGraph, topic_id: str) -> list[GraphEdge]:
    return graph.incoming.get(topic_id, [])


def prerequisite_satisfied(mastery: int, strength: float, pretest_passed: bool) -> bool:
    from app.services.learning.evidence import GATE

    return pretest_passed or mastery >= GATE * strength


def topic_available(
    graph: KnowledgeGraph,
    topic_id: str,
    mastery_of,
    pretest_passed: set[str],
) -> bool:
    for edge in incoming_edges(graph, topic_id):
        if edge.kind != "PREREQUISITE":
            continue
        if not prerequisite_satisfied(
            mastery_of(edge.source),
            edge.strength,
            edge.source in pretest_passed,
        ):
            return False
    return True
