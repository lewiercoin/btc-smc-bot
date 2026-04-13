from typing import Dict, Any, List
from engine.smc_signal_engine import SMCSignalEngine
from engine.config.feature_engine_config import FeatureEngineConfig

class SignalGenerator:
    '''Drop-in replacement dla starego SignalGenerator – używa czystej SMC-only'''
    def __init__(
        self,
        binance_client,
        news_client=None,           # ignorujemy news (nie używamy w SMC-only)
        db=None,                    # zostawiamy referencję na przyszłość
        confluence_threshold: int = 65,
        max_risk_pct: float = 0.01,
        max_leverage: int = 3,
        **kwargs
    ):
        # Tworzymy config SMC-only
        self.config = FeatureEngineConfig(
            confluence_min_for_bias=float(confluence_threshold),
            confluence_min_for_entry=float(confluence_threshold) + 17,  # 82 jak w Phase 2
        )
        
        self.smc_engine = SMCSignalEngine(self.config)
        self.binance_client = binance_client
        self.db = db
        self.max_risk_pct = max_risk_pct
        self.max_leverage = max_leverage
        
        print('✅ SMC-only SignalGenerator initialized (drop-in replacement)')

    def generate(self) -> List[Dict[str, Any]]:
        '''Kompatybilna metoda – dokładnie tak jak stary SignalGenerator'''
        snapshot = self._get_current_snapshot()
        return self.smc_engine.generate_signals(snapshot)

    def _get_current_snapshot(self) -> Dict[str, Any]:
        '''Pobiera aktualny snapshot z Binance (minimalna implementacja)'''
        # TODO: w następnym handoffie rozwiniesz do pełnego snapshotu
        # na razie placeholder – zwróci pusty dict (testowy)
        return {'timestamp': None}

    def get_bias(self, snapshot: Dict[str, Any] = None) -> str:
        if snapshot is None:
            snapshot = self._get_current_snapshot()
        return self.smc_engine.get_bias(snapshot)
