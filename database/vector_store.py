import sqlite3
import os
import uuid
from typing import List, Dict, Optional
import sqlite_vec


def load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """
    Load the sqlite-vec extension into a sqlite3 connection.
    
    Args:
        conn: SQLite connection to load the extension into
        
    Raises:
        RuntimeError: If sqlite-vec extension cannot be loaded
    """
    try:
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


def store_embedding(context_id: str, task_id: str, embedding: List[float], db_path: Optional[str] = None) -> None:
    """
    Store a vector embedding in the database.
    
    Args:
        context_id: The ID of the context this embedding represents
        task_id: The ID of the task this embedding belongs to
        embedding: The vector embedding as a list of floats
        db_path: Optional path to database file. If None, uses the same path as the main database.
    """
    if db_path is None:
        from database.schema import DB_PATH
        db_path = DB_PATH
    
    with sqlite3.connect(db_path) as conn:
        # Enable extension loading and load sqlite-vec extension
        conn.enable_load_extension(True)
        load_sqlite_vec(conn)
        
        # Generate new UUID for the embedding
        embedding_id = str(uuid.uuid4())
        
        # Serialize the embedding using sqlite_vec
        serialized_embedding = sqlite_vec.serialize_float32(embedding)
        
        # Insert the embedding
        conn.execute("""
            INSERT INTO context_embeddings (id, context_id, task_id, embedding)
            VALUES (?, ?, ?, ?)
        """, (embedding_id, context_id, task_id, serialized_embedding))
        
        conn.commit()


def retrieve(task_id: str, query_embedding: List[float], k: int = 5, db_path: Optional[str] = None) -> List[Dict]:
    """
    Perform a KNN similarity search against context_embeddings filtered to the given task_id.
    
    Args:
        task_id: The ID of the task to search within
        query_embedding: The query vector embedding as a list of floats
        k: Number of nearest neighbors to return (default: 5)
        db_path: Optional path to database file. If None, uses the same path as the main database.
        
    Returns:
        List of dictionaries with context_id, distance, key, and value fields
    """
    if db_path is None:
        from database.schema import DB_PATH
        db_path = DB_PATH
    
    with sqlite3.connect(db_path) as conn:
        # Enable extension loading
        conn.enable_load_extension(True)
        
        # Load sqlite-vec extension
        load_sqlite_vec(conn)
        
        # Serialize the query embedding
        serialized_query = sqlite_vec.serialize_float32(query_embedding)
        
        # Perform KNN search with join against context_store
        cursor = conn.execute("""
            SELECT 
                ce.context_id,
                vec_distance_cosine(ce.embedding, ?) as distance,
                cs.key,
                cs.value
            FROM context_embeddings ce
            JOIN context_store cs ON ce.context_id = cs.id
            WHERE ce.task_id = ?
            ORDER BY distance
            LIMIT ?
        """, (serialized_query, task_id, k))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'context_id': row[0],
                'distance': row[1],
                'key': row[2],
                'value': row[3]
            })
        
        return results


def retrieve_global(query_embedding: List[float], k: int = 5, db_path: Optional[str] = None) -> List[Dict]:
    """
    Perform a KNN similarity search against context_embeddings across ALL tasks.
    
    Args:
        query_embedding: The query vector embedding as a list of floats
        k: Number of nearest neighbors to return (default: 5)
        db_path: Optional path to database file. If None, uses the same path as the main database.
        
    Returns:
        List of dictionaries with context_id, distance, key, and value fields
    """
    if db_path is None:
        from database.schema import DB_PATH
        db_path = DB_PATH
    
    with sqlite3.connect(db_path) as conn:
        # Enable extension loading
        conn.enable_load_extension(True)
        
        # Load sqlite-vec extension
        load_sqlite_vec(conn)
        
        # Serialize the query embedding
        serialized_query = sqlite_vec.serialize_float32(query_embedding)
        
        # Perform KNN search across all tasks with join against context_store
        cursor = conn.execute("""
            SELECT 
                ce.context_id,
                vec_distance_cosine(ce.embedding, ?) as distance,
                cs.key,
                cs.value
            FROM context_embeddings ce
            JOIN context_store cs ON ce.context_id = cs.id
            ORDER BY distance
            LIMIT ?
        """, (serialized_query, k))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'context_id': row[0],
                'distance': row[1],
                'key': row[2],
                'value': row[3]
            })
        
        return results


if __name__ == "__main__":
    # Initialize vector store when run directly
    init_vector_store()
    print(f"Vector store initialized at: {DB_PATH}")
