# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "jupyter",
# META     "jupyter_kernel_name": "python3.11"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "1d7761b2-7df4-4f89-b042-3fd49f3bd776",
# META       "default_lakehouse_name": "lakehouse",
# META       "default_lakehouse_workspace_id": "31f66446-fbac-4a10-b8cd-612c2c7b9c9d",
# META       "known_lakehouses": [
# META         {
# META           "id": "1d7761b2-7df4-4f89-b042-3fd49f3bd776"
# META         }
# META       ]
# META     },
# META     "environment": {}
# META   }
# META }

# PARAMETERS CELL ********************

mission_id = 5000
vehicle_id = "AMB-30"
origin_coord = "45.51013857141289,9.184226615721908"
incident_coord = "45.52490166289738,9.187160360153706"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

#added function to split coordinates because activator supports max 5 parameters
def split_coordinates(coordinates: str) -> (float, float):
    lat_str, lon_str = coordinates.split(',')
    return float(lat_str), float(lon_str)

origin_lat, origin_lon = split_coordinates(origin_coord)
dest_lat, dest_lon = split_coordinates(incident_coord)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

import logging
from datetime import datetime
from notebookutils import udf
import sempy.fabric as fabric
import mlflow
from mlflow.pyfunc import load_model
import time
import random
import pandas as pd

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
log = logging.getLogger("hero-notebook")
log.info("HERO route decision pipeline starting")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

# ---------- CONFIG ----------

# Load variable library
variable_lib = notebookutils.variableLibrary.getLibrary("Variables")

#Load variables
VAULT_URL = variable_lib.getVariable("azure-key-vault")
MODEL_NAME = variable_lib.getVariable("siren-model")

#get secrests from AKV
API_KEY = notebookutils.credentials.getSecret(VAULT_URL,"google-maps-api-key")
EH_CONN_ANALYSIS = notebookutils.credentials.getSecret(VAULT_URL,"conn-str-route-analysis")
EH_CONN_SEGMENTS = notebookutils.credentials.getSecret(VAULT_URL,"conn-str-route-segments")
EH_CONN_TELEMETRY = notebookutils.credentials.getSecret(VAULT_URL,"conn-str-vehicles-telemetry")
TWILIO_SID = notebookutils.credentials.getSecret(VAULT_URL,"twilio-sid")
TWILIO_FROM = notebookutils.credentials.getSecret(VAULT_URL,"twilio-from-number")
TWILIO_TOKEN = notebookutils.credentials.getSecret(VAULT_URL,"twilio-token")
TO_PHONE = notebookutils.credentials.getSecret(VAULT_URL,"twilio-to-number")

#UDF handles
WORKSPACE_ID = fabric.get_workspace_id()
FUNC_COLLECTION = "hero_functions"
hero_functions = notebookutils.udf.getFunctions(FUNC_COLLECTION, WORKSPACE_ID)

log.info("Config done")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

# Load latest version from Fabric MLflow model registry

model_uri = f"models:/{MODEL_NAME}/2"
ml_model = load_model(model_uri)

log.info("Loaded model: {MODEL_NAME}")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

# ============================================================
#  HERO Route Decision Python Notebook 
# ------------------------------------------------------------
# - Gets Google traffic-aware optimal route (as baseline)
# - Gets theoretical route with TRAFFIC_UNAWARE
# - Applies HERO (emergency) advantage to theoretical and google route
# - Picks faster option and publishes to Eventstream via UDF
# ============================================================

dispatch = {
    "mission_id": mission_id,
    "vehicle_id": vehicle_id,
    "origin_lat": origin_lat,
    "origin_lon": origin_lon,
    "dest_lat": dest_lat,
    "dest_lon": dest_lon
}


# ---------- 1) Google traffic-aware optimal (baseline) ----------
try:
    log.info("Fetching Google TRAFFIC_AWARE_OPTIMAL route...")
    aware = hero_functions.get_route(params={
        "origin_lat": dispatch["origin_lat"],
        "origin_lon": dispatch["origin_lon"],
        "dest_lat":   dispatch["dest_lat"],
        "dest_lon":   dispatch["dest_lon"],
        "api_key":    API_KEY,
        "routing_preference": "TRAFFIC_AWARE_OPTIMAL"
    })

    eta_google = float(aware["eta_min"])
    dist_google = int(aware["distance_m"])
    congestion_score = aware["congestion_score"]
    congestion_label = aware["congestion_label"]
    pts_google = aware["coordinates"]           # list of (lat, lon)
    route_id_google = aware["route_id"]


    log.info(f"Google aware: ETA={eta_google:.2f} min, dist={dist_google/1000:.2f} km, congestion={congestion_label}")
except Exception as e:
    log.exception("get_route failed for Google-aware")
    raise

