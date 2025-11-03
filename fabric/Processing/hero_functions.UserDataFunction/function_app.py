import logging
import requests
import polyline
import uuid
from datetime import datetime, timedelta
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
import fabric.functions as fn
from azure.eventhub import EventHubProducerClient, EventData
import json
import random
import time
import sys
from typing import List, Dict, Any, Tuple
import base64

udf = fn.UserDataFunctions()

@udf.function()
def get_route(params: dict) -> dict:
    """
    Fetches route with or without traffic data from Google Maps API.

    params:
      origin_lat: float
      origin_lon: float
      dest_lat: float
      dest_lon: float
      api_key: str
      routing_preference: str (TRAFFIC_AWARE_OPTIMAL, TRAFFIC_AWARE, TRAFFIC_UNAWARE)

    Returns:
      {
        "route_id": str,
        "routing_mode": str,
        "eta_min": float,
        "distance_m": int,
        "polyline": str,
        "coordinates": list[(lat,lon)],
        "segments": list[{start, end, speed_category}],
        "congestion_score": float,
        "congestion_label": str
      }
    """
    logging.info("HERO | Fetching route from Google Routes API")

    # --- Inputs ---
    origin_lat = params.get("origin_lat")
    origin_lon = params.get("origin_lon")
    dest_lat = params.get("dest_lat")
    dest_lon = params.get("dest_lon")
    api_key = params.get("api_key")
    routing_pref = params.get("routing_preference", "TRAFFIC_AWARE_OPTIMAL")

    if not all([origin_lat, origin_lon, dest_lat, dest_lon, api_key]):
        raise ValueError("Missing required parameters: origin_lat, origin_lon, dest_lat, dest_lon, api_key")

    # --- Build request ---
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "routes.duration,"
            "routes.distanceMeters,"
            "routes.polyline.encodedPolyline,"
            "routes.legs.travelAdvisory.speedReadingIntervals"
        )
    }

    body = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}}},
        "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lon}}},
        "travelMode": "DRIVE",
        "extraComputations" : "TRAFFIC_ON_POLYLINE"
    }

    if routing_pref in ("TRAFFIC_AWARE", "TRAFFIC_AWARE_OPTIMAL"):
        body["routingPreference"] = routing_pref
        body["departureTime"] = (datetime.utcnow() + timedelta(minutes=1)).isoformat("T") + "Z"

    # --- Request ---
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("routes"):
            raise ValueError("No routes found in API response")

        route = data["routes"][0]
        route_id = str(uuid.uuid4())

        # --- Base info ---
        eta_min = int(route["duration"].replace("s", "")) / 60
        distance_m = route["distanceMeters"]
        polyline_encoded = route["polyline"]["encodedPolyline"]
        coordinates = polyline.decode(polyline_encoded)

        # --- Segments / congestion ---
        segments = []
        slow = jam = 0
        for leg in route.get("legs", []):
            for interval in leg.get("travelAdvisory", {}).get("speedReadingIntervals", []):
                start = interval["startPolylinePointIndex"]
                end = interval["endPolylinePointIndex"]
                speed = interval["speed"]
                segments.append({
                    "route_id": route_id,
                    "start": start,
                    "end": end,
                    "speed_category": speed
                })
                if speed == "SLOW":
                    slow += 1
                elif speed == "TRAFFIC_JAM":
                    jam += 1

        # --- Congestion metrics ---
        total = len(segments) or 1
        congestion_score = round((slow * 0.5 + jam * 1.0) / total, 2)
        if congestion_score < 0.3:
            congestion_label = "LOW"
        elif congestion_score < 0.7:
            congestion_label = "MEDIUM"
        else:
            congestion_label = "HIGH"

        return {
            "route_id": route_id,
            "routing_mode": routing_pref,
            "eta_min": eta_min,
            "distance_m": distance_m,
            "polyline": polyline_encoded,
            "coordinates": coordinates,
            "segments": segments,
            "congestion_score": congestion_score,
            "congestion_label": congestion_label
        }

    except requests.exceptions.RequestException as e:
        logging.error(f"Route fetch failed: {e}")
        raise


def _get_param(params: dict, key: str, default=None):
    """Helper to extract parameter value from both flat and {value:...} formats."""
    v = params.get(key, default)
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def _normalize_events(events_param: Any) -> List[Dict]:
    """Normalize events parameter to a list of dictionaries."""
    if isinstance(events_param, list):
        return events_param
    if isinstance(events_param, dict):
        return [events_param]
    raise ValueError("'events' must be a JSON object or an array of JSON objects")


