"""M4: fronteira de ingestão — broker, dispatcher, consumidor, índice."""

from chat.services.ingest.broker import Broker, LocalBroker
from chat.services.ingest.index import (
    ElasticIndex,
    LocalFtsIndex,
    RetrievalIndex,
    select_index,
)

__all__ = [
    "Broker",
    "LocalBroker",
    "ElasticIndex",
    "LocalFtsIndex",
    "RetrievalIndex",
    "select_index",
]
