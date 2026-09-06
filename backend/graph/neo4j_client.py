"""
Neo4j client with graceful fallback to in-memory NetworkX.

If NEO4J_URI not reachable, builder/analytics fall back to networkx so
Task 1 pipeline remains demonstrable without Docker (CI / offline judge).
"""
from typing import Optional
import os

try:
    from neo4j import GraphDatabase, exceptions as neo4j_exceptions
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False
    GraphDatabase = None

from backend.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE

_driver = None

def get_driver():
    global _driver
    if not HAS_NEO4J:
        return None
    if _driver is not None:
        return _driver
    try:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        _driver.verify_connectivity()
        return _driver
    except Exception as e:
        print(f"[neo4j] connection failed ({NEO4J_URI}): {e} — falling back to in-memory graph")
        _driver = None
        return None

def is_available() -> bool:
    return get_driver() is not None

def close():
    global _driver
    if _driver:
        _driver.close()
        _driver = None

def run_query(query: str, params: dict = None):
    drv = get_driver()
    if not drv:
        raise RuntimeError("Neo4j not available — use in-memory graph")
    with drv.session(database=NEO4J_DATABASE) as session:
        result = session.run(query, params or {})
        return [dict(r) for r in result]

def run_write(query: str, params: dict = None):
    drv = get_driver()
    if not drv:
        raise RuntimeError("Neo4j not available")
    with drv.session(database=NEO4J_DATABASE) as session:
        return session.execute_write(lambda tx: list(tx.run(query, params or {})))


def delete_investigation_graph(iid: str) -> int:
    """Delete all Neo4j nodes (and their edges) tagged with an investigation_id.

    Returns the number of deleted nodes, or 0 when Neo4j is unavailable.
    """
    drv = get_driver()
    if not drv:
        return 0
    with drv.session(database=NEO4J_DATABASE) as session:
        result = session.run(
            "MATCH (n {investigation_id: $iid}) DETACH DELETE n RETURN count(n) AS deleted",
            iid=iid,
        )
        record = result.single()
        return int(record["deleted"]) if record else 0
