"""Shared helpers (stdlib only, so the cheap 'is there work?' check stays fast)."""
import json, os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))


def p(*parts):
    return os.path.join(ROOT, *parts)


def load_json(name, default):
    try:
        with open(p(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(name, data):
    with open(p(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def now_utc():
    return datetime.now(timezone.utc)


def parse(ts):
    return datetime.fromisoformat(ts)


def pending(queue):
    return [i for i in queue if i.get("status", "pending") == "pending"]


def due_items(queue, now):
    return [i for i in pending(queue) if parse(i["publish_at"]) <= now]


def metrics_due(queue, now):
    return [i for i in queue
            if i.get("status") == "posted" and i.get("media_id")
            and i["type"] in ("REEL", "CAROUSEL") and "metrics" not in i
            and now - parse(i["posted_at"]) > timedelta(hours=48)]


def needs_generation(queue, state, cfg, now):
    if len(pending(queue)) >= cfg.get("min_pending", 6):
        return False
    last = state.get("last_generation_attempt")
    return not last or now - parse(last) > timedelta(hours=6)


def needs_work(queue, state, cfg, now):
    return bool(due_items(queue, now) or metrics_due(queue, now)
                or needs_generation(queue, state, cfg, now))


def tz(cfg):
    return ZoneInfo(cfg.get("timezone", "Asia/Tehran"))
