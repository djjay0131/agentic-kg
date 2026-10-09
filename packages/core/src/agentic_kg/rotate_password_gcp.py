"""Entrypoint: rotate the Neo4j password inside GCP (ADR-0007).

``python -m agentic_kg.rotate_password_gcp`` — see ``rotate_password`` for the
procedure. Kept as its own module so an older job image without in-GCP
generation fails to start instead of running the legacy rotation.
"""

from agentic_kg.rotate_password import main_gcp

if __name__ == "__main__":
    main_gcp()
