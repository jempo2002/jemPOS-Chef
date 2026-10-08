/* Caja: en un gasto Mixto pide cuanto sale en efectivo y muestra el resto. */
(function () {
  'use strict';
  var metodo = document.getElementById('gasto-metodo');
  if (!metodo) return;
  var bloque = document.getElementById('gasto-bloque-mixto');
  var monto = document.getElementById('gasto-monto');
  var efectivo = document.getElementById('gasto-efectivo');
  var resto = document.getElementById('gasto-transferencia');

  function numero(v) { return Number(String(v || '').replace(/[^\d]/g, '')) || 0; }
  function pesos(v) { return '$' + Math.round(v).toLocaleString('es-CO'); }

  function pintar() {
    var mixto = metodo.value === 'mixto';
    bloque.hidden = !mixto;
    efectivo.required = mixto;
    var diferencia = numero(monto.value) - numero(efectivo.value);
    resto.textContent = mixto && numero(monto.value) ? 'Transferencia ' + pesos(Math.max(0, diferencia)) : '';
  }

  [metodo, monto, efectivo].forEach(function (campo) {
    campo.addEventListener('input', pintar);
    campo.addEventListener('change', pintar);
  });
  pintar();
})();
