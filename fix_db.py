from sqlalchemy import text
from app.db.session import engine

def check_columns():
    with engine.connect() as conn:
        res = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'capture_sites'"))
        columns = [r[0] for r in res]
        print("Columns in capture_sites:")
        for col in columns:
            print(f"- {col}")
        
        if 'is_active' not in columns:
            print("\nMissing column: is_active")
            print("Adding it now...")
            conn.execute(text("ALTER TABLE capture_sites ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            conn.commit()
            print("Column is_active added successfully.")
        else:
            print("\nColumn is_active already exists.")

if __name__ == "__main__":
    check_columns()
