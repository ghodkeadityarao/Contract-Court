"""A small local warehouse shaped like samples.wanderbricks, for tests and offline development.

The Databricks run introspects the REAL tables instead; this only exists so the pipeline can be
tested on a laptop. Nothing in the engine depends on these names.
"""
import duckdb
import numpy as np
import pandas as pd


def make(n: int = 400, seed: int = 7):
    rng = np.random.default_rng(seed)
    ts = lambda k: pd.date_range("2025-01-01", periods=k, freq="6h").astype(str)  # noqa: E731
    frames = {
        "users": pd.DataFrame({"user_id": np.arange(1, 91), "country": rng.choice(["US", "DE", "IN", "BR"], 90),
                               "user_type": rng.choice(["guest", "host"], 90), "email": [f"u{i}@x.com" for i in range(90)]}),
        "properties": pd.DataFrame({"property_id": np.arange(1, 41), "property_type": rng.choice(["flat", "house", "cabin"], 40),
                                    "base_price": rng.gamma(4, 40, 40).round(2), "host_id": rng.integers(1, 15, 40)}),
        "bookings": pd.DataFrame({"booking_id": np.arange(1, n + 1), "user_id": rng.integers(1, 91, n),
                                  "property_id": rng.integers(1, 41, n), "total_amount": rng.gamma(4, 120, n).round(2),
                                  "status": rng.choice(["confirmed", "cancelled", "pending"], n, p=[.7, .2, .1]),
                                  "created_at": ts(n)}),
        "payments": pd.DataFrame({"payment_id": np.arange(1, n + 1), "booking_id": np.arange(1, n + 1),
                                  "amount": rng.gamma(4, 110, n).round(2), "payment_method": rng.choice(["card", "paypal"], n),
                                  "paid_at": ts(n)}),
        "reviews": pd.DataFrame({"review_id": np.arange(1, 200), "property_id": rng.integers(1, 41, 199),
                                 "user_id": rng.integers(1, 91, 199), "rating": rng.integers(1, 6, 199),
                                 "created_at": ts(199)}),
    }
    con = duckdb.connect()
    for name, df in frames.items():
        con.register("df_" + name, df)
        con.execute(f"CREATE TABLE {name}__orig AS SELECT * FROM df_{name}")
    return con
