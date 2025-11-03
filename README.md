<img width="96" height="96" alt="image" src="https://github.com/user-attachments/assets/6495aabb-4644-40dd-b248-123b3ad08a88" />

# HERO — Hybrid Emergency Route Optimizer on Microsoft Fabric

> **TL;DR**: We built an end-to-end, real-time “lights & sirens” routing solution on **Microsoft Fabric**.  
> It compares Google traffic-aware vs theoretical routes, applies a **siren advantage** (heuristic + ML), picks the faster one, **streams** the chosen path + live vehicle telemetry, renders it in **Power BI**, and **texts an SMS with a static map** via **Twilio**.

---

## ✨ What it does

- Calls **Google Routes API** twice:
  - **TRAFFIC_AWARE_OPTIMAL** (live traffic baseline)
  - **TRAFFIC_UNAWARE** (theoretical no-traffic baseline)
- Computes a “**siren advantage**”:
  - Heuristic fallback
  - **AutoML regression** (trained in Fabric) when available
- Chooses the faster option and **publishes**:
  - Route analysis (decision, ETAs, congestion score)
  - Route segments (polyline decoded to points)
  - Live vehicle telemetry (simulated at ETA pace, stops on arrival; expected to come from real vehicles)
- **Power BI** report shows:
  - **LINESTRING** route (WKT)
  - Moving vehicle icon
  - Vehicle telemetry history
- Sends an **SMS with a static route map** via **Twilio**

---

## 🧱 Architecture (high level)

### 🧰 Tech stack
- **Microsoft Fabric**: Eventhouse (KQL), Eventstreams, Notebooks, UDFs, AutoML/MLflow, Lakehouse, Pipelines, Variables, Activator
- **Azure**: SQL Azure Database, Key Vault
- **Google**: Routes API (Directions v2)
- **Power BI**
- **Twilio**: Programmable SMS
  



    



**Core Fabric pieces**  
- **Eventhouse (KQL DB)**: `tb_route_analysis`, `tb_route_segments`, `tb_vehicles_telemetry` (bronze)  
- **Silver tables**: cleaned/cast versions + processed timestamp  
- **Routes lookup**: `tb_routes_lookup` (per-route WKT LINESTRING)  
- **Gold function**: final shape for the map (vehicle icon rows + WKT rows)  
- **UDFs** (`hero_functions`): for modularity and to avoid installing non default libraries in environments for speed
  - `get_route(params)` – calls Google, returns polyline + congestion (requires `extraComputations: TRAFFIC_ON_POLYLINE`)
  - `publish_events(params)` – pushes to Event Hubs
  - `publish_vehicle_telemetry(params)` – sends telemetry batches/points
- **Notebook**: **hero_route_decision.ipynb** only python kernel for speed, the goal was to keep latency as little as possible. 
  - Gets routes, applies heuristic/ML, decides, publishes events, simulates and streams telemetry, sends SMS
- **AutoML**: regression to predict **siren_advantage**  
- **Power BI**: report (ambulance icon + LINESTRING)
- **Activator**: sends an email to alert the Emergency Operations Center or the Ambulance Emergency Dispatch of a new dispatch with triage color code and it triggers `hero_route_decision.ipynb` notebook, the core of the solution when triage color <> 'green'

---

## 🧪 Key features

- **Two-route comparison** (aware vs theoretical)
- **Siren advantage**:
  - Heuristic: `0.10 + 0.25 * congestion_score (capped 35%)`
  - ML model (AutoML regression) with fallback to heuristic model
- **Congestion** from Google: `extraComputations: TRAFFIC_ON_POLYLINE`
- **Telemetry** paced by ETA; **stops on arrival**
- **WKT LINESTRING** builder for smooth Maps visual rendering
- **SMS** with Google Static Map link via **Twilio**

---

## 🔐 Secrets & config

- **Azure Key Vault** for:
  - `google-maps-api-key`
  - `conn-str-route-analysis`
  - `conn-str-route-segments`
  - `conn-str-vehicles-telemetry`
  - `twilio-sid`
  - `twilio-token`
 
  - **Fabric Variables** for:
  - `azure-key-vault`
  - `ML-model`

- **UDFs** use `fabric user data functions` SDK.

---

## 🗃️ Data model (essentials)

**Bronze (ingest mirrors)**  
- `tb_route_analysis` — decision rows  
- `tb_route_segments` — chosen route decoded to points (`route_id`, `sequence`, `latitude`, `longitude`)  
- `tb_vehicles_telemetry` — stream of positions for each vehicle

**Silver (cleaned & typed)**  
- Add `processed_timestamp = now()`
- Cast `latitude/longitude` to `real`, `sequence` to `int`, etc.
- Correct `status`

**Routes wkt (helper)**  
- `tb_routes_lookup(route_id, wkt)`  
- Built from `tb_route_segments_silver`:
  - `LINESTRING(lon lat, lon lat, ...)` sorted by `sequence`

**Gold (view/function for Power BI)**  
- **Materialized View** to get latest position by vehicle `mv_latest_telem`  
- **Function** `routes_latest_vehicles_gold` which is the union of:
  - **Vehicle icon rows** (with `icon_map` URL, real lat/lon) from `mv_latest_telem`
  - **WKT rows** (with `icon_map = wkt`, lat/lon set to `null`) from `tb_routes_wkt_silver`
- **Function** `vehicle_telemetry_gold` for telemetry analysis

---

## 🚀 How to run (demo path)

1. **Configure database for CDC**
2. **Deploy UDFs** (`hero_functions` collection):
   - `get_route`, `publish_events`, `publish_vehicle_telemetry`
3. **Set Fabric Variables** (see Secrets & config).
4. **Set Azure Key Vault Secrets**  (see Secrets & config).
5. **Run notebook** `hero_route_decision.ipynb`:
   - Inputs: `mission_id`, `vehicle_id`, `origin_lat`, `origin_lon`, `dest_lat`, `dest_lon`
   - It:
     - Calls Google (aware + theoretical)
     - Computes ML siren advantage (fallback to heuristic)
     - Publishes analysis + segments
     - Starts telemetry streaming to Event Hubs
     - Sends Twilio SMS with static map
6. **KQL**:
   - Build silver tables and `tb_routes_lookup`  
   - Use **gold function** in Power BI for Azure Maps
7. **Power BI**:
   - Use **Icon Map** visual
   - Bind **icon rows** to point layer (ambulance PNG)
   - Bind **WKT rows** to line layer (route polyline)
   - Filter by `route_id` and/or `vehicle_id`

---

## ⚙️ Notable implementation details

- **Congestion extraction** requires:
  ```json
  "extraComputations": "TRAFFIC_ON_POLYLINE"```
  and field mask includes:
    routes.legs.travelAdvisory.speedReadingIntervals.(startPolylinePointIndex,endPolylinePointIndex,speed)
- **ML live scoring**:
  - Loads the latest registered model via MLflow
  - Input schema: [congestion_score, eta_theoretical_min, distance_m_theoretical, hour_of_day, dow, avg_speed_kmh, telemetry_points]
  - If validation/predict fails → heuristic fallback
- **Telemetry**:
  - Currently simulated, with intervals derived from chosen ETA
  - Emits progress_pct and status (arrived on last point)
  - Expected to come from real vehicle telemetry systems.
 
## Known limitations

- **avg_speed_kmh** input param for ML model is a placeholder (50 km/h) until TomTom or historical speed model is integrated for calculating avg speed from segments.
- **Notebook + UDFs** assume polyline decoding and ETA are available — handle Google API quotas
- **Materialized views** in KQL have restrictions; we use functions and update policies where needed
