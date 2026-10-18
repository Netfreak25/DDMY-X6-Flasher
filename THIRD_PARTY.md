# Abhängigkeiten und Hinweise

## fflate

ZIP-Verarbeitung: fflate 0.8.3 von 101arrowz.
Die mitgelieferte MIT-Lizenz liegt unter [vendor/fflate.LICENSE](vendor/fflate.LICENSE).

## Actions-Protokoll

Die Umsetzung basiert auf dem Actions-Protokoll und Boot-Leseablauf von
[ilyakurdyukov/actions_flash](https://github.com/ilyakurdyukov/actions_flash),
Commit `0a7d9cf6`, sowie dem bestehenden X6-Toolkit.
Der Haftungshinweis aus der ursprünglichen C-Quelle lautet:

> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
> OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
> THE SOFTWARE.

Die Windows-Release enthält die sechs im Geräteprofil per
SHA-256 festgelegten generischen Hilfsdateien. Darunter sind der X6-NAND-Treiber
und dessen Konfiguration; es wird keine vollständige Firmware verteilt.

Ab Beta 6 enthält die App zusätzlich unseren eigenen RAM-Ausschaltcode aus
`tools/x6_poweroff.c`. Dieser wird mit ARM GCC gebaut, per SHA-256 geprüft und
greift nicht auf NAND oder SD zu. Quelltext und Linkerskript sind enthalten.
Ab Beta 7 wartet er vor den Ausschaltwerten auf das Entfernen der USB-Stromversorgung
und bedient dabei den Watchdog.
Unicorn wird ausschließlich für den ARM-Emulatortest verwendet und nicht mitgeliefert.

## Windows-Paket

Die portable App wird mit Python 3.11, Tcl/Tk und PyInstaller 6.22.3 gebaut.
Die jeweiligen Python- und Tcl/Tk-Lizenztexte sind der Windows-Release beigefügt.

Für Entwicklung und Tests werden ESLint und Playwright verwendet. Alle
Versionen stehen in `package-lock.json`; sie sind keine Browser-CDN-Abhängigkeiten.
