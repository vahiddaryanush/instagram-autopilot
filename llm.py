"""Idea generation with the Gemini API (free tier)."""
import json, os, re
import requests

RULES = {
    "REEL": "hook: max 9 words. points: 3 or 4 items, each max 15 words (they will be read aloud, 20-30 seconds total).",
    "CAROUSEL": "hook: max 9 words. points: 4 to 6 items, each max 22 words, each one a standalone useful tip.",
    "STORY": "hook: max 8 words. points: exactly 1 item, max 14 words.",
}


def ideas(kind, count, cfg, recent_hooks, winners):
    key = os.environ["GEMINI_API_KEY"]
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    prompt = f"""You write short-form social media content for an Instagram account.
Topic area: {cfg['topic']}
Output language: {cfg['language_name']} (all text fields except "keyword" must be in this language).
Format: {kind}. Create {count} DIFFERENT ideas.
Rules for this format: {RULES[kind]}
General rules:
- The first line (hook) must create curiosity or promise a clear benefit; no clickbait lies.
- Content must be accurate, practical and safe. No medical, legal or financial claims. No dangerous advice (e.g. mixing chemicals).
- No brand names, no real people, no copyrighted material.
- cta: one short call to action (save / share / follow).
- caption: 1-2 sentences plus one question that invites comments, then 5-8 relevant hashtags.
- keyword: 1-3 English words describing a concrete, filmable visual for stock footage (e.g. "kitchen sink", "pouring tea").
- Do not repeat or closely paraphrase these recent hooks: {json.dumps(recent_hooks[-40:], ensure_ascii=False)}
- Topics that performed best so far (make more in this spirit, not copies): {json.dumps(winners, ensure_ascii=False)}
Return ONLY JSON: {{"ideas":[{{"hook":"","points":[""],"cta":"","caption":"","keyword":""}}]}}"""
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0}},
        timeout=120)
    data = r.json()
    if r.status_code >= 400:
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:500])
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    out = []
    for i in json.loads(text).get("ideas", []):
        pts = [x for x in i.get("points", []) if isinstance(x, str) and x.strip()]
        if i.get("hook") and pts and i.get("caption"):
            i["points"] = pts[:1] if kind == "STORY" else pts
            out.append(i)
    return out
