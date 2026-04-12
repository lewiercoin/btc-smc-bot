from dataclasses import dataclass
from typing import List

@dataclass
class FeatureEngineConfig:
    # === SMC CONFLUENCE WEIGHTS ===
    weight_order_block: float = 0.25
    weight_fvg: float = 0.22
    weight_liquidity_sweep: float = 0.20
    weight_bos_choch: float = 0.18
    weight_market_structure: float = 0.15
    
    # === THRESHOLDS ===
    confluence_min_for_bias: float = 65.0      # 0-100
    confluence_min_for_entry: float = 82.0
    
    # === FILTERS ===
    use_session_filter: bool = True
    allowed_sessions: List[str] = None  # None = wszystkie, lub ["london", "ny"]
    
    # === DETEKTORY (ścieżki względne) ===
    smc_detectors_path: str = "engine/smc"
    
    def __post_init__(self):
        if self.allowed_sessions is None:
            self.allowed_sessions = ["london", "newyork", "tokyo"]
