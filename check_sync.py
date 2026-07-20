#!/usr/bin/env python3
"""
בדיקה מלאה של סנכרון תוכניות אימון
"""

import json
import sys
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("/Users/topaz/clode/data") if Path("/Users/topaz/clode/data").exists() else Path("/Users/topaz/clode")

print("=" * 80)
print("📋 בדיקת סנכרון תוכניות אימון")
print("=" * 80)

# 1. בדוק pending_plans.json
print("\n1️⃣  קובץ pending_plans:")
pending_file = Path("/Users/topaz/clode/pending_plans.json")
if pending_file.exists():
    with open(pending_file, 'r', encoding='utf-8') as f:
        pending = json.load(f)
    print(f"   ✓ קיים ({pending_file})")
    print(f"   משתמשים: {list(pending.keys())}")
    for uid, content in pending.items():
        if isinstance(content, dict):
            print(f"   User {uid}:")
            print(f"     - branch: {content.get('branch', 'N/A')}")
            print(f"     - plan_date: {content.get('plan_date', 'N/A')}")
            print(f"     - reply snippet: {str(content.get('reply', ''))[:100]}...")
        else:
            print(f"   User {uid}: {str(content)[:100]}...")
else:
    print(f"   ❌ לא קיים ({pending_file})")

# 2. בדוק training_log.json
print("\n2️⃣  קובץ training_log:")
log_file = DATA_DIR / "training_log.json"
if log_file.exists():
    with open(log_file, 'r', encoding='utf-8') as f:
        logs = json.load(f)
    print(f"   ✓ קיים ({log_file})")

    # חפש entries מ-20/7 (היום)
    recent = [l for l in logs if "2026-07-20" in str(l.get('date', ''))]
    if recent:
        print(f"   entries מ-20/7: {len(recent)}")
        for entry in recent[-3:]:
            print(f"     - {entry.get('date')}: {entry.get('branch')} - {entry.get('group')}")
    else:
        print("   אין entries מ-20/7")
else:
    print(f"   ⚠️  לא קיים ({log_file})")

# 3. בדוק logs אם קיימים
print("\n3️⃣  קובץ לוג סנכרון:")
log_sync = Path.home() / "logs" / "training_plans_sync.log"
if log_sync.exists():
    print(f"   ✓ קיים ({log_sync})")
    with open(log_sync, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # חפש entries מ-20/7
    recent_lines = [l for l in lines if "2026-07-20" in l]
    if recent_lines:
        print(f"   entries מ-20/7: {len(recent_lines)}")
        print("\n   📝 Last 10 entries:")
        for line in recent_lines[-10:]:
            print(f"      {line.strip()}")
    else:
        print("   אין entries מ-20/7")
else:
    print(f"   ⚠️  לא קיים ({log_sync}) — לא הפעל עדיין")

# 4. בדוק action_history
print("\n4️⃣  קובץ action_history:")
action_file = DATA_DIR / "action_history.json"
if action_file.exists():
    with open(action_file, 'r', encoding='utf-8') as f:
        actions = json.load(f)
    print(f"   ✓ קיים ({action_file})")

    # חפש plan_save actions
    plan_saves = [a for a in actions if "plan" in str(a).lower()]
    if plan_saves:
        print(f"   plan actions: {len(plan_saves)}")
        for action in plan_saves[-3:]:
            print(f"     - {action}")
    else:
        print("   אין plan actions")
else:
    print(f"   ⚠️  לא קיים ({action_file})")

print("\n" + "=" * 80)
print("✅ בדיקה הסתיימה")
print("=" * 80)
