# Plain-English reading of one OPIN line, and the parts the logger needs:
# relayed check-ins, more than one ZBO, a jammed precedence such as 1P,
# ZOK (relay via...), an FLAMP file header, and a missing-block report.

import re

DEFAULT_ALIASES = ["OPIN", "READYX", "QSPEDITOR", "QSPLETTER", "WXOBSREP", "US", "OH"]
CALL = re.compile(r"^[A-Z]{1,2}\d[A-Z]{1,4}$")
PREC = {"R": "Routine", "P": "Priority", "O": "Immediate", "Z": "Flash", "Y": "Emergency"}


# WL2K by itself is a Winlink suffix, not a station.
NOT_A_CALL = {"WL2K", "IP", "OPIN", "ALL", "AA"}


def station_id(token):
    """Callsign, keeping tactical suffixes such as W8SGM/FCEMHS/WL2K. /NCS is dropped."""
    clean = token.upper().strip(".,:;<>()")
    if not clean or clean in NOT_A_CALL:
        return None
    parts = [part for part in clean.split("/") if part]
    base = parts[0] if parts else ""
    if not CALL.match(base):
        return None
    suffixes = [part for part in parts[1:] if part != "NCS" and re.match(r"^[A-Z0-9]{1,12}$", part)]
    if not suffixes:
        return base
    return "/".join([base, *suffixes])


def _speaker(text):
    line = " ".join(text.upper().split())
    if line.startswith("DE "):
        line = "X " + line
    if " DE " not in line:
        return None
    return station_id(line.split(" DE ", 1)[1].split()[0])


def _called(text):
    line = " ".join(text.upper().split())
    if " DE " not in line:
        return None
    before = line.split(" DE ", 1)[0].split()
    return station_id(before[-1]) if before else None


def relayed_calls(text, aliases=None):
    """
    Stations reported by the speaker.

    They sit after ZKE and before that group's ZBO. IP, WL2K, the speaker's
    own ZNB, and QRU are skipped. A second ZKE starts another group.
    The destination after ZBO is not a check-in.
    """
    alias = {a.upper() for a in (aliases or DEFAULT_ALIASES)}
    tokens = " ".join(text.upper().split()).split()
    speaker = _speaker(text)
    found = []
    seen = set()
    i = 0
    while i < len(tokens):
        if tokens[i] != "ZKE":
            i += 1
            continue
        i += 1
        while i < len(tokens) and tokens[i] != "ZKE":
            token = tokens[i]
            if token in {"ZBO", "ZOF", "ZEV", "ZOK", "K", "AR", "AS", "INT"}:
                break
            ident = station_id(token)
            if ident and ident != speaker and ident not in alias and token not in alias and ident not in seen:
                seen.add(ident)
                found.append(ident)
            i += 1
    return found


def _dest(token, aliases):
    raw = token.upper().strip(".,")
    collapsed = raw.replace("-", "")
    if collapsed in aliases or raw in aliases:
        return collapsed
    return station_id(raw) or collapsed


def zev_serials(text):
    """3-to-5 digit numbers after ZEV are message serials. ZEV 2 is not."""
    tokens = text.split()
    serials = []
    for i, token in enumerate(tokens):
        if token != "ZEV":
            continue
        j = i + 1
        while j < len(tokens) and re.fullmatch(r"\d{3,5}", tokens[j]):
            serials.append(tokens[j])
            j += 1
    return serials


