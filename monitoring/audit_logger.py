from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


class AuditLogger:
    SEVERITY_INFO = "info"
    SEVERITY_WARNING = "warning"
    SEVERITY_DECISION = "decision"
    SEVERITY_TRADE = "trade"
    SEVERITY_CRITICAL = "critical"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def log_info(self, component: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self._write_alert("audit", self.SEVERITY_INFO, component, message, payload or {})

    def log_warning(self, component: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self._write_alert("audit", self.SEVERITY_WARNING, component, message, payload or {})

    def log_decision(self, component: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self._write_alert("decision", self.SEVERITY_DECISION, component, message, payload or {})

    def log_trade(self, component: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self._write_alert("trade", self.SEVERITY_TRADE, component, message, payload or {})

    def log_error(self, component: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self._write_alert("error", self.SEVERITY_CRITICAL, component, message, payload or {})

    def query_recent(self, component: str | None = None, severity: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alerts_errors"
        clauses: list[str] = []
        params: list[Any] = []

        if component:
            clauses.append("component = ?")
            params.append(component)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(max(int(limit), 1))

        rows = self.connection.execute(sql, tuple(params)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            payload_raw = record.get("payload_json")
            if isinstance(payload_raw, str):
                try:
                    record["payload"] = json.loads(payload_raw)
                except json.JSONDecodeError:
                    record["payload"] = {}
            else:
                record["payload"] = {}
            result.append(record)
        return result

    def _write_alert(self, event_type: str, severity: str, component: str, message: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO alerts_errors (timestamp, type, severity, component, message, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                event_type,
                severity,
                component,
                message,
                json.dumps(payload),
            ),
        )
        self.connection.commit()
