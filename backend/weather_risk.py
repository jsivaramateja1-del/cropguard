"""Weather-driven disease risk.

Two separate jobs, deliberately kept apart:

1. ``fetch_weather`` retrieves current conditions from Open-Meteo. No API key, no
   vendor lock-in, one HTTP call.
2. ``compute_risk`` turns those conditions into a Low / Moderate / High / Critical
   band using a fixed, documented point table. This is *not* an AI model and it is
   not a black box: every level can be recomputed by hand from the numbers returned
   alongside it.

The scoring table (points are additive, maximum 9):

    relative humidity
        >= 90 %             3
        >= 80 %             2
        >= 70 %             1
        below 70 %          0

    temperature
        15-30 C             1     (the band most foliar pathogens favour)
        outside that band   0

    rainfall now
        >= 2.5 mm           3
        >= 0.5 mm           2
        > 0 mm              1
        none                0
        plus 1 if the next 6 hours carry >= 70 % chance of rain

    leaf-wetness proxy
        humidity >= 90 % and 10-25 C   2   (the classic late-blight window)

    Total: 0-2 Low, 3-4 Moderate, 5-6 High, 7-9 Critical.

When an advisory entry is supplied, its ``weather_sensitivity`` block adds up to
three more points for that specific pathogen, because the generic table is too coarse
to distinguish, say, yellow rust (cool and moist) from spider mites (hot and dry).
That adjustment is reported separately so the generic score is never hidden.

Weather failure is never fatal: the diagnosis still returns and the risk is reported
as unavailable, with the reason.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from config import settings

logger = logging.getLogger("cropguard.weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_CUSTOMER_URL = "https://customer-api.open-meteo.com/v1/forecast"
MET_NO_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

RISK_LEVELS = ("Low", "Moderate", "High", "Critical")
RISK_THRESHOLDS: Tuple[Tuple[int, str], ...] = ((7, "Critical"), (5, "High"), (3, "Moderate"), (0, "Low"))
MAX_GENERIC_SCORE = 9
MAX_ADVISORY_SCORE = 3


class WeatherError(RuntimeError):
    """Open-Meteo could not be reached or returned something unusable."""


@dataclass
class WeatherSnapshot:
    temperature_c: float
    humidity_pct: float
    precipitation_mm: float
    precipitation_probability_pct: Optional[float]
    wind_kph: Optional[float]
    observed_at: Optional[str]
    source: str = "open-meteo"
    cached: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "temperature_c": self.temperature_c,
            "humidity_pct": self.humidity_pct,
            "precipitation_mm": self.precipitation_mm,
            "precipitation_probability_pct": self.precipitation_probability_pct,
            "wind_kph": self.wind_kph,
            "observed_at": self.observed_at,
            "source": self.source,
            "cached": self.cached,
        }


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------
# Open-Meteo's free endpoint is rate-limited per IP, and cloud platforms share egress
# IPs, so a shared host can be throttled (HTTP 429) through no fault of its own. Two
# defences: cache by rounded location so repeated demo submissions cost one request,
# and retry the throttled ones with backoff.
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[Tuple[float, float], Tuple[float, WeatherSnapshot]] = {}


def _cache_key(latitude: float, longitude: float) -> Tuple[float, float]:
    """Round to ~1 km so a field and its neighbouring rows share one entry."""
    return (round(latitude, 2), round(longitude, 2))


def clear_weather_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def weather_status() -> Dict[str, object]:
    """Non-secret weather configuration summary for /health."""
    with _CACHE_LOCK:
        entries = len(_CACHE)
    return {
        "cache_entries": entries,
        "cache_ttl_seconds": settings.weather_cache_ttl,
        "api_key_configured": bool(settings.open_meteo_api_key),
        "endpoint": "customer" if settings.open_meteo_api_key else "public",
        "provider_order": _provider_order(),
    }


@dataclass
class RiskFactor:
    name: str
    points: int
    detail: str


@dataclass
class RiskAssessment:
    level: str
    score: int
    max_score: int
    factors: List[RiskFactor] = field(default_factory=list)
    advisory_adjustment: int = 0
    advisory_label: Optional[str] = None
    available: bool = True
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "level": self.level,
            "score": self.score,
            "max_score": self.max_score,
            "advisory_adjustment": self.advisory_adjustment,
            "available": self.available,
            "reason": self.reason,
            "factors": [
                {"name": f.name, "points": f.points, "detail": f.detail} for f in self.factors
            ],
        }


def _level_for(score: int) -> str:
    for threshold, level in RISK_THRESHOLDS:
        if score >= threshold:
            return level
    return "Low"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def _request_json(
    url: str,
    params: dict,
    headers: dict,
    timeout: float,
    client: Optional[httpx.Client],
    retries: int,
) -> dict:
    """GET with retry on 429/5xx. Raises WeatherError with an actionable message."""
    own_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                response = http.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                last_error = error
                if status_code != 429 and status_code < 500:
                    raise WeatherError(f"HTTP {status_code}") from error
                if attempt < retries:
                    delay = 0.5 * (2 ** attempt)
                    logger.info(
                        "weather request throttled (HTTP %s), retrying in %.1fs",
                        status_code, delay,
                    )
                    time.sleep(delay)
                    continue
                if status_code == 429:
                    raise WeatherError("HTTP 429 (rate limited)") from error
                raise WeatherError(f"HTTP {status_code} after {retries + 1} attempts") from error
            except httpx.TimeoutException as error:
                last_error = error
                if attempt < retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise WeatherError("no response in time") from error
            except httpx.HTTPError as error:
                raise WeatherError(f"could not be reached: {error}") from error
            except ValueError as error:
                raise WeatherError("response was not JSON") from error
        raise WeatherError(f"could not be reached: {last_error}")
    finally:
        if own_client:
            http.close()


def _parse_open_meteo(payload: dict) -> WeatherSnapshot:
    current = payload.get("current") or {}
    try:
        temperature = float(current["temperature_2m"])
        humidity = float(current["relative_humidity_2m"])
    except (KeyError, TypeError, ValueError) as error:
        raise WeatherError("Open-Meteo response is missing temperature or humidity") from error

    precipitation = float(current.get("precipitation") or 0.0)
    wind = current.get("wind_speed_10m")
    observed = current.get("time")

    probability = None
    hourly = payload.get("hourly") or {}
    probabilities = hourly.get("precipitation_probability") or []
    if isinstance(probabilities, list) and probabilities:
        window = [p for p in probabilities[:6] if isinstance(p, (int, float))]
        if window:
            probability = float(max(window))

    return WeatherSnapshot(
        temperature_c=round(temperature, 1),
        humidity_pct=round(humidity, 1),
        precipitation_mm=round(precipitation, 2),
        precipitation_probability_pct=probability,
        wind_kph=float(wind) if isinstance(wind, (int, float)) else None,
        observed_at=observed if isinstance(observed, str) else datetime.now(timezone.utc).isoformat(),
        source="open-meteo",
    )


def _parse_met_no(payload: dict) -> WeatherSnapshot:
    """Parse MET Norway's locationforecast 2.0 'compact' product.

    Schema: properties.timeseries[] with data.instant.details.air_temperature,
    relative_humidity and wind_speed, plus an optional next_1_hours block carrying
    precipitation_amount and probability_of_precipitation. Parsed defensively because
    MET's terms and schema are stable but the optional blocks are not guaranteed on
    every entry.
    """
    series = (payload.get("properties") or {}).get("timeseries") or []
    if not isinstance(series, list) or not series:
        raise WeatherError("MET Norway response has no timeseries")

    entry = series[0]
    details = ((entry.get("data") or {}).get("instant") or {}).get("details") or {}
    try:
        temperature = float(details["air_temperature"])
        humidity = float(details["relative_humidity"])
    except (KeyError, TypeError, ValueError) as error:
        raise WeatherError("MET Norway response is missing temperature or humidity") from error

    precipitation = 0.0
    probability = None
    for upcoming in series[:6]:
        block = ((upcoming.get("data") or {}).get("next_1_hours") or {}).get("details") or {}
        amount = block.get("precipitation_amount")
        if isinstance(amount, (int, float)):
            precipitation += float(amount)
        chance = block.get("probability_of_precipitation")
        if isinstance(chance, (int, float)):
            probability = max(probability or 0.0, float(chance))

    wind = details.get("wind_speed")
    return WeatherSnapshot(
        temperature_c=round(temperature, 1),
        humidity_pct=round(humidity, 1),
        precipitation_mm=round(precipitation, 2),
        precipitation_probability_pct=probability,
        wind_kph=round(float(wind) * 3.6, 1) if isinstance(wind, (int, float)) else None,
        observed_at=entry.get("time") if isinstance(entry.get("time"), str) else None,
        source="met.no",
    )


def _provider_order() -> List[str]:
    """Which providers to try, in order, for this configuration."""
    configured = (settings.weather_provider or "auto").strip().lower()
    if configured in {"open-meteo", "met-no"}:
        return [configured]
    return ["open-meteo", "met-no"]


def fetch_weather(
    latitude: float,
    longitude: float,
    timeout: Optional[float] = None,
    client: Optional[httpx.Client] = None,
    use_cache: bool = True,
    retries: int = 2,
) -> WeatherSnapshot:
    """Current conditions at a point, from the first provider that answers.

    Providers, in order:

    1. **Open-Meteo** — keyless. Its free tier has no API key option and applies limits
       per IP; Open-Meteo's own terms name Render among hosts likely to be limited
       early, which is what produces HTTP 429 on this deployment. ``OPEN_METEO_API_KEY``
       is supported for a paid plan only.
    2. **MET Norway** — keyless and free, but requires an identifying User-Agent
       (``WEATHER_CONTACT``). Different rate limits, so it usually works when the
       first does not.

    A successful response is cached per ~1 km square for ``WEATHER_CACHE_TTL`` seconds,
    so repeated submissions from one field cost a single request. If every provider
    fails, the error names each one, because "weather unavailable" alone is not
    actionable.
    """
    timeout = timeout or settings.weather_timeout
    key = _cache_key(latitude, longitude)

    if use_cache:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit is not None and (time.time() - hit[0]) < settings.weather_cache_ttl:
            cached = hit[1]
            return WeatherSnapshot(
                temperature_c=cached.temperature_c,
                humidity_pct=cached.humidity_pct,
                precipitation_mm=cached.precipitation_mm,
                precipitation_probability_pct=cached.precipitation_probability_pct,
                wind_kph=cached.wind_kph,
                observed_at=cached.observed_at,
                source=cached.source,
                cached=True,
            )

    failures: List[str] = []

    for provider in _provider_order():
        try:
            if provider == "open-meteo":
                params = {
                    "latitude": f"{latitude:.4f}",
                    "longitude": f"{longitude:.4f}",
                    "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
                    "hourly": "precipitation_probability",
                    "forecast_days": 1,
                    "timezone": "auto",
                }
                url = OPEN_METEO_URL
                if settings.open_meteo_api_key:
                    url = OPEN_METEO_CUSTOMER_URL
                    params["apikey"] = settings.open_meteo_api_key
                payload = _request_json(url, params, {}, timeout, client, retries)
                snapshot = _parse_open_meteo(payload)
            else:
                params = {"lat": f"{latitude:.4f}", "lon": f"{longitude:.4f}"}
                headers = {"User-Agent": settings.weather_user_agent}
                payload = _request_json(
                    MET_NO_URL, params, headers, timeout, client, retries
                )
                snapshot = _parse_met_no(payload)
        except WeatherError as error:
            failures.append(f"{provider}: {error}")
            logger.warning("weather provider %s failed for %.4f,%.4f: %s",
                           provider, latitude, longitude, error)
            continue

        with _CACHE_LOCK:
            _CACHE[key] = (time.time(), snapshot)
        return snapshot

    raise WeatherError(
        "all weather providers failed -> " + "; ".join(failures)
        + ". Note: Open-Meteo's free tier has no API key and applies its limits per IP; "
        "it names cloud hosts such as Render explicitly as likely to be limited early. "
        "OPEN_METEO_API_KEY only exists on a paid plan. The keyless MET Norway fallback "
        "and the response cache are the free mitigations already in use here."
    )


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------
def compute_risk(
    weather: Optional[WeatherSnapshot],
    advisory_entry: Optional[dict] = None,
) -> RiskAssessment:
    """Deterministic risk band. See the module docstring for the point table."""
    if weather is None:
        return RiskAssessment(
            level="Low",
            score=0,
            max_score=MAX_GENERIC_SCORE,
            available=False,
            reason="Weather data unavailable; risk could not be computed.",
        )

    factors: List[RiskFactor] = []
    score = 0

    humidity = weather.humidity_pct
    if humidity >= 90:
        points, detail = 3, f"{humidity:.0f}% relative humidity — sustained leaf wetness likely"
    elif humidity >= 80:
        points, detail = 2, f"{humidity:.0f}% relative humidity — favourable for foliar disease"
    elif humidity >= 70:
        points, detail = 1, f"{humidity:.0f}% relative humidity — mildly favourable"
    else:
        points, detail = 0, f"{humidity:.0f}% relative humidity — unfavourable"
    score += points
    factors.append(RiskFactor("humidity", points, detail))

    temperature = weather.temperature_c
    if 15 <= temperature <= 30:
        points, detail = 1, f"{temperature:.1f} C — within the range most foliar pathogens favour"
    else:
        points, detail = 0, f"{temperature:.1f} C — outside the 15-30 C favourable band"
    score += points
    factors.append(RiskFactor("temperature", points, detail))

    rain = weather.precipitation_mm
    if rain >= 2.5:
        points, detail = 3, f"{rain:.1f} mm rain now — active splash dispersal"
    elif rain >= 0.5:
        points, detail = 2, f"{rain:.1f} mm rain now — splash dispersal likely"
    elif rain > 0:
        points, detail = 1, f"{rain:.1f} mm rain now — light, but leaves are wet"
    else:
        points, detail = 0, "No rain at the moment"
    probability = weather.precipitation_probability_pct
    if probability is not None and probability >= 70:
        points += 1
        detail += f"; {probability:.0f}% chance in the next few hours"
    score += points
    factors.append(RiskFactor("rainfall", points, detail))

    if humidity >= 90 and 10 <= temperature <= 25:
        score += 2
        factors.append(
            RiskFactor(
                "leaf_wetness",
                2,
                "Humid and cool — the classic late-blight infection window",
            )
        )
    else:
        factors.append(RiskFactor("leaf_wetness", 0, "No extended leaf-wetness window"))

    generic_score = score
    adjustment = 0
    label = None
    sensitivity = (advisory_entry or {}).get("weather_sensitivity")
    if isinstance(sensitivity, dict):
        label = (advisory_entry or {}).get("condition")
        if "humidity_min" in sensitivity and humidity >= float(sensitivity["humidity_min"]):
            adjustment += 1
        if "humidity_max" in sensitivity and humidity <= float(sensitivity["humidity_max"]):
            adjustment += 1
        window = sensitivity.get("temp_range_c")
        if isinstance(window, list) and len(window) == 2:
            if float(window[0]) <= temperature <= float(window[1]):
                adjustment += 1
        if sensitivity.get("rain_driven") and rain > 0:
            adjustment = min(MAX_ADVISORY_SCORE, adjustment + 1)
        adjustment = min(MAX_ADVISORY_SCORE, adjustment)

    total = generic_score + adjustment
    return RiskAssessment(
        level=_level_for(total),
        score=total,
        max_score=MAX_GENERIC_SCORE + MAX_ADVISORY_SCORE,
        factors=factors,
        advisory_adjustment=adjustment,
        advisory_label=label,
        available=True,
    )


def assess_location(
    latitude: float,
    longitude: float,
    advisory_entry: Optional[dict] = None,
    timeout: float = 8.0,
) -> Tuple[Optional[WeatherSnapshot], RiskAssessment]:
    """Fetch and score in one step, degrading gracefully when weather is down.

    The failure reason is carried on the assessment rather than swallowed, so the API
    can tell a farmer *why* the risk is missing ("did not respond in time" versus
    "HTTP 429") instead of only that it is.
    """
    try:
        snapshot = fetch_weather(latitude, longitude, timeout=timeout)
    except WeatherError as error:
        logger.warning("weather unavailable for %.4f,%.4f: %s", latitude, longitude, error)
        assessment = compute_risk(None, advisory_entry)
        assessment.reason = f"Weather data unavailable: {error}"
        return None, assessment
    return snapshot, compute_risk(snapshot, advisory_entry)
