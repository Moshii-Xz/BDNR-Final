## Diseño completo (fuente de verdad — no reinterpretar ni cambiar sin avisar)

Entidades en la tabla única `AppTable` (PK/SK string, on-demand): **Product, Review,
CartItem, Coupon, User, Address, WishlistItem, Order/OrderItem, Promotion**.
Las llaves exactas de cada una ya están en `app/lambdas/comun/claves.py` — son la
fuente de verdad, no se improvisan strings nuevos en un handler.

GSIs que `stack.py` todavía NO tiene y hay que agregar a `AppTable`:
- **GSI1**: `GSI1PK` / `GSI1SK` — reutilizado por dos patrones distintos:
  - Productos por categoría: `GSI1PK=CAT#<categoria>`, `GSI1SK=PRODUCT#<id>`
  - Una orden por su número (sin saber el usuario): `GSI1PK=ORDER#<orderId>`,
    `GSI1SK=ORDER#HEADER` (header) / `ITEM#<productId>` (líneas)
- **GSI2**: `GSI2PK` / `GSI2SK` — productos por marca (y opcionalmente categoría):
  `GSI2PK=BRAND#<marca>`, `GSI2SK=CAT#<categoria>#PRODUCT#<id>`

18 patrones de acceso ya documentados y entregados en PDF (tabla completa +
Single Table Design + reflexión) y en un Word con la justificación endpoint por
endpoint — ambos en `docs/entregables/` (si no están ahí todavía, pídemelos antes
de rediseñar nada). Resumen de rutas por dominio:

- `productos` → `GET/POST /products`, `GET/DELETE /products/{id}`,
  `GET /categories/{cat}/products`, `GET /brands/{marca}/products`,
  `GET/POST /products/{id}/reviews`
- `busqueda` → `GET /search?q=` (Elasticsearch, no DynamoDB)
- `carrito` → `GET /cart`, `PUT/DELETE /cart/{productId}`
- `cupones` → `GET /coupons/{codigo}`
- `usuarios` → `GET /users/{username}`, `GET /users/{username}/addresses`,
  `GET /users/{username}/wishlist`
- `ordenes` → `POST /orders` (checkout transaccional), `GET /users/{username}/orders`,
  `GET /users/{username}/orders/ultima`, `GET /orders/{orderId}`
- `promociones` → `GET /promotions/today` (Write-Through + TTL hasta medianoche
  Bogotá), `POST /promotions`
- `health` → extender para chequear también Elasticsearch, no solo DynamoDB/Redis

Patrones de caché usados (no agregar un cuarto sin justificarlo primero):
**Cache-Aside** (detalle de producto, orden por número), **Write-Through + TTL
hasta medianoche** (promoción del día), **Sorted Set nativo de Redis** (ranking de
más vistos, no es caché de DynamoDB).

## Plan de trabajo pendiente (en este orden)

1. Agregar GSI1 y GSI2 a `AppTable` en `stack.py` (ya existe la tabla, falta esto).
2. Lambda `productos` (patrones 1-6) + su ruta en `stack.py`.
3. Lambda `carrito` (patrón 7).
4. Lambda `cupones` (patrón 8).
5. Lambda `usuarios` (patrones 9-11: perfil, direcciones, wishlist).
6. Lambda `ordenes` (patrones 12-15, incluye transacción de checkout).
7. Lambda `promociones` (patrones 16-17).
8. Lambda `busqueda` (Elasticsearch) + agregar el servicio `elasticsearch` a
   `docker-compose.yml` (no existe todavía) + actualizar `health/handler.py`.
9. Tests por dominio en `app/tests/` (mismo estilo que `test_items.py` /
   `test_cache.py`: moto + fakeredis, sin emulador).
10. Script de seed de datos de prueba.
11. Frontend simple para la demo .

No saltar al paso siguiente sin que el anterior tenga sus tests pasando.

## Reglas de trabajo con git (obligatorias, no implícitas)

- **Nunca** trabajar directo sobre `main`. Todo cambio va en una rama `feature/<algo>`
  (ya existe `feature/catalogo`; crear una nueva por cada fase/dominio si tiene sentido,
  o seguir usando la misma si son cambios relacionados — preguntar si hay duda).
- Se permite hacer **commits locales** en la rama de trabajo sin pedir permiso cada vez,
  siempre que sean commits pequeños y coherentes (un cambio lógico por commit, mensaje en
  español, estilo `feat(dominio): ...` / `fix(dominio): ...` / `test(dominio): ...`).
- **Nunca ejecutar `git push`, `git push --force`, merge a `main`, ni abrir/cerrar un PR
  sin pedir autorización explícita primero**, mostrando qué se va a subir (`git log` y/o
  `git diff` del rango de commits) y esperando confirmación antes de correr el push.
- Nunca usar `git commit --amend`, `rebase`, `reset --hard` ni reescribir historia sin
  que yo lo pida explícitamente.
- Si un comando de git queda trabado por un `.lock` (puede pasar por el bridge de Claude
  Desktop), está bien borrarlo con `rm -f .git/*.lock` u otro `.lock` específico — eso no
  cuenta como una operación que necesite autorización, es solo destrabar git.
- Antes de instalar una dependencia nueva (`requirements.txt` / layer), decirlo y para qué
  es, no agregarla "de paso" dentro de otro commit.