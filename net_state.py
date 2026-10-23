# net_state.py
# Live state of one OPIN digital net: who is pending authentication,
# who is on the ZKS roll, which ZBO listings are still open, and which
# files have been acknowledged (ZEV) or refused (ZUG ZEV) so they can
# be finished on Winlink after the net.

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set
import json


@dataclass
class Station:
    callsign: str
    checkin_time: datetime
    last_heard: datetime
    notes: str = ""
    traffic: List[dict] = field(default_factory=list)
    authenticated: bool = False
    pending_auth: bool = True
    last_challenge: Optional[str] = None
    last_volunteered_znb: Optional[str] = None
    auth_challenges: List[str] = field(default_factory=list)
    zug_znb: bool = False


@dataclass
class TrafficItem:
    """One ZBO listing."""
    originator: str
    quantity: int
    precedence: str
    destination: str
    reported_time: datetime
    file_name: Optional[str] = None
    received_by: Set[str] = field(default_factory=set)       # ZEV
    not_received_by: Set[str] = field(default_factory=set)   # ZUG ZEV
    zof_sent: bool = False
    completed: bool = False
    notes: str = ""


class NetState:
    def __init__(self, ncs_callsign: str = "N8TAG"):
        self.ncs_callsign = ncs_callsign.upper()
        self.stations: Dict[str, Station] = {}
        self.zks_order: List[str] = []
        self.traffic_items: List[TrafficItem] = []
        self.completed_traffic: List[TrafficItem] = []
        self.session_start = datetime.utcnow()
        self.log_entries: List[dict] = []
        self.last_tx: str = ""
        self.band: str = ""

    def log(self, message: str, source: str = "SYSTEM"):
        self.log_entries.append({
            "time": datetime.utcnow().isoformat() + "Z",
            "source": source,
            "message": message,
        })

    def check_in(self, callsign: str, raw_text: str = "") -> Station:
        """Record a ZKE. Not placed on the ZKS list until authentication or ZUG ZNB."""
        callsign = callsign.upper()
        now = datetime.utcnow()
        if callsign not in self.stations:
            st = Station(callsign=callsign, checkin_time=now, last_heard=now)
            self.stations[callsign] = st
            self.log(f"{callsign} checked in (ZKE) — pending authentication", source=callsign)
        else:
            st = self.stations[callsign]
            st.last_heard = now
        if raw_text:
            st.notes += f"\n[{now.strftime('%H%M')}] {raw_text}"
        return st

    def add_to_zks(self, callsign: str, reason: str = "authenticated"):
        callsign = callsign.upper()
        if callsign not in self.zks_order and callsign in self.stations:
            self.zks_order.append(callsign)
            self.log(f"{callsign} added to ZKS ({reason})", source=callsign)

    def mark_authenticated(self, callsign: str):
        callsign = callsign.upper()
        if callsign in self.stations:
            st = self.stations[callsign]
            st.authenticated = True
            st.pending_auth = False
            self.add_to_zks(callsign, "DRYAD authenticated")

    def mark_zug_znb(self, callsign: str):
        """ZUG ZNB — on the roll with no DRYAD check. Creates the station if needed."""
        callsign = callsign.upper()
        if callsign not in self.stations:
            self.check_in(callsign, "ZUG ZNB")
        st = self.stations[callsign]
        st.zug_znb = True
        st.pending_auth = False
        st.authenticated = False
        self.add_to_zks(callsign, "ZUG ZNB")

    def add_traffic(self, originator: str, quantity: int, precedence: str,
                    destination: str, raw_text: str = "") -> TrafficItem:
        originator = originator.upper()
        now = datetime.utcnow()
        item = TrafficItem(
            originator=originator,
            quantity=quantity,
            precedence=precedence.upper(),
            destination=destination.upper(),
            reported_time=now,
        )
        for existing in reversed(self.traffic_items):
            age = abs((now - existing.reported_time).total_seconds())
            if (existing.originator == originator
                    and existing.quantity == quantity
                    and existing.precedence == item.precedence
                    and existing.destination == item.destination
                    and age < 900):
                self.log(
                    f"{originator} repeated ZBO {quantity} {item.precedence} {item.destination} — already listed"
                )
                return existing
        self.traffic_items.append(item)
        if originator in self.stations:
            self.stations[originator].traffic.append({
                "quantity": quantity,
                "precedence": precedence,
                "destination": destination,
                "time": item.reported_time.isoformat(),
            })
        self.log(
            f"{originator} has traffic: {quantity} {precedence} for {destination}",
            source=originator,
        )
        return item

    def mark_zof(self, file_name: str):
        """The listing has been directed for relay (ZOF). Attach the filename when we have one."""
        for item in reversed(self.traffic_items):
            if item.file_name == file_name or item.file_name is None:
                item.file_name = file_name
                item.zof_sent = True
                self.log(f"ZOF for {file_name}")
                return
        self.log(f"ZOF seen for {file_name} with no open ZBO")

    def record_zev(self, callsign: str, file_name: Optional[str] = None, negative: bool = False):
        """
        ZEV = received. ZUG ZEV = do not acknowledge (SOI), which this net
        treats as 'I do not have the file' so it can go out on Winlink later.
        When every station on the ZKS roll has answered, the listing leaves
        the active list.
        """
        callsign = callsign.upper()
        target = None
        if file_name:
            for item in self.traffic_items:
                if item.file_name == file_name:
                    target = item
                    break
        if target is None:
            for item in reversed(self.traffic_items):
                if item.zof_sent and not item.completed:
                    target = item
                    break
        if target is None and self.traffic_items:
            target = self.traffic_items[-1]
        if target is None:
            self.log(f"{callsign} sent {'ZUG ZEV' if negative else 'ZEV'} but no traffic is open")
            return
        if file_name and not target.file_name:
            target.file_name = file_name
        if negative:
            target.not_received_by.add(callsign)
            target.received_by.discard(callsign)
            self.log(f"{callsign} ZUG ZEV — does not have {target.file_name or target.destination}")
        else:
            target.received_by.add(callsign)
            target.not_received_by.discard(callsign)
            self.log(f"{callsign} ZEV — has {target.file_name or target.destination}")
        answered = target.received_by | target.not_received_by
        if target.zof_sent and set(self.zks_order).issubset(answered) and self.zks_order:
            target.completed = True
            self.completed_traffic.append(target)
            self.traffic_items.remove(target)
            self.log(f"Completed {target.file_name or target.destination} — on the Winlink follow-up list")

    def mark_complete(self, index: int) -> bool:
        """Move a listing to follow-up even if some stations have not answered."""
        if index < 0 or index >= len(self.traffic_items):
            return False
        item = self.traffic_items.pop(index)
        item.completed = True
        item.zof_sent = True
        self.completed_traffic.append(item)
        self.log(f"Operator marked complete: {item.file_name or item.destination}")
        return True

    def pending_calls(self) -> List[str]:
        return [
            c for c, s in self.stations.items()
            if s.pending_auth and c not in self.zks_order
        ]

    def unanswered(self, item: TrafficItem) -> List[str]:
        answered = item.received_by | item.not_received_by
        return [c for c in self.zks_order if c not in answered]

    def get_completed_summary(self) -> List[dict]:
        result = []
        for item in self.completed_traffic:
            result.append({
                "file": item.file_name,
                "originator": item.originator,
                "precedence": item.precedence,
                "destination": item.destination,
                "received": sorted(item.received_by),
                "not_received": sorted(item.not_received_by),
                "no_report": [c for c in self.zks_order if c not in item.received_by and c not in item.not_received_by],
            })
        return result

    def get_zks_list(self) -> str:
        return " ".join(self.zks_order)

    def export_ics214(self) -> str:
        lines = [
            "ICS-214 ACTIVITY LOG",
            f"NCS: {self.ncs_callsign}",
            f"Session start: {self.session_start.strftime('%Y-%m-%d %H:%M')} UTC",
            f"Band: {self.band or '(not set)'}",
            "",
            "Time (UTC) | Source | Activity",
            "-" * 60,
        ]
        for e in self.log_entries:
            lines.append(f"{e['time'][11:16]} | {e['source']:8} | {e['message']}")
        lines.append("")
        lines.append("ZKS list:")
        lines.append(self.get_zks_list() or "(none)")
        return "\n".join(lines)

    def export_session_report(self) -> str:
        """
        Plain-text OPIN RF session report.
        The QTC note asks for the watch window, the bands monitored,
        every station in the net, and the traffic taken or directed.
        Structured messages count as P unless the originator said otherwise.
        """
        end = datetime.utcnow()
        minutes = int((end - self.session_start).total_seconds() // 60)
        lines = [
            "OPIN RF SESSION REPORT",
            f"NCS: {self.ncs_callsign}",
            f"Start: {self.session_start.strftime('%Y-%m-%d %H:%M')} UTC",
            f"End:   {end.strftime('%Y-%m-%d %H:%M')} UTC",
            f"Duration: {minutes} minutes",
            f"Band: {self.band or '(set this before sending)'}",
            "",
            "Stations in the net (ZKS order):",
            self.get_zks_list() or "(none)",
            "",
            "Still pending authentication:",
            " ".join(self.pending_calls()) or "(none)",
            "",
            "Traffic still open:",
        ]
        if not self.traffic_items:
            lines.append("(none)")
        for t in self.traffic_items:
            lines.append(
                f"  {t.originator} ZBO {t.quantity} {t.precedence} {t.destination}"
                f"  file={t.file_name or '-'} ZOF={t.zof_sent}"
                f"  ZEV={','.join(sorted(t.received_by)) or '-'}"
                f"  ZUG ZEV={','.join(sorted(t.not_received_by)) or '-'}"
            )
        lines.append("")
        lines.append("Follow-up after the net (Winlink for anyone who sent ZUG ZEV):")
        if not self.completed_traffic:
            lines.append("(none)")
        for s in self.get_completed_summary():
            lines.append(
                f"  {s['file'] or s['destination']} from {s['originator']} "
                f"{s['quantity'] if 'quantity' in s else ''}{s['precedence']} {s['destination']}"
            )
            lines.append(f"    received: {', '.join(s['received']) or 'none'}")
            lines.append(f"    need Winlink: {', '.join(s['not_received']) or 'none'}")
            lines.append(f"    no report: {', '.join(s['no_report']) or 'none'}")
        return "\n".join(lines)

    def to_json(self) -> str:
        data = {
            "ncs_callsign": self.ncs_callsign,
            "session_start": self.session_start.isoformat(),
            "band": self.band,
            "zks_order": self.zks_order,
            "stations": {c: {
                "checkin_time": s.checkin_time.isoformat(),
                "last_heard": s.last_heard.isoformat(),
                "notes": s.notes,
                "authenticated": s.authenticated,
                "pending_auth": s.pending_auth,
                "zug_znb": s.zug_znb,
            } for c, s in self.stations.items()},
            "traffic": [{
                "originator": t.originator,
                "quantity": t.quantity,
                "precedence": t.precedence,
                "destination": t.destination,
                "file_name": t.file_name,
                "received_by": list(t.received_by),
                "not_received_by": list(t.not_received_by),
                "zof_sent": t.zof_sent,
            } for t in self.traffic_items],
            "completed": [{
                "originator": t.originator,
                "destination": t.destination,
                "file_name": t.file_name,
                "received_by": list(t.received_by),
                "not_received_by": list(t.not_received_by),
            } for t in self.completed_traffic],
            "log": self.log_entries,
        }
        return json.dumps(data, indent=2)
