from typing import Dict, Any
from engine.config.feature_engine_config import FeatureEngineConfig

class SMCConfluence:
    def __init__(self, config: FeatureEngineConfig):
        self.config = config
        self.detectors = {}  # będzie wypełniane w feature_engine

    def calculate_confluence(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Zwraca confluence_score 0-100 + dict z breakdownem"""
        score = 0.0
        breakdown = {}

        # Order Block
        ob_score = snapshot.get('ob_strength', 0) * self.config.weight_order_block
        score += ob_score
        breakdown['order_block'] = round(ob_score, 2)

        # FVG
        fvg_score = snapshot.get('fvg_score', 0) * self.config.weight_fvg
        score += fvg_score
        breakdown['fvg'] = round(fvg_score, 2)

        # Liquidity Sweep + Reclaim
        liq_score = snapshot.get('liquidity_sweep_score', 0) * self.config.weight_liquidity_sweep
        score += liq_score
        breakdown['liquidity'] = round(liq_score, 2)

        # BOS / CHOCH
        structure_score = snapshot.get('structure_score', 0) * self.config.weight_bos_choch
        score += structure_score
        breakdown['structure'] = round(structure_score, 2)

        # Market Structure (HH/HL, LH/LL)
        ms_score = snapshot.get('market_structure_score', 0) * self.config.weight_market_structure
        score += ms_score
        breakdown['market_structure'] = round(ms_score, 2)

        final_score = min(100.0, max(0.0, score * 100))
        
        return {
            'confluence_score': round(final_score, 2),
            'breakdown': breakdown,
            'bias': 'LONG' if final_score >= self.config.confluence_min_for_bias else 'SHORT' if final_score >= 35 else 'NEUTRAL',
            'entry_ready': final_score >= self.config.confluence_min_for_entry
        }
