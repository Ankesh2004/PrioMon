import sqlite3

conn = sqlite3.connect("PrioMonDB.db")
cursor = conn.cursor()

# Get all schema creation statements
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table';")
schemas = cursor.fetchall()

print("=== Database Schema ===")
for schema in schemas:
    print(schema[0])
    print()

conn.close()