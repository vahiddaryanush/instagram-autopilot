"""Autopilot: generate ideas -> schedule -> render -> host media -> publish -> learn from insights."""
import os, random, shutil, subprocess, sys, tempfile, time, uuid
from datetime import datetime, timedelta, time as dtime
import requests
from common import (ROOT, p, load_json, save_json, now_utc, parse, pending, due_items, metrics_due,
                    needs_generation, tz)  # noqa
import ig, llm, render, stock

OUT = p("out")
REPO = os.environ.get("GITHUB_REPOSITORY", "")


def log(*a):
    print(*a, flush=True)


# ---------------- scheduling + generation ----------------
def free_slots(queue, cfg, now):
    zone = tz(cfg)
    taken = {(parse(i["publish_at"]).astimezone().isoformat(), i["type"]) for i in queue}
    today = now.astimezone(zone).date()
    for d in range(cfg.get("horizon_days", 7) + 1):
        day = today + timedelta(days=d)
        for s in cfg["slots"]:
            if "weekdays" in s and day.weekday() not in s["weekdays"]:
                continue
            h, m = map(int, s["time"].split(":"))
            dt = datetime.combine(day, dtime(h, m), tzinfo=zone)
            if dt <= now + timedelta(minutes=15):
                continue
            if (dt.astimezone().isoformat(), s["type"]) in taken:
                continue
            yield dt, s["type"]


def winners(queue):
    scored = []
    for i in queue:
        m = i.get("metrics") or {}
        if m.get("reach"):
            sc = (3 * m.get("saved", 0) + 3 * m.get("shares", 0) + 2 * m.get("comments", 0) + m.get("likes", 0)) / m["reach"]
            scored.append((sc, i["spec"]["hook"]))
    return [h for _, h in sorted(scored, reverse=True)[:5]]


def generate(queue, cfg, now):
    slots = sorted(free_slots(queue, cfg, now))
    by_type = {}
    for dt, t in slots:
        by_type.setdefault(t, []).append(dt)
    recent = [i["spec"]["hook"] for i in queue]
    win = winners(queue)
    added = 0
    for kind, dts in by_type.items():
        try:
            got = llm.ideas(kind, len(dts), cfg, recent, win)
        except Exception as e:
            log(f"idea generation failed for {kind}: {e}")
            continue
        for dt, spec in zip(dts, got):
            if spec["hook"] in recent:
                continue
            recent.append(spec["hook"])
            queue.append({"id": f"{kind.lower()}-{dt.strftime('%Y%m%d-%H%M')}", "type": kind,
                          "publish_at": dt.isoformat(), "status": "pending", "spec": spec})
            added += 1
    queue.sort(key=lambda i: i["publish_at"])
    log(f"generated {added} new items")


# ---------------- media ----------------
def build(item, cfg, workdir):
    spec, kind, tag = item["spec"], item["type"], item["id"] + "-" + uuid.uuid4().hex[:6]
    kw = spec.get("keyword") or cfg["topic"][:30]
    if kind == "REEL":
        bg = stock.video(kw, os.path.join(workdir, tag + "_bg.mp4")) or stock.photo(kw, os.path.join(workdir, tag + "_bg.jpg"))
        return render.reel(spec, bg, workdir, cfg, tag)
    bg = stock.photo(kw, os.path.join(workdir, tag + "_bg.jpg"))
    return (render.carousel if kind == "CAROUSEL" else render.story)(spec, bg, workdir, cfg, tag)


