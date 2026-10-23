# Plain-English reading of one OPIN line, and the parts the logger needs:
# relayed check-ins, more than one ZBO, a jammed precedence such as 1P,
# ZOK (relay via...), an FLAMP file header, and a missing-block report.

import re

DEFAULT_ALIASES = ["OPIN", "READYX", "QSPEDITOR", "QSPLETTER", "WXOBSREP", "US", "OH"]
CALL = re.compile(r"^[A-Z]{1,2}\d[A-Z]{1,4}$")
PREC = {"R": "Routine", "P": "Priority", "O": "Immediate", "Z": "Flash", "Y": "Emergency"}


def station_id(token):
    clean = token.upper().strip(".,:;<>()")
    if not clean:
        return None
    base, _, suffix = clean.partition("/")
    if not CALL.match(base):
        return None
    if not suffix or suffix == "NCS":
        return base
    if not re.match(r"^[A-Z0-9]{1,8}$", suffix):
        return base
    return f"{base}/{suffix}"


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
    alias = {a.upper() for a in (aliases or DEFAULT_ALIASES)}
    tokens = " ".join(text.upper().split()).split()
    if "ZKE" not in tokens:
        return []
    found = []
    for token in tokens[tokens.index("ZKE") + 1:]:
        if token in {"ZBO", "ZOF", "ZNB", "K", "AR", "INT", "ZOK", "ZEV", "QRU"}:
            if token != "QRU":
                break
            continue
        if token in alias:
            break
        ident = station_id(token)
        if ident:
            found.append(ident)
        else:
            break
    return found


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
            dest = rest[0]
            rest = rest[1:]
        via = rest[1] if rest and rest[0] == "ZOK" and len(rest) > 1 else None
        owner = speaker or ""
        if token == "ZOF":
            owner = _called(text) or owner
        elif relays:
            prior = [station_id(tok) for tok in tokens[:i]]
            prior = [p for p in prior if p in relays]
            owner = prior[-1] if prior else relays[-1]
        out.append({
            "kind": token, "owner": owner, "quantity": qty, "precedence": prec,
            "assumed": assumed, "destination": dest, "via": via,
        })
    return out


def file_start(raw):
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
    parts = file_name.split("-")
    return {
        "queue": queue,
        "file": file_name,
        "sender": station_id(parts[0]) if parts else None,
        "destination": parts[1] if len(parts) > 1 else None,
        "precedence": parts[2] if len(parts) > 2 and parts[2] in PREC else None,
    }


def missing_blocks(raw):
    text = " ".join(raw.upper().split())
    if "MISSING" not in text:
        return None
    queue_match = re.search(r"\{([A-Z0-9]+)\}", text)
    queue = queue_match.group(1) if queue_match else None
    after = text.split("MISSING", 1)[1]
    if queue and "{" + queue + "}" in after:
        after = after.split("{" + queue + "}", 1)[1]
    after = re.sub(r"DE\s+[A-Z0-9/]+.*", "", after)
    blocks = " ".join(tok for tok in after.replace("<", " ").replace(">", " ").split() if re.match(r"^\d+[A-Z]?$", tok))
    return {"station": _speaker(text), "queue": queue, "blocks": blocks}


def explain_line(raw, aliases=None):
    text = " ".join(raw.upper().split())
    if not text:
        return "Nothing to read."
    speaker = _speaker(text)
    called = _called(text)
    started = file_start(raw)
    if started and ("<FILE" in text or "FILE :" in text or ".K2S" in text or ".TXT" in text):
        where = f" for queue {started['queue']}" if started["queue"] else ""
        return f"A file transmission is starting{where}.\nFile: {started['file']}"
    miss = missing_blocks(raw)
    if miss and "MISSING" in text:
        who = miss["station"] or "The station"
        where = f" on queue {miss['queue']}" if miss["queue"] else ""
        blocks = miss["blocks"] or "the report did not list block numbers"
        return f"{who} is missing blocks{where}: {blocks}."
    if speaker and " INT ZNB " in text:
        chal = text.split(" INT ZNB ", 1)[1].split()[0]
        return f"{speaker} is asking {called or 'a station'} for an authentication response to {chal}."
    items = listings_of(text, aliases)
    if speaker and "ZOF" in text.split() and "INT" not in text.split():
        zof = next((item for item in items if item["kind"] == "ZOF"), None)
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
        parts.append(f"{speaker} does not acknowledge the file." if "ZUG" in text.split() else f"{speaker} acknowledges the file.")
    if not parts:
        return "No automatic interpretation. The print may be garbled — record what you can read on the receipt line."
    return " ".join(parts)
