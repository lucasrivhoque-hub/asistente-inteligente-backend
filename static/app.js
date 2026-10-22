const API = "";
let token = leerToken();
let rolActual = null;
let temporizador = null;
let graficoActividad = null;
const cacheImagenes = {};   // id de consulta -> URL de la imagen ya descargada

const SECCIONES = {
  resumen:      { titulo: "Resumen", sub: "Vista general de la actividad del sistema." },
  imagenes:     { titulo: "Imágenes enviadas", sub: "Lo que ven los lentes, lo que preguntó el usuario y lo que respondió AVIVA." },
  rutas:        { titulo: "Rutas solicitadas", sub: "Registro de las indicaciones pedidas a AVIVA." },
  alertas:      { titulo: "Alertas", sub: "Eventos registrados por el sistema." },
  dispositivos: { titulo: "Dispositivos", sub: "Lentes inteligentes vinculados al sistema." },
  config:       { titulo: "Configuración", sub: "Registro de dispositivos y administración de usuarios." },
};
const SECCIONES_POR_ROL = {
  administrador: ["resumen", "imagenes", "rutas", "alertas", "dispositivos", "config"],
  operador: ["resumen", "imagenes", "rutas", "alertas"],
  tecnico: ["dispositivos", "alertas", "config"],
};
const ICO = (c) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${c}</svg>`;
const ICONOS = {
  resumen: ICO('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>'),
  imagenes: ICO('<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>'),
  rutas: ICO('<polygon points="3 11 22 2 13 21 11 13 3 11"/>'),
  alertas: ICO('<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>'),
  dispositivos: ICO('<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/>'),
  config: ICO('<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>'),
};

/* ---------- Utilidades ---------- */
function leerToken() { try { return sessionStorage.getItem("aviva_token"); } catch (e) { return null; } }
function guardarToken(t) { try { t ? sessionStorage.setItem("aviva_token", t) : sessionStorage.removeItem("aviva_token"); } catch (e) {} }
function esc(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fechaUTC(ts) { return ts && !/[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? ts + "Z" : ts; }
function formatoFecha(ts) {
  if (!ts) return "—";
  return new Date(fechaUTC(ts)).toLocaleString("es-BO", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
async function api(ruta, opciones = {}) {
  const res = await fetch(`${API}${ruta}`, {
    ...opciones,
    headers: { ...(opciones.headers || {}), Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) { cerrarSesion(); throw new Error("Sesión expirada"); }
  return res;
}

/* ---------- Acceso ---------- */
function mostrarRegistro() {
  document.getElementById("login-view").classList.add("hidden");
  document.getElementById("registro-view").classList.remove("hidden");
}
function mostrarLogin() {
  document.getElementById("registro-view").classList.add("hidden");
  document.getElementById("login-view").classList.remove("hidden");
}

async function registrarUsuario() {
  const nombre = document.getElementById("reg-nombre").value;
  const email = document.getElementById("reg-email").value;
  const password = document.getElementById("reg-password").value;
  const rol_nombre = document.getElementById("reg-rol").value;
  const errorMsg = document.getElementById("registro-error-msg");
  const okMsg = document.getElementById("registro-ok-msg");
  errorMsg.textContent = ""; okMsg.textContent = "";
  if (!nombre || !email || !password) { errorMsg.textContent = "Completa todos los campos"; return; }

  const res = await fetch(`${API}/usuarios/registro`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nombre, email, password, rol_nombre }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    errorMsg.textContent = data.detail || "No se pudo crear la cuenta";
    return;
  }
  okMsg.textContent = "Cuenta creada. Ya puedes iniciar sesión.";
  setTimeout(mostrarLogin, 1200);
}

async function iniciarSesion() {
  const email = document.getElementById("email").value;
  const password = document.getElementById("password").value;
  const errorMsg = document.getElementById("error-msg");
  errorMsg.textContent = "";

  const body = new URLSearchParams();
  body.append("username", email);
  body.append("password", password);

  const res = await fetch(`${API}/login`, { method: "POST", body });
  if (!res.ok) { errorMsg.textContent = "Correo o contraseña incorrectos"; return; }
  const data = await res.json();
  token = data.access_token;
  guardarToken(token);
  await cargarDashboard();
}

async function cargarDashboard() {
  let usuario;
  try {
    const res = await api("/usuarios/me");
    usuario = await res.json();
  } catch (e) { return; }
  rolActual = usuario.rol;

  document.getElementById("auth-view").classList.add("hidden");
  document.getElementById("app-view").classList.remove("hidden");
  document.getElementById("saludo").textContent = usuario.nombre;
  document.getElementById("rol-badge").textContent = usuario.rol === "tecnico" ? "Servicio técnico" : usuario.rol;
  document.getElementById("avatar").textContent = (usuario.nombre || "?").charAt(0).toUpperCase();

  construirNavegacion(usuario.rol);
}

function construirNavegacion(rol) {
  const secciones = SECCIONES_POR_ROL[rol] || [];
  document.getElementById("nav").innerHTML = secciones.map((s) =>
    `<button data-sec="${s}" onclick="cambiarSeccion('${s}')">${ICONOS[s]}<span>${SECCIONES[s].titulo}</span></button>`
  ).join("");
  if (secciones.length) cambiarSeccion(secciones[0]);
}

function cambiarSeccion(nombre) {
  document.querySelectorAll(".nav button").forEach((b) => b.classList.toggle("active", b.dataset.sec === nombre));
  document.querySelectorAll(".pane").forEach((p) => p.classList.remove("visible"));
  document.getElementById(`tab-${nombre}`).classList.add("visible");
  document.getElementById("titulo-seccion").textContent = SECCIONES[nombre].titulo;
  document.getElementById("subtitulo-seccion").textContent = SECCIONES[nombre].sub;

  if (temporizador) { clearInterval(temporizador); temporizador = null; }

  const acciones = {
    resumen: [cargarResumen, 15000],
    imagenes: [cargarImagenes, 4000],
    rutas: [cargarRutas, 5000],
    alertas: [cargarAlertas, 0],
    dispositivos: [cargarDispositivos, 0],
    config: [cargarConfig, 0],
  };
  const [funcion, cada] = acciones[nombre];
  funcion();
  if (cada) temporizador = setInterval(funcion, cada);
}

/* ---------- Resumen ---------- */
function claveDia(d) { return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`; }

