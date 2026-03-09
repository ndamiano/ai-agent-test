import os
import requests
from typing import List
from dotenv import load_dotenv

load_dotenv()

class EmbeddingClient:
    def __init__(self, base_url: str = "http://localhost:1234", model_name: str = "nomic-ai/nomic-embed-text-v1.5-GGUF"):
        """
        Initialize the EmbeddingClient.
        
        Args:
            base_url: Base URL for the LMStudio server. Defaults to "http://localhost:1234".
                     Can be overridden by EMBEDDING_BASE_URL environment variable.
            model_name: Name of the embedding model to use. Defaults to "nomic-ai/nomic-embed-text-v1.5-GGUF".
                       Can be overridden by EMBEDDING_MODEL environment variable.
        """
        self._base_url = base_url
        self._model_name = model_name
        self._endpoint = None
    
    @property
    def base_url(self):
        """Lazy property to get base URL from environment variable."""
        return os.getenv("EMBEDDING_BASE_URL", self._base_url)
    
    @property
    def model_name(self):
        """Lazy property to get model name from environment variable."""
        return os.getenv("EMBEDDING_MODEL", self._model_name)
    
    @property
    def endpoint(self):
        """Lazy property to construct endpoint URL."""
        if self._endpoint is None:
            self._endpoint = f"{self.base_url}/v1/embeddings"
        return self._endpoint
    
    def embed(self, text: str) -> List[float]:
        """
        Generate an embedding for the given text.
        
        Args:
            text: The text to embed
            
        Returns:
            List of floats representing the embedding vector
            
        Raises:
            RuntimeError: If the server is unreachable or returns a non-200 status
        """
        try:
            response = requests.post(
                self.endpoint,
                json={
                    "model": self.model_name,
                    "input": text
                },
                headers={
                    "Content-Type": "application/json"
                },
                timeout=30
            )
            
            if response.status_code != 200:
                raise RuntimeError(f"Embedding server returned status {response.status_code}: {response.text}")
            
            data = response.json()
            if "data" not in data or not data["data"]:
                raise RuntimeError("Invalid response format from embedding server")
            
            return data["data"][0]["embedding"]
            
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"Could not connect to embedding server at {self.base_url}. Please ensure LMStudio is running and the embedding model is loaded.")
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Request to embedding server at {self.base_url} timed out")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Request to embedding server failed: {str(e)}")
    
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors (each as a list of floats)
        """
        return [self.embed(text) for text in texts]
    
    def health_check(self) -> bool:
        """
        Check if the embedding endpoint is reachable.
        
        Returns:
            True if the endpoint is reachable, False otherwise
        """
        try:
            response = requests.get(self.base_url, timeout=5)
            return response.status_code == 200
        except:
            return False

# Module-level singleton instance
embedding_client = EmbeddingClient()