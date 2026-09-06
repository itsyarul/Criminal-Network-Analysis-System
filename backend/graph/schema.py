"""
Schema — constraints & indexes per criminal-network-live-reveal.md:68

Nodes: Person(id, name, role, cell), Phone, Account, Event(id, cell, day), Location, FIR, Post
Edges: CALLED(day, dur, tower), TRANSACTED(amount, day, type), MENTIONED_IN(conf), ASSOCIATED_WITH, BRIDGES_VIA

Idempotent — IF NOT EXISTS.
"""
from backend.graph.neo4j_client import get_driver, is_available
from backend.config import NEO4J_DATABASE

CONSTRAINTS_AND_INDEXES = [
    # Constraints
    "CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT phone_number_unique IF NOT EXISTS FOR (ph:Phone) REQUIRE ph.number IS UNIQUE",
    "CREATE CONSTRAINT account_id_unique IF NOT EXISTS FOR (a:Account) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT event_id_unique IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT fir_id_unique IF NOT EXISTS FOR (f:FIR) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT post_id_unique IF NOT EXISTS FOR (s:Post) REQUIRE s.id IS UNIQUE",
    # Indexes
    "CREATE INDEX phone_number_index IF NOT EXISTS FOR (ph:Phone) ON (ph.number)",
    "CREATE INDEX event_day_index IF NOT EXISTS FOR (e:Event) ON (e.day)",
    "CREATE INDEX person_cell_index IF NOT EXISTS FOR (p:Person) ON (p.cell)",
    "CREATE INDEX txn_day_index IF NOT EXISTS FOR ()-[r:TRANSACTED]-() ON (r.day)",
    "CREATE INDEX called_day_index IF NOT EXISTS FOR ()-[r:CALLED]-() ON (r.day)",
]

def ensure_schema():
    drv = get_driver()
    if not drv:
        print("[schema] Neo4j unavailable — skipping constraints (in-memory mode)")
        return False
    with drv.session(database=NEO4J_DATABASE) as session:
        for q in CONSTRAINTS_AND_INDEXES:
            try:
                session.run(q).consume()
                print(f"[schema] OK: {q[:60]}...")
            except Exception as e:
                print(f"[schema] warn: {q[:60]} -> {e}")
    return True

def clear_graph():
    """Delete all nodes/edges — used by loader --clean for idempotency."""
    drv = get_driver()
    if not drv:
        print("[schema] clear_graph: no Neo4j, nothing to clear")
        return
    with drv.session(database=NEO4J_DATABASE) as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
        print("[schema] graph cleared")
