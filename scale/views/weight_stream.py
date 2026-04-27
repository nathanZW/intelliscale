"""
Server-Sent Events (SSE) endpoint that streams live weight readings from the
satellite service to the browser. Replaces AJAX polling with a single long-lived
HTTP connection that receives push updates as the satellite reads each scale.
"""
import json
import time
import logging
import redis
from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404

from ..models import Scale
from ..satellite_service import (
    channel_for_scale_key,
    get_cached_weight_by_scale_id,
)

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 15
SUBSCRIBE_TIMEOUT_SECONDS = 1.0


def _sse_format(data, event=None):
    lines = []
    if event:
        lines.append(f'event: {event}')
    lines.append(f'data: {json.dumps(data)}')
    return '\n'.join(lines) + '\n\n'


def _cache_key_for(scale):
    return str(scale.scale_id) if scale.scale_id else str(scale.pk)


def _stream(scale):
    """Generator that yields SSE frames for one scale's live weight."""
    cache_key = _cache_key_for(scale)
    channel = channel_for_scale_key(cache_key)

    snapshot = get_cached_weight_by_scale_id(cache_key)
    if snapshot:
        snapshot = dict(snapshot)
        snapshot['cache_key'] = cache_key
        yield _sse_format(snapshot, event='weight')

    redis_url = getattr(django_settings, 'CELERY_BROKER_URL', 'redis://localhost:6379/0')
    pubsub = None
    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(channel)
    except redis.RedisError as exc:
        logger.warning(f"SSE stream could not subscribe to Redis: {exc}")
        yield _sse_format({'error': 'realtime_unavailable'}, event='error')
        return

    last_heartbeat = time.monotonic()
    try:
        while True:
            message = pubsub.get_message(timeout=SUBSCRIBE_TIMEOUT_SECONDS)
            if message and message.get('type') == 'message':
                try:
                    payload = json.loads(message['data'])
                except (ValueError, TypeError):
                    continue
                yield _sse_format(payload, event='weight')

            if time.monotonic() - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                yield ': heartbeat\n\n'
                last_heartbeat = time.monotonic()
    except GeneratorExit:
        pass
    finally:
        try:
            if pubsub is not None:
                pubsub.close()
        except Exception:
            pass


@login_required
def stream_weight(request, scale_id):
    scale = get_object_or_404(Scale, pk=scale_id)
    response = StreamingHttpResponse(_stream(scale), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
