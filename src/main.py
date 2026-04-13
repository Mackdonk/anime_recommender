import requests
from groq import Groq
from dotenv import load_dotenv
import os
import json

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def fetch_anime(query):
    url = "https://api.jikan.moe/v4/anime"
    params = {"q": query, "limit": 10}
    response = requests.get(url, params=params, timeout=10)
    data = response.json()
    return data.get("data", [])


def get_mal_id(anime_name):
    results = fetch_anime(anime_name)
    if results:
        return results[0]["mal_id"]
    return None


def fetch_recommendations(mal_id):
    url = f"https://api.jikan.moe/v4/anime/{mal_id}/recommendations"
    response = requests.get(url, timeout=10)
    data = response.json()
    return data.get("data", [])[:15]


def fetch_by_genre(genre_ids, limit=15, max_episodes=None):
    url = "https://api.jikan.moe/v4/anime"
    params = {
        "genres": ",".join(str(g) for g in genre_ids),
        "order_by": "score",
        "sort": "desc",
        "limit": limit,
        "sfw": True,
    }
    if max_episodes:
        params["max_episodes"] = max_episodes
    response = requests.get(url, params=params, timeout=10)
    data = response.json()
    if "error" in data:
        print(f"Jikan error: {data['error']}")
        return []
    return data.get("data", [])


def ask_groq(messages):
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile", messages=messages
    )
    return response.choices[0].message.content


def parse_user_input(user_input):
    messages = [
        {
            "role": "system",
            "content": """You analyze what a user wants in anime.
            Extract any anime names they mention, their vibe/mood description,
            and any episode count limit they mention.
            Reply ONLY with a JSON object like this, nothing else:
            {"anime_names": ["name1", "name2"], "vibe": "description", "max_episodes": null}
            If no episode limit mentioned use null.
            If they say 12 episodes or less use 12, if they say short use 13.""",
        },
        {"role": "user", "content": user_input},
    ]
    response = ask_groq(messages)
    return json.loads(response)


def get_genre_ids(vibe):
    genre_map = {
        "action": 1,
        "adventure": 2,
        "comedy": 4,
        "drama": 8,
        "fantasy": 10,
        "horror": 14,
        "mystery": 7,
        "romance": 22,
        "sci-fi": 24,
        "slice of life": 36,
        "sports": 30,
        "supernatural": 37,
        "suspense": 41,
        "psychological": 40,
        "mecha": 18,
        "space": 29,
        "historical": 13,
        "military": 38,
        "thriller": 41,
        "isekai": 62,
        "school": 23,
        "super power": 31,
        "survival": 76,
        "time travel": 78,
        "gore": 58,
        "detective": 39,
        "high stakes game": 59,
        "samurai": 21,
        "martial arts": 17,
        "vampire": 32,
    }

    messages = [
        {
            "role": "system",
            "content": f"""You extract anime genres from a description.
            Only use genres from this list: {list(genre_map.keys())}
            Reply ONLY with a JSON array like: ["psychological", "thriller"]
            Maximum 3 genres.""",
        },
        {"role": "user", "content": vibe},
    ]
    response = ask_groq(messages)
    try:
        genres = json.loads(response)
    except json.JSONDecodeError:
        return []
    return [genre_map[g] for g in genres if g in genre_map]


def main():
    print("🎌 Welcome to Anime Recommender!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    user_input = input("\nWhat kind of anime are you looking for?\n> ")

    # Step 1: parse what the user wants
    parsed = parse_user_input(user_input)
    print(f"\nAnime names found: {parsed['anime_names']}")
    print(f"Vibe: {parsed['vibe']}")
    print(f"Max episodes: {parsed.get('max_episodes')}")

    candidates = []
    max_episodes = parsed.get("max_episodes")

    # Step 2a: fetch fan recommendations for each anime name
    if parsed["anime_names"]:
        for name in parsed["anime_names"]:
            print(f"\nFetching fan recommendations for: {name}")
            mal_id = get_mal_id(name)
            if mal_id:
                recs = fetch_recommendations(mal_id)
                candidates.extend(recs)

    # Step 2b: fetch top rated by genre
    vibe = parsed["vibe"] or " ".join(parsed["anime_names"])
    genre_ids = get_genre_ids(vibe)
    print(f"Genre IDs: {genre_ids}")

    if genre_ids:
        print(f"\nFetching top rated anime by genre...")
        top_rated = fetch_by_genre(genre_ids, max_episodes=max_episodes)
        for anime in top_rated:
            candidates.append(
                {
                    "entry": {"title": anime["title"], "mal_id": anime["mal_id"]},
                    "score": anime.get("score"),
                    "synopsis": anime.get("synopsis", ""),
                    "episodes": anime.get("episodes"),
                }
            )

    # Step 3: deduplicate
    seen = set()
    unique_candidates = []
    for anime in candidates:
        title = anime["entry"]["title"]
        if title not in seen:
            seen.add(title)
            unique_candidates.append(anime)

    # Step 4: filter by episode count if specified
    if max_episodes:
        unique_candidates = [
            a
            for a in unique_candidates
            if a.get("episodes") and a["episodes"] <= max_episodes
        ]
        print(f"After episode filter: {len(unique_candidates)} candidates")

    print(f"\nTotal unique candidates: {len(unique_candidates)}")

    # Step 5: build candidate text for Groq
    candidate_text = ""
    for anime in unique_candidates:
        episodes = anime.get("episodes", "?")
        score = anime.get("score", "?")
        candidate_text += (
            f"- {anime['entry']['title']} | Episodes: {episodes} | Score: {score}\n"
        )

    # Step 6: send to Groq for final recommendations
    episode_instruction = ""
    if max_episodes:
        episode_instruction = f"IMPORTANT: Only recommend anime with {max_episodes} episodes or less. No exceptions."

    messages = [
        {
            "role": "system",
            "content": f"""You are an expert anime recommender.
            The user wants: {user_input}
            {episode_instruction}

            Here are candidate anime from MyAnimeList:
            {candidate_text}

            Pick the 5 best matches. Mix well known hits with hidden gems.
            For each one give:
            - Title and episode count
            - Whether it's a well known hit or hidden gem
            - 2-3 sentences explaining exactly why it matches what the user wants.
            Format it nicely.""",
        },
        {"role": "user", "content": "Give me my 5 recommendations."},
    ]

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🎌 Your recommendations:\n")
    response = ask_groq(messages)
    print(response)

    # Step 7: conversation loop for feedback
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    while True:
        user_reply = input("\nWant different recommendations or have feedback?\n> ")
        if user_reply.lower() in ["quit", "exit", "done", "bye", "no", "nope"]:
            print("\nEnjoy your anime! 👋")
            break

        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": user_reply})
        response = ask_groq(messages)
        print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(response)


main()
