const DATA_DIR = "../data";

let productosOrdenados = [];
let filtroTipo = "todos";
let filtroTexto = "";

async function cargarJSON(ruta) {
  const resp = await fetch(ruta, { cache: "no-store" });
  if (!resp.ok) throw new Error(`No se pudo cargar ${ruta}`);
  return resp.json();
}

function formatoPrecio(n) {
  return n.toLocaleString("es-AR", { style: "currency", currency: "ARS", minimumFractionDigits: 0 });
}

async function init() {
  const latest = await cargarJSON(`${DATA_DIR}/latest.json`);
  document.getElementById("fecha-captura").textContent =
    `Precios al ${latest.fecha}`;

  productosOrdenados = Object.values(latest.productos).sort(
    (a, b) => a.precios[0].precio - b.precios[0].precio
  );

  render();

  document.getElementById("buscador").addEventListener("input", (e) => {
    filtroTexto = e.target.value.trim().toLowerCase();
    render();
  });

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach((c) => c.classList.remove("activo"));
      chip.classList.add("activo");
      filtroTipo = chip.dataset.tipo;
      render();
    });
  });

  document.getElementById("cerrar").addEventListener("click", cerrarPanel);
  document.getElementById("overlay").addEventListener("click", (e) => {
    if (e.target.id === "overlay") cerrarPanel();
  });
}

function render() {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";

  const visibles = productosOrdenados.filter((p) => {
    const pasaTipo = filtroTipo === "todos" || p.tipo === filtroTipo;
    const pasaTexto = !filtroTexto || (p.nombre || "").toLowerCase().includes(filtroTexto);
    return pasaTipo && pasaTexto;
  });

  document.getElementById("vacio").hidden = visibles.length > 0;

  for (const p of visibles) {
    const masBarato = p.precios[0];
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      ${p.precios.length > 1 ? `<span class="badge-oferta">Mejor precio</span>` : ""}
      <img src="${p.imagen}" alt="${p.nombre}" loading="lazy">
      <div class="nombre">${p.nombre || ""}</div>
      <div class="cadena-min">${masBarato.cadena || ""}</div>
      <div class="precio">${formatoPrecio(masBarato.precio)}</div>
    `;
    card.addEventListener("click", () => abrirPanel(p));
    grid.appendChild(card);
  }
}

async function abrirPanel(producto) {
  const overlay = document.getElementById("overlay");
  const contenido = document.getElementById("panel-contenido");
  overlay.hidden = false;

  contenido.innerHTML = `
    <div class="detalle-header">
      <img src="${producto.imagen}" alt="${producto.nombre}">
      <div>
        <h2>${producto.nombre || ""}</h2>
        <div class="marca">${producto.marca || ""} ${producto.presentacion || ""}</div>
      </div>
    </div>
    <div class="lista-precios">
      ${producto.precios
        .map(
          (pr) => `
        <div class="fila-precio">
          <div>
            <span class="cadena-nombre">${pr.cadena || "Comercio"}</span>
            <span class="sucursal-nombre">${pr.sucursal || ""}</span>
          </div>
          <span class="precio-monto">${formatoPrecio(pr.precio)}</span>
        </div>`
        )
        .join("")}
    </div>
    <div class="evolucion">
      <h3>Evolución de precio</h3>
      <div class="rango-botones">
        <button data-dias="7" class="activo">7 días</button>
        <button data-dias="30">30 días</button>
        <button data-dias="183">6 meses</button>
      </div>
      <div id="tabla-evolucion">Cargando...</div>
    </div>
  `;

  contenido.querySelectorAll(".rango-botones button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      contenido.querySelectorAll(".rango-botones button").forEach((b) => b.classList.remove("activo"));
      btn.classList.add("activo");
      await pintarEvolucion(producto.id_producto, parseInt(btn.dataset.dias, 10));
    });
  });

  await pintarEvolucion(producto.id_producto, 7);
}

async function pintarEvolucion(idProducto, dias) {
  const cont = document.getElementById("tabla-evolucion");
  try {
    const historial = await cargarJSON(`${DATA_DIR}/history_by_product/${idProducto}.json`);
    const limite = new Date();
    limite.setDate(limite.getDate() - dias);
    const limiteStr = limite.toISOString().slice(0, 10);

    const serie = historial.serie.filter((d) => d.fecha >= limiteStr);

    const cadenas = new Set();
    serie.forEach((d) => d.precios.forEach((p) => cadenas.add(p.cadena)));
    const listaCadenas = Array.from(cadenas);

    if (serie.length === 0) {
      cont.innerHTML = "<p>Todavía no hay historial suficiente para este rango.</p>";
      return;
    }

    let filas = "";
    for (const dia of serie) {
      const porCadena = Object.fromEntries(dia.precios.map((p) => [p.cadena, p.precio]));
      filas += `<tr><td>${dia.fecha}</td>${listaCadenas
        .map((c) => `<td>${porCadena[c] != null ? formatoPrecio(porCadena[c]) : "-"}</td>`)
        .join("")}</tr>`;
    }

    cont.innerHTML = `
      <table class="tabla-evolucion">
        <thead><tr><th>Fecha</th>${listaCadenas.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
        <tbody>${filas}</tbody>
      </table>
    `;
  } catch (e) {
    cont.innerHTML = "<p>No se pudo cargar el historial.</p>";
  }
}

function cerrarPanel() {
  document.getElementById("overlay").hidden = true;
}

init();
