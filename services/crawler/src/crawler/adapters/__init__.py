from typing import cast

from crawler.adapters.base import Adapter
from crawler.adapters.isacostore import IsacoStoreAdapter
from crawler.adapters.sarayyadak import SarayYadakAdapter
from crawler.adapters.yadakyar import YadakYarAdapter

_ADAPTERS: dict[str, Adapter] = {
    adapter.key: cast(Adapter, adapter)
    for adapter in (IsacoStoreAdapter(), SarayYadakAdapter(), YadakYarAdapter())
}


def get_adapter(key: str) -> Adapter:
    try:
        return _ADAPTERS[key]
    except KeyError as exc:
        raise LookupError(f"No adapter registered for {key!r}") from exc


def registered_adapter_keys() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))
