import os
from azure.storage.blob import BlobServiceClient, ContentSettings

CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

ALLOWED_EXTENSIONS = {
    ".pdf", ".jpeg", ".jpg", ".png", ".gif", ".webp",
    ".doc", ".docx", ".xls", ".xlsx", ".txt", ".csv", ".zip", ".rar",
    ".json",  # metadata sidecar de cada PDF descargado
}

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".json": "application/json",
}


def _client() -> BlobServiceClient:
    conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    return BlobServiceClient.from_connection_string(conn_str)


def upload_file(local_path: str, blob_name: str) -> str:
    ext = os.path.splitext(local_path)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Extensión no permitida: {ext}")

    size = os.path.getsize(local_path)
    if size > MAX_FILE_SIZE:
        raise ValueError(f"Archivo excede el límite de 100 MB: {size} bytes")

    container_name = os.environ["AZURE_CONTAINER_NAME"]
    blob_client = _client().get_blob_client(container=container_name, blob=blob_name)
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")

    with open(local_path, "rb") as f:
        blob_client.upload_blob(
            f,
            overwrite=True,
            max_concurrency=10,
            content_settings=ContentSettings(content_type=content_type),
        )

    return blob_client.url


def upload_bytes(data: bytes, blob_name: str) -> str:
    """Sube datos directo desde memoria, sin pasar por disco local."""
    ext = os.path.splitext(blob_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Extensión no permitida: {ext}")

    if len(data) > MAX_FILE_SIZE:
        raise ValueError(f"Archivo excede el límite de 100 MB: {len(data)} bytes")

    container_name = os.environ["AZURE_CONTAINER_NAME"]
    blob_client = _client().get_blob_client(container=container_name, blob=blob_name)
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")

    blob_client.upload_blob(
        data,
        overwrite=True,
        max_concurrency=10,
        content_settings=ContentSettings(content_type=content_type),
    )

    return blob_client.url
