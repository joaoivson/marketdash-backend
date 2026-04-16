#!/usr/bin/env python3
"""
Apply database migrations directly to Supabase PostgreSQL

Usage:
    python apply_sup abase_direct.py migrations/004_add_performance_indexes.sql
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv

def apply_migration(migration_file: str):
    """Apply SQL migration to Supabase database."""
    
    # Supabase connection string from docker-compose.yml
    # Load environment variables
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("❌ ERROR: DATABASE_URL not found in .env")
        sys.exit(1)
    # URL-encoded password is required for psycopg2 to parse correctly when password contains @
    # database_url = database_url.replace("%40", "@") # DO NOT REPLACE    
    # Read migration file
    if not os.path.exists(migration_file):
        print(f"❌ ERROR: Migration file not found: {migration_file}")
        sys.exit(1)
    
    with open(migration_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    print(f"📄 Applying migration: {migration_file}")
    print(f"🔗 Database: Supabase (db.rsejwvxealraianensoz.supabase.co)")
    
    try:
        # Connect to Supabase database
        print("🔌 Connecting to Supabase...")
        password = "K@pilc@2804@"
        conn = psycopg2.connect(
            host="db.iprdyorxqdiivthtcvxf.supabase.co",
            port=5432,
            user="postgres",
            password=password,
            database="postgres",
            sslmode="require"
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Execute migration
        print("⚙️  Executing SQL...")
        cursor.execute(sql_content)
        
        print(f"✅ Migration applied successfully!")
        
        cursor.close()
        conn.close()
        
    except psycopg2.Error as e:
        print(f"❌ ERROR applying migration:")
        print(f"   {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python apply_supabase_direct.py <migration_file.sql>")
        sys.exit(1)
    
    migration_file = sys.argv[1]
    apply_migration(migration_file)
