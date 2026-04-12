from typing import Dict, Any, List
from engine.config.feature_engine_config import FeatureEngineConfig
from engine.smc_feature_engine import SMCFeatureEngine

class SMCSignalEngine:
    def __init__(self, config: FeatureEngineConfig):
        self.config = config
        self.feature_engine = SMCFeatureEngine(config)
        print('✅ SMCSignalEngine initialized (SMC-only)')

    def generate_signals(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        '''Główna metoda – zwraca listę sygnałów (czysta SMC)'''
        features = self.feature_engine.extract_features(snapshot)
        
        signals = []
        
        confluence = features.get('confluence_score', 0)
        bias = features.get('bias', 'NEUTRAL')
        entry_ready = features.get('entry_ready', False)
        
        if confluence >= self.config.confluence_min_for_entry and entry_ready:
            signal_type = 'LONG' if bias == 'LONG' else 'SHORT'
            
            signals.append({
                'type': signal_type,
                'confluence_score': confluence,
                'bias': bias,
                'entry_ready': True,
                'features': features,           # pełny breakdown dla debug/research
                'timestamp': snapshot.get('timestamp'),
                'strategy': 'SMC-ONLY'
            })
        
        return signals

    def get_bias(self, snapshot: Dict[str, Any]) -> str:
        '''Szybki shortcut używany przez GovernanceLayer'''
        features = self.feature_engine.extract_features(snapshot)
        return features.get('bias', 'NEUTRAL')
