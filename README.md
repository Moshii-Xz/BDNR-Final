# Marea — ecommerce sobre DynamoDB, Redis y Elasticsearch

Proyecto final de **Bases de Datos No Relacionales** (Universidad del Magdalena).
Backend serverless de una tienda de tecnología: catálogo, reseñas, carrito, cupones,
usuarios con direcciones y favoritos, checkout transaccional, historial de órdenes,
promoción del día, ranking de más vistos y búsqueda de texto libre. Incluye una tienda
web ("Marea") para la demo.

> En NoSQL no se diseñan tablas, se diseñan patrones de acceso.

| Pieza | Rol |
|---|---|
| **DynamoDB** | Fuente de verdad. Una sola tabla (Single Table Design) con dos GSIs |
| **Redis** | Caché (Cache-Aside, Write-Through) y ranking en un Sorted Set |
| **Elasticsearch** | Copia indexada de los productos para búsqueda con tolerancia a errores de escritura |
| **Lambda (Python 3.11)** | Una función por dominio, detrás de API Gateway |
| **AWS CDK (Python)** | Toda la infraestructura en `app/infra/stack.py` |
| **Docker Compose + LocalStack** | Entorno local completo, sin cuenta de AWS |

## Arquitectura

```
 Navegador ──▶ frontend (nginx :8080) ──proxy──▶ emulador (LocalStack :4566)
                                                   ├─ API Gateway
                                                   ├─ Lambdas ──┬──▶ DynamoDB (AppTable)
                                                   │            ├──▶ redis :6379
                                                   │            └──▶ elasticsearch :9200
 cdk-dev (Python, CDK, pytest) ──cdklocal deploy──▶┘
```

Cinco contenedores en la red `bdnr-final-local`: `emulador`, `redis`, `elasticsearch`
(`bdnr-elasticsearch`), `cdk-dev`, `frontend` (`bdnr-frontend`). El emulador lanza cada
Lambda en esa misma red, por eso llegan a Redis y Elasticsearch por nombre de servicio.

El frontend llama al API a través de su propio nginx (mismo origen, sin CORS) y descubre
solo el `api-id` del despliegue actual.

## Puesta en marcha

Requisitos: Docker Desktop (con al menos 4 GB de memoria asignados).

```bash
docker compose up -d --build          # levanta los contenedores
docker compose exec cdk-dev bash      # todo lo siguiente va dentro de cdk-dev

cdklocal bootstrap                    # primera vez y tras cada reinicio del emulador
cdklocal deploy                       # crea tabla, Lambdas y API (imprime ApiUrl)
python scripts/seed.py                # datos de demostración
```

Luego abrir **http://localhost:8080**. Con `http://localhost:8080/?inspector=1` la tienda
abre con el panel **"Por dentro"**, que muestra cada petición con el patrón de acceso que
resuelve y de dónde salió el dato (Redis, DynamoDB o Elasticsearch).

> LocalStack Community no guarda estado: si se reinicia el emulador (o el PC), hay que
> repetir `bootstrap`, `deploy` y `seed`. El `api-id` cambia en cada despliegue; el
> frontend y los scripts lo encuentran solos.

### Datos de demostración (`scripts/seed.py`)

- 18 productos en pesos colombianos, 6 categorías, varias marcas, con reseñas.
- Usuarios `camilo_dev`, `caro_dev`, `ana_compras`, `beto_tech` con direcciones y favoritos.
- Cupones: `TECH10` (válido), `BIENVENIDA15` (mínimo de compra), `NAVIDAD25` (aún no
  vigente), `VERANO5` (vencido), `PRUEBAOFF` (desactivado).
- Promoción del día, órdenes hechas con el checkout real, un carrito abierto y visitas
  para el ranking.

El seed se puede correr varias veces sin duplicar datos.

## Endpoints

Base: `http://localhost:4566/restapis/<api-id>/prod/_user_request_`. Los `POST`/`PUT`
llevan `Content-Type: application/json`. El carrito y el checkout identifican al usuario
con el header `X-Username` (no hay autenticación: queda fuera del alcance del proyecto).

| # | Patrón de acceso | Ruta | Cómo se resuelve |
|---|---|---|---|
| 1 | Detalle de un producto | `GET /products/{id}` | `GetItem` + **Cache-Aside** (TTL 5 min) |
| 2 | Productos de una categoría | `GET /categories/{cat}/products` | `Query` en **GSI1** `CAT#<cat>` |
| 3 | Catálogo completo | `GET /products` | `Scan` (baja frecuencia, catálogo pequeño) |
| 4 | Reseñas de un producto | `GET/POST /products/{id}/reviews` | `Query` `PRODUCT#<id>` + `begins_with REVIEW#`, más reciente primero |
| 5 | Productos de una marca (y categoría) | `GET /brands/{marca}/products?categoria=` | `Query` en **GSI2** `BRAND#<marca>` + `begins_with CAT#<cat>#` |
| 6 | Búsqueda de texto libre | `GET /search?q=` | **Elasticsearch** `multi_match` con `fuzziness` |
| 7 | Carrito del usuario | `GET /cart`, `PUT/DELETE /cart/{productId}` | `Query` `USER#<u>` + `begins_with CART#` |
| 8 | Validar un cupón | `GET /coupons/{codigo}` | `GetItem` `COUPON#<codigo>` |
| 9 | Perfil | `GET /users/{u}` | `GetItem` `USER#<u>` |
| 10 | Direcciones | `GET /users/{u}/addresses`, `PUT/DELETE …/{nombre}` | `Query` `begins_with ADDRESS#` |
| 11 | Favoritos | `GET /users/{u}/wishlist`, `PUT/DELETE …/{productId}` | `Query` `begins_with WISH#` |
| 12 | Checkout | `POST /orders` | **`TransactWriteItems`**: orden + líneas, vacía el carrito, descuenta stock con condición |
| 13 | Última orden | `GET /users/{u}/orders/ultima` | `Query` inverso `Limit=1` + `Query` de esa orden |
| 14 | Historial de órdenes | `GET /users/{u}/orders` | `Query` `begins_with ORDER#` (SK ordenada por fecha), filtro `tipo = ORDER` para dejar solo las cabeceras |
| 15 | Orden por número | `GET /orders/{orderId}` | `Query` en **GSI1** `ORDER#<id>` + **Cache-Aside** (600 s) |
| 16 | Promoción del día | `GET /promotions/today` | **Redis** (Write-Through) con respaldo en DynamoDB `PROMO#<fecha>` |
| 17 | Publicar promoción | `POST /promotions` | `PutItem` + **Write-Through** con TTL hasta medianoche de Bogotá |
| 18 | Más vistos | `GET /products/ranking` | **Sorted Set** de Redis (`ZINCRBY` en cada detalle) |

