from dataclasses import dataclass
from typing import List

@dataclass
class FeatureEngineConfig:
    # === SMC CONFLUENCE WEIGHTS (dostrojeone po teście) ===
    weight_order_block: float = 0.28      # podniesione – OB jest kluczowe
    weight_fvg: float = 0.22
    weight_liquidity_sweep: float = 0.23  # podniesione – liquidity jest silne
    weight_bos_choch: float = 0.17
    weight_market_structure: float = 0.10

    # === THRESHOLDS ===
    confluence_min_for_bias: float = 55.0
    confluence_min_for_entry: float = 75.0   # powrót do wyższego, ale nie 82.0

    # === FILTERS ===
    use_session_filter: bool = True
    allowed_sessions: List[str] = None

    smc_detectors_path: str = "engine/smc"

    def __post_init__(self):
        if self.allowed_sessions is None:
            self.allowed_sessions = ["london", "newyork", "tokyo"]