@udf.function()
def publish_events(params: dict) -> dict:
    """
    Publishes events to Azure Event Hubs.
    
    Parameters:
      connection_string: str - Event Hubs-compatible connection string
      events: list[dict] or dict - Event(s) to publish
      partition_key: str (optional) - Partition key for routing
    
    Returns:
      dict with 'published' count and 'status'
    """
    logging.info('Publishing events to Event Hub')
    
    # Extract parameters
    conn_str = _get_param(params, "connection_string")
    events_param = _get_param(params, "events")
    partition_key = _get_param(params, "partition_key", None)

    # Validate required parameters
    if not conn_str or events_param is None:
        raise ValueError("Missing required parameters: connection_string and events")

    # Normalize events to list
    events = _normalize_events(events_param)
    
    if not events:
        raise ValueError("Events list cannot be empty")

    # Clean connection string (remove whitespace/newlines)
    conn_str = str(conn_str).strip()

    # Create EventData objects
    event_data_list = []
    for event in events:
        try:
            event_json = json.dumps(event)
            event_data_list.append(EventData(event_json))
        except (TypeError, ValueError) as e:
            logging.error(f"Failed to serialize event: {e}")
            raise ValueError(f"Event serialization failed: {e}")

    # Publish to Event Hub
    producer = None
    try:
        producer = EventHubProducerClient.from_connection_string(conn_str)
        
        # Create a batch and send
        event_batch = producer.create_batch(partition_key=partition_key)
        
        for event_data in event_data_list:
            try:
                event_batch.add(event_data)
            except ValueError:
                # Batch is full, send it and create a new one
                producer.send_batch(event_batch)
                event_batch = producer.create_batch(partition_key=partition_key)
                event_batch.add(event_data)
        
        # Send remaining events
        if len(event_batch) > 0:
            producer.send_batch(event_batch)
        
        logging.info(f"Successfully published {len(event_data_list)} event(s)")
        sys.stdout.flush()
        return {
            "status": "success",
            "published": len(event_data_list)
        }
        
    except Exception as e:
        logging.error(f"Failed to publish events: {e}")
        raise
    finally:
        if producer:
            try:
                producer.close()
            except Exception as e:
                logging.warning(f"Error closing producer: {e}")


@udf.function()
def publish_vehicle_telemetry(params: dict) -> dict:
    """
    Publish simulated vehicle telemetry to Event Hub.

    Params (dict):
      connection_string : str   (required)
      vehicle_id        : str   (required)
      route_id          : str   (required)
      points            : list  (required)  # list of (lat, lon) pairs
      sequence          : int   (optional)  # base sequence for the first point in this call (default 0)
      partition_key     : str   (optional)  # e.g., vehicle_id
      # Optional fields that, if provided, are included in each event:
      speed_kmh         : float (optional)
      extra             : dict  (optional)  # arbitrary extra fields merged into each event

    Returns:
      { "status": "success", "count": <events_sent>, "first_seq": <int>, "last_seq": <int> }
    """
    log = logging.getLogger("udf.publish_vehicle_telemetry")
    conn_str = params.get("connection_string")
    vehicle_id = params.get("vehicle_id")
    route_id = params.get("route_id")
    points: List[Tuple[float, float]] = params.get("points", [])
    base_seq = int(params.get("sequence", 0))
    partition_key = params.get("partition_key") or vehicle_id
    status = params.get("status")
    progress_pct = params.get("progress_pct")

    speed_kmh = params.get("speed_kmh")    # optional
    extra = params.get("extra") or {}      # optional

    if not conn_str or not vehicle_id or not route_id or not points:
        raise ValueError("Missing required parameters: connection_string, vehicle_id, route_id, points")

    producer = None
    try:
        producer = EventHubProducerClient.from_connection_string(conn_str)

        total = len(points)
        first_seq = base_seq
        last_seq = base_seq + total - 1

        for i, pt in enumerate(points):
            lat, lon = pt
            seq = base_seq + i
            local_status = status or ("arrived" if i == total - 1 else "en_route")
            local_progress = progress_pct #or round(((i + 1) / total) * 100, 1)

            event_payload = {
                "vehicle_id": vehicle_id,
                "route_id": route_id,
                "sequence": seq,
                "timestamp": datetime.utcnow().isoformat(),
                "latitude": lat,
                "longitude": lon,
                "status": local_status,
                "progress_pct": local_progress
            }

            if speed_kmh is not None:
                event_payload["speed_kmh"] = float(speed_kmh)
            if isinstance(extra, dict) and extra:
                event_payload.update(extra)

            # send one-by-one to preserve order and simplify error handling
            producer.send_batch([EventData(json.dumps(event_payload))], partition_key=partition_key)

        log.info(f"Published {total} telemetry events for {vehicle_id} [{first_seq}..{last_seq}]")
        return {"status": "success", "count": total, "first_seq": first_seq, "last_seq": last_seq}

    except Exception as e:
        logging.exception("Telemetry publishing failed")
        raise
    finally:
        if producer:
            try:
                producer.close()
            except Exception:
                pass

