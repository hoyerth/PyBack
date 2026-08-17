# main.py
"""
main.py - Einstiegspunkt fuer PyBack (MT5 Backtest Framework).

Startet das Hauptfenster (main_win.MainWin), das beim Oeffnen automatisch
die MT5-Verbindung prueft und ein sofortiges Marktdaten-Update ausloest.
Ein "Scan"-Button ermoeglicht die manuelle erneute Synchronisation.
"""

from main_win import main

if __name__ == "__main__":
    main()
