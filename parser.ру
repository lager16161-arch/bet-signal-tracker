"""Parse betting signal text without connecting to any external service."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple


_NUMBER = r"\d[\d \u00a0]*(?:[,.]\d+)?"


def _parse_decimal(value: str) -> Optional[Decimal]:
    normalized = value.replace(" ", "").replace("\u00a0", "")
    if "," in normalized and "." not in normalized:
        normalized = normalized.replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _extract_money(text: str, label: str) -> Tuple[Optional[Decimal], Optional[str]]:
    pattern = (
        re.escape(label)
        + r"[ \t]*[:：]?[ \t]*(?P<amount>"
        + _NUMBER
        + r")[ \t]*(?P<currency>[A-Z]{3,5})"
    )
    match = re.search(pattern, text)
    if not match:
        return None, None
    return _parse_decimal(match.group("amount")), match.group("currency")


def _coefficient_color(odds: Optional[Decimal]) -> Optional[str]:
    if odds is None or odds < Decimal("1.01"):
        return None
    if odds <= Decimal("1.50"):
        return "green"
    if odds <= Decimal("2.00"):
        return "yellow"
    return "red"


def _extract_match_event(lines: list) -> Optional[str]:
    payout_index = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("💰")),
        None,
    )
    if payout_index is None:
        return None

    competition_index = next(
        (
            i
            for i, line in enumerate(lines)
            if i > payout_index and line.strip().startswith("🏆")
        ),
        None,
    )
    if competition_index is None:
        return None

    for line in lines[payout_index + 1 : competition_index]:
        candidate = line.strip()
        if candidate:
            return candidate
    return None


def _strip_leading_icons(value: str) -> str:
    return re.sub(r"^[^\w]+", "", value, flags=re.UNICODE).strip()


def _parse_express_leg(number: int, lines: list) -> Dict[str, Any]:
    raw_lines = list(lines)
    if raw_lines and re.fullmatch(
        r"\s*(?:[🟢🟡🔴]\s*)?(?:✅|❌|➖)\s*", raw_lines[-1]
    ):
        raw_lines.pop()

    leg: Dict[str, Any] = {
        "leg_number": number,
        "sport": None,
        "match_event": None,
        "competition": None,
        "start_time_text": None,
        "market": None,
        "selection": None,
        "odds": None,
        "raw_text": "\n".join(raw_lines).strip(),
    }
    if not raw_lines:
        return leg

    header = re.sub(r"^\s*\d+\.\s*", "", raw_lines[0]).strip()
    if "·" in header or "•" in header:
        delimiter = "·" if "·" in header else "•"
        sport_text, event = header.split(delimiter, 1)
        sport = _strip_leading_icons(sport_text)
        leg["sport"] = sport or None
        leg["match_event"] = event.strip() or None
    else:
        leg["match_event"] = _strip_leading_icons(header) or None

    for line in raw_lines[1:]:
        competition_match = re.match(r"\s*🏆\s*(.+)", line)
        if competition_match:
            leg["competition"] = competition_match.group(1).strip()
            continue

        start_match = re.match(r"\s*📅\s*Начало\s*[:：]\s*(.+)", line)
        if start_match:
            leg["start_time_text"] = start_match.group(1).strip()
            continue

        selection_match = re.match(
            r"\s*📝\s*(?P<market>.+?)\s*[—–-]\s*"
            r"(?P<selection>.+?)\s*\((?P<odds>\d+(?:[,.]\d+)?)\)\s*$",
            line,
        )
        if selection_match:
            leg["market"] = selection_match.group("market").strip()
            leg["selection"] = selection_match.group("selection").strip()
            leg["odds"] = _parse_decimal(selection_match.group("odds"))

    return leg


def _extract_express_legs(lines: list) -> list:
    starts = []
    for index, line in enumerate(lines):
        match = re.match(r"\s*(\d+)\.\s+", line)
        if match:
            starts.append((index, int(match.group(1))))

    legs = []
    for position, (start, number) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        legs.append(_parse_express_leg(number, lines[start:end]))
    return legs


def parse_signal(text: str) -> Dict[str, Any]:
    """Return parsed signal fields; unavailable values are ``None``.

    Result is always one of ``win``, ``loss``, ``void``, or ``unknown``.
    """
    lines = text.splitlines()
    parsed: Dict[str, Any] = {
        "sport": None,
        "bet_type": "unknown",
        "signal_size_type": "unknown",
        "express_leg_count": None,
        "express_legs": [],
        "stake": None,
        "currency": None,
        "approx_usd": None,
        "approx_usd_currency": None,
        "odds": None,
        "possible_payout": None,
        "payout_currency": None,
        "match_event": _extract_match_event(lines),
        "competition": None,
        "start_time": None,
        "market": None,
        "selection": None,
        "mode": None,
        "coefficient_color": None,
        "result": "unknown",
    }

    header = next((line.strip() for line in lines if line.strip()), "")
    large_express = re.match(
        r"🔥\s*Крупный\s+экспресс\s*🔥\s*[·•-]\s*"
        r"(?P<count>\d+)\s+матч\w*",
        header,
        re.IGNORECASE,
    )
    regular_express = re.match(
        r"(?:🎯\s*)?Экспресс\s*[·•-]\s*(?P<count>\d+)\s+матч\w*",
        header,
        re.IGNORECASE,
    )
    if large_express:
        parsed["bet_type"] = "parlay"
        parsed["signal_size_type"] = "large"
        parsed["express_leg_count"] = int(large_express.group("count"))
    elif regular_express:
        parsed["bet_type"] = "parlay"
        parsed["signal_size_type"] = "regular"
        parsed["express_leg_count"] = int(regular_express.group("count"))
    elif re.search(r"Крупная\s+ставка", header, re.IGNORECASE):
        parsed["bet_type"] = "single"
        parsed["signal_size_type"] = "large"
    elif re.match(
        r"(?:🧾\s*)?Ставка\s*[·•-]\s*\S",
        header,
        re.IGNORECASE,
    ):
        parsed["bet_type"] = "single"
        parsed["signal_size_type"] = "regular"

    sport_match = re.search(
        r"(?:🧾\s*Ставка|Крупная\s+ставка).*?[·•-]\s*(.+)$",
        header,
        re.IGNORECASE,
    )
    if sport_match:
        parsed["sport"] = sport_match.group(1).strip()

    parsed["stake"], parsed["currency"] = _extract_money(text, "💵 Сумма")
    parsed["approx_usd"], parsed["approx_usd_currency"] = _extract_money(
        text, "💵 Примерно в USD"
    )
    parsed["possible_payout"], parsed["payout_currency"] = _extract_money(
        text, "💰 Возможная выплата"
    )

    odds_match = re.search(
        r"📊\s*Общий\s+коэффициент\s*[:：]?\s*(\d+(?:[,.]\d+)?)",
        text,
    )
    if not odds_match:
        odds_match = re.search(
            r"📊\s*Коэффициент\s*[:：]?\s*(\d+(?:[,.]\d+)?)", text
        )
    if odds_match:
        parsed["odds"] = _parse_decimal(odds_match.group(1))
    parsed["coefficient_color"] = _coefficient_color(parsed["odds"])

    if parsed["bet_type"] == "parlay":
        parsed["match_event"] = None
        parsed["express_legs"] = _extract_express_legs(lines)
    else:
        competition_match = re.search(r"🏆\s*(.+)", text)
        if competition_match:
            parsed["competition"] = competition_match.group(1).strip()

        start_match = re.search(r"📅\s*Начало\s*[:：]\s*(.+)", text)
        if start_match:
            parsed["start_time"] = start_match.group(1).strip()

        selection_match = re.search(
            r"📝\s*(?P<market>.+?)\s*[—–-]\s*(?P<selection>.+?)\s*$",
            text,
            re.MULTILINE,
        )
        if selection_match:
            parsed["market"] = selection_match.group("market").strip()
            parsed["selection"] = selection_match.group("selection").strip()

    if re.search(r"(?:🔴\s*)?(?:Лайв|Live)\b", text, re.IGNORECASE):
        parsed["mode"] = "live"
    elif re.search(
        r"(?:🟢\s*)?(?:Прематч|До начала|Pre-match|Prematch)\b",
        text,
        re.IGNORECASE,
    ):
        parsed["mode"] = "prematch"

    result_match = re.search(
        r"\s*(?:[🟢🟡🔴]\s*)?(✅|❌|➖)\s*$",
        text,
    )
    if result_match:
        parsed["result"] = {
            "✅": "win",
            "❌": "loss",
            "➖": "void",
        }[result_match.group(1)]

    return parsed
