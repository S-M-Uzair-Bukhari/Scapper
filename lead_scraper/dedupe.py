import hashlib
import re


def normalize(value):
    text = str(value or "").lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s:/.-]", "", text)
    return text.strip()


def create_dedupe_key(lead):
    posted_at = lead.get("postedAt")
    posted_value = posted_at.date().isoformat() if posted_at else lead.get("postedAtRaw")
    parts = [
        lead.get("source"),
        lead.get("sourceLeadId"),
        lead.get("url"),
        lead.get("title"),
        lead.get("description"),
        lead.get("country"),
        posted_value,
    ]
    stable = "|".join(normalize(part) for part in parts)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def is_duplicate(lead, existing):
    url = normalize(lead.get("url"))
    source_lead_id = normalize(lead.get("sourceLeadId"))
    dedupe_key = normalize(lead.get("dedupeKey"))

    for row in existing:
        if url and normalize(row.get("url")) == url:
            return True
        if source_lead_id and normalize(row.get("sourceLeadId")) == source_lead_id:
            return True
        if dedupe_key and normalize(row.get("dedupeKey")) == dedupe_key:
            return True

    return False

