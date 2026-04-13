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
        
        detector_classes = {
            'swing': ('swing_detector.py', 'SwingDetector'),
            'structure': ('structure_analyzer.py', 'StructureAnalyzer'),
            'ob': ('ob_detector.py', 'OrderBlockDetector'),
            'fvg': ('fvg_detector.py', 'FairValueGapDetector'),
            'liquidity': ('liquidity_detector.py', 'LiquidityDetector'),
        }
        
        for name, (filename, class_name) in detector_classes.items():
            file_path = smc_path / filename
            if file_path.exists():
                spec = importlib.util.spec_from_file_location(name, file_path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                
                # Tworzymy instancję klasy detektora
                detector_class = getattr(module, class_name)
                detectors[name] = detector_class()
                print(f'✅ Loaded detector: {filename} → {class_name}')
            else:
                print(f'⚠️  Missing detector: {filename}')
        
        return detectors

    def extract_features(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Główna metoda – zwraca wszystkie SMC features + confluence"""
        features = {}
        candles = snapshot.get('candles', [])
        
        if not candles or len(candles) < 14:
            # Za mało danych - zwracamy puste features
            features['ob_strength'] = 0.0
            features['ob_mitigated'] = True
            features['fvg_score'] = 0.0
            features['fvg_type'] = None
            features['liquidity_sweep_score'] = 0.0
            features['sweep_reclaimed'] = False
            features['structure_score'] = 0.0
            features['bos_choch'] = None
            features['market_structure_score'] = 0.0
            
            confluence_result = self.confluence.calculate_confluence(features)
            features.update(confluence_result)
            return features
        
        # Krok 1: Swing detection (podstawa dla wszystkich innych detektorów)
        swings = None
        if 'swing' in self.detectors:
            swings = self.detectors['swing'].detect(candles)
            features['market_structure_score'] = 0.5 if swings else 0.0
        
        # Krok 2: Structure analysis (BOS/CHOCH)
        structure = None
        if 'structure' in self.detectors and swings:
            structure = self.detectors['structure'].analyze(swings)
            features['structure_score'] = 0.7 if structure and (structure.bos or structure.choch) else 0.0
            features['bos_choch'] = 'BOS' if structure and structure.bos else 'CHOCH' if structure and structure.choch else None
        else:
            features['structure_score'] = 0.0
            features['bos_choch'] = None
        
        # Krok 3: Order Block detection
        if 'ob' in self.detectors and swings:
            ob_list = self.detectors['ob'].detect(candles, swings)
            features['ob_strength'] = min(1.0, len(ob_list) * 0.3) if ob_list else 0.0
            features['ob_mitigated'] = len(ob_list) == 0 or not any(ob.is_valid for ob in ob_list)
        else:
            features['ob_strength'] = 0.0
            features['ob_mitigated'] = True
        
        # Krok 4: FVG detection
        if 'fvg' in self.detectors:
            fvg_list = self.detectors['fvg'].detect(candles, structure)
            features['fvg_score'] = min(1.0, len(fvg_list) * 0.4) if fvg_list else 0.0
            features['fvg_type'] = 'bullish' if fvg_list and fvg_list[0].direction == 'bullish' else 'bearish' if fvg_list else None
        else:
            features['fvg_score'] = 0.0
            features['fvg_type'] = None
        
        # Krok 5: Liquidity Sweep detection
        if 'liquidity' in self.detectors and swings:
            sweep_list = self.detectors['liquidity'].detect(candles, swings)
            features['liquidity_sweep_score'] = min(1.0, len(sweep_list) * 0.35) if sweep_list else 0.0
            features['sweep_reclaimed'] = len(sweep_list) > 0 and any(sweep.reclaimed for sweep in sweep_list)
        else:
            features['liquidity_sweep_score'] = 0.0
            features['sweep_reclaimed'] = False
        
        # Confluence
        confluence_result = self.confluence.calculate_confluence(features)
        features.update(confluence_result)
        
        return features