async function cargarResumen() {
  try {
    const stats = await (await api("/estadisticas")).json();
    document.getElementById("stat-consultas").textContent = stats.total_consultas;
    document.getElementById("stat-rutas").textContent = stats.total_rutas;
    document.getElementById("stat-alertas").textContent = stats.total_alertas;
    document.getElementById("stat-dispositivos").textContent = `${stats.dispositivos_activos}/${stats.total_dispositivos}`;
    document.getElementById("stat-actividad").textContent = stats.ultima_actividad ? formatoFecha(stats.ultima_actividad) : "Sin actividad";

    const [consultas, rutas] = await Promise.all([
      (await api("/interacciones?limite=100")).json(),
      (await api("/rutas/historial?limite=200")).json(),
    ]);

    const dias = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - i); dias.push(d);
    }
    const conteo = (lista) => {
      const mapa = {};
      lista.forEach((x) => { const k = claveDia(new Date(fechaUTC(x.timestamp))); mapa[k] = (mapa[k] || 0) + 1; });
      return dias.map((d) => mapa[claveDia(d)] || 0);
    };

    if (graficoActividad) graficoActividad.destroy();
    graficoActividad = new Chart(document.getElementById("grafico-actividad"), {
      type: "bar",
      data: {
        labels: dias.map((d) => d.toLocaleDateString("es-BO", { weekday: "short", day: "numeric" })),
        datasets: [
          { label: "Consultas", data: conteo(consultas), backgroundColor: "#028090", borderRadius: 6 },
          { label: "Rutas", data: conteo(rutas), backgroundColor: "#e1b12c", borderRadius: 6 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
        plugins: { legend: { position: "bottom" } },
      },
    });
  } catch (e) { console.error(e); }
}

