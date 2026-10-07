/* jemPOS Chef: utilidades de las pantallas del salon y la cola sin conexion.
 *
 * Cola sin conexion (misma idea que static/js/caja.js de jemPOS): si una
 * peticion del pedido falla por red, queda guardada en el dispositivo
 * (localStorage) y se reenvia sola al volver internet. Cada linea y cada
 * cobro llevan un uuid, asi que reenviar nunca duplica: el servidor ignora lo
 * que ya tiene. Si el servidor rechaza algo (sin inventario, caja cerrada),
 * queda marcado con el motivo y se puede reintentar o descartar.
 */
(function () {
  'use strict';

  var CLAVE_COLA = 'chef_cola_v1';
  var csrf = document.querySelector('meta[name="csrf-token"]');
  var sincronizando = false;

  function leer(clave, defecto) {
    try {
      var crudo = window.localStorage.getItem(clave);
      return crudo ? JSON.parse(crudo) : defecto;
    } catch (_) { return defecto; }
  }

  function guardar(clave, valor) {
    try { window.localStorage.setItem(clave, JSON.stringify(valor)); } catch (_) { /* lleno o privado */ }
  }

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }

  function pesos(valor) {
    return '$' + Math.round(Number(valor) || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  }

  function mostrar(msg, error) {
    var caja = document.getElementById('mensaje');
    if (!caja) return;
    caja.textContent = msg;
    caja.className = 'aviso aviso--' + (error ? 'error' : 'ok');
    caja.hidden = false;
    window.clearTimeout(mostrar._t);
    mostrar._t = window.setTimeout(function () { caja.hidden = true; }, error ? 8000 : 3500);
  }

  function ErrorRed() { this.red = true; }

  /* Llama a la API. Resuelve con el JSON (ok true o false); rechaza con
   * ErrorRed si no hubo respuesta. */
  function api(metodo, url, cuerpo) {
    return fetch(url, {
      method: metodo,
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRFToken': csrf ? csrf.content : ''
      },
      body: cuerpo ? JSON.stringify(cuerpo) : null
    }).then(function (r) {
      if (r.status === 401) { window.location.href = '/login'; return new Promise(function () {}); }
      return r.json().catch(function () { return { ok: false, msg: 'Error ' + r.status }; }).then(function (data) {
        if (data && data.code === 'sin_sede') window.location.href = '/seleccionar-sede';
        return data;
      });
    }, function () { throw new ErrorRed(); });
  }

  /* --- cola ------------------------------------------------------------ */

  function cola() { return leer(CLAVE_COLA, []); }

  function encolar(op) {
    var lista = cola();
    lista.push({
      id: uuid(), metodo: op.metodo, url: op.url, cuerpo: op.cuerpo || null,
      etiqueta: op.etiqueta || '', mesa: op.mesa || null, creado: new Date().toISOString(),
      estado: 'pendiente', error: ''
    });
    guardar(CLAVE_COLA, lista);
    pintarEstado();
  }

  /* Como api(), pero si no hay red guarda la operacion y resuelve con
   * { ok: false, offline: true }. */
  function enviar(metodo, url, cuerpo, op) {
    if (!navigator.onLine) {
      encolar({ metodo: metodo, url: url, cuerpo: cuerpo, etiqueta: op && op.etiqueta, mesa: op && op.mesa });
      return Promise.resolve({ ok: false, offline: true });
    }
    return api(metodo, url, cuerpo).catch(function (e) {
      if (!e || !e.red) throw e;
      encolar({ metodo: metodo, url: url, cuerpo: cuerpo, etiqueta: op && op.etiqueta, mesa: op && op.mesa });
      return { ok: false, offline: true };
    });
  }

  function pendientesDeMesa(idMesa) {
    return cola().filter(function (op) { return String(op.mesa) === String(idMesa); });
  }

  function sincronizar() {
    if (sincronizando || !navigator.onLine) return Promise.resolve();
    var lista = cola().filter(function (op) { return op.estado === 'pendiente'; });
    if (!lista.length) { pintarEstado(); return Promise.resolve(); }
    sincronizando = true;
    pintarEstado();
    var huboCambios = false;
    function siguiente(i) {
      if (i >= lista.length) return Promise.resolve();
      var op = lista[i];
      return api(op.metodo, op.url, op.cuerpo).then(function (data) {
        var actual = cola();
        var idx = actual.findIndex(function (o) { return o.id === op.id; });
        if (idx === -1) return siguiente(i + 1);
        if (data && data.ok) {
          actual.splice(idx, 1);
          huboCambios = true;
        } else {
          actual[idx].estado = 'fallo';
          actual[idx].error = (data && data.msg) || 'Rechazado por el servidor.';
        }
        guardar(CLAVE_COLA, actual);
        return siguiente(i + 1);
      }, function () { /* sin red otra vez: se sigue en el proximo intento */ });
    }
    return siguiente(0).then(function () {
      sincronizando = false;
      pintarEstado();
      var fallos = cola().filter(function (o) { return o.estado === 'fallo'; });
      if (fallos.length) mostrar('No se pudo sincronizar: ' + fallos[0].etiqueta + '. ' + fallos[0].error, true);
      else if (huboCambios) mostrar('Pedidos guardados sin conexión ya sincronizados.', false);
      if (huboCambios) document.dispatchEvent(new CustomEvent('chef:sincronizado'));
    });
  }

  function reintentar(id) {
    var lista = cola();
    lista.forEach(function (o) { if (!id || o.id === id) { o.estado = 'pendiente'; o.error = ''; } });
    guardar(CLAVE_COLA, lista);
    return sincronizar();
  }

  function descartar(id) {
    guardar(CLAVE_COLA, cola().filter(function (o) { return o.id !== id; }));
    pintarEstado();
  }

  function pintarEstado() {
    var chip = document.getElementById('cola-estado');
    if (!chip) return;
    var lista = cola();
    var fallos = lista.filter(function (o) { return o.estado === 'fallo'; }).length;
    var n = lista.length;
    chip.hidden = navigator.onLine && n === 0;
    chip.className = 'chip chip--cola' + (fallos ? ' chip--error' : (navigator.onLine ? '' : ' chip--offline'));
    if (!navigator.onLine) chip.textContent = 'Sin conexión' + (n ? ' · ' + n + ' guardado' + (n === 1 ? '' : 's') : '');
    else if (sincronizando) chip.textContent = 'Sincronizando ' + n + '…';
    else if (fallos) chip.textContent = fallos + ' sin sincronizar · ver';
    else chip.textContent = n + ' por sincronizar';
    chip.title = lista.map(function (o) { return o.etiqueta + (o.error ? ': ' + o.error : ''); }).join('\n');
  }

  function verCola() {
    var lista = cola();
    if (!lista.length) return;
    var fallo = lista.find(function (o) { return o.estado === 'fallo'; });
    if (!fallo) { sincronizar(); return; }
    var texto = fallo.etiqueta + '\n' + fallo.error + '\n\nAceptar: reintentar. Cancelar: descartarlo.';
    if (window.confirm(texto)) reintentar(fallo.id); else if (window.confirm('¿Descartar "' + fallo.etiqueta + '"? No se enviará.')) descartar(fallo.id);
  }

  window.addEventListener('online', function () { pintarEstado(); sincronizar(); });
  window.addEventListener('offline', pintarEstado);
  document.addEventListener('DOMContentLoaded', function () {
    var chip = document.getElementById('cola-estado');
    if (chip) chip.addEventListener('click', verCola);
    pintarEstado();
    sincronizar();
    window.setInterval(sincronizar, 20000);
  });

  window.Chef = {
    api: api, enviar: enviar, uuid: uuid, pesos: pesos, mostrar: mostrar, leer: leer, guardar: guardar,
    pendientesDeMesa: pendientesDeMesa, sincronizar: sincronizar, ErrorRed: ErrorRed
  };
})();
