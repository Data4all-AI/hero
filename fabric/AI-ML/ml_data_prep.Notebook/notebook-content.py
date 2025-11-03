# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
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
# META     }
# META   }
# META }

# CELL ********************

from pyspark.sql import functions as F
from pyspark.sql import Window as W

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from pyspark.sql import functions as F

# --- Load data ---
dec = spark.table("lakehouse.dbo.tb_route_analysis_silver")
tel = spark.table("lakehouse.dbo.tb_vehicles_telemetry_silver")

# --- Compute actual ETA from telemetry ---
tel_agg = (tel
    .groupBy("route_id")
    .agg(
        F.min("timestamp").alias("t_start"),
        F.max("timestamp").alias("t_end"),
        F.avg("speed_kmh").alias("avg_speed_kmh"),
        F.count("*").alias("telemetry_points")
    )
    .withColumn("actual_eta_min", 
                (F.unix_timestamp("t_end") - F.unix_timestamp("t_start")) / 60.0)
    .filter(F.col("actual_eta_min") > 0)
)

# --- Join with route analysis ---
df = (dec
    .join(tel_agg, on="route_id", how="inner")
    .withColumn("analysis_ts", F.col("timestamp").cast("timestamp"))
    .withColumn("hour_of_day", F.hour("analysis_ts"))
    .withColumn("dow", F.dayofweek("analysis_ts"))
    .withColumn("is_weekend", F.col("dow").isin(1,7))
)

# --- Compute target (REAL siren advantage) ---
df = df.withColumn(
    "siren_advantage_real",
    F.round(
        (F.col("eta_theoretical_min") - F.col("actual_eta_min")) /
        F.col("eta_theoretical_min"), 4
    )
)

# --- Cleaning ---
df = df.filter(
    (F.col("siren_advantage_real").isNotNull()) &
    (F.col("siren_advantage_real") < 1.0) &  # remove outliers
    (F.col("siren_advantage_real") > -0.5)
)

# --- Features available before dispatch to avoid leakage ---
features = [
    "congestion_score",
    "eta_theoretical_min",
    "distance_m_theoretical",
    "hour_of_day",
    "dow",
    "is_weekend",
    "avg_speed_kmh",
    "telemetry_points"
]

df_train = df.select("route_id", *features, "siren_advantage_real")

df_train_casted = (df_train
    .withColumn("congestion_score", F.col("congestion_score").cast(DoubleType()))
    .withColumn("eta_theoretical_min", F.col("eta_theoretical_min").cast(DoubleType()))
    .withColumn("distance_m_theoretical", F.col("distance_m_theoretical").cast(DoubleType()))
    .withColumn("hour_of_day", F.col("hour_of_day").cast(IntegerType()))
    .withColumn("dow", F.col("dow").cast(IntegerType()))
    .withColumn("is_weekend", F.col("is_weekend").cast(BooleanType()))
    .withColumn("avg_speed_kmh", F.col("avg_speed_kmh").cast(DoubleType()))
    .withColumn("telemetry_points", F.col("telemetry_points").cast(IntegerType()))
    .withColumn("siren_advantage_real", F.col("siren_advantage_real").cast(DoubleType()))
)


df_train.createOrReplaceTempView("vw_siren_advantage_source")



# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# --- Save incrementally for AutoML ---
df_train.createOrReplaceTempView("vw_siren_advantage_source")
spark.sql("""
MERGE INTO lakehouse.dbo.ml_siren_advantage_regression as t
USING vw_siren_advantage_source as s
ON s.route_id = t.route_id
WHEN NOT MATCHED THEN INSERT *
""")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
