"""
Run this whenever a code update adds a new database column, so your EXISTING
ats.db (with real accounts/orders already in it) gets the new column added
in place, instead of you having to delete the database and lose all accounts.

SQLAlchemy's create_all() (called on every server startup) only creates
tables that don't exist yet — it never ALTERs an existing table. This script
is the deliberate, explicit place that does that, so schema changes stay
debuggable: one file to check, one command to run, never a silent surprise.

Usage (from the backend/ directory):
    python3 scripts/migrate.py

Safe to run multiple times — it only adds a column if it's actually missing.
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ats.db")

# Each entry: (table, column, SQL type + default) to ensure exists.
# Add a new line here every time a future change adds a column to database.py.
REQUIRED_COLUMNS = [
    ("orders", "currency", "TEXT DEFAULT 'INR'"),
]


def migrate_sqlite(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for table, column, coltype in REQUIRED_COLUMNS:
        cur.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cur.fetchall()}
        if not existing:
            print(f"[skip] table '{table}' doesn't exist yet — it'll be created fresh with the right columns on next server start.")
            continue
        if column in existing:
            print(f"[ok]   {table}.{column} already exists")
        else:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            print(f"[done] added {table}.{column}")
    conn.commit()
    conn.close()


def main():
    if not DATABASE_URL.startswith("sqlite"):
        print("DATABASE_URL is not SQLite — this script only handles SQLite.")
        print("For Postgres, use a real migration tool (Alembic) or run the equivalent ALTER TABLE by hand.")
        sys.exit(1)
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        print(f"No database file at {db_path} yet — nothing to migrate, it'll be created fresh on next server start.")
        return
    migrate_sqlite(db_path)
    print("\nMigration check complete.")


if __name__ == "__main__":
    main()