@udf.function()
def send_sms_with_map(params: dict) -> dict:
    """
    Sends an SMS containing a Google Static Map link for the chosen route.

    params:
      to_phone:        str   E.164 phone number, e.g. +390123456789
      text_prefix:     str   Message prefix (optional)
      gmaps_api_key:   str   Google Maps API Key
      twilio_sid:      str   Twilio Account SID
      twilio_token:    str   Twilio Auth Token
      twilio_from:     str   Twilio from number (E.164)
      polyline:        str   Encoded polyline for path (preferred)
      coords:          list  Optional list of {lat,lon} if polyline not provided
      decision:        str   Hero decision
    """
    to_phone      = params.get("to_phone")
    text_prefix   = params.get("text_prefix", "HERO alert:")
    gmaps_api_key = params.get("gmaps_api_key")
    twilio_sid    = params.get("twilio_sid")
    twilio_token  = params.get("twilio_token")
    twilio_from   = params.get("twilio_from")
    polyline      = params.get("polyline")
    coords        = params.get("coords", None)
    decision      = params.get("decision")

    if not all([to_phone, gmaps_api_key, twilio_sid, twilio_token, twilio_from]) or (not polyline and not coords):
        raise ValueError("Missing required parameters.")

    # Build polyline if only coords provided
    if not polyline and coords:
        # Google encoded polyline spec; minimal encoder to avoid extra deps
        def _enc(v):  # v in 1e-5 degrees
            v = int(round(v))
            v = ~(v << 1) if v < 0 else (v << 1)
            s = []
            while v >= 0x20:
                s.append(chr((0x20 | (v & 0x1f)) + 63)); v >>= 5
            s.append(chr(v + 63))
            return "".join(s)
        last_lat = last_lon = 0
        out = []
        for p in coords:
            lat, lon = int(round(p["lat"]*1e5)), int(round(p["lon"]*1e5))
            out.append(_enc(lat - last_lat)); out.append(_enc(lon - last_lon))
            last_lat, last_lon = lat, lon
        polyline = "".join(out)

    # Static map URL (800x600; adjust zoom via auto fit)
    static_map_url = (
        "https://maps.googleapis.com/maps/api/staticmap"
        "?size=800x600"
        f"&path=weight:5|color:0x0066FF|enc:{polyline}"
        f"&key={gmaps_api_key}"
    )

    # SMS body with link
    # try:
    #     short_map_url = requests.get("https://tinyurl.com/api-create.php", params={"url": static_map_url}).text
    # except Exception: pass

    try:
        short_map_url = requests.get("https://is.gd/create.php", params={"format": "simple", "url": static_map_url}).text
    except Exception:
        short_map_url = static_map_url  # fallback to original

    # body = f"{text_prefix} Suggested route map: {short_map_url}"
    body = f"""Emergency Dispatch
    Decision: Use {decision} route.
    Suggested Route map:
    {short_map_url}"""


# https://demo.twilio.com/welcome/sms/reply/
    # Twilio SMS
    tw_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
    resp = requests.post(
        tw_url,
        data={"To": to_phone, "From": twilio_from, "Body": body},
        auth=(twilio_sid, twilio_token),
        timeout=15
    )
    try:
        resp.raise_for_status()
    except Exception as e:
        logging.error(f"Twilio send failed: {resp.status_code} - {resp.text}")
        raise

    sid = resp.json().get("sid", "")
    return {"status": "sent", "twilio_sid": sid, "map_url": short_map_url}
