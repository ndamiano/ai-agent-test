import unittest
import tempfile
import os
import shutil
import sqlite3
from unittest.mock import patch, MagicMock
from typing import List
import sys

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.vector_store import (
    init_vector_store, 
    store_embedding, 
    retrieve, 
    retrieve_global,
    load_sqlite_vec
)
from database.schema import init_db


class TestVectorStore(unittest.TestCase):
    """Test suite for vector store functionality using temporary SQLite database."""
    
    def setUp(self):
        """Set up a fresh temporary database for each test."""
        # Create a temporary directory for the test database
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = os.path.join(self.temp_dir, "test_vector_store.db")
        
        # Clear any existing singleton instances
        # Note: vector_store doesn't have singletons, but we ensure clean state
        
        # Initialize the main database schema first
        init_db(self.temp_db_path)
        
        # Initialize the vector store
        init_vector_store(self.temp_db_path)
    
    def tearDown(self):
        """Clean up temporary database after each test."""
        # Remove the temporary directory and all its contents
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def _create_test_context(self, task_id: str, key: str, value: str) -> str:
        """Helper to create a context entry for testing."""
        context_id = f"context_{task_id}_{key}"
        with sqlite3.connect(self.temp_db_path) as conn:
            conn.execute("""
                INSERT INTO context_store (id, task_id, key, value, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (context_id, task_id, key, value))
            conn.commit()
        return context_id
    
    def _generate_fake_embedding(self, seed: int = 0) -> List[float]:
        """Generate a fake embedding vector for testing."""
        # Generate a deterministic 768-dimensional vector
        import random
        random.seed(seed)
        return [random.uniform(-1, 1) for _ in range(768)]
    
    def test_init_vector_store_creates_table(self):
        """Test that init_vector_store creates the context_embeddings table."""
        # Verify the table exists by trying to query it
        with sqlite3.connect(self.temp_db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master WHERE type='table' AND name='context_embeddings'
            """)
            result = cursor.fetchone()
            self.assertIsNotNone(result, "context_embeddings table should exist")
            self.assertEqual(result[0], "context_embeddings")
    
    def test_store_embedding_writes_row(self):
        """Test that store_embedding writes a row to context_embeddings."""
        # Note: This test will only work if sqlite-vec extension is available
        try:
            task_id = "test_task_1"
            context_id = self._create_test_context(task_id, "test_key", "test_value")
            embedding = self._generate_fake_embedding(1)
            
            # Store the embedding
            store_embedding(context_id, task_id, embedding, self.temp_db_path)
            
            # Verify the row was created
            with sqlite3.connect(self.temp_db_path) as conn:
                cursor = conn.execute("""
                    SELECT id, context_id, task_id FROM context_embeddings
                    WHERE context_id = ? AND task_id = ?
                """, (context_id, task_id))
                result = cursor.fetchone()
                
                self.assertIsNotNone(result, "Embedding row should exist")
                self.assertEqual(result[1], context_id)
                self.assertEqual(result[2], task_id)
        except Exception as e:
            # If sqlite-vec extension is not available, skip this test
            if "no such module: vec0" in str(e) or "sqlite-vec extension not found" in str(e):
                self.skipTest("sqlite-vec extension not available")
            else:
                raise
    
    def test_retrieve_returns_results_ordered_by_similarity(self):
        """Test that retrieve returns results ordered by similarity."""
        task_id = "test_task_similarity"
        
        # Create multiple context entries with different embeddings
        context_ids = []
        embeddings = []
        
        for i in range(3):
            context_id = self._create_test_context(task_id, f"key_{i}", f"value_{i}")
            context_ids.append(context_id)
            embedding = self._generate_fake_embedding(i)
            embeddings.append(embedding)
            
            # Store embeddings
            store_embedding(context_id, task_id, embedding, self.temp_db_path)
        
        # Create a query embedding that should be closest to the first embedding
        query_embedding = embeddings[0].copy()
        # Make it slightly different but still closest to the original
        query_embedding[0] += 0.1
        
        # Retrieve results
        results = retrieve(task_id, query_embedding, k=3, db_path=self.temp_db_path)
        
        # Verify we got results
        self.assertEqual(len(results), 3)
        
        # Verify results are ordered by distance (ascending)
        distances = [result['distance'] for result in results]
        self.assertEqual(distances, sorted(distances), "Results should be ordered by similarity")
        
        # Verify the first result has the smallest distance
        self.assertEqual(results[0]['context_id'], context_ids[0])
    
    def test_retrieve_correctly_filtered_by_task_id(self):
        """Test that retrieve correctly filters results to the right task_id."""
        task_id_1 = "test_task_1"
        task_id_2 = "test_task_2"
        
        # Create context entries for both tasks
        context_id_1 = self._create_test_context(task_id_1, "key1", "value1")
        context_id_2 = self._create_test_context(task_id_2, "key2", "value2")
        
        embedding_1 = self._generate_fake_embedding(1)
        embedding_2 = self._generate_fake_embedding(2)
        
        store_embedding(context_id_1, task_id_1, embedding_1, self.temp_db_path)
        store_embedding(context_id_2, task_id_2, embedding_2, self.temp_db_path)
        
        # Query with task_id_1
        query_embedding = embedding_1.copy()
        query_embedding[0] += 0.1
        
        results = retrieve(task_id_1, query_embedding, k=5, db_path=self.temp_db_path)
        
        # Verify only results from task_id_1 are returned
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['context_id'], context_id_1)
        
        # Verify the result includes the correct task data
        self.assertEqual(results[0]['key'], "key1")
        self.assertEqual(results[0]['value'], "value1")
    
    def test_retrieve_global_returns_results_across_multiple_tasks(self):
        """Test that retrieve_global returns results across multiple tasks."""
        task_id_1 = "test_task_1"
        task_id_2 = "test_task_2"
        
        # Create context entries for both tasks
        context_id_1 = self._create_test_context(task_id_1, "key1", "value1")
        context_id_2 = self._create_test_context(task_id_2, "key2", "value2")
        
        embedding_1 = self._generate_fake_embedding(1)
        embedding_2 = self._generate_fake_embedding(2)
        
        store_embedding(context_id_1, task_id_1, embedding_1, self.temp_db_path)
        store_embedding(context_id_2, task_id_2, embedding_2, self.temp_db_path)
        
        # Create a query embedding that should be closest to embedding_1
        query_embedding = embedding_1.copy()
        query_embedding[0] += 0.1
        
        # Retrieve globally
        results = retrieve_global(query_embedding, k=5, db_path=self.temp_db_path)
        
        # Verify we get results from both tasks
        self.assertEqual(len(results), 2)
        
        # Verify results are ordered by similarity
        context_ids = [result['context_id'] for result in results]
        self.assertIn(context_id_1, context_ids)
        self.assertIn(context_id_2, context_ids)
        
        # The first result should be from task_id_1 (closer to query)
        self.assertEqual(results[0]['context_id'], context_id_1)
    
    def test_retrieve_includes_key_and_value_from_context_store_join(self):
        """Test that retrieve results include key and value from the context store join."""
        task_id = "test_task_join"
        context_id = self._create_test_context(task_id, "test_key", "test_value")
        embedding = self._generate_fake_embedding(1)
        
        store_embedding(context_id, task_id, embedding, self.temp_db_path)
        
        # Retrieve results
        query_embedding = embedding.copy()
        query_embedding[0] += 0.1
        
        results = retrieve(task_id, query_embedding, k=1, db_path=self.temp_db_path)
        
        # Verify the result includes key and value from context store
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['key'], "test_key")
        self.assertEqual(results[0]['value'], "test_value")
        self.assertEqual(results[0]['context_id'], context_id)
    
    def test_load_sqlite_vec_extension_not_found(self):
        """Test that load_sqlite_vec raises RuntimeError with helpful message when extension not found."""
        # Mock sqlite_vec.load to raise ImportError
        with patch('database.vector_store.sqlite_vec.load') as mock_load:
            mock_load.side_effect = ImportError("Extension not found")
            
            conn = sqlite3.connect(":memory:")
            conn.enable_load_extension(True)
            
            with self.assertRaises(RuntimeError) as context:
                load_sqlite_vec(conn)
            
            error_message = str(context.exception)
            self.assertIn("sqlite-vec extension not found", error_message)
            self.assertIn("pip install sqlite-vec", error_message)
            self.assertIn("SQLite compiled with extension support", error_message)
    
    def test_store_embedding_with_extension_error(self):
        """Test that store_embedding handles sqlite-vec extension errors gracefully."""
        # Mock sqlite_vec.load to raise ImportError
        with patch('database.vector_store.sqlite_vec.load') as mock_load:
            mock_load.side_effect = ImportError("Extension not found")
            
            with self.assertRaises(RuntimeError) as context:
                store_embedding("test_context", "test_task", [1.0] * 768, self.temp_db_path)
            
            error_message = str(context.exception)
            self.assertIn("sqlite-vec extension not found", error_message)
    
    def test_retrieve_with_extension_error(self):
        """Test that retrieve handles sqlite-vec extension errors gracefully."""
        # Mock sqlite_vec.load to raise ImportError
        with patch('database.vector_store.sqlite_vec.load') as mock_load:
            mock_load.side_effect = ImportError("Extension not found")
            
            with self.assertRaises(RuntimeError) as context:
                retrieve("test_task", [1.0] * 768, db_path=self.temp_db_path)
            
            error_message = str(context.exception)
            self.assertIn("sqlite-vec extension not found", error_message)
    
    def test_retrieve_global_with_extension_error(self):
        """Test that retrieve_global handles sqlite-vec extension errors gracefully."""
        # Mock sqlite_vec.load to raise ImportError
        with patch('database.vector_store.sqlite_vec.load') as mock_load:
            mock_load.side_effect = ImportError("Extension not found")
            
            with self.assertRaises(RuntimeError) as context:
                retrieve_global([1.0] * 768, db_path=self.temp_db_path)
            
            error_message = str(context.exception)
            self.assertIn("sqlite-vec extension not found", error_message)
    
    def test_write_context_with_embedding_failure_still_persists_context(self):
        """Test that write_context with embedding failure still persists the context entry."""
        from database.task_store import TaskStore
        
        # Create a task store instance
        task_store = TaskStore()
        
        # Mock embedding_client.embed to raise an exception
        with patch('database.task_store.embedding_client.embed') as mock_embed:
            mock_embed.side_effect = RuntimeError("Embedding service unavailable")
            
            # This should still succeed and create the context entry
            task_store.write_context("test_task", "test_key", "test_value")
            
            # Verify the context entry was still created
            value = task_store.get_context("test_task", "test_key")
            self.assertEqual(value, "test_value")
            
            # Verify no embedding was stored (since embedding failed)
            # Note: This test will only work if sqlite-vec extension is available
            try:
                with sqlite3.connect(self.temp_db_path) as conn:
                    cursor = conn.execute("""
                        SELECT COUNT(*) FROM context_embeddings
                    """)
                    embedding_count = cursor.fetchone()[0]
                    self.assertEqual(embedding_count, 0, "No embeddings should be stored when embedding fails")
            except Exception as e:
                # If sqlite-vec extension is not available, skip this part of the test
                if "no such module: vec0" in str(e) or "sqlite-vec extension not found" in str(e):
                    pass  # Skip the embedding count check
                else:
                    raise
    
    def test_store_embedding_with_different_dimensions(self):
        """Test storing embeddings with different dimensions (should work with any size)."""
        # Note: This test will only work if sqlite-vec extension is available
        # The table is created with 768 dimensions, so we can only test with that size
        try:
            task_id = "test_task_dims"
            context_id = self._create_test_context(task_id, "test_key", "test_value")
            
            # Test with 768 dimensions (the table is created with this fixed size)
            embedding = self._generate_fake_embedding(1)  # This generates 768 dimensions
            
            # This should work with 768 dimensions
            store_embedding(context_id, task_id, embedding, self.temp_db_path)
            
            # Verify it was stored
            with sqlite3.connect(self.temp_db_path) as conn:
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM context_embeddings WHERE context_id = ?
                """, (context_id,))
                count = cursor.fetchone()[0]
                self.assertEqual(count, 1, "Embedding of size 768 should be stored")
        except Exception as e:
            # If sqlite-vec extension is not available, skip this test
            if "no such module: vec0" in str(e) or "sqlite-vec extension not found" in str(e):
                self.skipTest("sqlite-vec extension not available")
            else:
                raise
    
    def test_retrieve_with_k_parameter(self):
        """Test that retrieve respects the k parameter for limiting results."""
        task_id = "test_task_k"
        
        # Create multiple context entries
        context_ids = []
        for i in range(10):
            context_id = self._create_test_context(task_id, f"key_{i}", f"value_{i}")
            context_ids.append(context_id)
            embedding = self._generate_fake_embedding(i)
            store_embedding(context_id, task_id, embedding, self.temp_db_path)
        
        query_embedding = self._generate_fake_embedding(0)
        
        # Test different k values
        for k in [1, 3, 5, 10]:
            results = retrieve(task_id, query_embedding, k=k, db_path=self.temp_db_path)
            self.assertEqual(len(results), k, f"Should return exactly {k} results")
            
            # Verify all results have the expected fields
            for result in results:
                self.assertIn('context_id', result)
                self.assertIn('distance', result)
                self.assertIn('key', result)
                self.assertIn('value', result)
    
    def test_retrieve_empty_results_when_no_data(self):
        """Test that retrieve returns empty list when no data exists."""
        task_id = "nonexistent_task"
        query_embedding = self._generate_fake_embedding(1)
        
        results = retrieve(task_id, query_embedding, k=5, db_path=self.temp_db_path)
        self.assertEqual(results, [])
    
    def test_retrieve_global_empty_results_when_no_data(self):
        """Test that retrieve_global returns empty list when no data exists."""
        query_embedding = self._generate_fake_embedding(1)
        
        results = retrieve_global(query_embedding, k=5, db_path=self.temp_db_path)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()