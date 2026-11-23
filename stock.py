"""Free stock backgrounds from Pexels (license allows free use without attribution)."""
import os, random
import requests

KEY = os.environ.get("PEXELS_API_KEY", "")
H = {"Authorization": KEY}


def _download(url, dest):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


def photo(keyword, dest):
    if not KEY:
        return None
    try:
        r = requests.get("https://api.pexels.com/v1/search", headers=H, timeout=30,
                         params={"query": keyword, "orientation": "portrait", "per_page": 15}).json()
        res = r.get("photos", [])
        if not res:
            return None
        return _download(random.choice(res[:10])["src"]["large2x"], dest)
    except Exception as e:
        print("pexels photo failed:", e)
        return None


def video(keyword, dest):
    if not KEY:
        return None
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers=H, timeout=30,
                         params={"query": keyword, "orientation": "portrait", "size": "medium", "per_page": 15}).json()
        vids = [v for v in r.get("videos", []) if 5 <= v.get("duration", 0) <= 60]
        if not vids:
            return None
        files = [f for f in random.choice(vids[:10])["video_files"] if f.get("file_type") == "video/mp4"]
        files = [f for f in files if (f.get("height") or 0) >= (f.get("width") or 0)]
        if not files:
            return None
        ok = [f for f in files if 1080 <= (f["height"] or 0) <= 2000] or files
        best = min(ok, key=lambda f: f["height"])
        return _download(best["link"], dest)
    except Exception as e:
        print("pexels video failed:", e)
        return None