/* ---------- Imágenes enviadas ---------- */
async function cargarImagenProtegida(img, id) {
  if (cacheImagenes[id]) { img.src = cacheImagenes[id]; return; }
  try {
    const res = await api(`/interacciones/${id}/imagen`);
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    cacheImagenes[id] = url;
    img.src = url;
  } catch (e) {}
}

function ampliarImagen(src) {
  const visor = document.createElement("div");
  visor.className = "visor";
  visor.innerHTML = `<img src="${src}" alt="Imagen ampliada">`;
  visor.onclick = () => visor.remove();
  document.body.appendChild(visor);
}

async function cargarImagenes() {
  try {
    // Última imagen recibida
    const ultima = await (await api("/video/ultimo-frame")).json();
    const img = document.getElementById("img-ultima");
    const vacio = document.getElementById("img-ultima-vacio");
    if (ultima.frame) {
      img.src = `data:image/jpeg;base64,${ultima.frame}`;
      img.classList.remove("hidden");
      vacio.classList.add("hidden");
      document.getElementById("img-ultima-hora").textContent = ultima.hora ? formatoFecha(ultima.hora) : "—";
    }

    // Historial de consultas con imagen, pregunta y respuesta
    const consultas = await (await api("/interacciones?solo_imagenes=true&limite=20")).json();
    const cont = document.getElementById("lista-imagenes");
    if (!consultas.length) {
      cont.innerHTML = `<div class="card"><span class="vacio-texto">Todavía no hay consultas con imagen. Habla con AVIVA y pídele que describa algo.</span></div>`;
      return;
    }
    cont.innerHTML = consultas.map((c) => `
      <article class="consulta">
        <div class="thumb"><img data-id="${c.id}" alt="Imagen de la consulta"></div>
        <div>
          <div class="meta">
            <span>${formatoFecha(c.timestamp)}</span>
            <span class="tag ${c.detalle ? "detalle" : ""}">${c.detalle ? "Foto de detalle" : "Vista general"}</span>
          </div>
          <div class="bloque"><b>Pregunta</b>
            ${c.pregunta ? `<p>${esc(c.pregunta)}</p>` : `<p class="vacio-texto">No se registró la pregunta</p>`}</div>
          <div class="bloque resp"><b>Respuesta de AVIVA</b>
            ${c.respuesta ? `<p>${esc(c.respuesta)}</p>` : `<p class="vacio-texto">No se registró la respuesta</p>`}</div>
        </div>
      </article>`).join("");
    cont.querySelectorAll("img[data-id]").forEach((el) => {
      cargarImagenProtegida(el, el.dataset.id).then(() => { el.onclick = () => ampliarImagen(el.src); });
    });
  } catch (e) { console.error(e); }
}

/* ---------- Rutas ---------- */
async function cargarRutas() {
  try {
    const rutas = await (await api("/rutas/historial")).json();
    const tabla = document.getElementById("tabla-rutas");
    if (!rutas.length) {
      tabla.innerHTML = `<tr><td class="vacio-texto">Aún no se han solicitado rutas. Dile a AVIVA "llévame a…" para generar una.</td></tr>`;
      return;
    }
    tabla.innerHTML = `
      <tr><th>Fecha</th><th>Destino</th><th>Distancia</th><th>Tiempo</th><th>Estado</th></tr>
      ${rutas.map((r) => `
        <tr>
          <td>${formatoFecha(r.timestamp)}</td>
          <td>
            <b>${esc(r.destino)}</b>
            <div class="sub">Desde: ${esc(r.origen || "ubicación no registrada")}</div>
            ${r.resumen ? `<details><summary>Ver indicaciones</summary><ol class="pasos">${
              r.resumen.split("\n").map((l) => `<li>${esc(l.replace(/^\d+\.\s*/, ""))}</li>`).join("")
            }</ol></details>` : ""}
          </td>
          <td>${r.distancia_m != null ? (r.distancia_m >= 1000 ? (r.distancia_m / 1000).toFixed(1) + " km" : r.distancia_m + " m") : "—"}</td>
          <td>${r.duracion_min != null ? r.duracion_min + " min" : "—"}</td>
          <td><span class="estado ${r.exito ? "on" : "off"}">${r.exito ? "Calculada" : "Sin ruta"}</span></td>
        </tr>`).join("")}`;
  } catch (e) { console.error(e); }
}

