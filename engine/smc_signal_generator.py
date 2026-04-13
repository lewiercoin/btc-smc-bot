from typing import Dict, Any, List
from engine.smc_signal_engine import SMCSignalEngine
from engine.config.feature_engine_config import FeatureEngineConfig
import time

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
        '''PEŁNY snapshot H1 dla detektorów SMC – tylko lokalnie'''
        try:
            candles = self.binance_client.get_candles(
                instrument="BTCUSDT",
                granularity="1h",
                count=100
            )
            
            if not candles or len(candles) < 5:
                raise ValueError("Brak danych z Binance")

            current_candle = candles[-1]
            current_price = current_candle.close
            timestamp = int(current_candle.timestamp.timestamp())

            snapshot = {
                "timestamp": timestamp,
                "current_price": current_price,
                "candles": candles,
                "open": current_candle.open,
                "high": current_candle.high,
                "low": current_candle.low,
                "close": current_price,
                "volume": current_candle.volume,
                "symbol": "BTCUSDT",
                "interval": "1h"
            }
            print(f"✅ Snapshot H1 pobrany lokalnie – {len(candles)} świec | cena: {current_price:.2f}")
            return snapshot

        except Exception as e:
            print(f"⚠️ Błąd snapshotu (lokalny): {e}")
            return {"timestamp": int(time.time()), "error": str(e)}

    def get_bias(self, snapshot: Dict[str, Any] = None) -> str:
        if snapshot is None:
            snapshot = self._get_current_snapshot()
        return self.smc_engine.get_bias(snapshot)