# ---------- 2) Theoretical (no live traffic) ----------
try:
    log.info("Fetching Google TRAFFIC_UNAWARE (theoretical) route...")
    theoretical = hero_functions.get_route(params={
        "origin_lat": dispatch["origin_lat"],
        "origin_lon": dispatch["origin_lon"],
        "dest_lat":   dispatch["dest_lat"],
        "dest_lon":   dispatch["dest_lon"],
        "api_key":    API_KEY,
        "routing_preference": "TRAFFIC_UNAWARE"
    })
    
    eta_theoretical = float(theoretical["eta_min"])
    dist_theoretical = int(theoretical["distance_m"])
    pts_theoretical = theoretical["coordinates"] # list of (lat, lon)
    route_id_theoretical = theoretical["route_id"]

    log.info(f"Theoretical: ETA={eta_theoretical:.2f} min, dist={dist_theoretical/1000:.2f} km")

except Exception as e:
    log.exception("get_route failed for Google-aware")
    raise

#---------- 3) HERO adjustment (apply ONLY to theoretical) ----------
def compute_hero_eta(eta_min: float, congestion_score: float) -> float:
    """
    Emergency advantage heuristic applied to theoretical route:
    - Base advantage 10% -> reflects use of sirens, right-of-way, priority lanes (buses), skip queues and traffic signals
    - The advantage increases as congestion grows
    - Cap at 35% reduction -> failsafe tp prevent the model from exaggerating emergency gains and to stay realistic
    This is a simple PoC heuristic, it will be replaced in the furure by an ML model 
    train on the telemetry and route decision historical data accumulated by this solution.
    """
    advantage = 0.10 + 0.25 * congestion_score
    advantage = min(0.35, 0.10 + 0.25 * congestion_score)
    return round(eta_min * (1 - advantage), 2)


# Create a pandas DF 
features_for_current_trip = pd.DataFrame([{
    "congestion_score": congestion_score,
    "eta_theoretical_min": eta_theoretical,
    "distance_m_theoretical": dist_theoretical,
    "hour_of_day": datetime.utcnow().hour,
    "dow": datetime.utcnow().weekday(),
    "avg_speed_kmh": 50, #in the future replace with avg speed from all segments from tomtom or custom model      
    "telemetry_points": len(pts_theoretical)
}])

# Cast all to float64 to match MLflow schema
features_for_current_trip = features_for_current_trip.astype({
    "congestion_score": "float64",
    "eta_theoretical_min": "float64",
    "distance_m_theoretical": "float64",
    "hour_of_day": "float64",
    "dow": "float64",
    "avg_speed_kmh": "float64",
    "telemetry_points": "float64"
})

# eta hero theoretical: apply ml model
try:
    predicted_adv = float(ml_model.predict(features_for_current_trip)[0])
    # sanity clamp
    predicted_adv = max(0.05, min(predicted_adv, 0.35))  # between 5% and 35% improvement
    eta_theoretical_hero = round(eta_theoretical * (1 - predicted_adv), 2)
    used_model = "ml"
except Exception as e:
    log.warning(f"ML prediction failed, fallback to heuristic: {e}")
    predicted_adv = None
    eta_theoretical_hero = compute_hero_eta(eta_theoretical, congestion_score)  # heuristic
    used_model = "heuristic"

time_saved_vs_google = round(eta_google - eta_theoretical_hero, 2)
saved_min = -time_saved_vs_google if time_saved_vs_google < 0 else time_saved_vs_google

# Decision rule 
REROUTE_THRESHOLD_MIN = 2.0
if eta_theoretical_hero < eta_google - REROUTE_THRESHOLD_MIN:
    decision = "hero"
else:
    decision = "google"

log.info(f"Decision={decision} | Model={used_model} | PredAdv={predicted_adv}")

chosen_pts = pts_theoretical if decision == "hero" else pts_google
chosen_dist = dist_theoretical if decision == "hero" else dist_google
chosen_eta  = eta_theoretical_hero if decision == "hero" else eta_google
chosen_route_id = route_id_theoretical if decision == "hero" else route_id_google
chosen_mode = "TRAFFIC_UNAWARE" if decision == "hero" else "TRAFFIC_AWARE_OPTIMAL"


log.info(f"Decision: {decision.upper()} | google={eta_google:.2f} | hero={eta_theoretical_hero:.2f} | saved={saved_min:.2f} min")


#  -----------   4) send sms with static map ------------
try:
    hero_functions.send_sms_with_map(params={
        "to_phone": TO_PHONE,
        "text_prefix": f"HERO REROUTE for {dispatch['vehicle_id']}:", 
        "gmaps_api_key": API_KEY,
        "twilio_sid": TWILIO_SID,
        "twilio_token": TWILIO_TOKEN,
        "twilio_from": TWILIO_FROM,
        "polyline": aware["polyline"] if decision=="GOOGLE" else theoretical["polyline"],
        "decision": decision
    })
    log.info(f"Sent SMS message")
