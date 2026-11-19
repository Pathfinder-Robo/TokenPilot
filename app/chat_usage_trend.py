from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
import math
from typing import Any

from pricing import estimate_usage_usd

_TWO_DAYS_SECONDS = 48 * 3600
_THIRTY_ONE_DAYS_SECONDS = 31 * 86400
_HOUR_SECONDS = 3600
_SIX_HOUR_SECONDS = 21600
_DAY_SECONDS = 86400
ROLLING_TREND_WINDOW_SECONDS = 3 * 3600
ROLLING_TREND_BUCKET_SECONDS = 15 * 60
_EDGE_EPSILON_SECONDS = 1e-6

def _is_valid_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _parse_iso_epoch(value: Any) -> float | None:
    """Parse a timezone-aware ISO-8601 timestamp into UTC epoch seconds."""
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value)
        if date.utcoffset() is not None:
            return date.timestamp()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _format_utc_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _choose_bucket_seconds(elapsed: float) -> int:
    if elapsed <= _TWO_DAYS_SECONDS:
        return _HOUR_SECONDS
    if elapsed <= _THIRTY_ONE_DAYS_SECONDS:
        return _SIX_HOUR_SECONDS
    return _DAY_SECONDS


def _build_bucket_edges(start: float, end: float, bucket_seconds: int) -> list[float]:
    edges = [start]
    if end > start:
        step_index = 1
        while True:
            next_edge = start + step_index * bucket_seconds
            if next_edge < end - _EDGE_EPSILON_SECONDS:
                edges.append(next_edge)
                step_index += 1
            else:
                break
    edges.append(end)
    return edges


def _locate_bucket_index(ts: float, start: float, end: float, bucket_seconds: int, edges: Sequence[float]) -> int:
    num_buckets = len(edges) - 1
    if num_buckets <= 1 or ts == end:
        return num_buckets - 1

    idx = int((ts - start) // bucket_seconds)
    if idx < 0:
        idx = 0
    elif idx >= num_buckets:
        idx = num_buckets - 1

    if idx + 1 < num_buckets and ts >= edges[idx + 1]:
        idx += 1
    elif idx > 0 and ts < edges[idx]:
        idx -= 1
    return idx


def _is_all_zero_event(event: Mapping[str, Any]) -> bool:
    for field in ("total_tokens", "cached_tokens", "input_tokens", "output_tokens"):
        val = event.get(field, 0)
        if isinstance(val, bool) or val != 0:
            return False
    return True


def _select_top_chats(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[tuple[float, str, str | None]] = []
    for row in rows:
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id or row_id == "unattributed":
            continue
        codex = row.get("codex")
        if not isinstance(codex, Mapping):
            continue
        estimated_usd = codex.get("estimated_usd")
        if _is_valid_finite_number(estimated_usd) and estimated_usd > 0:
            title = row.get("title")
            candidates.append((float(estimated_usd), row_id, title if isinstance(title, str) else None))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"id": row_id, "title": title, "codex_usd": cost}
        for cost, row_id, title in candidates[:3]
    ]


def _select_top_chats_from_priced_events(
    in_window_events: Sequence[tuple[int, float | None, str | None, Mapping[str, Any]]],
    titles: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    priced_totals: dict[str, list[float]] = {}
    for _, valid_cost, chat_id, event in in_window_events:
        if valid_cost is None or chat_id is None:
            continue
        acc = priced_totals.get(chat_id)
        if acc is None:
            priced_totals[chat_id] = [
                float(event["input_tokens"]),
                float(event["cached_tokens"]),
                float(event["output_tokens"]),
            ]
        else:
            acc[0] += float(event["input_tokens"])
            acc[1] += float(event["cached_tokens"])
            acc[2] += float(event["output_tokens"])

    candidates: list[tuple[float, str, str | None]] = []
    for chat_id, (inp, cached, out) in priced_totals.items():
        estimated_usd = estimate_usage_usd(
            {"input_tokens": inp, "cached_tokens": cached, "output_tokens": out}
        )
        if _is_valid_finite_number(estimated_usd) and estimated_usd > 0:
            raw_title = titles.get(chat_id) if isinstance(titles, Mapping) else None
            candidates.append(
                (float(estimated_usd), chat_id, raw_title if isinstance(raw_title, str) else None)
            )

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"id": row_id, "title": title, "codex_usd": cost}
        for cost, row_id, title in candidates[:3]
    ]


def _resolve_bucket_usd(priced_sum: float, has_priced: bool, has_unpriced: bool) -> float | None:
    if has_priced:
        return priced_sum
    if has_unpriced:
        return None
    return 0.0


def build_usage_trend(
    start: float,
    end: float,
    providers: Mapping[str, Iterable[Mapping[str, Any]]],
    rows: Sequence[Mapping[str, Any]] | None,
    partial: bool,
    *,
    bucket_seconds: int | None = None,
    titles: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build interval Codex usage in reference USD overall and for the top chats."""
    if not (_is_valid_finite_number(start) and _is_valid_finite_number(end)):
        raise ValueError("start and end must be finite numbers")
    start_f = float(start)
    end_f = float(end)
    if start_f > end_f:
        raise ValueError("start must be <= end")

    if bucket_seconds is None:
        resolved_bucket_seconds = _choose_bucket_seconds(end_f - start_f)
    else:
        if not isinstance(bucket_seconds, int) or isinstance(bucket_seconds, bool) or bucket_seconds <= 0:
            raise ValueError("bucket_seconds must be a positive integer")
        resolved_bucket_seconds = bucket_seconds

    edges = _build_bucket_edges(start_f, end_f, resolved_bucket_seconds)
    num_buckets = len(edges) - 1

    is_partial = bool(partial)
    in_window_events: list[tuple[int, float | None, str | None, Mapping[str, Any]]] = []

    for event in providers.get("codex", ()):
        if _is_all_zero_event(event):
            continue

        ts = _parse_iso_epoch(event.get("timestamp"))
        if ts is None:
            is_partial = True
            continue

        if ts < start_f or ts > end_f:
            continue

        bucket_idx = _locate_bucket_index(ts, start_f, end_f, resolved_bucket_seconds, edges)
        cost = estimate_usage_usd(event)
        valid_cost = (
            float(cost)
            if (cost is not None and _is_valid_finite_number(cost) and cost >= 0)
            else None
        )
        chat_key = event.get("chat_key")
        real_chat_id = (
            chat_key
            if (isinstance(chat_key, str) and chat_key and chat_key != "unattributed")
            else None
        )
        in_window_events.append((bucket_idx, valid_cost, real_chat_id, event))

    top_chats = (
        _select_top_chats(rows)
        if rows is not None
        else _select_top_chats_from_priced_events(in_window_events, titles)
    )
    selected_ids = [chat["id"] for chat in top_chats]
    selected_index = {chat_id: i for i, chat_id in enumerate(selected_ids)}
    num_selected = len(selected_ids)

    total_priced_sum = [0.0] * num_buckets
    total_has_priced = [False] * num_buckets
    total_has_unpriced = [False] * num_buckets

    chat_priced_sum = [[0.0] * num_selected for _ in range(num_buckets)]
    chat_has_priced = [[False] * num_selected for _ in range(num_buckets)]
    chat_has_unpriced = [[False] * num_selected for _ in range(num_buckets)]

    for bucket_idx, valid_cost, real_chat_id, _ in in_window_events:
        chat_idx = selected_index.get(real_chat_id) if real_chat_id is not None else None
        if valid_cost is None:
            is_partial = True
            total_has_unpriced[bucket_idx] = True
            if chat_idx is not None:
                chat_has_unpriced[bucket_idx][chat_idx] = True
        else:
            total_priced_sum[bucket_idx] += valid_cost
            total_has_priced[bucket_idx] = True
            if chat_idx is not None:
                chat_priced_sum[bucket_idx][chat_idx] += valid_cost
                chat_has_priced[bucket_idx][chat_idx] = True

    iso_edges = [_format_utc_iso(edge) for edge in edges]
    buckets: list[dict[str, Any]] = []
    for i in range(num_buckets):
        buckets.append(
            {
                "start_at": iso_edges[i],
                "end_at": iso_edges[i + 1],
                "total_usd": _resolve_bucket_usd(
                    total_priced_sum[i],
                    total_has_priced[i],
                    total_has_unpriced[i],
                ),
                "chat_usd": {
                    selected_ids[c_idx]: _resolve_bucket_usd(
                        chat_priced_sum[i][c_idx],
                        chat_has_priced[i][c_idx],
                        chat_has_unpriced[i][c_idx],
                    )
                    for c_idx in range(num_selected)
                },
            }
        )

    return {
        "bucket_seconds": resolved_bucket_seconds,
        "partial": is_partial,
        "top_chats": top_chats,
        "buckets": buckets,
    }