/* ---------- Alertas y dispositivos ---------- */
async function cargarAlertas() {
  const alertas = await (await api("/alertas")).json();
  const cont = document.getElementById("lista-alertas");
  cont.innerHTML = alertas.length
    ? alertas.map((a) => `<div class="item"><div><b>${esc(a.descripcion)}</b><small>${esc(a.tipo)} · ${formatoFecha(a.timestamp)}</small></div></div>`).join("")
    : `<span class="vacio-texto">Sin alertas registradas todavía.</span>`;
}

async function cargarDispositivos() {
  const dispositivos = await (await api("/dispositivos")).json();
  const cont = document.getElementById("lista-dispositivos");
  cont.innerHTML = dispositivos.length
    ? dispositivos.map((d) => `
        <div class="item">
          <div><b>${esc(d.nombre)}</b></div>
          <div style="display:flex;gap:10px;align-items:center">
            <span class="estado ${d.activo ? "on" : "off"}">${d.activo ? "Activo" : "Inactivo"}</span>
            <button class="mini-btn" onclick="toggleDispositivo(${d.id})">${d.activo ? "Desactivar" : "Activar"}</button>
          </div>
        </div>`).join("")
    : `<span class="vacio-texto">Sin dispositivos registrados todavía.</span>`;
}

async function toggleDispositivo(id) {
  await api(`/dispositivos/${id}/estado`, { method: "PATCH" });
  cargarDispositivos();
}

async function registrarDispositivo() {
  const nombre = document.getElementById("disp-nombre").value;
  const identificador_sdk = document.getElementById("disp-sdk").value || null;
  const msg = document.getElementById("disp-msg");
  msg.textContent = "";
  if (!nombre) { msg.textContent = "Escribe un nombre para el dispositivo"; msg.className = "error"; return; }

  const res = await api("/dispositivos", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nombre, identificador_sdk }),
  });
  if (!res.ok) { msg.textContent = "No se pudo registrar el dispositivo"; msg.className = "error"; return; }
  msg.textContent = "Dispositivo registrado correctamente";
  msg.className = "ok";
  document.getElementById("disp-nombre").value = "";
  document.getElementById("disp-sdk").value = "";
}

/* ---------- Configuración ---------- */
async function cargarConfig() {
  document.getElementById("disp-msg").textContent = "";
  if (rolActual === "administrador") {
    document.getElementById("seccion-usuarios").classList.remove("hidden");
    const res = await api("/usuarios");
    if (!res.ok) return;
    const usuarios = await res.json();
    document.getElementById("tabla-usuarios").innerHTML = `
      <tr><th>Nombre</th><th>Correo</th><th>Rol</th></tr>
      ${usuarios.map((u) => `<tr><td>${esc(u.nombre)}</td><td>${esc(u.email)}</td><td>${esc(u.rol)}</td></tr>`).join("")}`;
  }
}

/* ---------- Sesión ---------- */
function cerrarSesion() {
  token = null;
  guardarToken(null);
  if (temporizador) { clearInterval(temporizador); temporizador = null; }
  document.getElementById("app-view").classList.add("hidden");
  document.getElementById("auth-view").classList.remove("hidden");
  document.getElementById("password").value = "";
}

// Si ya había una sesión abierta en esta pestaña, se retoma
if (token) cargarDashboard();