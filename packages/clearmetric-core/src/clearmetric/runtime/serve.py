"""Minimal HTTP server for compiled query contracts."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from clearmetric.core.contracts import find_query_node, query_execution_sql
from clearmetric.core.errors import QueryExecutionError
from clearmetric.core.models import CatalogArtifact

from .execute import execute_query


def serve(
    artifact: CatalogArtifact,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    """Serve query contract execution over HTTP."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/query":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json_response(400, {"error": "invalid Content-Length"})
                return
            if length < 0:
                self._json_response(400, {"error": "invalid Content-Length"})
                return
            try:
                body = json.loads(self.rfile.read(length) if length else b"{}")
            except json.JSONDecodeError:
                self._json_response(400, {"error": "invalid JSON body"})
                return
            if not isinstance(body, dict):
                self._json_response(400, {"error": "body must be a JSON object"})
                return
            query_id = body.get("query_id")
            if not query_id:
                self._json_response(400, {"error": "query_id required"})
                return
            node = find_query_node(artifact, str(query_id))
            if node is None:
                self._json_response(404, {"error": f"query not found: {query_id}"})
                return
            sql = query_execution_sql(node)
            if not sql:
                self._json_response(
                    400, {"error": f"query has no executable SQL: {query_id}"}
                )
                return
            try:
                rows = execute_query(sql)
            except QueryExecutionError as exc:
                self._json_response(500, {"error": str(exc)})
                return
            self._json_response(200, {"rows": rows})

        def _json_response(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer((host, port), Handler)
    server.serve_forever()
