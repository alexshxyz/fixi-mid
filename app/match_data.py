def normalize_score(score: str | None) -> str:
    """Вернуть отображаемый счёт или дефис, если счёт неизвестен."""
    if score is None:
        return "-"

    normalized_score = score.strip()
    if not normalized_score or normalized_score.casefold() == "unknown":
        return "-"
    return normalized_score


def normalize_match_time(match_time: str | None) -> str:
    """Вернуть отображаемое время матча или вопросительный знак."""
    if match_time is None:
        return "?"

    normalized_match_time = match_time.strip()
    if not normalized_match_time or normalized_match_time.casefold() == "unknown":
        return "?"
    return normalized_match_time
