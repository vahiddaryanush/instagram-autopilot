"""Instagram Graph API: publishing + insights."""
import json, os, time
import requests

API = "https://graph.facebook.com/v21.0"
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
IG_ID = os.environ.get("IG_USER_ID", "")


def _post(path, **params):
    params["access_token"] = TOKEN
    r = requests.post(f"{API}/{path}", data=params, timeout=90)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(json.dumps(data.get("error", data), ensure_ascii=False))
    return data


def _wait(cid, timeout=420):
    end = time.time() + timeout
    while time.time() < end:
        r = requests.get(f"{API}/{cid}", params={"fields": "status_code,status", "access_token": TOKEN}, timeout=30).json()
        code = r.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {code}: {r.get('status')}")
        time.sleep(6)
    raise RuntimeError("timeout waiting for media processing")


def publish(kind, urls, caption=""):
    """kind: REEL | STORY | CAROUSEL | IMAGE. urls: list of public URLs."""
    if kind == "REEL":
        c = _post(f"{IG_ID}/media", media_type="REELS", video_url=urls[0], caption=caption, share_to_feed="true")
    elif kind == "STORY":
        c = _post(f"{IG_ID}/media", media_type="STORIES", image_url=urls[0])
    elif kind == "IMAGE":
        c = _post(f"{IG_ID}/media", image_url=urls[0], caption=caption)
    elif kind == "CAROUSEL":
        kids = []
        for u in urls:
            k = _post(f"{IG_ID}/media", is_carousel_item="true", image_url=u)
            _wait(k["id"])
            kids.append(k["id"])
        c = _post(f"{IG_ID}/media", media_type="CAROUSEL", children=",".join(kids), caption=caption)
    else:
        raise ValueError(kind)
    _wait(c["id"])
    return _post(f"{IG_ID}/media_publish", creation_id=c["id"])["id"]


def insights(media_id):
    r = requests.get(f"{API}/{media_id}/insights",
                     params={"metric": "reach,saved,shares,likes,comments", "access_token": TOKEN}, timeout=30).json()
    if "error" in r:
        raise RuntimeError(json.dumps(r["error"], ensure_ascii=False))
    return {m["name"]: m["values"][0]["value"] for m in r.get("data", [])}
