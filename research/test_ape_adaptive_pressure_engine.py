"""Small deterministic tests for the research-only APE feature builder."""
import unittest
import numpy as np
import pandas as pd
from research.ape_adaptive_pressure_engine import build_features, event_study

class APETests(unittest.TestCase):
    def setUp(self):
        n=1200
        rng=np.random.default_rng(17)
        # Synthetic trending OHLCV is only a software test, not a profitability test.
        close=100*np.exp(np.cumsum(rng.normal(0.0003,0.004,n)))
        open_=np.r_[close[0],close[:-1]]
        spread=np.maximum(close*0.002,0.001)
        high=np.maximum(open_,close)+spread*rng.uniform(0.2,1.0,n)
        low=np.minimum(open_,close)-spread*rng.uniform(0.2,1.0,n)
        self.data=pd.DataFrame({
          "timestamp":pd.date_range("2025-01-01",periods=n,freq="15min",tz="UTC"),
          "open":open_,"high":high,"low":low,"close":close,
          "volume":rng.lognormal(5,0.5,n)
        })

    def test_features_are_bounded_and_finite_after_warmup(self):
        x=build_features(self.data)
        valid=x["directional_efficiency"].dropna()
        self.assertTrue(((valid>=0)&(valid<=1)).all())
        self.assertTrue((x["relative_volume"].dropna()>=0).all())
        self.assertIn("ape_long_candidate",x.columns)
        self.assertIn("ape_short_candidate",x.columns)

    def test_event_study_schema_and_cost_sensitivity(self):
        x=build_features(self.data)
        cheap=event_study(x,horizon=12,cost_bps_round_trip=0)
        costly=event_study(x,horizon=12,cost_bps_round_trip=30)
        self.assertEqual(len(cheap["results"]),4)
        self.assertEqual(len(costly["results"]),4)
        for a,b in zip(cheap["results"],costly["results"]):
            if a["n"] and b["n"]:
                self.assertLessEqual(b["mean_net_return_pct"],a["mean_net_return_pct"])

if __name__=="__main__":
    unittest.main()