def host(files, cfg):
    """Force-push files to a throw-away orphan branch 'media' and return {local: public_url}."""
    base = cfg.get("media_base_url") or f"https://raw.githubusercontent.com/{REPO}/media"
    tmp = tempfile.mkdtemp()
    for f in files:
        shutil.copy(f, tmp)
    env = dict(os.environ, GIT_AUTHOR_NAME="autopilot", GIT_AUTHOR_EMAIL="bot@users.noreply.github.com",
               GIT_COMMITTER_NAME="autopilot", GIT_COMMITTER_EMAIL="bot@users.noreply.github.com")
    remote = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{REPO}.git"
    for cmd in (["git", "init", "-q", "-b", "media"], ["git", "add", "."], ["git", "commit", "-q", "-m", "media"],
                ["git", "push", "-q", "--force", remote, "media"]):
        subprocess.run(cmd, cwd=tmp, check=True, env=env)
    urls = {f: f"{base}/{os.path.basename(f)}" for f in files}
    end = time.time() + 400
    for u in urls.values():
        while time.time() < end:
            try:
                if requests.head(u, timeout=20, allow_redirects=True).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(8)
        else:
            raise RuntimeError(f"media URL never became reachable: {u}")
    return urls


# ---------------- main ----------------
def main():
    cfg, queue, state = load_json("config.json", {}), load_json("queue.json", []), load_json("state.json", {})
    now = now_utc()
    os.makedirs(OUT, exist_ok=True)

    for it in metrics_due(queue, now):  # learn from results
        try:
            it["metrics"] = ig.insights(it["media_id"])
        except Exception as e:
            it["metrics"] = {"error": str(e)[:200]}
    if needs_generation(queue, state, cfg, now):
        state["last_generation_attempt"] = now.isoformat()
        try:
            generate(queue, cfg, now)
        except Exception as e:
            log("generation error:", e)
    save_json("queue.json", queue)
    save_json("state.json", state)

    jobs = []
    for it in due_items(queue, now):
        if now - parse(it["publish_at"]) > timedelta(hours=48):
            it["status"] = "missed"
            continue
        try:
            jobs.append((it, build(it, cfg, OUT)))
        except Exception as e:
            fail(it, cfg, f"render: {e}")
    save_json("queue.json", queue)
    if not jobs:
        return
    try:
        urls = host([f for _, fs in jobs for f in fs], cfg)
    except Exception as e:
        for it, _ in jobs:
            fail(it, cfg, f"hosting: {e}")
        save_json("queue.json", queue)
        return
    for it, files in jobs:
        try:
            it["media_id"] = ig.publish(it["type"], [urls[f] for f in files], it["spec"]["caption"])
            it["status"], it["posted_at"] = "posted", now_utc().isoformat()
            log("posted", it["id"])
        except Exception as e:
            fail(it, cfg, f"publish: {e}")
        save_json("queue.json", queue)  # saved per item: a crash can never cause a double post
        time.sleep(3)

    keep = [i for i in queue if i["status"] == "pending"]
    done = [i for i in queue if i["status"] != "pending"][-150:]
    save_json("queue.json", sorted(keep + done, key=lambda i: i["publish_at"]))


def fail(it, cfg, msg):
    it["attempts"] = it.get("attempts", 0) + 1
    it["last_error"] = msg[:400]
    if it["attempts"] >= cfg.get("max_attempts", 3):
        it["status"] = "failed"
    log("FAIL", it["id"], msg)


def demo():
    """Local preview without any API keys: python pipeline.py --demo"""
    cfg = load_json("config.json", {})
    spec = {"hook": "۳ ترفند برای تمیز کردن سریع ماکروویو",
            "points": ["یک کاسه آب با چند برش لیمو را سه دقیقه داخل ماکروویو گرم کنید",
                       "پنج دقیقه صبر کنید تا بخار بنشیند و دیواره‌ها را پاک کنید",
                       "کثیفی‌ها بدون تلاش زیاد از بین می‌روند"],
            "cta": "ذخیره کن تا یادت بماند", "caption": "demo", "keyword": "kitchen"}
    os.makedirs(OUT, exist_ok=True)
    for kind in ("CAROUSEL", "STORY", "REEL"):
        log(kind, build({"id": "demo-" + kind.lower(), "type": kind, "spec": spec}, cfg, OUT))


if __name__ == "__main__":
    demo() if "--demo" in sys.argv else main()
