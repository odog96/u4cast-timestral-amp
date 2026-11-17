"""Settings read from environment variables, so they change without a code change."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL_NAME = "mistralai/Timestral-209M"
# Proposed starting size. Batch size does not change speed or results; the
# final size depends on the limits of the workbench model endpoint.
DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class Settings:
    """How the model is chosen and called."""

    model_name: str = DEFAULT_MODEL_NAME
    batch_size: int = DEFAULT_BATCH_SIZE

    @classmethod
    def from_environment(cls) -> Settings:
        """Read `U4CAST_MODEL_NAME` and `U4CAST_BATCH_SIZE`, with defaults for both."""
        batch_size = int(os.environ.get("U4CAST_BATCH_SIZE", DEFAULT_BATCH_SIZE))
        if batch_size < 1:
            raise ValueError(f"U4CAST_BATCH_SIZE must be at least 1; got {batch_size}.")
        return cls(
            model_name=os.environ.get("U4CAST_MODEL_NAME", DEFAULT_MODEL_NAME),
            batch_size=batch_size,
        )
