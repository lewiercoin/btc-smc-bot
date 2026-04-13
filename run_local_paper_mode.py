import time
from main import _setup_signal_generator
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

print("=== BTC-SMC-BOT LOCAL PAPER MODE ===")
print("Czysta strategia SMC-only | threshold = 75.0")
print("Naciśnij Ctrl+C aby zatrzymać\n")

gen, db = _setup_signal_generator("paper")

try:
    counter = 0
    while True:
        counter += 1
        print(f"\n--- SCAN #{counter} ---")
        signals = gen.generate()
        
        if signals:
            for sig in signals:
                print(f"🚀 SYGNAŁ SMC-ONLY → {sig['type']} | Confluence: {sig['confluence_score']:.1f}")
        else:
            print("⏳ Brak sygnału (confluence < 75.0)")
        
        print(f"Cena: {gen._get_current_snapshot().get('current_price', 'N/A')}")
        time.sleep(60)  # co 60 sekund (możesz zmienić na 30 lub 120)
except KeyboardInterrupt:
    print("\n\n🛑 Paper Mode zatrzymany ręcznie.")
