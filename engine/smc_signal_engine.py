from typing import Dict, Any, List
from engine.config.feature_engine_config import FeatureEngineConfig
from engine.smc_feature_engine import SMCFeatureEngine

class SMCSignalEngine:
    def __init__(self, config: FeatureEngineConfig):
        self.config = config
        self.feature_engine = SMCFeatureEngine(config)
        print("✅ SMCSignalEngine initialized (SMC-only)")

    def generate_signals(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        features = self.feature_engine.extract_features(snapshot)
        
        confluence = features.get("confluence_score", 0.0)
        bias = features.get("bias", "NEUTRAL")
        entry_ready = features.get("entry_ready", False)

        print(f"DEBUG SIGNAL → confluence={confluence:.1f} | bias={bias} | entry_ready={entry_ready}")

        signals = []
        
        # Poluzowana logika – wystarczy confluence powyżej min_for_entry
        if confluence >= self.config.confluence_min_for_entry:
            signal_type = "LONG" if bias == "LONG" else "SHORT" if bias == "SHORT" else None
            if signal_type:
                signals.append({
                    "type": signal_type,
                    "confluence_score": confluence,
                    "bias": bias,
                    "entry_ready": True,
                    "features": features,
                    "timestamp": snapshot.get("timestamp"),
                    "strategy": "SMC-ONLY"
                })
                print(f"✅ GENERUJE SYGNAŁ {signal_type} (confluence {confluence:.1f})")
            else:
                print("⚠️ Bias NEUTRAL – pomijam")
        else:
            print("❌ Confluence za niski")

        return signals

    def get_bias(self, snapshot: Dict[str, Any] = None) -> str:
        if snapshot is None:
            snapshot = {}  # placeholder
        features = self.feature_engine.extract_features(snapshot)
        return features.get("bias", "NEUTRAL")
