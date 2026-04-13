from engine.config.feature_engine_config import FeatureEngineConfig
from engine.smc_signal_engine import SMCSignalEngine

__all__ = ['SMCSignalEngine', 'FeatureEngineConfig']

def create_signal_engine(config: FeatureEngineConfig = None):
    '''Jedyny punkt wejścia do strategii SMC-only'''
    if config is None:
        config = FeatureEngineConfig()
    return SMCSignalEngine(config)
