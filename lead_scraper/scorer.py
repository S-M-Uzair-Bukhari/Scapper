from lead_scraper.date_parser import hours_old


def includes_any(text, keywords):
    haystack = text.lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def keyword_ratio(text, keywords):
    if not keywords:
        return 0
    haystack = text.lower()
    matched = [keyword for keyword in keywords if keyword.lower() in haystack]
    return len(matched) / len(keywords)


def score_lead(lead, config):
    categories = config["categories"]
    countries = config["countries"]
    scoring = config["scoring"]
    weights = scoring["weights"]
    text = " ".join(str(lead.get(field) or "") for field in ("title", "description", "category", "country"))

    score = 0
    reasons = []

    category = next((item for item in categories if item["name"] == lead.get("category")), None)
    if category:
        score += weights["categoryMatch"]
        reasons.append(f"category:{category['name']}")

    keyword_source = category["keywords"] if category else [keyword for item in categories for keyword in item["keywords"]]
    keyword_score = round(weights["keywordMatch"] * min(1, keyword_ratio(text, keyword_source) * 3))
    if keyword_score > 0:
        score += keyword_score
        reasons.append(f"keywords:{keyword_score}")

    country_matched = False
    for country in countries:
        aliases = [country["name"], *(country.get("aliases") or [])]
        if any(alias.lower() in text.lower() for alias in aliases):
            country_matched = True
            break

    if country_matched or not lead.get("country"):
        score += weights["countryMatch"] if country_matched else round(weights["countryMatch"] * 0.4)
        reasons.append("country:matched" if country_matched else "country:unknown")

    age_hours = hours_old(lead.get("postedAt"))
    if age_hours is None:
        score += round(weights["freshness"] * 0.5)
        reasons.append("freshness:unknown")
    elif age_hours <= scoring["preferredFreshHours"]:
        score += weights["freshness"]
        reasons.append("freshness:preferred")
    elif age_hours <= scoring["freshWindowHours"]:
        score += round(weights["freshness"] * 0.65)
        reasons.append("freshness:window")

    if lead.get("budget") or includes_any(text, scoring["intentKeywords"]):
        score += weights["budgetOrIntent"]
        reasons.append("intent:yes")

    if len(str(lead.get("description") or "")) >= 120 or len(str(lead.get("title") or "")) >= 25:
        score += weights["contentQuality"]
        reasons.append("content:useful")

    if includes_any(text, scoring["negativeKeywords"]):
        score -= 20
        reasons.append("penalty:negative-keyword")

    return {
        "score": max(0, min(100, score)),
        "scoreReasons": reasons,
    }

