# protocol.py
# Pure net-control decisions, kept out of the GUI so they can be tested
# against the Black Swan SOI (Version D) and the check-in lines in the logs.
#
# SOI points this module follows (https://www.blackswancomex.org/net/soi
# and the digital control script):
#   DE  introduces the station that is transmitting (the speaker is AFTER DE)
#   INT ZNB AB   "what is the authentication of AB?"
#   ZNB C        "authentication is C"
#   ZNB ABC      authentication of AB is C (volunteered, no challenge)
#   ZND 1        verify the authenticator system key (wrong DRYAD)
#   ZND 2        check the authentication of your last transmission
#   ZUG          leading negative. ZUG ZNB is accepted onto the ZKS list
#                without a DRYAD check (operator rule from this net).
#   ZUG ZEV      "do not acknowledge" — on this net that means the station
#                did not get the file and still needs it via Winlink.
#   ZEV          "I acknowledge" — full receipt.
#   IMI          say again. Never sent automatically; the operator asks for it.
#   ZKA ZKB AS   "I am net control, this net is directed, wait."
#   INT AGENCY / INT ZBO / INT ZKE   the three digital calls from the script.
#   ZKJ 73 AR    close the net.

import random
import re
from typing import Optional

# US-style amateur call: 1-2 letters, a digit, 1-4 letters. Suffixes after /
# (portable, tactical) are stripped before the test.
CALL_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z]{1,4}$")

# Tokens that end a traffic address and are not themselves the addressee.
STOP_TOKENS = {
    "K", "AR", "AS", "QRU", "ZNB", "ZKE", "ZUG", "ZEV", "ZOF", "ZKA", "ZKB",
    "ZKJ", "INT", "IMI", "QSY", "R",
}


def normalize_call(token: str) -> str:
    """Return the base callsign, dropping a /suffix and punctuation."""
    return token.upper().split("/")[0].strip(".,:;<>")


def looks_like_callsign(token: str) -> bool:
    """True for an amateur callsign. OPIN and prosigns fail this on purpose."""
    if not token:
        return False
    return bool(CALL_RE.fullmatch(normalize_call(token)))


def extract_speaker(line: str) -> Optional[str]:
    """
    The station that transmitted this line.

    Procedure: "CALLED DE CALLING ...". DE means "this is", so the speaker
    is the first callsign AFTER DE, not the station being called.
    "N8TAG DE KE8YTX ZKE K" was sent by KE8YTX.
    """
    text = line.upper().strip()
    # "DE KD8TTE ZEV AR" has no station in front of DE.
    if text.startswith("DE "):
        text = "X " + text
    if " DE " not in text:
        return None
    after = text.split(" DE ", 1)[1].strip().split()
    if not after:
        return None
    cand = normalize_call(after[0])
    if looks_like_callsign(cand):
        return cand
    return None


def extract_znb_group(line: str) -> Optional[str]:
    """
    The authenticator attached to ZNB.
    One letter answers a challenge (ZNB C).
    Three letters volunteer an authentication (ZNB ABC means AB authenticates as C).
    Two letters are a challenge and are ignored here (those follow INT).
    """
    tokens = line.upper().split()
    if "ZNB" not in tokens:
        return None
    idx = tokens.index("ZNB")
    if idx + 1 >= len(tokens):
        return None
    cand = tokens[idx + 1].strip(".,")
    if cand.isalpha() and len(cand) in (1, 3):
        return cand
    return None


def extract_filename(line: str) -> Optional[str]:
    """A filename from an FLAMP report line, if one is present."""
    text = line.upper()
    if "FILE :" in text:
        try:
            return text.split("FILE :", 1)[1].strip().split()[0].strip("<>")
        except IndexError:
            return None
    for token in text.replace("<", " ").replace(">", " ").split():
        if "." in token and token.replace(".", "").replace("-", "").replace("_", "").isalnum():
            if any(token.endswith(ext) for ext in (".TXT", ".K2S", ".CSV", ".HTML")):
                return token
    return None


def parse_zbo(line: str):
    """
    ZBO quantity precedence target.
    Example from the control script: ZBO 1 R W8OMR
    Returns (qty, precedence, destination) or None.
    """
    tokens = line.upper().replace("ZBO1", "ZBO 1").split()
    if "ZBO" not in tokens:
        return None
    idx = tokens.index("ZBO")
    rest = tokens[idx + 1:]
    if not rest:
        return (1, "R", "OPIN")
    qty = 1
    if rest[0].isdigit():
        qty = int(rest[0])
        rest = rest[1:]
    # Precedence is a single letter (R routine, P priority, ...).
    # R is also the prosign for "roger", so it is only taken as precedence
    # in this slot — not anywhere else in the line.
    prec = "R"
    if rest and rest[0] in {"R", "P", "O", "Z", "Y", "M", "C"}:
        prec = rest[0]
        rest = rest[1:]
    dest = "OPIN"
    if rest and rest[0] not in STOP_TOKENS:
        dest = rest[0]
    return (qty, prec, dest)


