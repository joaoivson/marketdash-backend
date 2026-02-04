#!/usr/bin/env python3
"""
Apply database migrations to PostgreSQL

Usage:
    python apply_migration.py migrations/004_add_performance_indexes.sql
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def apply_migration(migration_file: str):
    """
    Apply a SQL migration file to the database.
    
    Args:
        migration_file: Path to the SQL migration file
    """
    # Get database URL from environment
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("❌ ERROR: DATABASE_URL not found in environment variables")
        sys.exit(1)
    
    # Read migration file
    if not os.path.exists(migration_file):
        print(f"❌ ERROR: Migration file not found: {migration_file}")
        sys.exit(1)
    
    with open(migration_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    print(f"📄 Applying migration: {migration_file}")
    print(f"🔗 Database: {database_url.split('@')[1] if '@' in database_url else database_url}")
    
    try:
        # Connect to database
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Execute migration
        cursor.execute(sql_content)
        
        # Get affected rows/changes
        if cursor.rowcount > 0:
            print(f"✅ Migration applied successfully! {cursor.rowcount} rows affected.")
        else:
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
        print("Usage: python apply_migration.py <migration_file.sql>")
        sys.exit(1)
    
    migration_file = sys.argv[1]
    apply_migration(migration_file)
