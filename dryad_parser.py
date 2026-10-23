# dryad_parser.py
# Module for parsing OPIN DRYAD PDF tables and performing authentication lookups.
# Each monthly PDF contains one page per day (plus cover) with a 25-row (A-Y)
# scrambled alphabet table used for challenge-response authentication.
#
# Authentication procedure (from Black Swan / OPIN SOI and ACP 125):
#   Challenger picks a row letter (A-Y) and a letter that appears in that row.
#   The correct response is the letter directly below that letter (same column,
#   next row). If the challenge is on row Y, the response wraps to row A
#   (or is handled as an edge case; operators normally avoid Y).
#
# This module extracts the tables and provides a simple API for lookups.

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # Will be checked at runtime


class DryadTable:
    """Holds one day's DRYAD table (rows A-Y, each a 25-character string)."""

    def __init__(self, valid_from: datetime, valid_until: datetime, rows: Dict[str, str]):
        self.valid_from = valid_from
        self.valid_until = valid_until
        self.rows = rows  # e.g. {'A': 'IZREPQXJWN...', ...}

    def is_valid_for(self, dt: datetime) -> bool:
        """Return True if the given datetime falls inside this table's cryptoperiod."""
        return self.valid_from <= dt <= self.valid_until

    def authenticate(self, row_letter: str, challenge_letter: str) -> Optional[str]:
        """
        Perform the authentication lookup.
        row_letter: the row selected by the challenger (A-Y)
        challenge_letter: the letter found in that row
        Returns the letter directly below it, or None if not found.
        """
        row_letter = row_letter.upper()
        challenge_letter = challenge_letter.upper()
        if row_letter not in self.rows:
            return None
        row_str = self.rows[row_letter]
        if challenge_letter not in row_str:
            return None
        col = row_str.index(challenge_letter)
        # Next row (wrap from Y to A)
        next_row = chr(ord(row_letter) + 1) if row_letter != 'Y' else 'A'
        if next_row not in self.rows:
            return None
        return self.rows[next_row][col]


class DryadParser:
    """Parses an OPIN DRYAD PDF into a collection of daily DryadTable objects."""

    def __init__(self):
        if pdfplumber is None:
            raise ImportError("pdfplumber is required. Install with: pip install pdfplumber")
        self.tables: List[DryadTable] = []

    def parse_pdf(self, pdf_path: str) -> List[DryadTable]:
        """
        Parse the entire PDF and return a list of DryadTable objects, one per day.
        """
        self.tables = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                table = self._parse_page(text)
                if table:
                    self.tables.append(table)
        return self.tables

    def _parse_page(self, text: str) -> Optional[DryadTable]:
        """Extract one day's table from a page's text."""
        # Look for the date range
        m = re.search(r"From:\s*(\d{6}Z\s+[A-Z]{3}\s+\d{4})", text)
        if not m:
            return None
        from_str = m.group(1)
        m2 = re.search(r"Until:\s*(\d{6}Z\s+[A-Z]{3}\s+\d{4})", text)
        until_str = m2.group(1) if m2 else from_str

        try:
            valid_from = datetime.strptime(from_str, "%d%H%MZ %b %Y")
            valid_until = datetime.strptime(until_str, "%d%H%MZ %b %Y")
        except ValueError:
            return None

        # Extract data rows: lines that start with a single letter A-Y followed by groups of letters
        rows: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            # Match lines like "A IZRE PQX JWN SV FU YHT LM CD BK OG"
            # Skip header lines that contain the sequential alphabet groups
            m = re.match(r"^([A-Y])\s+([A-Z ]+)$", line)
            if m:
                letter = m.group(1)
                groups = m.group(2).split()
                # Concatenate the letter groups into one 25-char string
                row_str = "".join(groups)
                # Header lines produce nearly sequential alphabets; skip those.
                # A real data row will not be a contiguous sequential run.
                if (len(row_str) == 25
                        and letter not in rows
                        and not row_str.startswith("BCDEFGHIJKLMNOPQRSTUVWXYZ")
                        and not row_str.startswith("ABCDEFGHIJKLMNOPQRSTUVWXYZ")):
                    rows[letter] = row_str

        if len(rows) == 25:  # A through Y
            return DryadTable(valid_from, valid_until, rows)
        return None

    def get_table_for(self, dt: datetime) -> Optional[DryadTable]:
        """Return the DryadTable that is valid for the given datetime, or None."""
        for t in self.tables:
            if t.is_valid_for(dt):
                return t
        return None


# Simple self-test when run directly
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = DryadParser()
        tables = parser.parse_pdf(sys.argv[1])
        print(f"Parsed {len(tables)} daily tables.")
        if tables:
            t = tables[0]
            print(f"First table valid from {t.valid_from} to {t.valid_until}")
            print(f"Row A: {t.rows.get('A')}")
            # Example lookup
            resp = t.authenticate("A", "I")
            print(f"Authenticate A / I → {resp}")
    else:
        print("Usage: python dryad_parser.py <path-to-dryad.pdf>")
