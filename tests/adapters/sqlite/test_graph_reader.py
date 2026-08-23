"""SQLite and application tests for bounded relationship graph traversal."""

from __future__ import annotations

from datetime import UTC, datetime

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteGraphReader,
    SqlitePeopleRepository,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app.relationships import (
    FindConnection,
    GetRelationshipGraph,
    SetRelationship,
    SetRelationshipInput,
)
from people_context.domain.person import Person
from people_context.ports.clock import SystemClock
from people_context.ports.graph import GraphReader


def _setup():
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    store = SqliteRelationshipStore(conn)
    vocabulary = SqliteRelationshipVocabularyStore(conn)
    setter = SetRelationship(people, store, SqliteAuditLog(conn), SystemClock(), vocabulary)
    return conn, people, vocabulary, setter


def _person(people: SqlitePeopleRepository, name: str, *, is_self: bool = False) -> Person:
    person = Person(canonical_name=name, is_self=is_self)
    people.save_person(person)
    return person


def test_chain_subgraph_and_shortest_path_have_perspective_types() -> None:
    conn, people, vocabulary, setter = _setup()
    a = _person(people, "A", is_self=True)
    b = _person(people, "B")
    c = _person(people, "C")
    d = _person(people, "D")
    setter.execute(SetRelationshipInput(subject_id=a.id, object_id=b.id, type="reports to"))
    setter.execute(SetRelationshipInput(subject_id=b.id, object_id=c.id, type="reports_to"))
    setter.execute(SetRelationshipInput(subject_id=d.id, object_id=a.id, type="friend of"))

    reader = SqliteGraphReader(conn, SystemClock())
    graph = GetRelationshipGraph(people, reader, vocabulary).execute(a.id, depth=2)
    assert not hasattr(graph, "error")
    assert [node.name for node in graph.nodes] == ["A", "B", "D", "C"]
    assert {(edge.subject_id, edge.object_id, edge.type) for edge in graph.edges} == {
        (a.id, b.id, "reports_to"),
        (b.id, c.id, "reports_to"),
        tuple((*sorted((a.id, d.id)), "friend_of")),
    }
    assert graph.truncated is False

    connection = FindConnection(people, reader, vocabulary).execute(c.id, d.id)
    assert connection.connected is True
    assert [hop.person.name for hop in connection.hops] == ["B", "A", "D"]
    assert [hop.edge.display_type for hop in connection.hops] == ["manages", "manages", "friend_of"]


def test_graph_caps_nodes_reports_truncation_and_excludes_deleted_people() -> None:
    conn, people, vocabulary, setter = _setup()
    center = _person(people, "Center")
    leaves = [_person(people, f"Leaf {index:03d}") for index in range(150)]
    for leaf in leaves:
        setter.execute(SetRelationshipInput(subject_id=center.id, object_id=leaf.id, type="friend_of"))
    leaves[0].deleted_at = datetime.now(UTC)
    people.save_person(leaves[0])

    graph = GetRelationshipGraph(people, SqliteGraphReader(conn, SystemClock()), vocabulary).execute(center.id, depth=1)
    assert len(graph.nodes) == 100
    assert graph.truncated is True
    assert leaves[0].id not in {node.person_id for node in graph.nodes}
    assert all(leaves[0].id not in (edge.subject_id, edge.object_id) for edge in graph.edges)


def test_disconnected_and_not_found_results_are_structured() -> None:
    conn, people, vocabulary, _ = _setup()
    a = _person(people, "A")
    b = _person(people, "B")
    use_case = FindConnection(people, SqliteGraphReader(conn, SystemClock()), vocabulary)

    disconnected = use_case.execute(a.id, b.id)
    assert disconnected.model_dump(mode="json") == {
        "connected": False,
        "hops": [],
        "reason": "not_connected",
    }
    missing = use_case.execute(a.id, "missing")
    assert missing.model_dump(mode="json") == {"error": "person_not_found", "person_id": "missing"}


def test_traversal_stops_on_its_node_budget_and_reports_truncation() -> None:
    """Regression: the display cap alone let a depth-4 request materialize the whole store."""
    conn, people, _, setter = _setup()
    chain = [_person(people, f"Person {index:03d}") for index in range(40)]
    for previous, following in zip(chain, chain[1:], strict=False):
        setter.execute(SetRelationshipInput(subject_id=previous.id, object_id=following.id, type="friend_of"))

    reader = SqliteGraphReader(conn, SystemClock())
    unbounded = reader.neighbors(chain[0].id, 4)
    assert unbounded.truncated is False

    budgeted = reader.neighbors(chain[0].id, 4, 3)
    assert len(budgeted.nodes) == 3
    assert budgeted.truncated is True


def test_a_node_set_larger_than_one_bind_chunk_still_sees_every_edge() -> None:
    """A node set spanning several bind chunks must not drop, duplicate, or reorder edges.

    Edge queries chunk both sides of their `IN` clauses, so every edge is matched by
    exactly one pair of chunks and the results are merged. This exercises that boundary:
    an off-by-one in the chunking, or a merge that appended instead of keying by edge id,
    would show up here as a missing or repeated edge.
    """
    conn, people, _, setter = _setup()
    center = _person(people, "Center")
    leaves = [_person(people, f"Leaf {index:04d}") for index in range(450)]
    for leaf in leaves:
        setter.execute(SetRelationshipInput(subject_id=center.id, object_id=leaf.id, type="friend_of"))

    subgraph = SqliteGraphReader(conn, SystemClock()).neighbors(center.id, 1)

    assert len(subgraph.nodes) == len(leaves) + 1
    assert len(subgraph.edges) == len(leaves)
    assert len({edge.id for edge in subgraph.edges}) == len(leaves)
    assert [edge.id for edge in subgraph.edges] == sorted(edge.id for edge in subgraph.edges)


def test_sqlite_graph_reader_satisfies_exact_port() -> None:
    reader: GraphReader = SqliteGraphReader(open_db(":memory:"), SystemClock())
    assert isinstance(reader, GraphReader)
