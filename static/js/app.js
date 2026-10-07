/* jemPOS Chef: formularios y botones que llaman a la API.
 *
 *   <form data-api="POST /api/sedes">       envia los campos como JSON
 *   <button data-api="DELETE /api/sedes/3" data-confirmar="...">
 *
 * Sin JS inline (la CSP lo bloquea). Con ok recarga la pagina; con error
 * muestra el mensaje y, si es un limite del plan, el enlace para subir.
 */
(function () {
  'use strict';

  var csrf = document.querySelector('meta[name="csrf-token"]');

  function mostrar(msg, error, enlace) {
    var caja = document.getElementById('mensaje');
    if (!caja) { window.alert(msg); return; }
    caja.textContent = msg;
    caja.className = 'aviso aviso--' + (error ? 'error' : 'ok');
    if (enlace && enlace.url) {
      var a = document.createElement('a');
      a.href = enlace.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = enlace.texto;
      caja.appendChild(document.createTextNode(' '));
      caja.appendChild(a);
    }
    caja.hidden = false;
    caja.scrollIntoView({ block: 'nearest' });
  }

  function llamar(spec, cuerpo, boton) {
    var partes = spec.split(' ');
    if (boton) boton.disabled = true;
    return fetch(partes[1], {
      method: partes[0],
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRFToken': csrf ? csrf.content : ''
      },
      body: cuerpo ? JSON.stringify(cuerpo) : null
    })
      .then(function (r) {
        if (r.status === 401) { window.location.href = '/login'; return null; }
        return r.json().catch(function () { return { ok: false, msg: 'Error ' + r.status }; });
      })
      .then(function (data) {
        if (!data) return;
        if (data.ok) {
          mostrar(data.msg || 'Listo.', false);
          window.setTimeout(function () { window.location.reload(); }, 600);
          return;
        }
        if (data.code === 'sin_sede') { window.location.href = '/seleccionar-sede'; return; }
        mostrar(data.msg || 'No se pudo completar.', true,
          data.code === 'limite_plan' ? { url: data.accion_url, texto: data.accion_texto } : null);
      })
      .catch(function () { mostrar('Sin conexión. Intenta de nuevo.', true); })
      .finally(function () { if (boton) boton.disabled = false; });
  }

  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    if (!form.dataset || !form.dataset.api) return;
    ev.preventDefault();
    var datos = {};
    new FormData(form).forEach(function (v, k) { datos[k] = v; });
    llamar(form.dataset.api, datos, form.querySelector('[type="submit"]'));
  });

  document.addEventListener('click', function (ev) {
    var boton = ev.target.closest('button[data-api]');
    if (!boton) return;
    if (boton.dataset.confirmar && !window.confirm(boton.dataset.confirmar)) return;
    llamar(boton.dataset.api, null, boton);
  });
})();
