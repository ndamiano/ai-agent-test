import sqlite3
import os
from typing import Optional


def load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """
    Load the sqlite-vec extension into a sqlite3 connection.
    
    Args:
        conn: SQLite connection to load the extension into
        
    Raises:
        RuntimeError: If sqlite-vec extension cannot be loaded
    """
    try:
        import sqlite_vec
        sqlite_vec.load(conn)
    except ImportError:
        raise RuntimeError(
            "sqlite-vec extension not found. Please install it with: pip install sqlite-vec\n"
            "Note: sqlite-vec requires SQLite compiled with extension support."
        )


def init_vector_store(db_path: Optional[str] = None) -> None:
    """
    Initialize the vector store table alongside the existing schema.
    
    Creates a virtual table called context_embeddings using sqlite-vec's vec0 format
    with columns for storing vector embeddings of context data.
    
    Args:
        db_path: Optional path to database file. If None, uses the same path as the main database.
    """
    if db_path is None:
        # Import DB_PATH from schema module to maintain consistency
        from database.schema import DB_PATH
        db_path = DB_PATH
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True) if os.path.dirname(db_path) else None
    
    with sqlite3.connect(db_path) as conn:
        # Enable extension loading
        conn.enable_load_extension(True)
        
        # Load sqlite-vec extension
        load_sqlite_vec(conn)
        
        # Create virtual table for vector embeddings using vec0 format
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS context_embeddings USING vec0(
                id TEXT PRIMARY KEY,
                context_id TEXT,
                task_id TEXT,
                embedding FLOAT[768]
            )
        """)
        
        conn.commit()


if __name__ == "__main__":
    # Initialize vector store when run directly
    init_vector_store()
    print(f"Vector store initialized at: {DB_PATH}")