def generate_challenge(rows: dict, used=None) -> Optional[str]:
    """
    A two-letter INT ZNB challenge: a row letter plus a letter that sits in
    that row. The correct reply is the letter directly under it (see DryadTable).
    Row Y is avoided so the reply does not have to wrap.
    Combinations already in `used` are skipped. A combination may be used once.
    """
    spent = set(used or [])
    options = []
    for row, letters in rows.items():
        if row == "Y" or len(letters) != 25:
            continue
        for letter in letters:
            pair = f"{row}{letter}"
            if pair not in spent:
                options.append(pair)
    if not options:
        return None
    return random.choice(options)


def take_challenge(rows: dict, used: set) -> Optional[str]:
    """Pick an unused combination and mark it spent."""
    pair = generate_challenge(rows, used)
    if pair:
        used.add(pair)
    return pair


def volunteer_group(rows: dict, used, authenticate_fn) -> Optional[str]:
    """A three-letter ZNB the station can check: authentication of AB is C."""
    pair = generate_challenge(rows, used)
    if not pair:
        return None
    answer = authenticate_fn(pair[0], pair[1])
    if not answer:
        return None
    return pair + answer


def triplet_is_valid(rows_auth, group: str) -> bool:
    """
    ZNB ABC is valid when the letter under A/B in the current DRYAD is C.
    rows_auth is DryadTable.authenticate.
    """
    if not group or len(group) != 3:
        return False
    expected = rows_auth(group[0], group[1])
    return bool(expected) and expected == group[2]


def script_open(ncs: str, znb: Optional[str] = None) -> str:
    """
    Digital net-up call. ZKA says we are net control, ZKB says the net is
    directed, and ZNB is our own DRYAD so the other stations can check us.
    """
    if znb:
        return f"OPIN DE {ncs} ZKA ZKB ZNB {znb} AS"
    return f"OPIN DE {ncs} ZKA ZKB AS"


def script_agency(ncs: str) -> str:
    return f"OPIN DE {ncs} INT AGENCY K"


def script_traffic(ncs: str) -> str:
    return f"OPIN DE {ncs} INT ZBO K"


def script_checkin(ncs: str) -> str:
    return f"OPIN DE {ncs} INT ZKE K"


def script_close_net(ncs: str) -> str:
    return f"OPIN DE {ncs} ZKJ 73 AR"


def script_close_station(ncs: str, station: str) -> str:
    return f"{station} DE {ncs} ZKJ K"


def script_zks(ncs: str, calls) -> str:
    """Roll call. Callsigns only — no authentication tags."""
    body = " ".join(calls)
    return f"OPIN DE {ncs} ZKS {body}".rstrip()


def script_imi(ncs: str, station: str) -> str:
    """Ask one station to say again. Sent only when the operator clicks IMI."""
    return f"{station} DE {ncs} IMI K"


def script_ack_traffic(ncs: str, station: str, qty: int, prec: str, dest: str) -> str:
    """Control-script acknowledgement of a listing: roger, repeat the ZBO, wait, out."""
    return f"{station} DE {ncs} R ZBO {qty} {prec} {dest} AS AR"


def script_ack(ncs: str, station: str) -> str:
    return f"{station} DE {ncs} R AS AR"


def script_challenge(ncs: str, station: str, challenge: str, znd: Optional[str] = None) -> str:
    """INT ZNB challenge. znd is '2' when the previous authenticator was wrong."""
    prefix = f"ZND {znd} " if znd else ""
    return f"{station} DE {ncs} {prefix}INT ZNB {challenge}"