def listings_of(text, aliases=None):
    alias = {a.upper() for a in (aliases or DEFAULT_ALIASES)}
    tokens = " ".join(text.upper().split()).split()
    speaker = _speaker(text)
    relays = relayed_calls(text, alias)
    out = []
    for i, token in enumerate(tokens):
        if token not in {"ZBO", "ZOF"}:
            continue
        rest = tokens[i + 1:]
        # ZOF 7547 N2QFD means "send serial 7547", not 7547 messages.
        if token == "ZOF" and rest and re.fullmatch(r"\d{3,5}", rest[0]):
            serials = []
            while rest and re.fullmatch(r"\d{3,5}", rest[0]):
                serials.append(rest.pop(0))
            destinations = []
            via = None
            while rest and rest[0] not in {"K", "AR", "AS", "ZBO", "ZOF", "INT", "DE"}:
                if rest[0] == "ZOK":
                    via = _dest(rest[1], alias) if len(rest) > 1 else None
                    break
                destinations.append(_dest(rest.pop(0), alias))
            out.append({
                "kind": "ZOF",
                "owner": _called(text) or speaker or "",
                "quantity": 1,
                "precedence": "R",
                "assumed": False,
                "destination": destinations[0] if destinations else "OPIN",
                "via": via,
                "serials": serials,
            })
            continue
        qty, prec, assumed = 1, "R", True
        if rest and re.match(r"^\d+[A-Z]$", rest[0]):
            qty = int(rest[0][:-1])
            prec = rest[0][-1]
            assumed = False
            rest = rest[1:]
        elif rest and rest[0].isdigit():
            qty = int(rest[0])
            rest = rest[1:]
            if rest and rest[0] in PREC:
                prec = rest[0]
                assumed = False
                rest = rest[1:]
        dest = "OPIN"
        if rest and rest[0] not in {"ZOK", "K", "AR", "AS", "ZBO", "ZOF", "INT"}:
            dest = _dest(rest[0], alias)
            rest = rest[1:]
        via = rest[1] if rest and rest[0] == "ZOK" and len(rest) > 1 else None
        owner = speaker or ""
        if token == "ZOF":
            owner = _called(text) or owner
        elif relays:
            prior = [station_id(tok) for tok in tokens[:i]]
            prior = [p for p in prior if p in relays]
            if prior:
                owner = prior[-1]
        out.append({
            "kind": token, "owner": owner, "quantity": qty, "precedence": prec,
            "assumed": assumed, "destination": dest, "via": via,
        })
    return out


def file_start(raw):
    """FLAMP header. OPIN names look like ORIGIN--DEST-SERIAL-PREC-note.txt."""
    text = raw.upper()
    if "FILE" not in text and ".K2S" not in text and ".TXT" not in text:
        return None
    queue = None
    found = re.search(r"\{([A-Z0-9]+)\}", text)
    if found:
        queue = found.group(1)
    named = re.search(r":([A-Z0-9][A-Z0-9._-]{3,})", text)
    file_name = named.group(1) if named else ""
    if not file_name:
        loose = re.search(r"([A-Z0-9][A-Z0-9._-]*\.(?:K2S|TXT|CSV|HTML))", text)
        file_name = loose.group(1) if loose else ""
    if not file_name:
        return None
    doubled = re.match(r"^([A-Z0-9]+)--([A-Z0-9]+)-(\d{2,5})-([RPOZY])\b", file_name)
    if doubled:
        return {
            "queue": queue,
            "file": file_name,
            "sender": station_id(doubled.group(1)) or doubled.group(1),
            "destination": doubled.group(2),
            "precedence": doubled.group(4),
            "serial": doubled.group(3),
        }
    parts = file_name.split("-")
    serial = None
    if len(parts) > 2:
        hit = re.search(r"(\d{3,5})", "-".join(parts[2:]))
        serial = hit.group(1) if hit else None
    return {
        "queue": queue,
        "file": file_name,
        "sender": station_id(parts[0]) if parts else None,
        "destination": parts[1] if len(parts) > 1 else None,
        "precedence": parts[2] if len(parts) > 2 and parts[2] in PREC else None,
        "serial": serial,
    }


def missing_blocks(raw):
    """
    FLAMP's own report has no callsign:
    <MISSING 15 A772>{1740}CONFIRMED
    <MISSING 12 1C52>{C3E8}4 6 8
    <MISSING 14 5F91>{C3E8}PREAMBLE
    That report is the operator sitting at this computer, not the rest of the net.
    """
    text = " ".join(raw.upper().split())
    if "MISSING" not in text:
        return None
    tag = re.search(r"<MISSING\s+\d+\s+[0-9A-F]+>\{([A-Z0-9]+)\}\s*(.*)$", text)
    if tag:
        queue, tail = tag.group(1), tag.group(2).strip()
        if tail.startswith("CONFIRMED"):
            return {"station": None, "queue": queue, "blocks": "", "confirmed": True, "preamble": False}
        if tail.startswith("PREAMBLE"):
            return {"station": None, "queue": queue, "blocks": "PREAMBLE", "confirmed": False, "preamble": True}
        blocks = " ".join(tok for tok in tail.split() if re.match(r"^\d+[A-Z]?$", tok))
        return {"station": None, "queue": queue, "blocks": blocks, "confirmed": False, "preamble": False}
    queue_match = re.search(r"\{([A-Z0-9]+)\}", text)
    queue = queue_match.group(1) if queue_match else None
    after = text.split("MISSING", 1)[1]
    if queue and "{" + queue + "}" in after:
        after = after.split("{" + queue + "}", 1)[1]
    after = re.sub(r"DE\s+[A-Z0-9/]+.*", "", after)
    blocks = " ".join(tok for tok in after.replace("<", " ").replace(">", " ").split() if re.match(r"^\d+[A-Z]?$", tok))
    return {"station": _speaker(text), "queue": queue, "blocks": blocks, "confirmed": False, "preamble": False}


