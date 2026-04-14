"""Code Context integration configuration model."""

from typing import Optional

from pydantic import BaseModel, Field


class CodeContextConfig(BaseModel):
    """Configuration for optional Code Context retrieval integration.

    Attributes:
        enabled: Whether retrieval integration is enabled
        base_url: Code Context REST API base URL
        timeout: HTTP request timeout in seconds
        repo_name: Managed repository name (e.g. "group/project")
        branch: Branch to query in index (e.g. "main")
        max_queries: Max retrieval queries generated per file
        search_limit: Max hits requested from search endpoint per query
        max_hits_per_query: Max hits used from search results per query
        neighbors_depth: Graph depth for get_symbol_neighbors calls
        max_neighbors: Max neighbors retained per symbol
        max_context_chars: Hard cap for assembled retrieval context text
        fail_open: Continue review without retrieval on integration errors
    """

    enabled: bool = False
    base_url: str = "http://localhost:8000"
    timeout: float = Field(10.0, gt=0, le=120)
    repo_name: Optional[str] = None
    branch: Optional[str] = None

    max_queries: int = Field(3, ge=1, le=10)
    search_limit: int = Field(6, ge=1, le=50)
    max_hits_per_query: int = Field(3, ge=1, le=20)
    neighbors_depth: int = Field(2, ge=1, le=4)
    max_neighbors: int = Field(5, ge=0, le=50)
    max_context_chars: int = Field(20000, ge=1000, le=200000)
    fail_open: bool = True
