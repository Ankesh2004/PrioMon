# re-export query() so monitoring.py can do: from src import query_client
from src.app.query import query  # noqa: F401
