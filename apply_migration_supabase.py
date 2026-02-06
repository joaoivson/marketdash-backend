#!/usr/bin/env python3
"""
Apply database migrations to PostgreSQL (Supabase or local)

Usage:
    python apply_migration_supabase.py migrations/004_add_performance_indexes.sql
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs

# Load environment variables
load_dotenv()

def get_database_url():
    """Get database URL, preferring Supabase if available."""
    # Try Supabase first
    supabase_url = os.getenv('SUPABASE_URL')
    if supabase_url:
        # Extract project ID from Supabase URL
        # Format: https://PROJECT_ID.supabase.co
        project_id = supabase_url.replace('https://', '').replace('.supabase.co', '')
        
        # Construct Supabase PostgreSQL connection string
        # Format: postgresql://postgres.[PROJECT_ID]:[PASSWORD]@aws-0-us-east-1.pooler.supabase.com:6543/postgres
        # Note: You'll need the database password from Supabase dashboard
        print(f"ℹ️  Detected Supabase project: {project_id}")
        print(f"ℹ️  Para aplicar via Supabase, use:")
        print(f"   1. Acesse: https://supabase.com/dashboard/project/{project_id}/sql")
        print(f"   2. Cole o conteúdo de: {sys.argv[1] if len(sys.argv) > 1 else 'migration.sql'}")
        print(f"   3. Execute no SQL Editor")
        return None
    
    # Fallback to DATABASE_URL
    database_url = os.getenv('DATABASE_URL')
    return database_url

def apply_migration(migration_file: str):
    """
    Apply a SQL migration file to the database.
    
    Args:
        migration_file: Path to the SQL migration file
    """
    # Get database URL from environment
    database_url = get_database_url()
    if not database_url:
        print("\n⚠️  Configure DATABASE_URL ou use Supabase SQL Editor (instruções acima)")
        return
    
    # Read migration file
    if not os.path.exists(migration_file):
        print(f"❌ ERROR: Migration file not found: {migration_file}")
        sys.exit(1)
    
    with open(migration_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    print(f"\n📄 Applying migration: {migration_file}")
    print(f"🔗 Database: {database_url.split('@')[1] if '@' in database_url else database_url}")
    print(f"\n📋 SQL to execute:")
    print("="*60)
    print(sql_content[:500] + "..." if len(sql_content) > 500 else sql_content)
    print("="*60)
    
    try:
        # Connect to database
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Execute migration
        cursor.execute(sql_content)
        
        print(f"\n✅ Migration applied successfully!")
        
        cursor.close()
        conn.close()
        
    except psycopg2.Error as e:
        print(f"\n❌ ERROR applying migration:")
        print(f"   {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python apply_migration_supabase.py <migration_file.sql>")
        sys.exit(1)
    
    migration_file = sys.argv[1]
    apply_migration(migration_file)
