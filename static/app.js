const API = "";
let token = null;
let rolActual = null;
let intervaloVideo = null;
let graficoAlertas = null;

const TABS_POR_ROL = {
  administrador: ["resumen", "video", "alertas", "dispositivos", "config"],
  operador: ["resumen", "video", "alertas"],
  tecnico: ["dispositivos", "config"],
};
const NOMBRES_TAB = {
  resumen: "Resumen", video: "Video en vivo", alertas: "Alertas",
  dispositivos: "Dispositivos", config: "Configuración",
};

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
  errorMsg.textContent = "";
  okMsg.textContent = "";
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
  if (!res.ok) { errorMsg.textContent = "Email o contraseña incorrectos"; return; }
  const data = await res.json();
  token = data.access_token;
  await cargarDashboard();
}

async function cargarDashboard() {
  const res = await fetch(`${API}/usuarios/me`, { headers: { Authorization: `Bearer ${token}` } });
  const usuario = await res.json();
  rolActual = usuario.rol;

  document.getElementById("login-view").classList.add("hidden");
  document.getElementById("registro-view").classList.add("hidden");
  document.getElementById("dashboard-view").classList.remove("hidden");
  document.getElementById("saludo").textContent = `Hola, ${usuario.nombre}`;
  document.getElementById("rol-badge").textContent = usuario.rol;

  construirTabs(usuario.rol);
}

function construirTabs(rol) {
  const tabs = TABS_POR_ROL[rol] || [];
  const cont = document.getElementById("tabs");
  cont.innerHTML = tabs.map((t, i) =>
    `<button class="tab-btn ${i === 0 ? "active" : ""}" data-tab="${t}" onclick="cambiarTab('${t}')">${NOMBRES_TAB[t]}</button>`
  ).join("");
  tabs.forEach((t, i) => cambiarTab(t, i === 0));
}

function cambiarTab(nombre) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === nombre));
  document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("visible"));
  document.getElementById(`tab-${nombre}`).classList.add("visible");

  if (intervaloVideo) { clearInterval(intervaloVideo); intervaloVideo = null; }

  if (nombre === "resumen") cargarResumen();
  if (nombre === "video") iniciarVideo();
  if (nombre === "alertas") cargarAlertas();
  if (nombre === "dispositivos") cargarDispositivos();
  if (nombre === "config") cargarConfig();
}

async function cargarResumen() {
  const res = await fetch(`${API}/estadisticas`, { headers: { Authorization: `Bearer ${token}` } });
  const stats = await res.json();
  document.getElementById("stat-alertas").textContent = stats.total_alertas;
  document.getElementById("stat-dispositivos").textContent = `${stats.dispositivos_activos}/${stats.total_dispositivos}`;
  document.getElementById("stat-actividad").textContent = stats.ultima_actividad
    ? new Date(stats.ultima_actividad).toLocaleString() : "Sin actividad";

  const resAlertas = await fetch(`${API}/alertas`, { headers: { Authorization: `Bearer ${token}` } });
  const alertas = await resAlertas.json();
  const conteo = {};
  alertas.forEach(a => { conteo[a.tipo] = (conteo[a.tipo] || 0) + 1; });

  const ctx = document.getElementById("grafico-alertas");
  if (graficoAlertas) graficoAlertas.destroy();
  graficoAlertas = new Chart(ctx, {
    type: "bar",
    data: {
      labels: Object.keys(conteo),
      datasets: [{ label: "Alertas por tipo", data: Object.values(conteo), backgroundColor: "#028090" }],
    },
    options: { responsive: true, plugins: { legend: { display: false } } },
  });
}

function iniciarVideo() {
  const img = document.getElementById("video-frame");
  const placeholder = document.getElementById("video-placeholder");
  intervaloVideo = setInterval(async () => {
    const res = await fetch(`${API}/video/ultimo-frame`, { headers: { Authorization: `Bearer ${token}` } });
    const data = await res.json();
    if (data.frame) {
      img.src = `data:image/jpeg;base64,${data.frame}`;
      img.classList.remove("hidden");
      placeholder.classList.add("hidden");
    } else {
      img.classList.add("hidden");
      placeholder.classList.remove("hidden");
    }
  }, 1000);
}

async function cargarAlertas() {
  const res = await fetch(`${API}/alertas`, { headers: { Authorization: `Bearer ${token}` } });
  const alertas = await res.json();
  const cont = document.getElementById("lista-alertas");
  cont.innerHTML = alertas.length
    ? alertas.map(a => `<div class="item"><b>${a.tipo}</b> — ${a.descripcion}<br><small>${a.timestamp}</small></div>`).join("")
    : "<p>Sin alertas registradas todavía.</p>";
}

async function cargarDispositivos() {
  const res = await fetch(`${API}/dispositivos`, { headers: { Authorization: `Bearer ${token}` } });
  const dispositivos = await res.json();
  const cont = document.getElementById("lista-dispositivos");
  cont.innerHTML = dispositivos.length
    ? dispositivos.map(d => `
        <div class="item item-row">
          <div><b>${d.nombre}</b> — ${d.activo ? "Activo" : "Inactivo"}</div>
          <button class="mini-btn" onclick="toggleDispositivo(${d.id})">${d.activo ? "Desactivar" : "Activar"}</button>
        </div>`).join("")
    : "<p>Sin dispositivos registrados todavía.</p>";
}

async function toggleDispositivo(id) {
  await fetch(`${API}/dispositivos/${id}/estado`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}` },
  });
  cargarDispositivos();
}

async function registrarDispositivo() {
  const nombre = document.getElementById("disp-nombre").value;
  const identificador_sdk = document.getElementById("disp-sdk").value || null;
  const msg = document.getElementById("disp-msg");
  msg.textContent = "";
  if (!nombre) { msg.textContent = "Escribe un nombre para el dispositivo"; msg.className = "error"; return; }

  const res = await fetch(`${API}/dispositivos`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ nombre, identificador_sdk }),
  });
  if (!res.ok) { msg.textContent = "No se pudo registrar el dispositivo"; msg.className = "error"; return; }
  msg.textContent = "Dispositivo registrado correctamente";
  msg.className = "ok";
  document.getElementById("disp-nombre").value = "";
  document.getElementById("disp-sdk").value = "";
}

async function cargarConfig() {
  document.getElementById("disp-msg").textContent = "";
  if (rolActual === "administrador") {
    document.getElementById("seccion-usuarios").classList.remove("hidden");
    cargarUsuarios();
  }
}

async function cargarUsuarios() {
  const res = await fetch(`${API}/usuarios`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) return;
  const usuarios = await res.json();
  const tabla = document.getElementById("tabla-usuarios");
  tabla.innerHTML = `
    <tr><th>Nombre</th><th>Email</th><th>Rol</th></tr>
    ${usuarios.map(u => `<tr><td>${u.nombre}</td><td>${u.email}</td><td>${u.rol}</td></tr>`).join("")}
  `;
}

function cerrarSesion() {
  token = null;
  if (intervaloVideo) clearInterval(intervaloVideo);
  document.getElementById("dashboard-view").classList.add("hidden");
  document.getElementById("login-view").classList.remove("hidden");
  document.getElementById("email").value = "";
  document.getElementById("password").value = "";
}