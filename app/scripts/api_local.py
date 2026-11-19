"""
api_local
─────────
Cliente HTTP mínimo (solo librería estándar) para el API desplegado en el
emulador. Lo usan scripts/humo.py y scripts/seed.py.

La URL es  <endpoint>/restapis/<api-id>/prod/_user_request_/<ruta>, sin DNS.
  - endpoint: AWS_ENDPOINT_URL si existe (dentro de cdk-dev: http://emulador:4566),
    si no http://localhost:4566 (desde el host).
  - api-id: cambia en cada `cdklocal deploy`. Si no se pasa, se busca con
    boto3 el RestApi llamado "ProyectoFinal-API" (requiere boto3: corre
    dentro de cdk-dev, o en el host si lo tiene instalado).
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

NOMBRE_API = "ProyectoFinal-API"


def endpoint_por_defecto() -> str:
    return os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566").rstrip("/")


def descubrir_api_id(endpoint: str) -> str:
    try:
        import boto3
    except ImportError as e:
        raise SystemExit(
            "No se pasó el api-id y boto3 no está instalado: pase el api-id como "
            "argumento o corra el script dentro de cdk-dev"
        ) from e
    cliente = boto3.client(
        "apigateway",
        endpoint_url=endpoint,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )
    apis = [a for a in cliente.get_rest_apis(limit=500)["items"] if a["name"] == NOMBRE_API]
    if not apis:
        raise SystemExit(f"No hay un RestApi '{NOMBRE_API}' desplegado: corra `cdklocal deploy`")
    # Si quedaron restos de deploys anteriores, el más reciente es el vigente
    return max(apis, key=lambda a: a["createdDate"])["id"]


class Api:
    def __init__(self, api_id: str | None = None, endpoint: str | None = None) -> None:
        self.endpoint = (endpoint or endpoint_por_defecto()).rstrip("/")
        self.api_id = api_id or descubrir_api_id(self.endpoint)
        self.base = f"{self.endpoint}/restapis/{self.api_id}/prod/_user_request_"

    def pedir(
        self,
        metodo: str,
        ruta: str,
        body: Any = None,
        usuario: str | None = None,
    ) -> tuple[int, Any]:
        datos = json.dumps(body).encode() if body is not None else None
        # Codifica espacios, tildes, etc. (ej. /search?q=audífonos inalámbricos)
        # sin tocar los separadores de la ruta ni de la query
        ruta = urllib.parse.quote(ruta, safe="/?=&")
        req = urllib.request.Request(self.base + ruta, data=datos, method=metodo)
        req.add_header("Content-Type", "application/json")
        if usuario:
            req.add_header("X-Username", usuario)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            cuerpo = e.read()
            try:
                return e.code, json.loads(cuerpo)
            except ValueError:
                return e.code, {"_crudo": cuerpo.decode(errors="replace")[:300]}