def explain_line(raw, aliases=None):
    text = " ".join(raw.upper().split())
    text = re.sub(r"\bZEV(\d{3,5})\b", r"ZEV \1", text)
    text = re.sub(r"\bZOF(\d{3,5})\b", r"ZOF \1", text)
    if not text:
        return "Nothing to read."
    speaker = _speaker(text)
    called = _called(text)
    started = file_start(raw)
    if started and ("<FILE" in text or "FILE :" in text or ".K2S" in text or ".TXT" in text):
        where = f" for queue {started['queue']}" if started["queue"] else ""
        serial = f" Serial {started['serial']}." if started.get("serial") else ""
        return f"A file transmission is starting{where}.\nFile: {started['file']}{serial}"
    miss = missing_blocks(raw)
    if miss and "MISSING" in text:
        where = f" queue {miss['queue']}" if miss["queue"] else ""
        if miss.get("confirmed"):
            return f"FLAMP copy{where} is confirmed. That is this station's own copy, not another station's report."
        if miss.get("preamble"):
            return f"FLAMP copy{where} is missing the preamble."
        who = miss["station"] or "This station's FLAMP copy"
        blocks = miss["blocks"] or "the report did not list block numbers"
        return f"{who} is missing blocks{(' on' + where) if where else ''}: {blocks}."
    if speaker and " IMI " in f" {text} ":
        return f"{speaker} is asking {called or 'the other station'} to say again."
    if speaker and " INT ZNB " in text:
        chal = text.split(" INT ZNB ", 1)[1].split()[0]
        return f"{speaker} is asking {called or 'a station'} for an authentication response to {chal}."
    items = listings_of(text, aliases)
    if speaker and "ZOF" in text.split() and "INT" not in text.split():
        zof = next((item for item in items if item["kind"] == "ZOF"), None)
        if zof and zof.get("serials") and called:
            dest = f" to {zof['destination']}" if zof["destination"] != "OPIN" else ""
            via = f" via {zof['via']}" if zof.get("via") else ""
            return f"{speaker} directs {called} to send serial {' '.join(zof['serials'])}{dest}{via}."
        if zof and called:
            word = "one" if zof["quantity"] == 1 else str(zof["quantity"])
            prec = PREC.get(zof["precedence"], zof["precedence"])
            return f"{speaker} directs {called} to send {word} {prec} message to {zof['destination']}."
    if speaker and "ZKS" in text.split():
        return f"{speaker} is supplying a station roster."
    relays = relayed_calls(text, aliases)
    parts = []
    if speaker and "ZKE" in text.split():
        group = None
        tokens = text.split()
        if "ZNB" in tokens:
            nxt = tokens[tokens.index("ZNB") + 1] if tokens.index("ZNB") + 1 < len(tokens) else ""
            if nxt.isalpha() and len(nxt) <= 3:
                group = nxt
        if relays:
            parts.append(f"{speaker} reports check-ins for {', '.join(relays)}.")
        elif called:
            parts.append(f"{speaker} is checking into the net with {called}.")
        else:
            parts.append(f"{speaker} is checking into the net.")
        if group:
            parts.append(f"Authentication code {group} was supplied.")
    assumed = False
    for item in items:
        if item["kind"] != "ZBO":
            continue
        who = item["owner"] or speaker or "The station"
        prec = PREC.get(item["precedence"], item["precedence"])
        word = "one" if item["quantity"] == 1 else str(item["quantity"])
        via = f" via {item['via']}" if item["via"] else ""
        parts.append(f"{who} has {word} {prec} message for {item['destination']}{via}.")
        if item["assumed"]:
            assumed = True
    if assumed:
        parts.append("Assumption: no precedence was stated for at least one listing; Routine was used for that listing.")
    if speaker and "ZEV" in text.split() and "INT" not in text.split():
        serials = zev_serials(text)
        which = f" serial {' '.join(serials)}" if serials else " the file"
        if "ZUG" in text.split():
            parts.append(f"{speaker} does not acknowledge{which}.")
        else:
            parts.append(f"{speaker} acknowledges{which}.")
    if not parts:
        return "No automatic interpretation. The print may be garbled — record what you can read on the receipt line."
    return " ".join(parts)
