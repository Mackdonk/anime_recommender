import requests


def fetch_anime(query):
    url = "https://api.jikan.moe/v4/anime"
    params = {"q": query, "limit": 5}
    response = requests.get(url, params=params)
    data = response.json()
    return data["data"]


# results = fetch_anime("fullmetal alchemist brotherhood") # testing the function to see if it works

# for anime in results:         # testing printing the results to see if it works
#     print(anime["title"])
#     print(anime["score"])
#     print(anime["rank"])


# results = fetch_anime("death note")

# print(results[0]["episodes"])


def main():
    print("Welcome to Anime Recommender!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    user_input = input("\nWhat kind of anime are you looking for?\n> ")

    print(f"\nyou said: {user_input}")
    print("(Claude will respond here soon)")


main()
