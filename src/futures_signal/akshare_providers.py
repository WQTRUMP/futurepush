from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AkshareClientAdapter:
    ak: Any
    rows_parser: Any
    quiet_caller: Any

    def futures_realtime_rows(self, symbol: str) -> list[dict[str, Any]]:
        df = self.quiet_caller(self.ak.futures_zh_realtime, symbol=symbol)
        return self.rows_parser(df)


@dataclass
class RealtimeQuoteBundle:
    client: AkshareClientAdapter
    rows_by_symbol: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def rows(self, symbol: str) -> list[dict[str, Any]]:
        if symbol not in self.rows_by_symbol:
            self.rows_by_symbol[symbol] = self.client.futures_realtime_rows(symbol)
        return self.rows_by_symbol[symbol]


@dataclass(frozen=True)
class RealtimeQuoteBundleProvider:
    client: AkshareClientAdapter

    def create(self) -> RealtimeQuoteBundle:
        return RealtimeQuoteBundle(client=self.client)


@dataclass(frozen=True)
class ProviderObservation:
    provider: str
    status: str
    duration_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, provider: str, details: dict[str, Any] | None = None) -> "ProviderObservation":
        return cls(provider=provider, status="ok", details=details or {})

    @classmethod
    def degraded(cls, provider: str, details: dict[str, Any] | None = None) -> "ProviderObservation":
        return cls(provider=provider, status="degraded", details=details or {})

    @classmethod
    def failed(cls, provider: str, details: dict[str, Any] | None = None) -> "ProviderObservation":
        return cls(provider=provider, status="failed", details=details or {})

    @classmethod
    def skipped(cls, provider: str, details: dict[str, Any] | None = None) -> "ProviderObservation":
        return cls(provider=provider, status="skipped", details=details or {})

    def with_duration(self, duration_ms: float) -> "ProviderObservation":
        return ProviderObservation(
            provider=self.provider,
            status=self.status,
            duration_ms=duration_ms,
            details=self.details,
        )


@dataclass(frozen=True)
class FetchObservation:
    observations: list[ProviderObservation] = field(default_factory=list)

    def add(self, observation: ProviderObservation) -> "FetchObservation":
        return FetchObservation(observations=[*self.observations, observation])
