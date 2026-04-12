from typing import Dict, Any
import importlib.util
import sys
from pathlib import Path

from engine.config.feature_engine_config import FeatureEngineConfig
from engine.smc_confluence import SMCConfluence

class SMCFeatureEngine:
    def __init__(self, config: FeatureEngineConfig):
        self.config = config
        self.confluence = SMCConfluence(config)
        self.detectors = self._load_detectors()

    def _load_detectors(self) -> Dict[str, Any]:
        """Dynamicznie ładuje wszystkie detektory z engine/smc/"""
        detectors = {}
        smc_path = Path(self.config.smc_detectors_path)
        
        detector_files = {
            'ob': 'ob_detector.py',
            'fvg': 'fvg_detector.py',
            'liquidity': 'liquidity_detector.py',
            'structure': 'structure_analyzer.py',
            'swing': 'swing_detector.py',
            'utils': 'utils.py'
        }
        
        for name, filename in detector_files.items():
            file_path = smc_path / filename
            if file_path.exists():
                spec = importlib.util.spec_from_file_location(name, file_path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                detectors[name] = module
                print(f'✅ Loaded detector: {filename}')
            else:
                print(f'⚠️  Missing detector: {filename}')
        
        return detectors

    def extract_features(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Główna metoda – zwraca wszystkie SMC features + confluence"""
        features = {}
        
        # Order Block
        if 'ob' in self.detectors:
            ob_result = self.detectors['ob'].detect_order_blocks(snapshot)
            features['ob_strength'] = ob_result.get('strength', 0.0)
            features['ob_mitigated'] = ob_result.get('mitigated', True)
        
        # FVG
        if 'fvg' in self.detectors:
            fvg_result = self.detectors['fvg'].detect_fvg(snapshot)
            features['fvg_score'] = fvg_result.get('score', 0.0)
            features['fvg_type'] = fvg_result.get('type', None)
        
        # Liquidity Sweep
        if 'liquidity' in self.detectors:
            liq_result = self.detectors['liquidity'].detect_liquidity_sweep(snapshot)
            features['liquidity_sweep_score'] = liq_result.get('score', 0.0)
            features['sweep_reclaimed'] = liq_result.get('reclaimed', False)
        
        # Structure (BOS/CHOCH)
        if 'structure' in self.detectors:
            struct_result = self.detectors['structure'].analyze_structure(snapshot)
            features['structure_score'] = struct_result.get('score', 0.0)
            features['bos_choch'] = struct_result.get('bos_choch', None)
        
        # Market Structure + Swing
        if 'swing' in self.detectors:
            swing_result = self.detectors['swing'].detect_swings(snapshot)
            features['market_structure_score'] = swing_result.get('structure_score', 0.0)
        
        # Confluence
        confluence_result = self.confluence.calculate_confluence(features)
        features.update(confluence_result)
        
        return features
