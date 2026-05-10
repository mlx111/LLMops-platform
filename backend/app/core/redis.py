import redis.asyncio as async_redis_lib
import redis as sync_redis_lib

from app.config import settings

_sync_client: sync_redis_lib.Redis | None = None
_async_client: async_redis_lib.Redis | None = None


def get_redis() -> sync_redis_lib.Redis | None:
    """Get or create synchronous Redis client. Returns None if unavailable."""
    global _sync_client
    if _sync_client is not None:
        try:
            _sync_client.ping()
            return _sync_client
        except Exception:
            _sync_client = None
            return None

    try:
        _sync_client = sync_redis_lib.from_url(
            settings.redis_url, decode_responses=True, socket_connect_timeout=2
        )
        _sync_client.ping()
        return _sync_client
    except Exception:
        _sync_client = None
        return None


async def get_async_redis() -> async_redis_lib.Redis | None:
    """Get or create async Redis client. Returns None if unavailable."""
    global _async_client
    if _async_client is not None:
        try:
            await _async_client.ping()
            return _async_client
        except Exception:
            _async_client = None
            return None

    try:
        _async_client = async_redis_lib.from_url(
            settings.redis_url, decode_responses=True, socket_connect_timeout=2
        )
        await _async_client.ping()
        return _async_client
    except Exception:
        _async_client = None
        return None
