import requests
from groq import Groq
from dotenv import load_dotenv
import os

load_dotenv()


def fetch_anime(query):
    url = "https://api.jikan.moe/v4/anime"
    params = {"q": query, "limit": 5}
    response = requests.get(url, params=params)
    data = response.json()
    return data["data"]


def main():
    print("Welcome to Anime Recommender!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    user_input = input("\nWhat kind of anime are you looking for?\n> ")

    print(f"\nyou said: {user_input}")
    print("(Claude will respond here soon)")


main()
