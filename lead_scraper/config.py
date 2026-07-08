import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def from_root(*parts):
    return ROOT_DIR.joinpath(*parts)


def read_json(relative_path):
    with from_root(relative_path).open("r", encoding="utf-8") as file:
        return json.load(file)


def load_config():
    sources = read_json("config/sources.json")
    categories = read_json("config/categories.json")
    countries = read_json("config/countries.json")
    scoring = read_json("config/scoring.json")

    return {
        "sources": sources,
        "categories": [category for category in categories if category.get("enabled")],
        "countries": [country for country in countries if country.get("enabled")],
        "scoring": scoring,
    }

