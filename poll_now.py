"""
poll_now.py — Manual inbox poll trigger
─────────────────────────────────────────
Run this script from the command line to immediately check the Gmail inbox
for new messages, without waiting for the next scheduled poll.

Usage:
    python poll_now.py

Useful for:
  - Testing that Gmail is connected correctly
  - Processing a batch of emails immediately after setup
  - Debugging when you want to see output in real-time
"""

import sys
import os

# Make sure Python can find the app's modules
sys.path.insert(0, os.path.dirname(__file__))

from modules.request_processor import poll_and_process_inbox

if __name__ == "__main__":
    print("DataDealer — Manual Poll")
    print("=" * 40)
    count = poll_and_process_inbox()
    print("=" * 40)
    print(f"Done. Processed {count} email(s).")
