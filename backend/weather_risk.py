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
def fetch_weather(
    latitude: float,
    longitude: float,
    timeout: Optional[float] = None,
    client: Optional[httpx.Client] = None,
    use_cache: bool = True,
    retries: int = 2,
) -> WeatherSnapshot:
    """Current conditions at a point. Raises WeatherError on any failure.

    Order of operations, all of it aimed at surviving a shared cloud IP:

    1. Serve from the in-process cache when the same ~1 km square was fetched within
       ``WEATHER_CACHE_TTL`` seconds. Repeating a demo submission costs nothing.
    2. Request the customer endpoint when ``OPEN_METEO_API_KEY`` is set, which is not
       subject to the free endpoint's per-IP limit.
    3. Retry 429 and 5xx responses with a short backoff. A rate limit is often a
       momentary burst rather than a hard block.
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

    own_client = client is None
    http = client or httpx.Client(timeout=timeout)
    payload = None
    try:
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                response = http.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                last_error = error
                # 429 and 5xx are worth retrying; 4xx other than 429 will not improve.
                if status_code != 429 and status_code < 500:
                    raise WeatherError(
                        f"The weather service returned HTTP {status_code}."
                    ) from error
                if attempt < retries:
                    delay = 0.5 * (2 ** attempt)
                    logger.info(
                        "weather request throttled (HTTP %s), retrying in %.1fs",
                        status_code, delay,
                    )
                    time.sleep(delay)
                    continue
                if status_code == 429:
                    raise WeatherError(
                        "Open-Meteo rate limit reached for this server's IP (HTTP 429). "
                        "Set OPEN_METEO_API_KEY to use the customer endpoint, or retry "
                        "in a few minutes. A cached result for this location is reused "
                        "for WEATHER_CACHE_TTL seconds once one succeeds."
                    ) from error
                raise WeatherError(
                    f"The weather service returned HTTP {status_code} after "
                    f"{retries + 1} attempts."
                ) from error
            except httpx.TimeoutException as error:
                last_error = error
                if attempt < retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise WeatherError("The weather service did not respond in time.") from error
            except httpx.HTTPError as error:
                raise WeatherError(
                    f"The weather service could not be reached: {error}"
                ) from error
            except ValueError as error:
                raise WeatherError(
                    "The weather service returned a response that is not JSON."
                ) from error

        if payload is None:
            raise WeatherError(
                f"The weather service could not be reached: {last_error}"
            )
    finally:
        if own_client:
            http.close()

    current = payload.get("current") or {}
    try:
        temperature = float(current["temperature_2m"])
        humidity = float(current["relative_humidity_2m"])
    except (KeyError, TypeError, ValueError) as error:
        raise WeatherError(
            "The weather service response is missing temperature or humidity."
        ) from error

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

    snapshot = WeatherSnapshot(
        temperature_c=round(temperature, 1),
        humidity_pct=round(humidity, 1),
        precipitation_mm=round(precipitation, 2),
        precipitation_probability_pct=probability,
        wind_kph=float(wind) if isinstance(wind, (int, float)) else None,
        observed_at=observed if isinstance(observed, str) else datetime.now(timezone.utc).isoformat(),
    )
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), snapshot)
    return snapshot


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
