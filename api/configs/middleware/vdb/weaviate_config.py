from typing import Optional

from pydantic import Field, PositiveInt
from pydantic_settings import BaseSettings


class WeaviateConfig(BaseSettings):
    """
    Configuration settings for Weaviate vector database
    """

    WEAVIATE_ENDPOINT: Optional[str] = Field(
        description="URL of the Weaviate server (e.g., 'http://localhost:8080' or 'https://weaviate.example.com')",
        default=None,
    )

    WEAVIATE_API_KEY: Optional[str] = Field(
        description="API key for authenticating with the Weaviate server",
        default=None,
    )

    WEAVIATE_GRPC_ENABLED: bool = Field(
        description="Whether to enable gRPC for Weaviate connection (True for gRPC, False for HTTP)",
        default=True,
    )

    WEAVIATE_BATCH_SIZE: PositiveInt = Field(
        description="Number of objects to be processed in a single batch operation (default is 100)",
        default=100,
    )

    # Client timeout and retry settings
    WEAVIATE_CONNECT_TIMEOUT: PositiveInt = Field(
        description="Connection timeout in seconds for Weaviate client",
        default=5,
    )

    WEAVIATE_READ_TIMEOUT: PositiveInt = Field(
        description="Read timeout in seconds for Weaviate client",
        default=60,
    )

    WEAVIATE_TIMEOUT_RETRIES: PositiveInt = Field(
        description="Number of retries on timeouts for Weaviate batch operations",
        default=3,
    )

    WEAVIATE_BATCH_DYNAMIC: bool = Field(
        description="Dynamically adjust Weaviate batch size based on import speed",
        default=True,
    )
