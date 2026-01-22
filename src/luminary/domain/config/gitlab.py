"""GitLab configuration model."""

from typing import Optional

from pydantic import BaseModel


class GitLabConfig(BaseModel):
    """Configuration for GitLab integration.

    Attributes:
        url: GitLab instance URL (None = from GITLAB_URL env or gitlab.com)
        token: GitLab API token (None = from GITLAB_TOKEN env)
    """

    url: Optional[str] = None
    token: Optional[str] = None
