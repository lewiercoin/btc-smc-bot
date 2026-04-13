from engine import create_signal_engine
from engine.config.feature_engine_config import FeatureEngineConfig

config = FeatureEngineConfig()
engine = create_signal_engine(config)
print('✅ SMC-only engine initialized successfully')
print('Confluence weights:', {
    'ob': config.weight_order_block,
    'fvg': config.weight_fvg,
    'liq': config.weight_liquidity_sweep
})
