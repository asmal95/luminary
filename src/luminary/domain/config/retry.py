"""Retry configuration model."""

from pydantic import BaseModel, Field


class RetryConfig(BaseModel):
    """Configuration for retry logic.
    
    Attributes:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        backoff_multiplier: Exponential backoff multiplier
        jitter: Random jitter factor (0.0-1.0)
    """

    max_attempts: int = Field(3, gt=0, le=10)
    initial_delay: float = Field(1.0, ge=0.0)  # Allow 0 for tests
    backoff_multiplier: float = Field(2.0, ge=1.0, le=10.0)
    jitter: float = Field(0.1, ge=0.0, le=1.0)
