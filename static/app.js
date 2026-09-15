const API = "";
let token = null;

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

  if (!nombre || !email || !password) {
    errorMsg.textContent = "Completa todos los campos";
    return;
  }

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
  document.getElementById("reg-nombre").value = "";
  document.getElementById("reg-email").value = "";
  document.getElementById("reg-password").value = "";
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
  if (!res.ok) {
    errorMsg.textContent = "Email o contraseña incorrectos";
    return;
  }
  const data = await res.json();
  token = data.access_token;
  await cargarDashboard();
}

async function cargarDashboard() {
  const res = await fetch(`${API}/usuarios/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const usuario = await res.json();

  document.getElementById("login-view").classList.add("hidden");
  document.getElementById("registro-view").classList.add("hidden");
  document.getElementById("dashboard-view").classList.remove("hidden");
  document.getElementById("saludo").textContent = `Hola, ${usuario.nombre}`;
  document.getElementById("rol-badge").textContent = usuario.rol;

  if (usuario.rol === "administrador" || usuario.rol === "operador") {
    document.getElementById("seccion-alertas").classList.remove("hidden");
    cargarAlertas();
  }
  if (usuario.rol === "administrador" || usuario.rol === "tecnico") {
    document.getElementById("seccion-dispositivos").classList.remove("hidden");
    cargarDispositivos();
  }
}

async function cargarAlertas() {
  const res = await fetch(`${API}/alertas`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const alertas = await res.json();
  const cont = document.getElementById("lista-alertas");
  cont.innerHTML = alertas.length
    ? alertas.map(a => `<div class="item"><b>${a.tipo}</b> — ${a.descripcion}<br><small>${a.timestamp}</small></div>`).join("")
    : "<p>Sin alertas registradas todavía.</p>";
}

async function cargarDispositivos() {
  const res = await fetch(`${API}/dispositivos`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const dispositivos = await res.json();
  const cont = document.getElementById("lista-dispositivos");
  cont.innerHTML = dispositivos.length
    ? dispositivos.map(d => `<div class="item"><b>${d.nombre}</b> — ${d.activo ? "Activo" : "Inactivo"}</div>`).join("")
    : "<p>Sin dispositivos registrados todavía.</p>";
}

function cerrarSesion() {
  token = null;
  document.getElementById("dashboard-view").classList.add("hidden");
  document.getElementById("login-view").classList.remove("hidden");
  document.getElementById("email").value = "";
  document.getElementById("password").value = "";
}