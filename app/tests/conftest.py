"""
conftest.py
───────────
Fixtures compartidos por todos los tests.
Usa moto (mock DynamoDB) y fakeredis (mock Redis)
para que los tests corran sin infraestructura real.
"""
import os
import sys

import boto3
import pytest
from moto import mock_aws

# Agrega lambdas/items al path para poder importar handler y cache
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lambdas/items"))

# Dentro de cdk-dev, AWS_ENDPOINT_URL apunta al emulador; si se deja,
# boto3 ignora moto y los tests escriben en LocalStack real.
os.environ.pop("AWS_ENDPOINT_URL", None)

# Variables de entorno necesarias antes de importar los módulos
os.environ.setdefault("AWS_ACCESS_KEY_ID",     "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION",    "us-east-1")
os.environ.setdefault("TABLE_NAME",            "test-tabla")
os.environ.setdefault("REDIS_HOST",            "localhost")
os.environ.setdefault("REDIS_PORT",            "6379")
os.environ.setdefault("REDIS_TTL",             "300")


@pytest.fixture
def tabla_dynamodb():
    """
    Crea una tabla DynamoDB en memoria usando moto.
    Se destruye automáticamente al terminar cada test.
    """
    with mock_aws():
        cliente = boto3.resource("dynamodb", region_name="us-east-1")
        tabla = cliente.create_table(
            TableName="test-tabla",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        tabla.wait_until_exists()
        yield tabla


@pytest.fixture
def tabla_ecommerce():
    """
    Tabla en memoria con los mismos GSIs que AppTable en stack.py
    (GSI1: categoría/orden, GSI2: marca), para las Lambdas del ecommerce.
    """
    with mock_aws():
        cliente = boto3.resource("dynamodb", region_name="us-east-1")
        atributos = ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"]
        tabla = cliente.create_table(
            TableName="test-tabla",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": a, "AttributeType": "S"} for a in atributos
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": indice,
                    "KeySchema": [
                        {"AttributeName": f"{indice}PK", "KeyType": "HASH"},
                        {"AttributeName": f"{indice}SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
                for indice in ("GSI1", "GSI2")
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        tabla.wait_until_exists()
        yield tabla


# Módulos que las Lambdas importan "planos" y que chocan entre Lambdas
# (cada una tiene su handler.py; items/ tiene su propio cache.py).
_MODULOS_LAMBDA = ("handler", "cache", "claves", "cupones", "db", "es", "respuestas")
_DIR_LAMBDAS = os.path.join(os.path.dirname(__file__), "../lambdas")


@pytest.fixture
def cargar_lambda():
    """
    Importa el handler de una Lambda del ecommerce tal como quedaría en su
    asset: su carpeta + lambdas/comun/ al frente del sys.path. Al terminar
    restaura sys.path y sys.modules para no contaminar a test_items.py.
    """
    import importlib

    path_original = list(sys.path)
    modulos_originales = {m: sys.modules.pop(m) for m in _MODULOS_LAMBDA if m in sys.modules}

    def _cargar(nombre: str):
        for m in _MODULOS_LAMBDA:
            sys.modules.pop(m, None)
        sys.path[:0] = [
            os.path.join(_DIR_LAMBDAS, nombre),
            os.path.join(_DIR_LAMBDAS, "comun"),
        ]
        return importlib.import_module("handler")

    yield _cargar

    sys.path[:] = path_original
    for m in _MODULOS_LAMBDA:
        sys.modules.pop(m, None)
    sys.modules.update(modulos_originales)


@pytest.fixture
def redis_fake():
    """
    Instancia de fakeredis que reemplaza al cliente Redis real.
    Se reinicia automáticamente en cada test.
    """
    import fakeredis
    servidor = fakeredis.FakeRedis(decode_responses=True)
    return servidor