def interpret_line(net, line: str, dryad, ncs_mode: bool) -> Optional[str]:
    """
    Update net state from one received line and return a suggested reply,
    or None if the operator should not be prompted.

    Nothing in here transmits. The GUI puts the suggestion in the edit box.
    """
    text = " ".join(line.upper().split())
    if len(text) < 4:
        return None

    speaker = extract_speaker(text)
    ncs = net.ncs_callsign
    is_us = speaker == ncs
    suggestion = None

    # Our own echo can still be the ZOF that directed a relay.
    if "ZOF" in text and "INT" not in text:
        filename = extract_filename(text)
        if filename:
            net.mark_zof(filename)
        elif net.traffic_items:
            net.traffic_items[-1].zof_sent = True
            net.log("ZOF noted for the current traffic")

    # A station asking us to say again: offer the last thing we prepared.
    if speaker and not is_us and "IMI" in text.split():
        net.log(f"{speaker} sent IMI — say again", source=speaker)
        if net.last_tx:
            return net.last_tx

    if is_us or speaker is None:
        # FLAMP receipt lines sometimes still carry DE CALL.
        return suggestion

    # ZUG ZNB — onto the ZKS list with no DRYAD check.
    if "ZUG" in text and "ZNB" in text and "ZEV" not in text:
        net.mark_zug_znb(speaker)
        suggestion = script_ack(ncs, speaker)

    # Check-in. A volunteered ZNB ABC is checked before a new challenge is made.
    elif "ZKE" in text and "INT" not in text:
        net.check_in(speaker, text)
        group = extract_znb_group(text)
        if group and len(group) == 3 and speaker in net.stations:
            net.stations[speaker].last_volunteered_znb = group
        authed = False
        if ncs_mode and group and len(group) == 3 and dryad is not None:
            pair = group[:2]
            spent = pair in net.used_groups
            if triplet_is_valid(dryad.authenticate, group) and not spent:
                net.used_groups.add(pair)
                net.mark_authenticated(speaker)
                authed = True
                net.log(f"{speaker} volunteered ZNB {group} — matches the DRYAD", source=speaker)
            else:
                challenge = take_challenge(dryad.rows, net.used_groups)
                if challenge and speaker in net.stations:
                    net.stations[speaker].last_challenge = challenge
                suggestion = script_challenge(ncs, speaker, challenge or "??", znd="2")
                why = "that combination was already used" if spent else "does not match"
                net.log(f"{speaker} volunteered ZNB {group} — {why}. Suggested ZND 2.", source=speaker)
        elif ncs_mode and dryad is not None and speaker in net.stations and not net.stations[speaker].authenticated:
            challenge = take_challenge(dryad.rows, net.used_groups)
            if challenge:
                net.stations[speaker].last_challenge = challenge
                suggestion = script_challenge(ncs, speaker, challenge)
                net.log(f"Suggested INT ZNB {challenge} for {speaker}")
        if authed:
            zbo = parse_zbo(text)
            if zbo and "QRU" not in text:
                suggestion = script_ack_traffic(ncs, speaker, *zbo)
            else:
                suggestion = script_ack(ncs, speaker)

    # A one-letter (or triplet) answer to a challenge we already sent.
    elif "ZNB" in text and "INT" not in text and "ZUG" not in text:
        group = extract_znb_group(text)
        st = net.stations.get(speaker)
        if ncs_mode and group and st and st.last_challenge and dryad is not None:
            expected = dryad.authenticate(st.last_challenge[0], st.last_challenge[1])
            got = group[0] if len(group) == 1 else (group[2] if len(group) == 3 else "")
            if expected and got == expected:
                net.mark_authenticated(speaker)
                suggestion = script_ack(ncs, speaker)
                net.log(f"{speaker} authenticated with ZNB {group}", source=speaker)
            else:
                challenge = take_challenge(dryad.rows, net.used_groups)
                if challenge:
                    st.last_challenge = challenge
                    suggestion = script_challenge(ncs, speaker, challenge, znd="2")
                else:
                    suggestion = f"{speaker} DE {ncs} ZND 2"
                net.log(
                    f"{speaker} ZNB mismatch (got {got}, expected {expected})",
                    source=speaker,
                )

    # Traffic listing, including one that arrived on the check-in line.
    zbo = parse_zbo(text)
    if zbo and "INT" not in text:
        qty, prec, dest = zbo
        net.add_traffic(speaker, qty, prec, dest, text)
        # If we already decided to challenge, leave that suggestion alone.
        # If we have not suggested anything and they are already in the net, ack the listing.
        if suggestion is None and speaker in net.zks_order and "ZKE" not in text:
            suggestion = script_ack_traffic(ncs, speaker, qty, prec, dest)

    # Receipt. ZUG ZEV = do not acknowledge = did not get the file.
    if "ZEV" in text and "INT" not in text:
        net.record_zev(speaker, file_name=extract_filename(text), negative=("ZUG" in text))

    if "CONFIRMED" in text:
        filename = extract_filename(text)
        if filename:
            net.record_zev(speaker, file_name=filename, negative=False)

    if "ZKJ" in text and "INT" not in text:
        net.log(f"{speaker} closing down (ZKJ)", source=speaker)

    return suggestion
