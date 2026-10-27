from functools import lru_cache

from stripe import StripeClient

from backend.app.core.config import settings


@lru_cache(maxsize=1)
def get_stripe_client() -> StripeClient:
    api_key = settings.stripe_secret_key

    if not api_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")

    return StripeClient(api_key)
