# Exercises the decisions a net control actually has to get right.
# Run: python3 test_protocol.py

import sys
sys.path.insert(0, ".")

from dryad_parser import DryadTable
from datetime import datetime
from net_state import NetState
from protocol import (
    extract_speaker, interpret_line, script_zks, script_open, generate_challenge,
    triplet_is_valid, volunteer_group,
)
from explain import explain_line

# October 1 row A is IZRE... and the letter under A/I is Y (parser self-test).
ROWS = {
    "A": "IZREPQXJWNSVFUYHTLMCDBKOG",
    "B": "YABCDEFGHIJKLMNOPQRSTUVWX",
}
# Pad a full table so generate_challenge and authenticate work.
# authenticate needs the NEXT row. For this test only A and B matter,
# but DryadTable.authenticate looks up the letter in the row.
# Build rows A-Y as shifted copies so every challenge has an answer.
ALPH = "ABCDEFGHIJKLMNOPQRSTUVWXY"

def make_table():
    rows = {}
    base = "IZREPQXJWNSVFUYHTLMCDBKOG"
    for i, letter in enumerate(ALPH):
        rows[letter] = base[i:] + base[:i]
    return DryadTable(datetime(2026, 10, 1), datetime(2026, 10, 1, 23, 59), rows)


def main():
    assert extract_speaker("N8TAG DE KE8YTX ZKE ZNB GIO QRU K") == "KE8YTX"
    assert extract_speaker("OPIN DE N8TAG ZKA ZKB AS") == "N8TAG"
    assert extract_speaker("DE KD8TTE ZEV AR") == "KD8TTE"

    table = make_table()
    # Known: A/I -> letter under I in row A, which is index 0, next row B[0].
    expected = table.authenticate("A", "I")
    assert expected and len(expected) == 1
    good = "AI" + expected
    assert triplet_is_valid(table.authenticate, good)
    assert not triplet_is_valid(table.authenticate, "AI" + ("Q" if expected != "Q" else "Z"))

    net = NetState("N8TAG")
    # Good volunteered authentication goes on the ZKS list. No extra challenge.
    reply = interpret_line(net, f"N8TAG DE KE8YTX ZKE ZNB {good} QRU K", table, True)
    assert "KE8YTX" in net.zks_order, net.zks_order
    assert reply.startswith("KE8YTX DE N8TAG R"), reply
    assert "INT ZNB" not in reply

    # Bad authenticator stays off the roll and is challenged with ZND 2.
    reply = interpret_line(net, "N8TAG DE W8THV ZKE ZNB AIQ K", table, True)
    assert "W8THV" not in net.zks_order
    assert "W8THV" in net.pending_calls()
    assert reply.startswith("W8THV DE N8TAG ZND 2 INT ZNB "), reply

    # ZUG ZNB is allowed on the roll with no DRYAD check, even with no prior ZKE.
    reply = interpret_line(net, "N8TAG DE KD8TTE ZUG ZNB QRU K", table, True)
    assert "KD8TTE" in net.zks_order
    assert net.stations["KD8TTE"].zug_znb

    # Listing, relay, then one ZEV and one ZUG ZEV closes the item.
    interpret_line(net, "N8TAG DE KE8YTX ZBO 1 R FIELDSITREP K", table, True)
    assert any(t.destination == "FIELDSITREP" for t in net.traffic_items)
    interpret_line(net, "KE8YTX DE N8TAG ZOF 1 R OPIN K", table, True)
    assert net.traffic_items[-1].zof_sent or any(t.zof_sent for t in net.traffic_items + net.completed_traffic)
    interpret_line(net, "N8TAG DE KE8YTX ZEV AR", table, True)
    interpret_line(net, "N8TAG DE KD8TTE ZUG ZEV AR", table, True)
    # W8THV never authenticated, so the roll is KE8YTX and KD8TTE. Both answered.
    assert net.completed_traffic, "listing should have moved to follow-up"
    done = net.completed_traffic[-1]
    assert "KE8YTX" in done.received_by
    assert "KD8TTE" in done.not_received_by

    # A repeat of the same listing must not create a second ZBO.
    before = len(net.traffic_items) + len(net.completed_traffic)
    interpret_line(net, "N8TAG DE KE8YTX ZBO 1 P WD8SAB K", table, True)
    interpret_line(net, "N8TAG DE KE8YTX ZBO 1 P WD8SAB K", table, True)
    assert len(net.traffic_items) + len(net.completed_traffic) == before + 1

    # A combination used by one station cannot be reused by another.
    # ZND 2 is "check the authentication of your last transmission", then a new INT ZNB.
    other = interpret_line(net, f"N8TAG DE W1AW ZKE ZNB {good} K", table, True)
    assert "W1AW" not in net.zks_order
    assert other.startswith("W1AW DE N8TAG ZND 2 INT ZNB "), other
    new_pair = other.split()[-1]
    assert new_pair != good[:2]
    assert new_pair in net.used_groups

    opened = volunteer_group(table.rows, net.used_groups, table.authenticate)
    assert opened and opened[:2] not in net.used_groups
    net.used_groups.add(opened[:2])
    text = script_open("N8TAG", opened)
    assert text == f"OPIN DE N8TAG ZKA ZKB ZNB {opened} AS"

    relay = "WD8SDH DE KE8NHT ZKE AD8CM/WL2K N8ADO/WL2K QRU KC3QVF/WL2K ZBO 1 WXOBSREP ZOK IP ZBO 1 R QSPEDITOR ZOK IP K"
    meaning = explain_line(relay)
    assert "reports check-ins for AD8CM/WL2K, N8ADO/WL2K, KC3QVF/WL2K" in meaning
    assert "WXOBSREP" in meaning and "QSPEDITOR" in meaning and "Routine" in meaning
    assert "ZOF 1P" not in meaning
    assert explain_line("K8BCI DE WD8SDH ZOF 1P READYX K") == "WD8SDH directs K8BCI to send one Priority message to READYX."
    relay_net = NetState("WD8SDH")
    interpret_line(relay_net, relay, table, True)
    assert "KC3QVF/WL2K" in relay_net.stations
    assert "KC3QVF/WL2K" not in relay_net.zks_order
    assert any(t.originator == "KC3QVF/WL2K" and t.destination == "QSPEDITOR" for t in relay_net.traffic_items)

    zks = script_zks("N8TAG", net.zks_order)
    assert zks.startswith("OPIN DE N8TAG ZKS ")
    assert "[AUTH]" not in zks and "PENDING" not in zks

    challenge = generate_challenge(table.rows)
    assert challenge and len(challenge) == 2
    assert table.authenticate(challenge[0], challenge[1])

    # Our own transmission must not check the NCS into the net.
    interpret_line(net, "OPIN DE N8TAG INT ZKE K", table, True)
    assert "N8TAG" not in net.stations

    # A real check-in from the logs: IP WL2K and the speaker's ZNB come
    # before the stations he is reporting. The ZBO belongs to the last of those.
    live = "WD8SDH DE KE8NHT ZKE IP WL2K ZNB NHI QRU W8SGM/FCEMHS/WL2K KE8FMJ/WL2K QRU KC3QVF/WL2K ZBO 1 P WXOBSREP ZOK IP ZBO 1 R QSPEDITOR ZOK IP K"
    live_meaning = explain_line(live)
    assert "W8SGM/FCEMHS/WL2K" in live_meaning
    assert "KC3QVF/WL2K" in live_meaning and "Priority" in live_meaning
    live_net = NetState("WD8SDH")
    interpret_line(live_net, live, table, True)
    assert any(t.originator == "KC3QVF/WL2K" and t.destination == "WXOBSREP" and t.precedence == "P" for t in live_net.traffic_items)
    assert "KC3QVF/WL2K" not in live_net.zks_order

    # ZOF 7547 is a serial, not 7547 messages.
    assert explain_line("KE8NHT DE N8ADO ZOF 7547 N2QFD K") == "N8ADO directs KE8NHT to send serial 7547 to N2QFD."

    from explain import file_start
    started = file_start("<FILE 58 431B>{1740}20260111180736:US--READYX-7547-R-ZEU-ALTTP2-1463.TXT")
    assert started["serial"] == "7547" and started["queue"] == "1740" and started["destination"] == "READYX"

    assert "confirmed" in explain_line("<MISSING 15 A772>{1740}CONFIRMED").lower()
    assert "serial 7547" in explain_line("N8ADO DE K8ZD ZEV 7547 AR")

    # ZUG IP ZNB ABC is a real authenticator, not "I cannot authenticate".
    fresh = NetState("N8TAG")
    interpret_line(fresh, f"K8BCI DE KE8NHT ZKE WL2K ZUG IP ZNB {good} QRU K", table, True)
    assert not fresh.stations["KE8NHT"].zug_znb
    assert "KE8NHT" in fresh.zks_order

    from protocol import script_checkin
    assert script_checkin("N8TAG") == "OPIN DE N8TAG ZKA ZKB INT ZKE K"

    print("all protocol checks passed")
    print("sample ZKS:", zks)
    print("follow-up:", net.get_completed_summary()[-1])


if __name__ == "__main__":
    main()
