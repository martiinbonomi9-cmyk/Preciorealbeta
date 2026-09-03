"""
Cliente para la API pública que usa preciosclaros.gob.ar.

CONFIRMADO capturando el tráfico real del sitio (devtools > Network > Fetch/XHR),
tanto en https://preciosclaros.gob.ar/#!/buscar-productos (minorista) como en
https://mayoristas.preciosclaros.gob.ar/#!/buscar-productos (mayorista):

Los DOS entornos pegan al MISMO host, la diferencia es un query param:

  GET https://d3e6htiiul5ek9.cloudfront.net/prod/sucursales
      ?lat={lat}&lng={lng}&limit={limit}[&entorno=mayoristas]

  GET https://d3e6htiiul5ek9.cloudfront.net/prod/categorias
      [?entorno=mayoristas]

  GET https://d3e6htiiul5ek9.cloudfront.net/prod/productos
      ?string={termino_busqueda}&array_sucursales={ids_separados_por_coma}
      &offset={n}&limit={n<=100}&sort=-cant_sucursales_disponible
      [&entorno=mayoristas]
      -> listado de productos que matchean el término de búsqueda, con
         precioMin/precioMax AGREGADOS (no por cadena todavía). "total" te
         dice cuántos hay en total para paginar con offset.
         maxLimitPermitido=100, maxCantSucursalesPermitido=50 (o sea:
         array_sucursales no puede tener más de 50 ids por request).

  GET https://d3e6htiiul5ek9.cloudfront.net/prod/producto
      ?id_producto={id}&array_sucursales={ids}&limit={n}[&entorno=mayoristas]
      -> detalle de UN producto con precio por sucursal/cadena. Los nombres
         de campo para cadena/sucursal no los pude confirmar en pantalla
         (solo se vieron los de /productos), pero por los nombres de filtro
         reales que sí aparecen (comercio_bandera_nombre, sucursal_tipo,
         comercio_razon_social) lo más probable es que la cadena venga como
         "comercio_bandera_nombre". normalizar_producto() en run.py prueba
         varias variantes por las dudas — si al correrlo ves que las cadenas
         salen vacías, mirá un request a /producto en devtools y ajustá los
         nombres de campo ahí.

El campo "id" de cada producto es el CÓDIGO DE BARRAS (EAN), ej:
"7790230033031". Eso lo uso para pedir la imagen a Open Food Facts cuando
Precios Claros no la trae.
"""
from __future__ import annotations

import time

import requests

BASE = "https://d3e6htiiul5ek9.cloudfront.net/prod"
OFF_BASE = "https://world.openfoodfacts.org/api/v2/product"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (preciorealbeta scraper; contacto: martin)",
    "Accept": "application/json",
}

SAN_MARTIN_LAT = -34.5771
SAN_MARTIN_LNG = -58.5377

MAX_SUCURSALES_POR_REQUEST = 50  # maxCantSucursalesPermitido visto en la API
MAX_LIMIT_POR_REQUEST = 100      # maxLimitPermitido visto en la API


def _get(path: str, params: dict, retries: int = 3, backoff: float = 1.5) -> dict:
    url = f"{BASE}/{path}"
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Fallo GET {url} params={params}") from last_exc


def get_sucursales(entorno: str | None = None, lat: float = SAN_MARTIN_LAT,
                    lng: float = SAN_MARTIN_LNG, limit: int = 3000) -> list[dict]:
    """entorno=None -> minorista, entorno='mayoristas' -> mayorista."""
    params = {"lat": lat, "lng": lng, "limit": limit}
    if entorno:
        params["entorno"] = entorno
    data = _get("sucursales", params)
    return data.get("sucursales", data if isinstance(data, list) else [])


def get_categorias(entorno: str | None = None) -> list[dict]:
    params = {}
    if entorno:
        params["entorno"] = entorno
    data = _get("categorias", params)
    return data.get("categorias", data if isinstance(data, list) else [])


def buscar_productos(string: str, sucursal_ids: list[str], offset: int = 0,
                      limit: int = MAX_LIMIT_POR_REQUEST, entorno: str | None = None) -> dict:
    """Busca productos por texto (nombre/categoría) entre las sucursales dadas.
    Devuelve el dict crudo de la API: {status, total, productos: [...]}."""
    params = {
        "string": string,
        "array_sucursales": ",".join(sucursal_ids[:MAX_SUCURSALES_POR_REQUEST]),
        "offset": offset,
        "limit": limit,
        "sort": "-cant_sucursales_disponible",
    }
    if entorno:
        params["entorno"] = entorno
    return _get("productos", params)


def buscar_productos_todas_paginas(string: str, sucursal_ids: list[str],
                                    entorno: str | None = None, limite_paginas: int = 20) -> list[dict]:
    """Pagina automáticamente sobre /productos hasta traer todo lo que matchea 'string'."""
    productos: list[dict] = []
    offset = 0
    for _ in range(limite_paginas):
        data = buscar_productos(string, sucursal_ids, offset=offset, entorno=entorno)
        lote = data.get("productos", [])
        productos.extend(lote)
        total = data.get("total", len(productos))
        offset += len(lote)
        if not lote or offset >= total:
            break
        time.sleep(0.3)  # no golpear la API demasiado rápido
    return productos


def get_producto_detalle(id_producto: str, sucursal_ids: list[str],
                          entorno: str | None = None, limit: int = 50) -> dict:
    """Detalle de un producto con precio por sucursal/cadena."""
    params = {
        "id_producto": id_producto,
        "array_sucursales": ",".join(sucursal_ids[:MAX_SUCURSALES_POR_REQUEST]),
        "limit": limit,
    }
    if entorno:
        params["entorno"] = entorno
    return _get("producto", params)


def buscar_imagen_openfoodfacts(codigo_barras: str) -> str | None:
    """Fallback de imagen cuando Precios Claros no trae una, buscando el
    mismo código de barras (EAN) en Open Food Facts (gratis, sin API key)."""
    try:
        resp = requests.get(
            f"{OFF_BASE}/{codigo_barras}.json",
            params={"fields": "image_front_url,image_url"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        producto = data.get("product", {})
        return producto.get("image_front_url") or producto.get("image_url")
    except Exception:  # noqa: BLE001
        return None
