from __future__ import annotations

import os

from hypothesis import HealthCheck, settings


settings.register_profile(
    "focused",
    max_examples=25,
    deadline=None,
    print_blob=True,
)
settings.register_profile(
    "standard",
    max_examples=100,
    deadline=None,
    print_blob=True,
)
settings.register_profile(
    "stress",
    max_examples=500,
    deadline=None,
    print_blob=True,
    suppress_health_check=(HealthCheck.too_slow,),
)
settings.load_profile(os.environ.get("AEGIS_HYPOTHESIS_PROFILE", "standard"))
