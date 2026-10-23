# main.py
# OPIN NCS Console - Main application
# A cross-platform GUI to assist Net Control Station (NCS) operators
# running digital amateur radio nets that follow Black Swan / OPIN / QTC procedures.
#
# Features in this version:
#   - Load a monthly DRYAD PDF and perform authentication lookups
#   - Monitor FLDigi RX text via XML-RPC (live)
#   - Maintain ordered ZKS list of stations that check in with ZKE
#   - Log ZBO traffic and track flamp file receipt
#   - Suggest editable responses (no automatic TX)
#   - Export ICS-214 activity log
#   - Support for both NCS and station-view modes
#
# Run with: python main.py
# Requires: pip install -r requirements.txt

import sys
import os
from datetime import datetime
from typing import Optional

# GUI
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QLineEdit, QListWidget, QFileDialog,
    QTabWidget, QGroupBox, QFormLayout, QMessageBox, QComboBox, QCheckBox
)
from PyQt6.QtCore import Qt, QTimer

# Local modules
from dryad_parser import DryadParser, DryadTable
from net_state import NetState
from protocol import (
    interpret_line, script_open, script_agency, script_traffic, script_checkin,
    script_close_net, script_close_station, script_zks, script_imi, volunteer_group,
)

# Optional FLDigi integration (graceful if not running)
try:
    import xmlrpc.client
    FLDIGI_AVAILABLE = True
except ImportError:
    FLDIGI_AVAILABLE = False


# Dark olive desk, same colors as the preview.
DESK_STYLE = """
QMainWindow, QWidget { background: #12160f; color: #e7eedc; font-size: 14px; }
QLabel#title { font-size: 22px; font-weight: 600; }
QLabel#muted { color: #9aa58c; }
QLineEdit, QTextEdit, QListWidget, QComboBox {
  background: #12160f; color: #e7eedc; border: 1px solid #33402a;
  border-radius: 8px; padding: 6px;
  selection-background-color: #d4a24a; selection-color: #1a1408;
}
QTextEdit, QListWidget { font-family: Menlo, Consolas, monospace; }
QPushButton {
  background: #26301e; color: #e7eedc; border: 1px solid #33402a;
  border-radius: 8px; padding: 8px 12px; min-height: 28px;
}
QPushButton:hover { border-color: #d4a24a; }
QPushButton#accent { background: #d4a24a; color: #1a1408; font-weight: 600; }
QTabWidget::pane { border: 1px solid #33402a; background: #1c2416; }
QTabBar::tab {
  background: #26301e; color: #e7eedc; padding: 8px 14px;
  border: 1px solid #33402a;
}
QTabBar::tab:selected { background: #d4a24a; color: #1a1408; }
QStatusBar { background: #1c2416; color: #9aa58c; }
"""


