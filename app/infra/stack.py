"""
ProyectoFinalStack
──────────────────
Define la infraestructura serverless del proyecto final:
  - Tabla DynamoDB  (Single Table Design, PK+SK)
  - Lambda Layer    con las dependencias de las Lambdas (boto3, botocore, redis)
  - Lambda /items   (POST, GET, DELETE) con patrón Cache-Aside sobre Redis
  - Lambda /health  (GET) health check de DynamoDB, Redis y Elasticsearch
  - Lambda productos (catálogo, reseñas, ranking) con código de lambdas/comun
  - Lambda carrito  (GET /cart, PUT/DELETE /cart/{productId})
  - Lambda cupones  (GET /coupons/{codigo})
  - Lambda usuarios (perfil, direcciones, wishlist)
  - Lambda ordenes  (checkout transaccional, historial, orden por número)
  - Lambda promociones (promoción del día, Write-Through hasta medianoche)
  - Lambda busqueda (GET /search, Elasticsearch)
  - API Gateway     (RestApi) que enruta las peticiones
"""
import os
import shutil
import subprocess
import sys
from typing import Any

import aws_cdk as cdk
import jsii
from aws_cdk import (
    Stack,
    aws_dynamodb as ddb,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    RemovalPolicy,
    CfnOutput,
)
from constructs import Construct

# Runtime común a todas las Lambdas y a la layer
RUNTIME: _lambda.Runtime = _lambda.Runtime.PYTHON_3_11
DIR_LAYER: str = "layers/dependencias"
REQUIREMENTS_LAYER: str = os.path.join(DIR_LAYER, "requirements.txt")
# Código compartido por las Lambdas del ecommerce (claves, caché, db, ...)
DIR_COMUN: str = "lambdas/comun"

# LocalStack Community acepta las Lambda Layers pero no las monta al ejecutar
# (es una función de LocalStack Pro). Cuando se despliega en el emulador
# (cdk-dev define AWS_ENDPOINT_URL), las dependencias se empaquetan dentro
# del código de cada Lambda en lugar de usar la layer.
ES_LOCAL: bool = "AWS_ENDPOINT_URL" in os.environ


def _pip_install(requirements: str, destino: str) -> None:
    """Instala las dependencias en `destino` para el runtime de Lambda."""
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--quiet",
            "--requirement", requirements,
            "--target", destino,
            # Descargar wheels para el sistema y la versión de Python
            # de Lambda, no los del contenedor de desarrollo
            "--platform", "manylinux2014_x86_64",
            "--implementation", "cp",
            "--python-version", "3.11",
            "--only-binary=:all:",
            # Sin .pyc: hace el resultado reproducible (mismo hash de asset
            # si no cambian las dependencias → no se redespliega sin motivo)
            "--no-compile",
        ],
        check=True,
    )


@jsii.implements(cdk.ILocalBundling)
class _PipLocal:
    """
    Empaqueta con el pip del propio contenedor cdk-dev, sin necesitar Docker.

    - Layer:  instala en "python/", donde Lambda busca las librerías de una layer.
    - Lambda: copia el código fuente y las dependencias juntas en la raíz.
    """

    def __init__(
        self,
        requirements: str | None,
        subdir: str = "",
        copiar_desde: list[str] | None = None,
    ) -> None:
        # requirements=None: solo copia código (en AWS las dependencias van en la layer)
        self.requirements: str | None = requirements
        self.subdir: str = subdir
        self.copiar_desde: list[str] = copiar_desde or []

    def try_bundle(self, output_dir: str, *args: Any, **kwargs: Any) -> bool:
        # Todo se copia plano en la raíz del asset: las Lambdas importan
        # sus módulos y los de comun/ como `from cache import ...`
        for origen in self.copiar_desde:
            shutil.copytree(
                origen,
                output_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "__init__.py"),
            )
        if self.requirements:
            _pip_install(self.requirements, os.path.join(output_dir, self.subdir))
        return True


def _codigo_lambda(directorio: str, con_comun: bool = False) -> _lambda.Code:
    """
    Código de una Lambda. En AWS solo nuestros .py (las dependencias van en
    la layer); en LocalStack, nuestros .py + las dependencias de la layer.

    con_comun=True copia también lambdas/comun/ dentro del asset: cada asset
    se construye solo desde su carpeta, así que sin esto comun/ no viajaría.
    """
    origenes: list[str] = [DIR_COMUN, directorio] if con_comun else [directorio]
    if not ES_LOCAL:
        if not con_comun:
            return _lambda.Code.from_asset(directorio)
        return _lambda.Code.from_asset(
            directorio,
            asset_hash_type=cdk.AssetHashType.OUTPUT,
            bundling=cdk.BundlingOptions(
                image=RUNTIME.bundling_image,
                command=["bash", "-c", "echo 'Requiere bundling local' && exit 1"],
                local=_PipLocal(None, copiar_desde=origenes),
            ),
        )
    return _lambda.Code.from_asset(
        directorio,
        # El hash se calcula sobre el resultado, así también cambia
        # cuando cambian las dependencias y no solo el código
        asset_hash_type=cdk.AssetHashType.OUTPUT,
        bundling=cdk.BundlingOptions(
            # Solo se usaría si falla el bundling local (cdk-dev no tiene Docker)
            image=RUNTIME.bundling_image,
            command=["bash", "-c", "echo 'Requiere bundling local con pip' && exit 1"],
            local=_PipLocal(REQUIREMENTS_LAYER, copiar_desde=origenes),
        ),
    )


class ProyectoFinalStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── 1. TABLA DYNAMODB ─────────────────────────────────────────
        # Single Table Design: PK y SK genéricos para soportar
        # múltiples tipos de entidades en una sola tabla.
        tabla: ddb.Table = ddb.Table(
            self,
            "AppTable",
            partition_key=ddb.Attribute(
                name="PK",
                type=ddb.AttributeType.STRING,
            ),
            sort_key=ddb.Attribute(
                name="SK",
                type=ddb.AttributeType.STRING,
            ),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            # DESTROY permite borrar la tabla al hacer `cdklocal destroy`
            # En producción usar RETAIN
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── 1.1 GSI1 (reutilizado por dos patrones) ───────────────────
        # Las llaves exactas salen de lambdas/comun/claves.py.
        #  - Productos por categoría (#2):
        #      GSI1PK = CAT#<categoria>   GSI1SK = PRODUCT#<id>
        #  - Orden por número sin saber el usuario (#15):
        #      GSI1PK = ORDER#<orderId>   GSI1SK = ORDER#HEADER | ITEM#<productId>
        # Un solo índice con prefijos distintos en vez de uno por patrón.
        # Projection ALL: las pantallas que lo usan (listado de categoría,
        # detalle de orden) necesitan el ítem completo; con KEYS_ONLY habría
        # que hacer un GetItem extra por cada resultado.
        tabla.add_global_secondary_index(
            index_name="GSI1",
            partition_key=ddb.Attribute(name="GSI1PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="GSI1SK", type=ddb.AttributeType.STRING),
            projection_type=ddb.ProjectionType.ALL,
        )

        # ── 1.2 GSI2 (productos por marca) ────────────────────────────
        #  - Productos de una marca y opcionalmente de una categoría (#5):
        #      GSI2PK = BRAND#<marca>   GSI2SK = CAT#<categoria>#PRODUCT#<id>
        # La categoría va al inicio de la SK para que begins_with("CAT#<cat>")
        # sirva como filtro opcional con la misma consulta.
        tabla.add_global_secondary_index(
            index_name="GSI2",
            partition_key=ddb.Attribute(name="GSI2PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="GSI2SK", type=ddb.AttributeType.STRING),
            projection_type=ddb.ProjectionType.ALL,
        )

        # Variables de entorno comunes para todas las Lambdas
        env_comun: dict[str, str] = {
            "TABLE_NAME": tabla.table_name,
            "REDIS_HOST": os.environ.get("REDIS_HOST", "redis"),
            "REDIS_PORT": os.environ.get("REDIS_PORT", "6379"),
            "REDIS_TTL":  "300",   # segundos de vida en caché
            # Elasticsearch (espejo para la búsqueda libre); lo usan
            # productos, busqueda y el health check
            "ES_HOST": os.environ.get("ES_HOST", "elasticsearch"),
            "ES_PORT": os.environ.get("ES_PORT", "9200"),
        }

        # ── 2. LAMBDA LAYER (dependencias) ────────────────────────────
        # Code.from_asset("lambdas/...") solo empaqueta nuestros .py.
        # Las librerías externas van en una layer compartida, construida
        # con pip a partir de layers/dependencias/requirements.txt.
        # Se reconstruye solo cuando cambia ese archivo.
        # En LocalStack no se crea: no la aplicaría (ver ES_LOCAL).
        layers: list[_lambda.ILayerVersion] = []
        if not ES_LOCAL:
            layer_dependencias: _lambda.LayerVersion = _lambda.LayerVersion(
                self,
                "DependenciasLayer",
                code=_lambda.Code.from_asset(
                    DIR_LAYER,
                    bundling=cdk.BundlingOptions(
                        # image/command solo se usan si falla el bundling local
                        image=RUNTIME.bundling_image,
                        command=[
                            "bash", "-c",
                            "pip install -r requirements.txt -t /asset-output/python",
                        ],
                        local=_PipLocal(REQUIREMENTS_LAYER, subdir="python"),
                    ),
                ),
                compatible_runtimes=[RUNTIME],
                description="boto3, botocore y redis para las Lambdas",
            )
            layers.append(layer_dependencias)

        # ── 3. LAMBDA /items ──────────────────────────────────────────
        # Maneja POST /items, GET /items/{id} y DELETE /items/{id}
        # Implementa Cache-Aside: consulta Redis antes de ir a DynamoDB
        lambda_items: _lambda.Function = _lambda.Function(
            self,
            "ItemsHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/items"),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(10),
        )
        tabla.grant_read_write_data(lambda_items)

        # ── 4. LAMBDA /health ─────────────────────────────────────────
        # Health check: verifica conectividad con DynamoDB y Redis
        lambda_health: _lambda.Function = _lambda.Function(
            self,
            "HealthHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/health"),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(5),
        )
        tabla.grant_read_data(lambda_health)

        # ── 4.1 LAMBDA productos (patrones #1-#5 + ranking) ───────────
        # Cache-Aside en el detalle; doble escritura al índice de
        # Elasticsearch al crear/eliminar (búsqueda libre, patrón #6).
        lambda_productos: _lambda.Function = _lambda.Function(
            self,
            "ProductosHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/productos", con_comun=True),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(10),
        )
        tabla.grant_read_write_data(lambda_productos)

        # ── 4.2 LAMBDA carrito (patrón #7) ────────────────────────────
        # Sin caché ni Elasticsearch: solo Query/PutItem/DeleteItem sobre
        # la partición USER#<username>.
        lambda_carrito: _lambda.Function = _lambda.Function(
            self,
            "CarritoHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/carrito", con_comun=True),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(10),
        )
        tabla.grant_read_write_data(lambda_carrito)

        # ── 4.3 LAMBDA cupones (patrón #8) ────────────────────────────
        # Solo lectura (GetItem); sin caché para no aplicar un cupón
        # recién desactivado.
        lambda_cupones: _lambda.Function = _lambda.Function(
            self,
            "CuponesHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/cupones", con_comun=True),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(10),
        )
        tabla.grant_read_data(lambda_cupones)

        # ── 4.4 LAMBDA usuarios (patrones #9-#11) ─────────────────────
        # Solo lectura sobre la partición USER#<username>.
        lambda_usuarios: _lambda.Function = _lambda.Function(
            self,
            "UsuariosHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/usuarios", con_comun=True),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(10),
        )
        tabla.grant_read_data(lambda_usuarios)

        # ── 4.5 LAMBDA ordenes (patrones #12-#15) ─────────────────────
        # Checkout con TransactWriteItems (orden + líneas + vaciar carrito
        # + descontar stock) y orden por número con Cache-Aside sobre GSI1.
        lambda_ordenes: _lambda.Function = _lambda.Function(
            self,
            "OrdenesHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/ordenes", con_comun=True),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(15),
        )
        tabla.grant_read_write_data(lambda_ordenes)

        # ── 4.6 LAMBDA promociones (patrones #16-#17) ─────────────────
        # Write-Through a Redis con TTL hasta la medianoche de Bogotá.
        lambda_promociones: _lambda.Function = _lambda.Function(
            self,
            "PromocionesHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/promociones", con_comun=True),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(10),
        )
        tabla.grant_read_write_data(lambda_promociones)

        # ── 4.7 LAMBDA busqueda (patrón #6) ───────────────────────────
        # Solo consulta Elasticsearch: no necesita permisos sobre la tabla.
        lambda_busqueda: _lambda.Function = _lambda.Function(
            self,
            "BusquedaHandler",
            runtime=RUNTIME,
            handler="handler.main",
            code=_codigo_lambda("lambdas/busqueda", con_comun=True),
            layers=layers,
            environment=env_comun,
            timeout=cdk.Duration.seconds(10),
        )

        # ── 5. API GATEWAY ────────────────────────────────────────────
        # LambdaRestApi crea automáticamente una integración proxy
        # entre API Gateway y la Lambda raíz.
        # Luego agregamos recursos adicionales para /items y /health.
        api: apigw.RestApi = apigw.RestApi(
            self,
            "Api",
            rest_api_name="ProyectoFinal-API",
            description="API del proyecto final — DynamoDB + Redis + CDK",
            deploy_options=apigw.StageOptions(stage_name="prod"),
        )

        # /items  → GET (listar/obtener) y POST (crear)
        recurso_items: apigw.Resource = api.root.add_resource("items")
        recurso_items.add_method(
            "POST",
            apigw.LambdaIntegration(lambda_items),
        )
        # /items/{id}  → GET (obtener uno)
        recurso_item_id: apigw.Resource = recurso_items.add_resource("{id}")
        recurso_item_id.add_method(
            "GET",
            apigw.LambdaIntegration(lambda_items),
        )
        recurso_item_id.add_method(
            "DELETE",
            apigw.LambdaIntegration(lambda_items),
        )

        # /health  → GET
        recurso_health: apigw.Resource = api.root.add_resource("health")
        recurso_health.add_method(
            "GET",
            apigw.LambdaIntegration(lambda_health),
        )

        # ── 5.1 Rutas del catálogo → Lambda productos ─────────────────
        integ_productos = apigw.LambdaIntegration(lambda_productos)

        # /products → GET (#3 catálogo) y POST (crear)
        recurso_products: apigw.Resource = api.root.add_resource("products")
        recurso_products.add_method("GET", integ_productos)
        recurso_products.add_method("POST", integ_productos)
        # /products/ranking → GET (literal: API Gateway lo prefiere sobre {id})
        recurso_products.add_resource("ranking").add_method("GET", integ_productos)
        # /products/{id} → GET (#1 detalle) y DELETE
        recurso_product_id: apigw.Resource = recurso_products.add_resource("{id}")
        recurso_product_id.add_method("GET", integ_productos)
        recurso_product_id.add_method("DELETE", integ_productos)
        # /products/{id}/reviews → GET y POST (#4)
        recurso_reviews: apigw.Resource = recurso_product_id.add_resource("reviews")
        recurso_reviews.add_method("GET", integ_productos)
        recurso_reviews.add_method("POST", integ_productos)
        # /categories/{categoria}/products → GET (#2, GSI1)
        (api.root.add_resource("categories").add_resource("{categoria}")
            .add_resource("products").add_method("GET", integ_productos))
        # /brands/{marca}/products?categoria= → GET (#5, GSI2)
        (api.root.add_resource("brands").add_resource("{marca}")
            .add_resource("products").add_method("GET", integ_productos))

        # ── 5.2 Rutas del carrito → Lambda carrito ────────────────────
        # El usuario viaja en el header X-Username (no hay autenticación).
        integ_carrito = apigw.LambdaIntegration(lambda_carrito)
        recurso_cart: apigw.Resource = api.root.add_resource("cart")
        recurso_cart.add_method("GET", integ_carrito)
        recurso_cart_producto: apigw.Resource = recurso_cart.add_resource("{productId}")
        recurso_cart_producto.add_method("PUT", integ_carrito)
        recurso_cart_producto.add_method("DELETE", integ_carrito)

        # ── 5.3 Rutas de cupones → Lambda cupones ─────────────────────
        (api.root.add_resource("coupons").add_resource("{codigo}")
            .add_method("GET", apigw.LambdaIntegration(lambda_cupones)))

        # ── 5.4 Rutas de usuarios → Lambda usuarios ───────────────────
        # recurso_user_id lo reutilizan después las rutas de órdenes
        # (/users/{username}/orders).
        integ_usuarios = apigw.LambdaIntegration(lambda_usuarios)
        recurso_user_id: apigw.Resource = (
            api.root.add_resource("users").add_resource("{username}")
        )
        recurso_user_id.add_method("GET", integ_usuarios)                 # #9
        recurso_user_id.add_resource("addresses").add_method("GET", integ_usuarios)  # #10
        recurso_user_id.add_resource("wishlist").add_method("GET", integ_usuarios)   # #11

        # ── 5.5 Rutas de órdenes → Lambda ordenes ─────────────────────
        integ_ordenes = apigw.LambdaIntegration(lambda_ordenes)
        recurso_orders: apigw.Resource = api.root.add_resource("orders")
        recurso_orders.add_method("POST", integ_ordenes)                              # #12
        recurso_orders.add_resource("{orderId}").add_method("GET", integ_ordenes)     # #15
        recurso_user_orders: apigw.Resource = recurso_user_id.add_resource("orders")
        recurso_user_orders.add_method("GET", integ_ordenes)                          # #14
        recurso_user_orders.add_resource("ultima").add_method("GET", integ_ordenes)   # #13

        # ── 5.6 Rutas de promociones → Lambda promociones ─────────────
        integ_promociones = apigw.LambdaIntegration(lambda_promociones)
        recurso_promotions: apigw.Resource = api.root.add_resource("promotions")
        recurso_promotions.add_method("POST", integ_promociones)                       # #17
        recurso_promotions.add_resource("today").add_method("GET", integ_promociones)  # #16

        # ── 5.7 Ruta de búsqueda → Lambda busqueda ────────────────────
        (api.root.add_resource("search")
            .add_method("GET", apigw.LambdaIntegration(lambda_busqueda)))

        # ── 6. OUTPUTS ────────────────────────────────────────────────
        CfnOutput(self, "ApiUrl",
                  value=api.url,
                  description="URL base del API Gateway")
        CfnOutput(self, "TableName",
                  value=tabla.table_name,
                  description="Nombre de la tabla DynamoDB")
