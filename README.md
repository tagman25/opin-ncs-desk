# OPIN NCS Console

A cross-platform Python GUI application to assist Net Control Station (NCS) operators running digital amateur radio nets that follow Black Swan / OPIN / QTC procedures (Z-codes, DRYAD authentication, ZKS lists, ZBO traffic tracking with flamp, etc.).

## Features (v1)

- Load a monthly DRYAD PDF and look up authentication responses (ZNB)
- Live monitor of FLDigi RX text via XML-RPC
- Ordered ZKS list of stations that check in with ZKE
- Logging of ZBO traffic
- Suggested responses that can be edited before sending (no automatic TX)
- Export of ICS-214 activity log
- NCS mode and basic Station View
- Session save/load as JSON

## Requirements

- Python 3.9 or newer
- FLDigi running (with XML-RPC enabled, default port 7362)
- flamp (for file tracking — currently monitored via its receive folder in later versions)

Install dependencies:

```bash
pip install -r requirements.txt
```

## Running

```bash
python main.py
```

1. Click **Load DRYAD PDF...** and select the current month's OPIN-DRYAD PDF.
2. Make sure FLDigi is running. The program will connect automatically.
3. As stations check in with ZKE and report traffic with ZBO, the lists update.
4. Use the Authentication tab to look up challenge responses.
5. Edit the suggested response and click **Send via FLDigi** when ready.
6. Export an ICS-214 from the Logs tab when finished.

## File Structure

- `main.py` — GUI and application logic
- `dryad_parser.py` — Parses OPIN DRYAD PDFs and performs authentication lookups
- `net_state.py` — Tracks stations, ZKS order, traffic, and session log
- `requirements.txt` — Python dependencies

## What a check-in actually does

The station that is talking is the callsign **after** `DE`.

- `N8TAG DE KE8YTX ZKE ZNB ABC K` — KE8YTX is checking in, and ABC means "the authentication of AB is C". If that matches today's DRYAD, KE8YTX goes on the ZKS list and the box suggests `KE8YTX DE N8TAG R AS AR` (or a traffic acknowledgement if they also listed a ZBO).
- If ABC does not match, they stay off the roll and the box suggests `ZND 2 INT ZNB xx`. SOI: ZND 2 means "check the authentication of your last transmission". ZND 1 means "verify the authenticator key" (wrong DRYAD).
- `ZUG ZNB` puts them on the roll with no DRYAD check.
- `IMI` is never sent by itself. The IMI button prepares `CALL DE NCS IMI K` for a weak or missed copy.

## Follow-up tab

After a ZBO has been ZOF'd and every station on the ZKS roll has sent `ZEV` (has the file) or `ZUG ZEV` (does not — SOI "do not acknowledge"), the listing leaves the active list. The Follow-up tab shows who has it and who still needs it on Winlink. You can also move a listing there yourself.

## Completed traffic / Winlink follow-up

Once a ZBO has been ZOF’d and every station on the ZKS list has answered with either:
- `ZEV` (received the file), or
- `ZUG ZEV` (did not receive it),

the item is moved off the active traffic list into the **Completed / Winlink Follow-up** list.  
That list shows who received the file and who still needs it sent via Winlink after the net.

## Author Notes

Written with extensive comments so the logic is easy to follow and modify. Feel free to adapt the callsign default, add more Z-code handling, or extend the traffic tracking.