Rutas de administración y mantenimiento: `POST /products`, `DELETE /products/{id}`,
`POST /coupons`, `POST /users`, `POST /search/reindex` (reconstruye el índice de
Elasticsearch desde DynamoDB) y `GET /health` (estado de DynamoDB, Redis y Elasticsearch).

## Diseño de datos

Tabla única `AppTable` con `PK`/`SK` y dos índices (detalle de cada decisión en
`docs/entregables/`). Todas las llaves se construyen en
`app/lambdas/comun/claves.py`.

| Entidad | PK | SK | Índices |
|---|---|---|---|
| Producto | `PRODUCT#<id>` | `PRODUCT#<id>` | GSI1 `CAT#<cat>` / `PRODUCT#<id>` · GSI2 `BRAND#<marca>` / `CAT#<cat>#PRODUCT#<id>` |
| Reseña | `PRODUCT#<id>` | `REVIEW#<fecha>#<usuario>` | |
| Usuario | `USER#<u>` | `USER#<u>` | |
| Ítem de carrito | `USER#<u>` | `CART#<productId>` | |
| Dirección | `USER#<u>` | `ADDRESS#<nombre>` | |
| Favorito | `USER#<u>` | `WISH#<productId>` | |
| Orden | `USER#<u>` | `ORDER#<fecha>#<orderId>` | GSI1 `ORDER#<orderId>` / `ORDER#HEADER` |
| Línea de orden | `USER#<u>` | `ORDER#<fecha>#<orderId>#ITEM#<productId>` | GSI1 `ORDER#<orderId>` / `ITEM#<productId>` |
| Cupón | `COUPON#<codigo>` | `COUPON#<codigo>` | |
| Promoción | `PROMO#<fecha>` | `PROMO#<fecha>` | |

GSI1 se reutiliza para dos patrones distintos (categoría y orden por número): sus
prefijos no se mezclan, así que una sola tabla de índice sirve a ambos.

## Redis

| Uso | Patrón | Por qué |
|---|---|---|
| Detalle de producto | **Cache-Aside**, TTL 5 min, se invalida al modificar o comprar | Es la lectura más frecuente y cambia poco |
| Orden por número | **Cache-Aside**, TTL 600 s | Una orden ya creada no cambia; se consulta varias veces seguidas |
| Promoción del día | **Write-Through**, TTL hasta la medianoche de Bogotá | Se lee en cada visita a la tienda y vence sola cuando cambia el día |
| Ranking de más vistos | **Sorted Set** nativo | No es caché de DynamoDB: es un contador en tiempo real que solo vive en Redis |

**Modo degradado:** si Redis o Elasticsearch no responden, las Lambdas siguen
funcionando contra DynamoDB (solo se pierde la caché, el ranking o la búsqueda).

## Pruebas

```bash
docker compose exec cdk-dev python -m pytest tests/ -v     # unitarias: moto + fakeredis, sin emulador
docker compose exec cdk-dev python scripts/humo.py         # de punta a punta contra el API desplegado
```

La prueba de humo recorre todas las rutas con DynamoDB, Redis y Elasticsearch reales
(49 comprobaciones) y al terminar borra sus datos y restaura la promoción del día.

## Estructura

```
├── docker-compose.yml        # emulador, redis, elasticsearch, cdk-dev, frontend
├── Dockerfile                # imagen de cdk-dev
├── app/
│   ├── infra/stack.py        # tabla + GSIs, Lambdas, rutas de API Gateway
│   ├── lambdas/
│   │   ├── comun/            # llaves, caché, Elasticsearch, cupones, respuestas HTTP
│   │   ├── productos/  carrito/  cupones/  usuarios/
│   │   ├── ordenes/  promociones/  busqueda/  health/
│   │   └── items/            # CRUD del proyecto base del curso
│   ├── scripts/              # seed.py, humo.py, api_local.py
│   └── tests/                # una prueba por Lambda
└── frontend/                 # tienda Marea: HTML, CSS y JS sin compilación + nginx.conf
```

## Limitaciones conocidas

- **Sin autenticación:** el usuario viaja en `X-Username` y la sección Gestión está
  abierta. Es una decisión de alcance, no un descuido.
- **Sin persistencia local:** LocalStack Community pierde los datos al reiniciarse.
- **Memoria:** LocalStack deja vivo un contenedor por cada Lambda usada. Con poca memoria
  en Docker la primera llamada a una Lambda puede tardar o fallar; basta con reintentar.
