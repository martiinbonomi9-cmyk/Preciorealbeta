"""
Orquesta la captura diaria:
  1. Resuelve sucursales (minorista y mayorista) cercanas a Gral. San Martín.
  2. Trae categorías de cada entorno y busca productos categoría por
     categoría (paginando), porque la API es de tipo "búsqueda" y no tiene
     un "traeme todo" directo.
  3. Para cada producto encontrado, pide el detalle por sucursal/cadena y
     se queda con el menor precio entre normal y promoción.
  4. Si un producto no trae imagen, intenta completarla por código de
     barras (EAN) contra Open Food Facts.
  5. Descarta lo que, después de todo esto, sigue sin imagen o sin precio.
  6. Guarda data/history/{fecha}.json (pisando si corre 2 veces el mismo
     día) y actualiza el historial por producto + índices para el front.

Se ejecuta desde GitHub Actions dos veces por día (20hs y 2am ARG).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import client

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
HISTORY_BY_PRODUCT_DIR = DATA_DIR / "history_by_product"
ARG_TZ = timezone(timedelta(hours=-3))  # Argentina no tiene horario de verano
SEIS_MESES = timedelta(days=183)


def hoy_arg() -> str:
    return datetime.now(ARG_TZ).strftime("%Y-%m-%d")


def ahora_arg_iso() -> str:
    return datetime.now(ARG_TZ).isoformat()


def _num(valor) -> float | None:
    """La API devuelve '' (string vacío) en vez de None cuando no hay dato."""
    if valor in (None, "", "null"):
        return None
    try:
        n = float(valor)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def mejor_precio(*candidatos) -> float | None:
    validos = [_num(c) for c in candidatos]
    validos = [v for v in validos if v is not None]
    return min(validos) if validos else None


def extraer_precios_por_cadena(detalle_producto: dict) -> list[dict]:
    """Recorre la respuesta CONFIRMADA de /producto y arma
    [{cadena, sucursal, precio}], quedándose con el menor precio entre
    precio de lista y las promociones (promo1/promo2) de cada sucursal.
    Sucursales sin el producto traen {"message": "..."} y se descartan."""
    filas = detalle_producto.get("sucursales") or []
    resultado = []
    for f in filas:
        if "message" in f or "preciosProducto" not in f:
            continue  # "La sucursal no contiene el producto."

        pp = f["preciosProducto"]
        precio_final = mejor_precio(
            pp.get("precioLista"),
            (pp.get("promo1") or {}).get("precio"),
            (pp.get("promo2") or {}).get("precio"),
            pp.get("precio_unitario_con_iva"),   # mayorista (ej. Makro)
            pp.get("precio_bulto_con_iva"),       # mayorista (ej. Makro)
        )
        if precio_final is None:
            continue

        resultado.append({
            "cadena": f.get("banderaDescripcion"),
            "sucursal": f.get("sucursalNombre"),
            "precio": precio_final,
        })

    resultado.sort(key=lambda p: p["precio"])
    return resultado


def normalizar_y_enriquecer(item_busqueda: dict, sucursal_ids: list[str],
                             entorno: str | None, tipo: str) -> dict | None:
    id_producto = str(item_busqueda.get("id") or "")
    if not id_producto:
        return None

    detalle = client.get_producto_detalle(id_producto, sucursal_ids, entorno=entorno)
    precios = extraer_precios_por_cadena(detalle)

    if not precios:
        # sin precio por cadena no sirve para la comparación -> se descarta
        return None

    imagen = (
        item_busqueda.get("imagen")
        or detalle.get("imagen")
        or client.buscar_imagen_openfoodfacts(id_producto)
    )
    if not imagen:
        return None  # pedido explícito: si no hay imagen (ni por fallback), se descarta

    return {
        "id_producto": id_producto,
        "nombre": item_busqueda.get("nombre"),
        "marca": item_busqueda.get("marca"),
        "presentacion": item_busqueda.get("presentacion"),
        "imagen": imagen,
        "tipo": tipo,
        "precios": precios,
    }


def capturar_entorno(entorno: str | None, tipo: str, productos: dict[str, dict]) -> None:
    sucursales = client.get_sucursales(entorno=entorno)
    sucursal_ids = [s.get("id") for s in sucursales if s.get("id")]
    print(f"[{tipo}] sucursales cercanas: {len(sucursal_ids)}")
    if not sucursal_ids:
        print(f"[{tipo}] sin sucursales, se omite")
        return

    categorias = client.get_categorias(entorno=entorno)
    terminos = [c.get("nombre") or c.get("categoria") for c in categorias] or ["comida"]
    print(f"[{tipo}] categorías a recorrer: {len(terminos)}")

    for termino in terminos:
        if not termino:
            continue
        try:
            encontrados = client.buscar_productos_todas_paginas(termino, sucursal_ids, entorno=entorno)
        except Exception as exc:  # noqa: BLE001
            print(f"[{tipo}] error buscando '{termino}': {exc}")
            continue

        for item in encontrados:
            clave = f"{tipo}-{item.get('id')}"
            if clave in productos:
                continue  # ya lo tenemos de otra categoría
            try:
                prod = normalizar_y_enriquecer(item, sucursal_ids, entorno, tipo)
            except Exception as exc:  # noqa: BLE001
                print(f"[{tipo}] error en producto {item.get('id')}: {exc}")
                continue
            if prod:
                productos[clave] = prod
            time.sleep(0.1)


def capturar() -> dict[str, dict]:
    productos: dict[str, dict] = {}
    capturar_entorno(None, "minorista", productos)
    capturar_entorno("mayoristas", "mayorista", productos)
    return productos


def guardar_snapshot(productos: dict[str, dict]) -> None:
    fecha = hoy_arg()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_BY_PRODUCT_DIR.mkdir(parents=True, exist_ok=True)

    snapshot_path = HISTORY_DIR / f"{fecha}.json"
    snapshot = {
        "fecha": fecha,
        "ultima_captura": ahora_arg_iso(),
        "productos": productos,
    }
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Guardado {snapshot_path} ({len(productos)} productos)")

    for pid, prod in productos.items():
        hist_path = HISTORY_BY_PRODUCT_DIR / f"{pid}.json"
        if hist_path.exists():
            historial = json.loads(hist_path.read_text(encoding="utf-8"))
        else:
            historial = {
                "id_producto": prod["id_producto"],
                "nombre": prod["nombre"],
                "imagen": prod["imagen"],
                "serie": [],
            }

        serie = [d for d in historial["serie"] if d["fecha"] != fecha]
        serie.append({"fecha": fecha, "precios": prod["precios"]})

        limite = (datetime.now(ARG_TZ) - SEIS_MESES).strftime("%Y-%m-%d")
        serie = [d for d in serie if d["fecha"] >= limite]
        serie.sort(key=lambda d: d["fecha"])

        historial["serie"] = serie
        historial["nombre"] = prod["nombre"]
        historial["imagen"] = prod["imagen"]
        hist_path.write_text(json.dumps(historial, ensure_ascii=False, indent=2), encoding="utf-8")

    (DATA_DIR / "latest.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    index_path = DATA_DIR / "index.json"
    fechas = sorted(p.stem for p in HISTORY_DIR.glob("*.json"))
    index_path.write_text(json.dumps({"fechas": fechas}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    productos = capturar()
    guardar_snapshot(productos)
