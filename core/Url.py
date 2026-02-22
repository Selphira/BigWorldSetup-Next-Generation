_FORUM_CODES = {
    "beamdog.com": "BD",
    "gibberlings3.net": "G3",
    "shsforums.net": "SHS",
    "baldursgateworld.fr": "BGW",
    "pocketplane.net": "PPG",
    "blackwyrmlair.net": "BWL",
    "weaselmods.net": "WM",
}


def get_forum_code(url: str) -> str:
    """Extract short forum code from a forum URL. Returns empty string if unknown."""
    url_lower = url.lower()
    return next((code for domain, code in _FORUM_CODES.items() if domain in url_lower), "")
