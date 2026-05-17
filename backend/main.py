import requests
from groq import Groq
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import re
import threading
import time
import difflib
from urllib.parse import quote

load_dotenv()

_groq_api_key = os.getenv("GROQ_API_KEY")
client = Groq(api_key=_groq_api_key) if _groq_api_key else None


# This creates the FastAPI app — like Flask's app = Flask(__name__)
app = FastAPI()

_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# Browser requests need an explicit origin match. Local dev + optional production
# URLs from env (comma-separated), e.g. ALLOWED_ORIGINS=https://my-app.vercel.app
_cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
_extra_origins = os.getenv("ALLOWED_ORIGINS", "")
if _extra_origins.strip():
    _cors_origins.extend(
        [o.strip() for o in _extra_origins.split(",") if o.strip()]
    )

# Emergency bypass if preflight still fails after redeploy (any site can call your API from a browser).
_cors_allow_all = os.getenv("CORS_ALLOW_ALL", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

if _cors_allow_all:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        # Production + preview: https://<project>.vercel.app
        allow_origin_regex=r"https://[a-zA-Z0-9][-a-zA-Z0-9]*\.vercel\.app",
        allow_methods=["*"],
        allow_headers=["*"],
    )


# This defines what shape the incoming request data should be
# When frontend sends {"message": "something like death note"}
# FastAPI automatically validates and parses it
class RecommendRequest(BaseModel):
    message: str


class FeedbackRequest(BaseModel):
    message: str
    history: list


# ── these functions are identical to your CLI version ──


# ── Jikan (rate-limited public API): burst lookups → 429; throttle + User-Agent. ──
_jikan_lock = threading.Lock()
_jikan_headers = {
    "User-Agent": os.getenv(
        "JIKAN_USER_AGENT",
        "AnimeRecommender/1.0 (educational; https://github.com/MackayPorter/anime_recommender)",
    ),
}

_anilist_lock = threading.Lock()
_ANILIST_ENDPOINT = "https://graphql.anilist.co"
_ANILIST_SEARCH_QUERY = """
query ($search: String) {
  Page(perPage: 25) {
    media(search: $search, type: ANIME) {
      idMal
      format
      seasonYear
      title { romaji english native }
      synonyms
      startDate { year }
    }
  }
}
"""


