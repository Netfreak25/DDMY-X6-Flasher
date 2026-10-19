# X6 Recovery: ADFU bei schwarzem Display oder Bootschleife

1. USB abziehen.
2. Power drücken und gedrückt halten.
3. Zusätzlich Reset drücken und gedrückt halten.
4. USB anschließen, während **Power UND Reset gehalten bleiben**.
5. Nach der USB-Anmeldung beide loslassen.

Dieser Ablauf wurde am 19.10.2026 am X6 bestätigt. Es wurde keine exakte
Haltezeit gemessen. Reset vor dem Einstecken loszulassen ist nicht der
bestätigte Ablauf.

Windows erkennt ADFU als `10D6:10D6`; für diese Gerätekennung muss WinUSB
installiert sein. Der normale USB-Modus ist `10D6:1101`. Eine ADFU-Verbindung
ermöglicht die Wiederherstellung, repariert aber nicht selbst die Firmware.

„Player neustarten“ versucht den Software-Reset und prüft die erneute
Anmeldung im Normalmodus. Bestätigter Reset und erfolgreicher Start sind
unterschiedliche Ergebnisse. Bei ausgefallener Kommunikation ist der oben
beschriebene physische Einstieg erforderlich.