except Exception as e:
    log.exception("SMS sending failed")

# ---------- 5) Publish route_analysis ----------
try:
    ts = datetime.utcnow().isoformat() + "Z"
    analysis_event = {
        "mission_id": dispatch["mission_id"],
        "timestamp": ts,
        "vehicle_id": dispatch["vehicle_id"],
        "route_id": chosen_route_id,
        "eta_google_aware_min": eta_google,
        "eta_theoretical_min": eta_theoretical,
        "eta_hero_min": chosen_eta,
        "time_saved_vs_google_min": saved_min,
        "distance_m_theoretical": dist_theoretical,
        "distance_m_google": dist_google,
        "decision": decision,
        "congestion_score": congestion_score,
        "congestion_label": congestion_label
    }

    if analysis_event:
        hero_functions.publish_events(params={
            "connection_string": EH_CONN_ANALYSIS,
            "events": analysis_event,
            "partition_key": str(dispatch["mission_id"]) 
        })
        log.info(f"Published {len(analysis_event)} route_analysis events")
    else:
        log.warning("No analysis events to publish (empty coordinates list)")
except Exception as e:
    log.exception("Route_analysis publish failed")
    print(e)
# ---------- 6) Publish route_segments ----------
try:
    segment_events = [
        {
            "mission_id": dispatch["mission_id"],
            "route_id": chosen_route_id,
            "timestamp": ts,
            "sequence": i,
            "latitude": float(lat),
            "longitude": float(lon)
        } for i, (lat, lon) in enumerate(chosen_pts)
    ]

    if segment_events:
        hero_functions.publish_events(params={
            "connection_string": EH_CONN_SEGMENTS,
            "events": segment_events,
            "partition_key": str(dispatch["mission_id"]) 
        })
        log.info(f"Published {len(segment_events)} route_segments")
    else:
        log.warning("No segment events to publish (empty coordinates list)")
except Exception as e:
    log.exception("Route_segments publish failed")
# ---------- SUMMARY ----------
log.info("HERO pipeline completed.")
print({
    "eta_google_min": eta_google,
    "eta_theoretical_min": eta_theoretical,
    "eta_hero_min": chosen_eta,
    "decision": decision,
    "points": len(chosen_pts)
})


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

# ====================================================
# Telemetry Simulation
# ====================================================


def stream_telemetry_eta_based(points, vehicle_id, route_id, eta_min):
    """
    Streams telemetry in the background.
    - Stops at destination.
    - Interval chosen so total time ~ ETA.
    - Sequence increments from 0..N-1.
    """
    try:
        n = len(points)
        log.info(f"Starting telemetry for {vehicle_id}, ETA={eta_min:.1f} min, points={n}")
        if n < 2:
            log.warning("Not enough points for telemetry simulation.")
            return

        total_sec = max(10.0, float(eta_min) * 60.0)
        # spread intervals over transitions (n-1 gaps). Keep last point immediate.
        base_interval = total_sec / max(1, (n - 1))

        last_idx  = max(0, n - 1)

        for i, (lat, lon) in enumerate(points):
            # small variation for realism (±20%)
            jitter = random.uniform(0.8, 1.2)
            interval = base_interval * jitter
            # progress = 0 if last_idx == 0 else int(round(100.0 * i / last_idx))
            progress = 0 if i==0 else int(round(100.0 * i / last_idx))
            status   = "arrived" if i == last_idx else "en_route"

            # synthesize plausible speed/heading
            speed_kmh = round(random.uniform(35, 75), 1)
            heading_deg = round(random.uniform(0, 360), 1)

            hero_functions.publish_vehicle_telemetry(params={
                "connection_string": EH_CONN_TELEMETRY,
                "vehicle_id": vehicle_id,
                "route_id": route_id,
                "points": [(lat, lon)],     # one point per call
                "sequence": i,              # explicit sequence used by UDF
                "speed_kmh": speed_kmh,
                "status": status,
                "progress_pct": progress
            })

            # sleep only between points; after last point we stop immediately
            if i < n - 1:
                time.sleep(interval)

        log.info(f"Telemetry complete for {vehicle_id} — arrived at destination.")

    except Exception as e:
        log.error(f"Telemetry simulation error: {e}")


# Start streaming telemetry
telemetry_thread = stream_telemetry_eta_based(
    points=chosen_pts,
    vehicle_id=dispatch["vehicle_id"],
    route_id=chosen_route_id,
    eta_min=chosen_eta
)


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }
