"""Read Neo4j connection settings from environment variables or a .env file next to this script."""
import os

def settings():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    missing = [k for k in ("NEO4J_URI", "NEO4J_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in.")
    return {"uri": os.environ["NEO4J_URI"], "user": os.environ.get("NEO4J_USERNAME", "neo4j"),
            "password": os.environ["NEO4J_PASSWORD"], "database": os.environ.get("NEO4J_DATABASE", "neo4j")}
