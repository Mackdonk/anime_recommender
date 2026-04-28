import requests
from groq import Groq
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import json

load_dotenv()

_groq_api_key = os.getenv("GROQ_API_KEY")
client = Groq(api_key=_groq_api_key) if _groq_api_key else None


# This creates the FastAPI app — like Flask's app = Flask(__name__)
app = FastAPI()

_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# This allows the Next.js frontend to talk to this backend
# Without this the browser would block the requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
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


def fetch_anime(query):
    url = "https://api.jikan.moe/v4/anime"
    params = {"q": query, "limit": 1}
    response = requests.get(url, params=params, timeout=10)
    data = response.json()
    results = data.get("data", [])
    if results:
        anime = results[0]
        return {
            "title": anime["title_english"] or anime["title"],
            "score": anime.get("score", "N/A"),
            "episodes": anime.get("episodes", "?"),
            "synopsis": anime.get("synopsis", "")[:200],
        }
    return None


def fetch_mal_link(query: str):
    """
    Best-effort lookup of the first matching anime on Jikan.
    Returns a direct MyAnimeList URL like:
    https://myanimelist.net/anime/1535/Death_Note
    """
    url = "https://api.jikan.moe/v4/anime"
    params = {"q": query, "limit": 1}
    response = requests.get(url, params=params, timeout=10)
    data = response.json()
    results = data.get("data", [])
    if not results:
        return None
    anime = results[0]
    return anime.get("url")


def ask_groq(messages):
    if client is None:
        raise RuntimeError("GROQ_API_KEY is not set")
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile", messages=messages
    )
    return response.choices[0].message.content


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


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    index_path = os.path.join(_static_dir, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail="UI not found (missing backend/static/index.html)")
    return FileResponse(index_path)


@app.post("/recommend")
def recommend(req: RecommendRequest):
    try:
        recs = get_recommendations(req.message)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not recs:
        raise HTTPException(status_code=502, detail="Failed to generate recommendations")

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


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    """
    Continue the conversation after initial recommendations.
    The frontend should send a `history` list of Groq-style messages:
    [{"role":"system"|"user"|"assistant","content":"..."}]
    """
    if not isinstance(req.history, list):
        raise HTTPException(status_code=400, detail="history must be a list")

    messages = list(req.history)
    messages.append({"role": "user", "content": req.message})

    try:
        response = ask_groq(messages)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"response": response, "history": messages + [{"role": "assistant", "content": response}]}


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
