"""
test_busqueda.py
────────────────
Tests de la Lambda busqueda (patrón #6) y del cliente comun/es.py.
No hay Elasticsearch real: se reemplaza el cliente por un objeto falso.
"""
import json

import pytest
from elastic_transport import ConnectionError as ErrorConexionES


@pytest.fixture
def busqueda(cargar_lambda):
    return cargar_lambda("busqueda")


def _buscar(handler, query):
    r = handler.main({"httpMethod": "GET", "resource": "/search",
                      "queryStringParameters": query}, None)
    return r["statusCode"], json.loads(r["body"])


# ── Lambda ────────────────────────────────────────────────────────────

def test_busqueda_devuelve_resultados(busqueda, monkeypatch):
    llamadas = []

    def falso(texto, limite):
        llamadas.append((texto, limite))
        return [{"productId": "APPL-MBA-M3", "nombre": "MacBook Air"}]

    monkeypatch.setattr(busqueda, "buscar_productos", falso)
    status, cuerpo = _buscar(busqueda, {"q": "  macbok  "})
    assert status == 200
    assert cuerpo["total"] == 1
    assert cuerpo["q"] == "macbok"
    assert llamadas == [("macbok", 20)]


@pytest.mark.parametrize("limite, esperado", [("5", 5), ("0", 1), ("500", 50)])
def test_limite_se_acota(busqueda, monkeypatch, limite, esperado):
    llamadas = []
    monkeypatch.setattr(busqueda, "buscar_productos",
                        lambda t, n: llamadas.append(n) or [])
    _buscar(busqueda, {"q": "x", "limite": limite})
    assert llamadas == [esperado]


@pytest.mark.parametrize("query", [None, {}, {"q": "   "}, {"q": "x", "limite": "abc"}])
def test_parametros_invalidos_retornan_400(busqueda, query):
    status, _ = _buscar(busqueda, query)
    assert status == 400


def test_metodo_no_soportado_retorna_405(busqueda):
    r = busqueda.main({"httpMethod": "POST", "resource": "/search"}, None)
    assert r["statusCode"] == 405


# ── comun/es.py ───────────────────────────────────────────────────────

class _ClienteFalso:
    """Imita lo mínimo de elasticsearch.Elasticsearch que usa es.py."""

    def __init__(self, caido=False, indice_existe=False):
        self.caido = caido
        self.creados, self.indexados = [], []
        self.indice_existe = indice_existe
        cliente = self

        class _Indices:
            def exists(self, index):
                cliente._fallar_si_caido()
                return cliente.indice_existe

            def create(self, index, mappings):
                cliente.creados.append((index, mappings))
                cliente.indice_existe = True

        self.indices = _Indices()

    def _fallar_si_caido(self):
        if self.caido:
            raise ErrorConexionES("sin elasticsearch")

    def index(self, index, id, document):
        self._fallar_si_caido()
        self.indexados.append((index, id, document))

    def search(self, index, size, query):
        self._fallar_si_caido()
        return {"hits": {"hits": [{"_source": {"productId": "P1"}}]}}


@pytest.fixture
def es_modulo(cargar_lambda, monkeypatch):
    cargar_lambda("busqueda")
    import es
    monkeypatch.setattr(es, "_indice_listo", False)
    return es


def test_indexar_crea_indice_con_mapeo_la_primera_vez(es_modulo, monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(es_modulo, "_get_client", lambda: cliente)

    assert es_modulo.indexar_producto({"id": "P1", "nombre": "Mouse"}) is True
    assert es_modulo.indexar_producto({"id": "P2", "nombre": "Teclado"}) is True

    assert len(cliente.creados) == 1
    assert cliente.creados[0][1]["properties"]["categoria"] == {"type": "keyword"}
    assert [i[1] for i in cliente.indexados] == ["P1", "P2"]


def test_es_caido_no_lanza_excepcion(es_modulo, monkeypatch):
    monkeypatch.setattr(es_modulo, "_get_client", lambda: _ClienteFalso(caido=True))
    assert es_modulo.indexar_producto({"id": "P1"}) is False
    assert es_modulo.buscar_productos("mouse") == []


def test_buscar_devuelve_los_source(es_modulo, monkeypatch):
    monkeypatch.setattr(es_modulo, "_get_client", lambda: _ClienteFalso(indice_existe=True))
    assert es_modulo.buscar_productos("mouse") == [{"productId": "P1"}]


# ── POST /search/reindex ──────────────────────────────────────────────

def test_reindex_lee_productos_de_dynamo_sin_resenas(busqueda, tabla_ecommerce, monkeypatch):
    tabla_ecommerce.put_item(Item={"PK": "PRODUCT#P1", "SK": "PRODUCT#P1", "id": "P1",
                                   "nombre": "Mouse", "precio": 10})
    tabla_ecommerce.put_item(Item={"PK": "PRODUCT#P1", "SK": "REVIEW#2026-11-19#ana",
                                   "calificacion": 5})
    tabla_ecommerce.put_item(Item={"PK": "USER#ana", "SK": "USER#ana"})
    recibidos = []
    monkeypatch.setattr(busqueda, "reconstruir_indice",
                        lambda productos: recibidos.extend(productos) or len(productos))

    r = busqueda.main({"httpMethod": "POST", "resource": "/search/reindex"}, None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"]) == {"productos_en_dynamodb": 1, "indexados": 1}
    assert [p["id"] for p in recibidos] == ["P1"]
    assert recibidos[0]["precio"] == 10        # Decimal → int antes de mandarlo a ES


def test_reindex_con_es_caido_retorna_503(busqueda, tabla_ecommerce, monkeypatch):
    monkeypatch.setattr(busqueda, "reconstruir_indice", lambda productos: None)
    r = busqueda.main({"httpMethod": "POST", "resource": "/search/reindex"}, None)
    assert r["statusCode"] == 503
