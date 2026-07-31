# GitHub — Claude Desktop Extension

Gibt Claude Zugriff auf Repositories auf github.com: anlegen, beschreiben,
durchsuchen, Code lesen, ändern, committen, löschen.

Der Schwerpunkt liegt bewusst auf **Repository- und Code-Verwaltung**. Issues,
Pull Requests, Actions und Releases sind nicht enthalten — sie lassen sich
später als eigene Tools ergänzen, ohne das Paket umzubauen.

Antworten werden gekürzt ausgeliefert: ein einzelnes Repository-Objekt der
GitHub-API hat über hundert Felder, von denen eine Handvoll interessant ist.
Binärdateien kommen mit Größenangabe statt Inhalt zurück.

## Voraussetzung: uv

Wie die Kontakte-Extension braucht diese hier **[uv](https://docs.astral.sh/uv/)**
auf dem Zielrechner. uv besorgt beim ersten Start Python und die Abhängigkeiten
(`mcp`, `httpx`) selbst — deshalb bleibt das Paket wenige KB groß.

```powershell
winget install --id=astral-sh.uv -e
```

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Danach Claude Desktop **neu starten**, damit die App das aktualisierte PATH sieht.
Prüfen mit `uv --version`.

## Voraussetzung: Token

GitHub → Settings → Developer settings → Personal access tokens.

**Klassisches Token** (der einfachere Weg):

| Bereich | wofür |
|---|---|
| `repo` | alles Lesen und Schreiben in Repositories, auch privaten — Pflicht |
| `workflow` | nur nötig, wenn Dateien unter `.github/workflows/` geschrieben werden sollen |
| `delete_repo` | nur nötig, wenn ganze Repositories gelöscht werden sollen |

**Fine-grained Token** (feiner, aber pro Repository bzw. Organisation zu pflegen):

| Berechtigung | Stufe |
|---|---|
| Metadata | Read (setzt GitHub ohnehin voraus) |
| Contents | Read and write |
| Administration | Read and write — nur für `create_repo`, `update_repo`, `delete_repo` |

Ohne `Administration` funktionieren Lesen und Schreiben von Code weiterhin; nur
das Anlegen und Ändern von Repositories schlägt dann mit einem 403 fehl.

Fine-grained Tokens haben ein Ablaufdatum. Läuft es ab, meldet jede Anfrage
einen 401 — dann ein neues Token erzeugen und in den Einstellungen ersetzen.

## Installation

1. `github-1.0.0.mcpb` auf den Zielrechner kopieren.
2. Claude Desktop → Einstellungen → Extensions → **Erweiterte Einstellungen** →
   *Extension installieren…* → die `.mcpb`-Datei auswählen.
3. In den Einstellungen der Extension ausfüllen:
   - **GitHub-Token** — das Token von oben. Landet im Schlüsselspeicher des
     Betriebssystems, nicht im Paket.
   - **Standard-Konto oder Organisation** — optional. Ist hier z. B. `meine-firma`
     eingetragen, meint „das Repo `webshop`" automatisch `meine-firma/webshop`.
     Leer lassen für das Konto, dem das Token gehört. Ein ausgeschriebenes
     `besitzer/name` schlägt diese Vorgabe immer.
   - **Neue Repositories privat anlegen** — an lassen, außer öffentliche Repos
     sind der Normalfall.
   - **Repositories löschen erlauben** — aus lassen. Nur einschalten, wenn
     wirklich gelöscht werden soll.
4. Extension aktivieren und Claude fragen, z. B. *„welche Repos habe ich?"*

Läuft etwas nicht, ist `check_connection` das erste Mittel: es sagt, wem das
Token gehört, welche Bereiche es hat und wie viel API-Kontingent übrig ist.

## Wenn etwas nicht läuft

**„Server disconnected", im Log `ModuleNotFoundError: No module named '_win32sysloader'`**

Die uv-Umgebung ist unvollständig gebaut worden. `mcp` zieht auf Windows
`pywin32` mit; startet Claude Desktop den Server beim allerersten Mal mehrfach
gleichzeitig, können sich die parallelen uv-Prozesse beim Anlegen derselben
Cache-Umgebung in die Quere kommen — im `win32`-Ordner fehlen dann sämtliche
`.pyd`-Dateien. Betroffen ist nur der Kaltstart einer neuen Umgebung, also nach
Installation oder Versionswechsel.

Umgebung wegwerfen und einmal von Hand neu bauen lassen:

```powershell
Get-ChildItem "$env:LOCALAPPDATA\uv\cache\environments-v2" -Directory | Where-Object { -not (Test-Path "$($_.FullName)\Lib\site-packages\win32\_win32sysloader.pyd") -and (Test-Path "$($_.FullName)\Lib\site-packages\win32") } | Remove-Item -Recurse -Force
```

```powershell
uv run --script "$env:APPDATA\Claude\Claude Extensions\local.mcpb.thomas-weirich.github\server\server.py"
```

Der zweite Befehl baut die Umgebung neu auf und bleibt dann stumm stehen — das
ist der auf stdin wartende Server, mit Strg+C beenden. Danach die Extension in
Claude Desktop aus- und wieder einschalten.

**„Bad credentials" / 401** — Token abgelaufen oder falsch eingefügt. Fine-grained
Tokens haben ein Ablaufdatum.

**403 beim Anlegen oder Ändern eines Repositories** — dem fine-grained Token
fehlt `Administration: Read and write`. Lesen und Schreiben von Code geht davon
unabhängig weiter.

**403 beim Schreiben unter `.github/workflows/`** — dem klassischen Token fehlt
der Bereich `workflow`.

## Tools

**Verbindung** — `check_connection`

**Repositories** — `list_repos`, `get_repo`, `search_repos`, `create_repo`,
`update_repo`, `delete_repo`

**Lesen** — `list_files`, `read_file`, `get_readme`, `search_code`

**Schreiben** — `write_file`, `delete_file`, `push_files`

**Branches & Historie** — `list_branches`, `create_branch`, `delete_branch`,
`list_commits`, `get_commit`

Für mehrere Dateien auf einmal ist `push_files` das richtige Werkzeug: ein
Commit für alles, statt einer pro Datei. Existiert der angegebene Branch noch
nicht, wird er dabei gleich mit angelegt — und in einem frisch erstellten, noch
leeren Repository schreibt derselbe Aufruf den allerersten Commit.

## Was die Extension nicht tut

- Kein Merge, keine Pull Requests, keine Issues, keine Workflow-Läufe.
- Kein `git`-Client: es wird nichts lokal ausgecheckt, alles läuft über die
  REST-API. Große Repositories vollständig zu durchsuchen ist entsprechend teuer.
- Kein Umgehen von Branch-Protection. Ist der Standard-Branch geschützt,
  scheitert der direkte Schreibzugriff — dann auf einem eigenen Branch arbeiten.

## Aufbau

```
manifest.json     Metadaten, user_config, Startbefehl
server/server.py  der Server; Abhängigkeiten als PEP-723-Header in der Datei
icon.png
```

## Neu bauen

```bash
npx @anthropic-ai/mcpb pack . github-1.0.0.mcpb
```

Danach `version` in `manifest.json` erhöhen und den Dateinamen mitziehen.

## Sicherheit

Der Server läuft lokal und spricht ausschließlich mit `api.github.com`. Im
`.mcpb` steht kein Token — es wird bei der Installation abgefragt und von Claude
Desktop im Schlüsselspeicher des Betriebssystems abgelegt. Die Datei kann also
ohne Bedenken weitergegeben werden.

Die Extension hat **vollen Schreibzugriff** auf alles, was das Token sehen kann.
Geschriebenes landet sofort im echten Repository; ein Staging oder ein
Rückgängig gibt es nicht. Zwei Bremsen sind eingebaut:

- Repositories löschen ist ab Werk gesperrt und muss in den Einstellungen
  freigeschaltet werden. Zusätzlich muss der Repository-Name beim Aufruf
  wörtlich bestätigt werden.
- Neue Repositories sind voreingestellt privat.

Wer das Risiko kleiner halten will, gibt einem fine-grained Token nur die
Repositories, an denen Claude wirklich arbeiten soll.
