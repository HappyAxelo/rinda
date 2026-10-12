"""mhf -- Malaria Habitat Forecast.

Forecasting Anopheles-suitable breeding-habitat formation 7-14 days ahead from
multi-source Earth observation, meteorological and IoT data.

Design rules enforced throughout:
  1. No predictor may use information from after the prediction date t.
  2. No random split is used for a reported result.
  3. Absent data sources are skipped and reported, never imputed.
  4. Claim language is tied to the target actually modelled.
"""
__version__ = "0.1.0"

from .config import load_config, Config           # noqa: F401