def _normalize_title_for_lookup(title: str) -> str:
    """Lowercase, strip trailing (YYYY), collapse punctuation for lookup keys."""
    t = (title or "").strip().lower()
    t = re.sub(r"\s*\(\s*\d{4}\s*\)\s*$", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


_JIKAN_SEARCH_LIMIT = 25

# When the user query doesn't mention sequels/movies, down-rank obvious spin-offs.
_SPINOFF_IN_TITLE = re.compile(
    r"\b(?:season\s*\d+|s\d\b|part\s*(?:i+v?|\d+)|"
    r"ii\b|iii\b|iv\b|2nd|3rd|movie|film|ova|special)\b",
    re.I,
)


def _aired_start_year(anime: dict) -> int:
    aired = anime.get("aired")
    if isinstance(aired, dict):
        from_s = aired.get("from")
        if isinstance(from_s, str) and len(from_s) >= 4 and from_s[:4].isdigit():
            return int(from_s[:4])
    return 2999


def _type_rank_for_query(anime: dict, query_norm: str) -> int:
    """Lower is better. Prefer TV unless user asked for a movie."""
    t = (anime.get("type") or "").upper()
    wants_movie = bool(re.search(r"\b(movie|film|gekijou)\b", query_norm))
    if wants_movie:
        order = {"MOVIE": 0, "SPECIAL": 1, "OVA": 2, "ONA": 3, "TV": 4}
    else:
        order = {"TV": 0, "ONA": 1, "OVA": 2, "SPECIAL": 3, "MOVIE": 4}
    return order.get(t, 5)


def _anime_normalized_titles(anime: dict) -> list:
    out = []
    for key in ("title_english", "title"):
        v = anime.get(key)
        if isinstance(v, str) and v.strip():
            out.append(_normalize_title_for_lookup(v))
    raw_titles = anime.get("titles")
    if isinstance(raw_titles, list):
        for entry in raw_titles:
            if isinstance(entry, dict):
                tt = entry.get("title")
                if isinstance(tt, str) and tt.strip():
                    out.append(_normalize_title_for_lookup(tt))
    seen = set()
    uniq = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _score_query_vs_normalized_title_strings(query_norm: str, cand_norms: list) -> float:
    """Fuzzy title score for ranked catalog pick (Jikan + AniList)."""
    if not query_norm:
        return 0.0
    cand_norms = [c for c in cand_norms if c]
    if not cand_norms:
        return 0.0
    blob = " ".join(cand_norms)
    best = 0.0
    for c in cand_norms:
        if c == query_norm:
            return 1_000_000.0
        if c.startswith(query_norm + " ") or query_norm.startswith(c + " "):
            best = max(best, 500_000.0)
        elif query_norm in c or c in query_norm:
            lo, hi = sorted([len(query_norm), len(c)])
            best = max(best, 100_000.0 + 5000.0 * lo / max(hi, 1))
        ratio = difflib.SequenceMatcher(None, query_norm, c).ratio()
        best = max(best, ratio * 15_000.0)
    if not re.search(r"\b(season|part|movie|ova\b|special|film)\b", query_norm):
        if _SPINOFF_IN_TITLE.search(blob):
            best *= 0.42
    return best


def _score_anime_match(query_norm: str, anime: dict) -> float:
    """Higher = better fit for the Groq/Jikan search query."""
    titles = _anime_normalized_titles(anime)
    return _score_query_vs_normalized_title_strings(query_norm, titles)


def _mal_id_sort_key(anime: dict) -> int:
    mid = anime.get("mal_id")
    if isinstance(mid, int):
        return mid
    if isinstance(mid, str) and mid.strip().isdigit():
        return int(mid.strip())
    return 999_999


def _pick_best_jikan_anime(query: str, results: list):
    if not results:
        return None
    qn = _normalize_title_for_lookup(query)
    if not qn:
        return results[0]
    scored = []
    for anime in results:
        if isinstance(anime, dict):
            scored.append((_score_anime_match(qn, anime), anime))
    if not scored:
        return None
    scored.sort(
        key=lambda x: (
            -x[0],
            _type_rank_for_query(x[1], qn),
            _aired_start_year(x[1]),
            _mal_id_sort_key(x[1]),
        )
    )
    return scored[0][1]


def _jikan_search_animes(query: str, limit: int = _JIKAN_SEARCH_LIMIT) -> list:
    """Jikan anime search; returns 0..limit dicts."""
    q = (query or "").strip()
    if not q:
        return []
    lim = max(1, min(int(limit), 25))
    url = "https://api.jikan.moe/v4/anime"
    params = {"q": q, "limit": lim}
    with _jikan_lock:
        time.sleep(0.34)
        try:
            r = requests.get(
                url, params=params, headers=_jikan_headers, timeout=15
            )
        except requests.RequestException:
            return []
        if r.status_code == 429:
            time.sleep(2.5)
            try:
                r = requests.get(
                    url, params=params, headers=_jikan_headers, timeout=15
                )
            except requests.RequestException:
                return []
        if not r.ok:
            return []
        try:
            payload = r.json()
        except ValueError:
            return []
    results = payload.get("data") if isinstance(payload, dict) else None
    return results if isinstance(results, list) else []


def _jikan_search_first_anime(query: str):
    """Single-result search (legacy); uses same ranking as multi-result."""
    found = _jikan_search_animes(query, limit=_JIKAN_SEARCH_LIMIT)
    return _pick_best_jikan_anime(query, found)


def fetch_anime(query):
    anime = _jikan_search_first_anime(query)
    if not anime:
        return None
    return {
        "title": anime["title_english"] or anime["title"],
        "score": anime.get("score", "N/A"),
        "episodes": anime.get("episodes", "?"),
        "synopsis": anime.get("synopsis", "")[:200],
    }


def _normalize_mal_https_url(u: str):
    if not isinstance(u, str):
        return None
    u = u.strip()
    if u.startswith("http://myanimelist.net/"):
        u = "https://myanimelist.net/" + u.split("myanimelist.net/", 1)[-1]
    if not u.startswith("https://myanimelist.net/"):
        return None
    return u


def _mal_url_is_id_only(url: str) -> bool:
    u = (url or "").strip().rstrip("/")
    return bool(re.fullmatch(r"https://myanimelist\.net/anime/\d+", u))


def _jikan_get_anime_full_by_id(mal_id: int):
    """Full `/anime/{id}` payload — includes MAL's canonical `url` with slug."""
    try:
        mid = int(mal_id)
    except (TypeError, ValueError):
        return None
    endpoint = f"https://api.jikan.moe/v4/anime/{mid}"
    with _jikan_lock:
        time.sleep(0.34)
        try:
            r = requests.get(endpoint, headers=_jikan_headers, timeout=15)
        except requests.RequestException:
            return None
        if r.status_code == 429:
            time.sleep(2.5)
            try:
                r = requests.get(endpoint, headers=_jikan_headers, timeout=15)
            except requests.RequestException:
                return None
        if not r.ok:
            return None
        try:
            payload = r.json()
        except ValueError:
            return None
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def _jikan_mal_url_from_full_detail(data: dict):
    u = data.get("url")
    if isinstance(u, str):
        return _normalize_mal_https_url(u.strip())
    return None


def _mal_slug_fallback_url(mid_int: int, anime: dict):
    """
    Last resort: /anime/{id}/{slug} using Jikan title fields (romaji / English / Japanese).
    MAL ignores a wrong slug if the numeric id is correct; encoding handles Japanese.
    """
    for key in ("title", "title_english", "title_japanese"):
        raw = anime.get(key)
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s:
            continue
        slug = re.sub(r"\s+", "_", s)
        slug = re.sub(r"[\\/]", "_", slug)
        seg = quote(slug, safe="_")
        return f"https://myanimelist.net/anime/{mid_int}/{seg}"
    return None


def _enrich_mal_url_with_jikan_canonical(url: str) -> str:
    """Turn bare /anime/{id} into MAL's canonical URL using Jikan full detail."""
    if not url or not _mal_url_is_id_only(url):
        return url
    m = re.match(r"https://myanimelist\.net/anime/(\d+)", url.strip())
    if not m:
        return url
    mid_int = int(m.group(1))
    full = _jikan_get_anime_full_by_id(mid_int)
    if not full:
        return url
    ju = _jikan_mal_url_from_full_detail(full)
    return ju if ju else url


def _mal_url_from_jikan_anime(anime: dict):
    """Prefer search `url`; else full-by-id (canonical slug); else slug from Jikan titles."""
    if not isinstance(anime, dict):
        return None
    u = anime.get("url")
    if isinstance(u, str):
        nu = _normalize_mal_https_url(u.strip())
        if nu:
            return nu

    mid = anime.get("mal_id")
    if mid is None:
        return None
    try:
        mid_int = int(mid)
    except (TypeError, ValueError):
        return None

    full = _jikan_get_anime_full_by_id(mid_int)
    if full:
        ju = _jikan_mal_url_from_full_detail(full)
        if ju:
            return ju
        merged = {**anime, **full}
    else:
        merged = anime

    slug_try = _mal_slug_fallback_url(mid_int, merged)
    if slug_try:
        return slug_try
    return f"https://myanimelist.net/anime/{mid_int}"


def _anilist_normalized_titles(media: dict) -> list:
    out = []
    title = media.get("title") or {}
    if isinstance(title, dict):
        for k in ("english", "romaji", "native"):
            v = title.get(k)
            if isinstance(v, str) and v.strip():
                out.append(_normalize_title_for_lookup(v))
    syns = media.get("synonyms")
    if isinstance(syns, list):
        for s in syns:
            if isinstance(s, str) and s.strip():
                out.append(_normalize_title_for_lookup(s))
    seen = set()
    uniq = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _anilist_start_year(media: dict) -> int:
    sy = media.get("seasonYear")
    if isinstance(sy, int):
        return sy
    sd = media.get("startDate") or {}
    if isinstance(sd, dict):
        y = sd.get("year")
        if isinstance(y, int):
            return y
    return 2999


def _format_rank_anilist(media_fmt: str, query_norm: str) -> int:
    fmt = (media_fmt or "").upper().replace("-", "_")
    wants_movie = bool(re.search(r"\b(movie|film|gekijou)\b", query_norm))
    if wants_movie:
        order = {
            "MOVIE": 0,
            "SPECIAL": 1,
            "OVA": 2,
            "ONA": 3,
            "TV": 4,
            "TV_SHORT": 4,
        }
    else:
        order = {
            "TV": 0,
            "TV_SHORT": 0,
            "ONA": 1,
            "OVA": 2,
            "SPECIAL": 3,
            "MOVIE": 4,
        }
    return order.get(fmt, 5)


def _mal_id_from_anilist_media(media: dict):
    mid = media.get("idMal")
    if mid is None:
        return None
    try:
        return int(mid)
    except (TypeError, ValueError):
        return None


def _pick_best_anilist_media(query: str, media_list: list):
    if not media_list:
        return None
    qn = _normalize_title_for_lookup(query)
    if not qn:
        for m in media_list:
            if isinstance(m, dict) and _mal_id_from_anilist_media(m) is not None:
                return m
        return None
    scored = []
    for m in media_list:
        if isinstance(m, dict):
            norms = _anilist_normalized_titles(m)
            scored.append(
                (_score_query_vs_normalized_title_strings(qn, norms), m)
            )
    if not scored:
        return None
    scored.sort(
        key=lambda x: (
            -x[0],
            0 if _mal_id_from_anilist_media(x[1]) is not None else 1,
            _format_rank_anilist(x[1].get("format"), qn),
            _anilist_start_year(x[1]),
            _mal_id_from_anilist_media(x[1]) or 99999999,
        )
    )
    best = scored[0][1]
    if _mal_id_from_anilist_media(best) is not None:
        return best
    for _, m in scored:
        if _mal_id_from_anilist_media(m) is not None:
            return m
    return None


def _anilist_search_media(search: str) -> list:
    q = (search or "").strip()
    if not q:
        return []
    body = {"query": _ANILIST_SEARCH_QUERY, "variables": {"search": q}}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    with _anilist_lock:
        time.sleep(0.05)
        try:
            r = requests.post(
                _ANILIST_ENDPOINT,
                json=body,
                headers=headers,
                timeout=15,
            )
        except requests.RequestException:
            return []
    if not r.ok:
        return []
    try:
        payload = r.json()
    except ValueError:
        return []
    if isinstance(payload, dict) and payload.get("errors"):
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    page = data.get("Page") if isinstance(data, dict) else None
    media = page.get("media") if isinstance(page, dict) else None
    return media if isinstance(media, list) else []


def _mal_url_from_anilist(query: str):
    """Resolve via AniList GraphQL (includes idMal); no API key required."""
    picked = _pick_best_anilist_media(query, _anilist_search_media(query))
    if not picked:
        return None
    mid = _mal_id_from_anilist_media(picked)
    if mid is None:
        return None
    return f"https://myanimelist.net/anime/{mid}"


def fetch_mal_link(query: str):
    """
    Resolve to a concrete MyAnimeList anime page.
    Uses AniList for idMal when possible, then upgrades bare /anime/{id} URLs via
    Jikan full detail (`url` field — MAL's real slug). Jikan search uses the same path.
    """
    url = _mal_url_from_anilist(query)
    if not url:
        anime = _jikan_search_first_anime(query)
        if anime:
            url = _mal_url_from_jikan_anime(anime)

    if url:
        url = _enrich_mal_url_with_jikan_canonical(url)

    return url


def ask_groq(messages):
    if client is None:
        raise RuntimeError("GROQ_API_KEY is not set")
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile", messages=messages
    )
    return response.choices[0].message.content


