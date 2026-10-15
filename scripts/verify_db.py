"""Verify live PostgreSQL + pgvector connectivity and run one hybrid search."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotenv import load_dotenv
load_dotenv()

from knowledge_agent.config import load_settings
from knowledge_agent.db.search import hybrid_search

settings = load_settings()
print(f"DB: {settings.db_user}@{settings.db_host}:{settings.db_port}/{settings.db_name}")
print(f"Embed model: {settings.embedding_model_id} dims={settings.embedding_dims}")

hits = hybrid_search("VPN connection fails with error 809", settings, top_k=3)
print(f"Hits: {len(hits)}")
for h in hits:
    print(f"  {h['incident_number']} sem={h['semantic_score']} combined={h['combined_score']}")
    print(f"    {h['short_description']}")
