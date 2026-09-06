"""Offline eval gate — scores pipeline exports against ground truth.

Runs separately on exports (output/, data/ground_truth_network.json,
data/alias_map.json) and never touches Neo4j or the ingest path.
See docs/designs/criminal-network-live-reveal.md (score.py, eval gate).
"""
