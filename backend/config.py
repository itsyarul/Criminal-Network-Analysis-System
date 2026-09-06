import os
from pathlib import Path

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
AUDIT_PATH = PROJECT_ROOT / "audit.jsonl"

# Neo4j config — env-overridable, offline fallback handled by graph builder
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "criminal123")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# Thresholds per design doc criminal-network-live-reveal.md:75-78
BURST_Z_THRESHOLD = 2.0
BURST_WINDOW_DAYS = 6
BURST_CORRELATION_SPAN = 7
STRUCTURING_CASH_THRESHOLD = 50000
STRUCTURING_MIN_TXNS = 10
STRUCTURING_WINDOW_DAYS = 12
STRUCTURING_MIN_CONSOLIDATIONS = 2
STRUCTURING_CONSOLIDATION_THRESHOLD = 250000
STRUCTURING_CONSOLIDATION_WINDOW = 6
BRIDGE_NORM_BETWEENNESS_WEIGHT = 0.6
BRIDGE_CROSS_DEGREE_WEIGHT = 0.4
BRIDGE_TOP_K = 6
RESOLUTION_FUZZY_THRESHOLD = 85  # RapidFuzz ratio

# Audit
AUDIT_USER = "demo-operator"