class OpinNCSApp(QMainWindow):
    """Main window for the OPIN NCS Console."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OPIN Net control desk")
        self.resize(1200, 800)
        self.setStyleSheet(DESK_STYLE)

        # Core objects
        self.dryad_parser = DryadParser()
        self.current_dryad: Optional[DryadTable] = None
        self.net = NetState(ncs_callsign="N8TAG")  # Change to your callsign
        self.fldigi = None
        self.last_rx_text = ""

        self._build_ui()
        self._start_timers()

    def _build_ui(self):
        """Construct the main user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        title = QLabel("Net control desk")
        title.setObjectName("title")
        main_layout.addWidget(title)
        subtitle = QLabel(
            "Same layout as the preview. This window also listens to FLDigi. "
            "Nothing is sent until you click Send via FLDigi."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        main_layout.addWidget(subtitle)

        # Top bar: DRYAD load + mode + NCS callsign
        top = QHBoxLayout()
        self.btn_load_dryad = QPushButton("Load DRYAD PDF...")
        self.btn_load_dryad.clicked.connect(self.load_dryad)
        top.addWidget(self.btn_load_dryad)

        self.lbl_dryad_status = QLabel("No DRYAD loaded")
        top.addWidget(self.lbl_dryad_status)
        self.used_label = QLabel("No combinations spent")
        self.used_label.setObjectName("muted")
        top.addWidget(self.used_label)

        top.addStretch()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["NCS", "Station"])
        top.addWidget(QLabel("Mode:"))
        top.addWidget(self.mode_combo)

        self.callsign_edit = QLineEdit("N8TAG")
        self.callsign_edit.setMaximumWidth(100)
        self.callsign_edit.editingFinished.connect(self.apply_callsign)
        top.addWidget(QLabel("My Call:"))
        top.addWidget(self.callsign_edit)
        self.band_edit = QLineEdit()
        self.band_edit.setPlaceholderText("80m / 60m / 40m")
        self.band_edit.setMaximumWidth(140)
        self.band_edit.editingFinished.connect(self.apply_band)
        top.addWidget(QLabel("Band:"))
        top.addWidget(self.band_edit)
        main_layout.addLayout(top)

        # Control-script calls. Each one only fills the edit box.
        script = QHBoxLayout()
        for label, handler in (
            ("Open net", self.prepare_open),
            ("INT AGENCY", self.prepare_agency),
            ("INT ZBO", self.prepare_traffic_call),
            ("INT ZKE", self.prepare_checkin_call),
            ("IMI", self.prepare_imi),
            ("Close net", self.prepare_close),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            script.addWidget(btn)
        main_layout.addLayout(script)

        # Tabs
        tabs = QTabWidget()
        main_layout.addWidget(tabs)

        # --- Tab 1: Live Net ---
        net_tab = QWidget()
        net_layout = QHBoxLayout(net_tab)

        # Left: ZKS list + stations
        left = QVBoxLayout()
        left.addWidget(QLabel("Pending authentication"))
        self.pending_list = QListWidget()
        left.addWidget(self.pending_list)
        left.addWidget(QLabel("ZKS roll"))
        self.zks_list = QListWidget()
        left.addWidget(self.zks_list)
        self.btn_prepare_zks = QPushButton("ZKS roll")
        self.btn_prepare_zks.clicked.connect(self.prepare_zks_message)
        left.addWidget(self.btn_prepare_zks)
        self.btn_close_one = QPushButton("ZKJ selected station")
        self.btn_close_one.clicked.connect(self.prepare_close_one)
        left.addWidget(self.btn_close_one)
        net_layout.addLayout(left, 1)

        # Center: RX monitor + suggested response
        center = QVBoxLayout()
        center.addWidget(QLabel("What FLDigi printed"))
        self.rx_text = QTextEdit()
        self.rx_text.setReadOnly(True)
        center.addWidget(self.rx_text, 2)

        center.addWidget(QLabel("Suggested reply — edit, then send"))
        self.response_edit = QTextEdit()
        center.addWidget(self.response_edit, 1)

        btn_row = QHBoxLayout()
        self.btn_send_response = QPushButton("Send via FLDigi")
        self.btn_send_response.setObjectName("accent")
        self.btn_send_response.clicked.connect(self.send_response)
        btn_row.addWidget(self.btn_send_response)
        self.btn_clear_response = QPushButton("Clear")
        self.btn_clear_response.clicked.connect(lambda: self.response_edit.clear())
        btn_row.addWidget(self.btn_clear_response)
        center.addLayout(btn_row)
        net_layout.addLayout(center, 2)

        # Right: Traffic + Completed (Winlink follow-up)
        right = QVBoxLayout()
        right.addWidget(QLabel("Active traffic"))
        self.traffic_list = QListWidget()
        right.addWidget(self.traffic_list)
        self.btn_complete = QPushButton("Move selected to follow-up")
        self.btn_complete.clicked.connect(self.complete_selected)
        right.addWidget(self.btn_complete)
        self.follow_hint = QLabel("Finished files are on the Follow-up tab.")
        self.follow_hint.setWordWrap(True)
        right.addWidget(self.follow_hint)
        net_layout.addLayout(right, 1)

        tabs.addTab(net_tab, "Net")

        # --- Tab 2: Authentication ---
        auth_tab = QWidget()
        auth_layout = QVBoxLayout(auth_tab)
        form = QFormLayout()
        self.auth_row = QLineEdit()
        self.auth_row.setMaximumWidth(40)
        self.auth_letter = QLineEdit()
        self.auth_letter.setMaximumWidth(40)
        form.addRow("Row letter (A-Y):", self.auth_row)
        form.addRow("Challenge letter:", self.auth_letter)
        auth_layout.addLayout(form)
        self.btn_auth_lookup = QPushButton("Lookup Response")
        self.btn_auth_lookup.clicked.connect(self.do_auth_lookup)
        auth_layout.addWidget(self.btn_auth_lookup)
        self.auth_result = QLabel("Response will appear here")
        auth_layout.addWidget(self.auth_result)
        auth_layout.addStretch()
        tabs.addTab(auth_tab, "Authentication")

        # --- Follow-up: who has the file, who still needs Winlink ---
        follow_tab = QWidget()
        follow_layout = QVBoxLayout(follow_tab)
        follow_layout.addWidget(QLabel(
            "A listing moves here after it has been ZOF'd and every station "
            "on the ZKS list has sent ZEV (has it) or ZUG ZEV (does not — send it on Winlink). "
            "You can also move one here yourself from the Net tab."
        ))
        self.completed_list = QListWidget()
        follow_layout.addWidget(self.completed_list)
        tabs.addTab(follow_tab, "Follow-up")

        # --- Tab 3: Logs / Export ---
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        export_row = QHBoxLayout()
        self.btn_export_ics214 = QPushButton("Export ICS-214")
        self.btn_export_ics214.clicked.connect(self.export_ics214)
        export_row.addWidget(self.btn_export_ics214)
        self.btn_export_json = QPushButton("Save Session JSON")
        self.btn_export_json.clicked.connect(self.export_json)
        export_row.addWidget(self.btn_export_json)
        self.btn_export_session = QPushButton("OPIN session report")
        self.btn_export_session.clicked.connect(self.export_session)
        export_row.addWidget(self.btn_export_session)
        log_layout.addLayout(export_row)
        tabs.addTab(log_tab, "Logs / Export")

        # Status bar
        self.statusBar().showMessage("Ready. Load a DRYAD PDF to begin.")

    def _start_timers(self):
        """Start the timer that polls FLDigi for new RX text."""
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll_fldigi)
        self.timer.start(1500)  # every 1.5 seconds

    # -------------------------------------------------------------------------
    # DRYAD
    # -------------------------------------------------------------------------
    def load_dryad(self):
        """Open a file dialog and load a DRYAD PDF."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select DRYAD PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        try:
            tables = self.dryad_parser.parse_pdf(path)
            if not tables:
                QMessageBox.warning(self, "DRYAD", "No valid tables found in PDF.")
                return
            # Use today's table if possible, otherwise the first one
            today = datetime.utcnow()
            self.current_dryad = self.dryad_parser.get_table_for(today) or tables[0]
            self.lbl_dryad_status.setText(
                f"DRYAD loaded: {os.path.basename(path)} "
                f"({len(tables)} days, using {self.current_dryad.valid_from.strftime('%d %b')})"
            )
            self.statusBar().showMessage(f"Loaded {len(tables)} daily tables from {path}")
            self.net.log(f"Loaded DRYAD from {path}")
        except Exception as e:
            QMessageBox.critical(self, "DRYAD Error", str(e))

    def do_auth_lookup(self):
        """Look up the authentication response for the entered challenge."""
        if not self.current_dryad:
            self.auth_result.setText("Load a DRYAD first.")
            return
        row = self.auth_row.text().strip().upper()
        letter = self.auth_letter.text().strip().upper()
        if not row or not letter:
            self.auth_result.setText("Enter both row and letter.")
            return
        resp = self.current_dryad.authenticate(row, letter)
        if resp:
            self.auth_result.setText(f"I AUTHENTICATE {resp}  (ZNB {resp})")
            # Also put it in the response box for easy use
            self.response_edit.setPlainText(f"ZNB {resp}")
        else:
            self.auth_result.setText("Not found in current table.")

    # -------------------------------------------------------------------------
    # FLDigi integration
    # -------------------------------------------------------------------------
    def connect_fldigi(self):
        """Connect to a running FLDigi XML-RPC server (default port 7362)."""
        if not FLDIGI_AVAILABLE:
            return
        try:
            self.fldigi = xmlrpc.client.ServerProxy("http://127.0.0.1:7362")
            # Test the connection
            _ = self.fldigi.fldigi.name()
            self.statusBar().showMessage("Connected to FLDigi")
        except Exception:
            self.fldigi = None

    def poll_fldigi(self):
        """Called by timer: pull new RX text from FLDigi and parse it."""
        if self.fldigi is None:
            self.connect_fldigi()
            return
        try:
            # Get the current RX text buffer
            rx = self.fldigi.text.get_rx() or ""
            if rx != self.last_rx_text:
                new_part = rx[len(self.last_rx_text):] if rx.startswith(self.last_rx_text) else rx
                self.last_rx_text = rx
                self.rx_text.append(new_part)
                self.parse_rx_text(new_part)
        except Exception:
            self.fldigi = None  # will retry on next poll

    def parse_rx_text(self, text: str):
        """
        Hand each received line to the protocol module.
        The speaker is the callsign after DE. A volunteered ZNB of three
        letters is checked against the DRYAD before anyone is put on the ZKS
        list. The suggestion is only placed in the edit box — nothing is sent.
        """
        self.apply_callsign()
        for raw_line in text.splitlines():
            suggestion = interpret_line(
                self.net,
                raw_line,
                self.current_dryad,
                self.mode_combo.currentText() == "NCS",
            )
            if suggestion:
                self.response_edit.setPlainText(suggestion)
                self.net.last_tx = suggestion
        self.refresh_zks_display()
        self.refresh_traffic_display()
        self.refresh_log()
        self.refresh_used()

    def refresh_used(self):
        n = len(self.net.used_groups)
        if n == 0:
            self.used_label.setText("No combinations spent")
        elif n == 1:
            self.used_label.setText("1 combination spent")
        else:
            self.used_label.setText(f"{n} combinations spent")

    def _extract_flamp_filename(self, line: str) -> Optional[str]:
        """Pull a filename from an FLAMP-style line (File : name.txt or <FILE ...>)."""
        if "FILE :" in line:
            try:
                after = line.split("FILE :")[1].strip().split()[0]
                return after.strip("<>")
            except IndexError:
                return None
        if "<FILE" in line:
            # crude extract of the filename token
            parts = line.replace("<", " ").replace(">", " ").split()
            for p in parts:
                if p.lower().endswith(".txt") or "." in p:
                    return p
        return None

    def _most_recent_file(self) -> Optional[str]:
        """Return the filename of the most recently seen traffic item that has a file name."""
        for item in reversed(self.net.traffic_items):
            if item.file_name:
                return item.file_name
        return None

    def _extract_callsign(self, line: str) -> Optional[str]:
        """
        Extract the station callsign from a line.
        Prefers the callsign that appears before DE (the originator).
        Falls back to any token that looks like a callsign.
        """
        # Prefer "CALL DE ..." pattern
        if " DE " in line:
            before = line.split(" DE ")[0].strip().split()
            if before:
                cand = before[-1].strip(".,")
                if self._looks_like_callsign(cand):
                    return cand
        # Fallback: any callsign-like token
        for p in line.replace("DE", " ").split():
            p = p.strip(".,")
            if self._looks_like_callsign(p):
                return p
        return None

    def _looks_like_callsign(self, token: str) -> bool:
        """Basic amateur callsign test used by the parser."""
        if not token or len(token) < 3 or len(token) > 8:
            return False
        if not token[0].isalpha():
            return False
        if not any(c.isdigit() for c in token):
            return False
        # Reject pure noise
        return token.replace("/", "").isalnum()

    def _extract_znb_group(self, line: str) -> Optional[str]:
        """
        Extract a ZNB response group.
        Looks for ZNB followed by a 1–3 letter token (ISZ, GIO, MIC, IS, etc.).
        """
        tokens = line.split()
        if "ZNB" not in tokens:
            return None
        idx = tokens.index("ZNB")
        if idx + 1 < len(tokens):
            cand = tokens[idx + 1].strip(".,")
            if 1 <= len(cand) <= 3 and cand.isalpha():
                return cand
        return None

    def _generate_challenge(self) -> Optional[str]:
        """
        Generate a two-letter INT ZNB challenge from the current DRYAD table.
        Returns a string like "AB" or None if no DRYAD is loaded.
        """
        if not self.current_dryad:
            return None
        import random
        rows = [r for r in self.current_dryad.rows.keys() if r != "Y"]
        if not rows:
            return None
        row = random.choice(rows)
        row_str = self.current_dryad.rows[row]
        letter = random.choice(row_str)
        return f"{row}{letter}"

    def send_response(self):
        """Send the contents of the response box to FLDigi for transmission."""
        text = self.response_edit.toPlainText().strip()
        if not text or not self.fldigi:
            QMessageBox.information(self, "Send", "Nothing to send or FLDigi not connected.")
            return
        try:
            # Put the text into FLDigi's TX buffer and start transmitting
            self.fldigi.text.add_tx(text + "\n")
            self.fldigi.main.tx()
            self.net.last_tx = text
            self.net.log(f"TX: {text}")
            self.refresh_log()
            self.statusBar().showMessage("Sent to FLDigi")
        except Exception as e:
            QMessageBox.warning(self, "Send Error", str(e))

    # -------------------------------------------------------------------------
    # Display helpers
    # -------------------------------------------------------------------------
    def apply_callsign(self):
        call = self.callsign_edit.text().strip().upper()
        if call:
            self.net.ncs_callsign = call
            self.callsign_edit.setText(call)

    def apply_band(self):
        self.net.band = self.band_edit.text().strip()

    def _offer(self, text: str):
        """Put a prepared transmission in the edit box. Does not transmit."""
        self.apply_callsign()
        self.response_edit.setPlainText(text)
        self.net.last_tx = text
        self.net.log(f"Prepared: {text}")
        self.refresh_log()

    def prepare_open(self):
        """ZKA ZKB, plus a ZNB from today's DRYAD so the net can check net control."""
        self.apply_callsign()
        znb = None
        if self.current_dryad:
            znb = None
            group = volunteer_group(
                self.current_dryad.rows, self.net.used_groups, self.current_dryad.authenticate
            )
            if group:
                self.net.used_groups.add(group[:2])
                znb = group
                self.net.log(f"Opened with ZNB {znb}. That combination cannot be used again.")
            else:
                self.net.log("No unused DRYAD combination left for the open")
        else:
            self.net.log("Opened without a ZNB — load today's DRYAD first")
        self._offer(script_open(self.net.ncs_callsign, znb))
        self.refresh_used()

    def prepare_agency(self):
        self._offer(script_agency(self.net.ncs_callsign))

    def prepare_traffic_call(self):
        self._offer(script_traffic(self.net.ncs_callsign))

    def prepare_checkin_call(self):
        self._offer(script_checkin(self.net.ncs_callsign))

    def prepare_close(self):
        self._offer(script_close_net(self.net.ncs_callsign))

    def prepare_imi(self):
        """Ask the last station we heard, other than ourselves, to say again."""
        self.apply_callsign()
        target = None
        for call in reversed(list(self.net.stations)):
            if call != self.net.ncs_callsign:
                target = call
                break
        if not target:
            self.statusBar().showMessage("No station to send IMI to yet")
            return
        self._offer(script_imi(self.net.ncs_callsign, target))

    def prepare_close_one(self):
        item = self.zks_list.currentItem()
        if not item:
            self.statusBar().showMessage("Select a station on the ZKS list first")
            return
        call = item.text().split()[0]
        self._offer(script_close_station(self.net.ncs_callsign, call))

    def complete_selected(self):
        row = self.traffic_list.currentRow()
        if row < 0:
            self.statusBar().showMessage("Select a traffic line first")
            return
        self.net.mark_complete(row)
        self.refresh_traffic_display()
        self.refresh_log()

    def refresh_zks_display(self):
        """Pending stations stay off the roll until ZNB checks or they send ZUG ZNB."""
        self.pending_list.clear()
        for call in self.net.pending_calls():
            self.pending_list.addItem(call)
        self.zks_list.clear()
        for call in self.net.zks_order:
            st = self.net.stations.get(call)
            if st and st.zug_znb:
                self.zks_list.addItem(f"{call}  ZUG ZNB")
            else:
                self.zks_list.addItem(call)

    def prepare_zks_message(self):
        """OPIN DE <NCS> ZKS <callsigns only>."""
        self.apply_callsign()
        msg = script_zks(self.net.ncs_callsign, self.net.zks_order)
        self._offer(msg)
        if not self.net.zks_order:
            self.statusBar().showMessage("ZKS list is empty")

    def refresh_traffic_display(self):
        self.traffic_list.clear()
        for item in self.net.traffic_items:
            waiting = self.net.unanswered(item)
            base = (
                f"{item.originator}: {item.quantity} {item.precedence} {item.destination}"
                f"  ZOF={'yes' if item.zof_sent else 'no'}"
                f"  ZEV={len(item.received_by)} ZUG={len(item.not_received_by)}"
                f"  waiting={len(waiting)}"
            )
            if item.file_name:
                base = f"[{item.file_name}] " + base
            self.traffic_list.addItem(base)
        self.completed_list.clear()
        for summary in self.net.get_completed_summary():
            name = summary["file"] or summary["destination"]
            self.completed_list.addItem(
                f"{name}  has it: {', '.join(summary['received']) or 'none'}"
                f"   |   Winlink: {', '.join(summary['not_received']) or 'none'}"
                f"   |   no report: {', '.join(summary['no_report']) or 'none'}"
            )
        n = len(self.net.completed_traffic)
        self.follow_hint.setText(
            f"{n} file{'s' if n != 1 else ''} on the Follow-up tab."
        )

    def refresh_log(self):
        lines = [
            f"{e['time'][11:16]}  {e['source']:8}  {e['message']}"
            for e in self.net.log_entries[-200:]
        ]
        self.log_text.setPlainText("\n".join(lines))

    def export_ics214(self):
        text = self.net.export_ics214()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ICS-214", "ICS-214.txt", "Text Files (*.txt)"
        )
        if path:
            with open(path, "w") as f:
                f.write(text)
            self.statusBar().showMessage(f"ICS-214 saved to {path}")

    def export_session(self):
        self.apply_band()
        text = self.net.export_session_report()
        path, _ = QFileDialog.getSaveFileName(
            self, "OPIN RF session report", "OPIN-session-report.txt", "Text Files (*.txt)"
        )
        if path:
            with open(path, "w") as f:
                f.write(text)
            self.statusBar().showMessage(f"Session report saved to {path}")

    def export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Session", "session.json", "JSON Files (*.json)"
        )
        if path:
            with open(path, "w") as f:
                f.write(self.net.to_json())
            self.statusBar().showMessage(f"Session saved to {path}")


def main():
    app = QApplication(sys.argv)
    window = OpinNCSApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
