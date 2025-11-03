<img width="96" height="96" alt="image" src="https://github.com/user-attachments/assets/6495aabb-4644-40dd-b248-123b3ad08a88" />

# HERO — Hybrid Emergency Route Optimizer on Microsoft Fabric

## Table of Contents
- [Overview](#overview)
- [Key Feature](#key-features)
- [Expected Impact](#-expected-impact)
- [How It Works](#-how-it-works)
- [Architecture](#-architecture-high-level)
- [Data Model](#%EF%B8%8F-data-model-essentials)
- [Installation Guide](#-installation-guide-for-microsoft-fabric--hero)
- [Notable Implementation Details](#%EF%B8%8F-notable-implementation-details)
- [Known Limitations](#known-limitations)
- [Future Roadmap](#future-roadmap)


## Overview

Emergency response teams are true heroes — but the navigation tools they rely on often aren’t. Traditional systems like Google Maps or Waze are optimized for everyday drivers, not emergency vehicles. During critical missions, these systems can suggest routes congested by the very incidents they’re responding to, or fail to consider that emergency vehicles with sirens can bypass certain traffic rules.

HERO (Hybrid Emergency Route Optimizer) leverages Microsoft Fabric and real-time data intelligence to provide AI-driven route recommendations tailored for emergency missions. It ingests live traffic data, mission dispatch feeds, and vehicle telemetry into Fabric’s Real-Time Intelligence Hub, continuously comparing standard navigation routes with AI-adjusted emergency alternatives.

An embedded AI model learns how siren-equipped vehicles perform under different traffic and incident conditions — estimating the “siren advantage” dynamically. HERO then generates real-time alerts and dashboards for both dispatch control centers and field units, showing the optimal route, expected arrival time, and estimated time saved.

---

## Key Features

⚡ Real-Time Fabric Integration: Streams traffic and mission data through Fabric Real-Time Hub and Eventhouse.

🧠 AI-Enhanced Routing: Dynamically adjusts travel times using congestion data and emergency-vehicle performance profiles.

🛰️ Adaptive Rerouting: Responds instantly to changing traffic conditions or new incidents.

📊 Control-Center Dashboard: Built in Power BI on Fabric — live map, mission tracker, ETA savings, congestion hotspots.

🔔 Automated Notifications: Sends updates to mobile teams or dispatchers when route decisions change.

---

## ⚡ Expected Impact 

HERO aims to reduce emergency response times by 10–20%, helping first responders reach critical locations faster and more safely. Beyond routing, the system’s data foundation can support predictive dispatching, fleet optimization, emergency analytics, and cross-agency coordination.

By combining AI, real-time intelligence, and Microsoft Fabric’s unified analytics platform, HERO demonstrates how technology can empower the people who save lives with great scalability.

---

## ✨ How it works

- 🚑 Picks the **fastest emergency route** *right now*:
  - Compares Google **TRAFFIC_AWARE_OPTIMAL** vs **TRAFFIC_UNAWARE**
- 🧠 Applies **siren advantage**:
  - **AutoML regression** (trained in Fabric with historical telemetry and route decision data) or **heuristic fallback**
- 📡 Chooses the faster option and **Streams** everything into **Microsoft Fabric** for real-time decision-making:
  - Route analysis, decoded polyline segments, and vehicle telemetry paced by ETA
- 🗺️ Shows the route + moving vehicle in **Power BI**:
  - LINESTRING for the route + an icon for the vehicle
- 📲 Sends **SMS** with a **Google Static Map** link via Twilio

---

## 🧱 Architecture (high level)

 <img width="1122" height="949" alt="hero_HLA" src="https://github.com/user-attachments/assets/08d10ee2-4194-4b69-ac10-bce23de93e5d" />


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
- Helper tables like `tb_routes_wkt(route_id, wkt)` with derived columns needed to handle geometries

**Gold (view/function for Power BI)**  
- **Materialized View** to get latest position by vehicle `mv_latest_telem`  
- **Function** `routes_latest_vehicles_gold` which is the union of:
  - **Vehicle icon rows** (with `icon_map` URL, real lat/lon) from `mv_latest_telem`
  - **tb_routes_wkt** (with `icon_map = wkt`, lat/lon set to `null`) from `tb_routes_wkt_silver`
- **Function** `vehicle_telemetry_gold` for telemetry analysis

---

## 🚀 Installation Guide for Microsoft Fabric — HERO

To set up **HERO** end-to-end:

### 0) Setup Google API and Twilio accounts

Make sure to configure and get Google API key and Twilio SID, Token, Virtual Number from and Virtual Number To.

### 1) Prep your SQL source (Dispatches)

Run the 3 SQL files in `sql/` against your SQL database **in this order**:

1. `01_create_tables.sql`
2. `02_enable_cdc.sql`
3. `03_create_dispatches_simulation_sp.sql`

> This table + CDC are the upstream source that Fabric ingests and that activates the core of the solution: the `hero_route_decision.ipynb` notebook

---

### 2) Create a Microsoft Fabric workspace

Use a workspace with capacity that can safely run **Notebooks** and **Real-time intelligence**.

---

### 3) Fork / Import the repository

Fork or import this GitHub repo so you can link it to Fabric.

---

### 4) Link the `fabric/` folder to your Fabric workspace

Linking the `fabric/` folder will auto-hydrate the Fabric artifacts in your workspace
(Eventhouse, KQL Database, Notebooks, Data Functions, etc).

---

### 5) Verify Eventhouse & KQL database

Confirm the default **Eventhouse** and its **KQL database** were created.
These host the bronze/silver tables and gold functions/views.

---

### 6) EventStreams

Ensure these EventStreams exist and configure the connections:

- `dispatch` for dispatches coming from SQL DB — configure Azure SQL Database CDC connection using Azure Key Vault
- `routes_analysis` for route decision output —> copy custom point Event Hub SAS Key Authentication Conn Strg and create secret in Azure Key Vault 
- `routes_segments` for chosen route points (polyline decoded) —> copy custom point Event Hub SAS Key Authentication Conn Strg and create secret in Azure Key Vault 
- `vehicles_telemetry` for simulated vehicle telemetry —> copy custom point Event Hub SAS Key Authentication Conn Strg and create secret in Azure Key Vault 

---

### 7) Set Fabric Variables and remaining Azure Key Vault secrets

In **Azure Key Vault**, create/update:

- `google-maps-api-key`
- `conn-str-route-analysis`
- `conn-str-route-segments`
- `conn-str-vehicles-telemetry`
- `twilio-sid`
- `twilio-token`
- `twilio-from-number`
- `twilio-to-number`

> grant workspace MI access to Azure Key Vault with Key Vault Reader and Key Vault Secret User roles.

- In **Fabric → Variables**, create/update:

- `siren-model`
- `azure-key-vault`

---

### 8) (Optional) Train the ML model once

- run the ml_data_prep to prepare data for ML model. Schedule the pipeline to update table in batch at least once per day and in any case according to your EventHouse data retention rules.
- Open the **AutoML_siren_advantage** notebook from the workspace.
- Ensure training data table (`ml_siren_advantage_regression`) was created and correctly populated
- Run the notebook to create experiments and **register the model** in the Fabric Model Registry.
- Update model version in variables (see step above)

---

### 9) Smoke test

1. EXEC stored procedure created in step **1.3** to simulate dispatches `[hero].[RunFakeDispatchStream]`
2. Confirm dispatch inserts are flowing and that Dispatch EvenStream is correctly mirroring sql dispatched via CDC
3. Check the Activator starts triggering mail alert and `hero_route_decision.ipynb` notebook correctly passing parameters. The notebook:
   - Reads incoming **dispatches** (CDC) and calls **Google Routes** twice:
      - `TRAFFIC_AWARE_OPTIMAL` *(with `extraComputations=TRAFFIC_ON_POLYLINE`)*
      - `TRAFFIC_UNAWARE`
   - Computes congestion score from speed intervals on the polyline.
   - Applies **ML siren advantage** (falls back to heuristic if the model isn’t available).
   - Publishes:
      - **Route analysis** (decision, ETAs, congestion) → `tb_route_analysis`
      - **Chosen route segments** (decoded points) → `tb_route_segments`
      - Starts **ETA-paced telemetry** → `tb_vehicles_telemetry`
      - Sends **SMS** with **Google Static Map** to the configured number.  
4. Watch:
   - Eventhouse tables fill (**analysis**, **segments**, **telemetry**).
   - Power BI map shows **route** + **moving vehicle**.
   - SMS map link opens the correct static map.
---

### 10) Open the Power BI report

- Open the report from the workspace and ensure the semantic model connects to the **gold** function/view.
- Icon map setup:
  - **Layer 1 (Line)** → bind the **WKT `LINESTRING`** column (route).
  - **Layer 2 (Icon)** → bind **latitude/longitude** (vehicle) and icon URL (ambulance).
- Filter by `route_id` / `vehicle_id` to demo a mission.

---

### 11) Twilio check (SMS)

- Verify Twilio receiver (a virtual number is fine).
- Confirm the **SMS** arrives with a **clickable map URL**.

---

> After these steps, your **HERO** system should be operational. 🎉

---

#### Notes

- The repo includes **silver** transforms and **gold** functions to power the report.
- If you change EventStream names or table names, update the variable wiring and sinks accordingly.
- For production, schedule the **ML data prep** notebook (daily) and re-train periodically if desired; 


---
## ⚙️ Notable implementation details

- **Congestion extraction** requires adding this to the request body:
  ```json
  {
    "extraComputations": "TRAFFIC_ON_POLYLINE"
  }

  
- And **field mask** includes:
  ```http
  {
  POST https://routes.googleapis.com/directions/v2:computeRoutes
  Headers:
  Content-Type: application/json
  X-Goog-Api-Key: <YOUR_API_KEY>
  X-Goog-FieldMask: routes.duration,
                    routes.distanceMeters,
                    routes.polyline.encodedPolyline,
                    routes.legs.travelAdvisory.speedReadingIntervals.startPolylinePointIndex,
                    routes.legs.travelAdvisory.speedReadingIntervals.endPolylinePointIndex,
                    routes.legs.travelAdvisory.speedReadingIntervals.speed
  }

- **ML live scoring**:
  - Loads the latest registered model via MLflow
  - Input schema: [congestion_score, eta_theoretical_min, distance_m_theoretical, hour_of_day, dow, avg_speed_kmh, telemetry_points]
  - If validation/predict fails → heuristic fallback
- **Telemetry**:
  - Currently simulated, with intervals derived from chosen ETA
  - Emits progress_pct and status (arrived on last point)
 
## Known limitations

- **Machine Learning model**: currently trained on dummy data for this POC. The solution will improve as real ambulance telemetry is collected over time.
- **Telemetry simulation**: vehicle telemetry is simulated; future versions will connect to real fleet tracking or IoT systems.
- **Average speed input**: avg_speed_kmh is a fixed placeholder (50 km/h). Future releases will derive it from TomTom APIs or historical segment data.
- **Notebook & UDFs**: assume polyline decoding and ETA fields are always available. Production deployments must handle Google API quotas and errors.
- **KQL materialization**: materialized views have functional limits; some aggregations are implemented as functions with update policies instead.

## Future Roadmap

- **Real telemetry integration** connect directly to live ambulance GPS and IoT data streams.
- **Enhanced ML model**: retrain continuously on real missions to improve siren advantage accuracy.
- **Traffic intelligence**: integrate TomTom data for real-time speed estimation and congestion prediction.
- **Predictive dispatching**: use historical response data to anticipate optimal vehicle allocation before incidents occur.
- **Mobile integration**: deliver route guidance and alerts directly to drivers’ tablets or onboard systems.
- **Mid-route rerouting**: dynamically adjust paths based on evolving traffic and mission priorities.
- **Helicopter & multi-mode support**: extend routing to include air ambulances and hybrid transport chains.
- **Weather awareness**: incorporate live weather conditions to adjust ETA predictions and routing safety specially for helicopters.

