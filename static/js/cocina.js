/* Pantalla de cocina o bar. Consulta cada 5 s solo lo que cambio desde la
 * consulta anterior y suena cuando llega una comanda nueva. Tocar una
 * comanda la pasa al siguiente estado. */
(function () {
  'use strict';

  var estacion = document.getElementById('cocina').dataset.estacion;
  var SIGUIENTE = { nueva: 'preparando', preparando: 'lista', lista: 'entregada' };
  var ACCION = { nueva: 'Empezar', preparando: 'Marcar lista', lista: 'Entregada' };
  var comandas = {};
  var desde = null;
  var primera = true;
  var audio = null;

  function el(tag, clase, texto) {
    var n = document.createElement(tag);
    if (clase) n.className = clase;
    if (texto !== undefined && texto !== null) n.textContent = texto;
    return n;
  }

  function sonar() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      var o = audio.createOscillator();
      var g = audio.createGain();
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.25, audio.currentTime);
      g.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.6);
      o.connect(g); g.connect(audio.destination);
      o.start(); o.stop(audio.currentTime + 0.6);
    } catch (_) { /* sin audio */ }
  }

  function avanzar(c) {
    Chef.api('POST', '/api/cocina/comandas/' + c.id_comanda + '/estado', { estado: SIGUIENTE[c.estado] }).then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg, true); desde = null; }
      consultar();
    }, function () { Chef.mostrar('Sin conexión. Intenta de nuevo.', true); });
  }

  function tarjeta(c) {
    var t = el('article', 'comanda comanda--' + c.estado + (c.minutos >= 20 ? ' comanda--tarde' : ''));
    var cab = el('header', 'comanda__cab');
    cab.appendChild(el('strong', '', (c.llevar ? '' : 'Mesa ') + c.mesa));
    cab.appendChild(el('span', '', '#' + c.numero + ' · ' + c.minutos + ' min'));
    t.appendChild(cab);
    var ul = el('ul', 'comanda__items');
    c.items.forEach(function (i) {
      // Un adicional (salsa, extra) sale debajo de su plato.
      var li = el('li', (i.anulado ? 'anulado' : '') + (i.adicional ? ' adicional' : ''),
        (i.adicional ? '+ ' : '') + i.cantidad + ' × ' + i.nombre + (i.anulado ? ' (ANULADO)' : ''));
      if (i.nota) li.appendChild(el('small', 'comanda__nota', i.nota));
      ul.appendChild(li);
    });
    t.appendChild(ul);
    var b = el('button', 'btn ' + (c.estado === 'lista' ? '' : 'btn--primario'), ACCION[c.estado]);
    b.type = 'button';
    b.addEventListener('click', function () { b.disabled = true; avanzar(c); });
    t.appendChild(b);
    return t;
  }

  function pintar() {
    ['nueva', 'preparando', 'lista'].forEach(function (estado) {
      var col = document.getElementById('col-' + estado);
      col.textContent = '';
      var lista = Object.keys(comandas).map(function (k) { return comandas[k]; })
        .filter(function (c) { return c.estado === estado; })
        .sort(function (a, b) { return a.numero - b.numero; });
      lista.forEach(function (c) { col.appendChild(tarjeta(c)); });
      document.getElementById('n-' + estado).textContent = lista.length;
    });
  }

  function consultar() {
    var url = '/api/cocina/comandas?estacion=' + estacion + (desde ? '&desde=' + encodeURIComponent(desde) : '');
    Chef.api('GET', url).then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
      var previas = comandas;
      if (!desde) comandas = {};
      var nuevas = 0;
      datos.comandas.forEach(function (c) {
        if (!previas[c.id_comanda] && c.estado === 'nueva') nuevas += 1;
        if (c.estado === 'entregada') delete comandas[c.id_comanda];
        else comandas[c.id_comanda] = c;
      });
      if (nuevas && !primera) sonar();
      primera = false;
      desde = datos.desde;
      document.getElementById('estado-cocina').textContent = 'Actualizado ' +
        new Date().toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      pintar();
    }, function () {
      document.getElementById('estado-cocina').textContent = 'Sin conexión. Reintentando…';
    });
  }

  /* Los minutos de cada comanda se recalculan con una consulta completa cada minuto. */
  window.setInterval(consultar, 5000);
  window.setInterval(function () { desde = null; }, 60000);
  consultar();
})();