def groq_conversational_ack(user_input: str) -> str:
    """Natural-language reply for the chat UI; titles stay in side panels only."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a warm, knowledgeable anime companion in a chat app, similar in tone to ChatGPT. "
                "The user asked for recommendations. Specific anime titles are listed separately in three "
                "panels beside this chat (closest matches, genre picks, hidden gems). "
                "Do NOT list anime titles, numbered show lists, or JSON. "
                "Write 2–5 short paragraphs in a natural, conversational voice: acknowledge what they want, "
                "explain that curated picks are on the right, and invite follow-up about mood, pacing, "
                "episode count, tone, or tropes to avoid."
            ),
        },
        {"role": "user", "content": user_input.strip()},
    ]
    return ask_groq(messages).strip()


def get_recommendations(user_input):
    messages = [
        {
            "role": "system",
            "content": """You are an expert anime recommender.
            Based on what the user wants, return 15 anime in 3 categories.
            Reply ONLY with a JSON object like this, nothing else:
            {
                "most_similar": ["anime1", "anime2", "anime3", "anime4", "anime5"],
                "by_genre": ["anime1", "anime2", "anime3", "anime4", "anime5"],
                "hidden_gems": ["anime1", "anime2", "anime3", "anime4", "anime5"]
            }
            most_similar: most closely match what they asked for
            by_genre: top rated anime in the same genre
            hidden_gems: lesser known but fans of their taste love these""",
        },
        {"role": "user", "content": user_input},
    ]
    response = ask_groq(messages)
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return None


_JSON_REC_SYS = """You are an expert anime recommender.
Read the conversation carefully—the user may refine taste (e.g. funnier, more blood, like Code Geass).
Return 15 anime titles in 3 categories matching their CURRENT stated preferences.
Reply ONLY with a JSON object, nothing else:
{
    "most_similar": ["title1", "title2", "title3", "title4", "title5"],
    "by_genre": ["title1", "title2", "title3", "title4", "title5"],
    "hidden_gems": ["title1", "title2", "title3", "title4", "title5"]
}
most_similar: closest fit to what they want now
by_genre: strong picks in the same genre/mood space
hidden_gems: lesser-known titles that fit the ask
Use concise official English titles where possible."""


def _conversation_transcript_for_rec(messages: list, max_chars: int = 14000) -> str:
    lines = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "system":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        lines.append(f"{role.upper()}: {content}")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def get_recommendations_from_conversation(messages: list):
    """Produce fresh JSON rec lists from multi-turn chat context."""
    transcript = _conversation_transcript_for_rec(messages)
    if not transcript.strip():
        return None
    groq_messages = [
        {"role": "system", "content": _JSON_REC_SYS},
        {
            "role": "user",
            "content": "Here is the conversation so far. Pick anime that fit what they want NOW:\n\n"
            + transcript,
        },
    ]
    raw = ask_groq(groq_messages)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def enrich_rec_payload(recs: dict):
    def enrich(names):
        out = []
        for name in names or []:
            out.append({"name": name, "mal_url": fetch_mal_link(name)})
        return out

    return {
        "most_similar": enrich(recs.get("most_similar")),
        "by_genre": enrich(recs.get("by_genre")),
        "hidden_gems": enrich(recs.get("hidden_gems")),
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    """Serve legacy static UI if present; otherwise a small JSON landing page."""
    index_path = os.path.join(_static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {
        "service": "anime-recommender-api",
        "health": "/health",
        "docs": "/docs",
        "endpoints": {
            "recommend": "POST /recommend",
            "feedback": "POST /feedback",
        },
        "note": "The web UI is deployed separately (e.g. Vercel); use POST /recommend from the frontend.",
    }


@app.post("/recommend")
def recommend(req: RecommendRequest):
    try:
        recs = get_recommendations(req.message)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not recs:
        raise HTTPException(status_code=502, detail="Failed to generate recommendations")

    payload = enrich_rec_payload(recs)
    try:
        payload["assistant_chat"] = groq_conversational_ack(req.message)
    except Exception:
        payload["assistant_chat"] = (
            "I've pulled together some curated picks on the right—closest matches, solid genre choices, "
            "and a few hidden gems. Scroll those lists and tell me what you'd like to tweak next."
        )
    return payload


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    """
    Chat reply plus optional refreshed recommendation columns from full conversation context.
    Returns: response (chat text), history (for next round), recommendations (enriched or null).
    """
    if not isinstance(req.history, list):
        raise HTTPException(status_code=400, detail="history must be a list")

    messages = list(req.history)
    messages.append({"role": "user", "content": req.message})

    try:
        response = ask_groq(messages)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    recommendations_out = None
    try:
        recs_raw = get_recommendations_from_conversation(messages)
        if recs_raw:
            recommendations_out = enrich_rec_payload(recs_raw)
    except Exception:
        recommendations_out = None

    assistant_content = response
    if recommendations_out:
        try:
            catalog = json.dumps(
                {
                    "most_similar": [
                        x["name"] for x in recommendations_out["most_similar"]
                    ],
                    "by_genre": [x["name"] for x in recommendations_out["by_genre"]],
                    "hidden_gems": [
                        x["name"] for x in recommendations_out["hidden_gems"]
                    ],
                },
                ensure_ascii=False,
            )
            assistant_content = (
                response
                + "\n\n[Updated picks for context — same lists as UI panels]\n"
                + catalog
            )
        except Exception:
            assistant_content = response

    out = {
        "response": response,
        "history": messages + [{"role": "assistant", "content": assistant_content}],
        "recommendations": recommendations_out,
    }
    return out


def print_category(title, anime_names):
    print(f"\n{'─' * 40}")
    print(f"  {title}")
    print(f"{'─' * 40}")

    for name in anime_names:
        info = fetch_anime(name)
        if info:
            print(f"\n  {info['title']}")
            print(f"  ⭐ {info['score']}  |  {info['episodes']} eps")
            print(f"  {info['synopsis']}...")
        else:
            print(f"\n  {name}")
            print(f"  (no data found)")


def main():
    print("Anime Recommender")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    user_input = input("\nWhat kind of anime are you looking for?\n> ")

    print("\nFinding recommendations...")

    recommendations = get_recommendations(user_input)

    if not recommendations:
        print("Something went wrong, please try again.")
        return

    print_category("Most Similar", recommendations["most_similar"])
    print_category("By Genre", recommendations["by_genre"])
    print_category("Hidden Gems", recommendations["hidden_gems"])

    print(f"\n{'─' * 40}")

    messages = [
        {
            "role": "system",
            "content": "You are an expert anime recommender. The user has received recommendations and may want adjustments. Help them find the perfect anime based on their feedback.",
        },
        {"role": "user", "content": user_input},
        {"role": "assistant", "content": str(recommendations)},
    ]

    while True:
        user_reply = input("\nWant something different or have feedback?\n> ")

        if user_reply.lower() in ["quit", "exit", "done", "bye", "no", "nope"]:
            print("\nEnjoy your anime.")
            break

        messages.append({"role": "user", "content": user_reply})
        response = ask_groq(messages)
        print(f"\n{response}")
        messages.append({"role": "assistant", "content": response})


if __name__ == "__main__":
    main()
