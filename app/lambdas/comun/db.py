"""
─────
Acceso a la tabla DynamoDB (Single Table Design) compartido por todas
las Lambdas del ecommerce. boto3 lee AWS_ENDPOINT_URL del entorno para
redirigir al emulador local automáticamente
"""
import os
from typing import Any

import boto3
from boto3.resources.base import ServiceResource

dynamodb: ServiceResource = boto3.resource("dynamodb")
TABLE_NAME: str = os.environ["TABLE_NAME"]


def tabla() -> Any:
    """
    boto3 genera la clase Table en tiempo de ejecución
    """
    return dynamodb.Table(TABLE_NAME)